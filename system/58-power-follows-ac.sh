#!/usr/bin/env bash
# 58-power-follows-ac.sh — GNOME's Power Mode follows the charger, and a
# laptop left asleep on battery powers off instead of draining.
#
# The owner asked for Performance on the charger, Power Saver on battery, and a
# full power-off once the laptop has been asleep on battery for Y minutes
# (2026-11-09; "after 60 min asleep", chosen over a limit counted from the
# unplugging, which would cut off work in progress). None of that is a setting
# on Ubuntu 26.04:
#
#   - power-profiles-daemon keeps whatever profile was picked last and never
#     looks at the charger. GNOME's only automatic switch is "Automatic Power
#     Saver", which acts at LOW battery, not on unplugging.
#   - GNOME's "Automatic Suspend on battery" (sleep-inactive-battery-timeout)
#     already suspends after idle time, which is what was wanted for suspend,
#     so suspend is left to GNOME; this script never suspends anything.
#   - Nothing powers off a sleeping laptop. suspend-then-hibernate is that
#     idea with hibernate, but Ubuntu's kernel refuses to hibernate under
#     Secure Boot (lockdown), which the owner's machine has on
#     (`mokutil --sb-state`, 2026-11-09). Hence power-off, not hibernate.
#
# What is installed, and why each piece:
#
#   /usr/local/sbin/zenduo-power        decides; extracted and tested by tests/test-lib.sh
#   /etc/zenduo/power.conf              the two profiles and the minutes, parsed, never sourced
#   zenduo-power.service                at boot and, started by the udev rule below,
#                                       on every charger or battery change
#   zenduo-power-sleep.service          just before sleep: on battery, arm the power-off
#   zenduo-power-resume.service         just after waking: early, cancel it; late, power off
#   /etc/udev/rules.d/71-zenduo-power.rules
#
# The Power Mode acts on EDGES: the source it last acted on is kept in /run, so
# a run that finds the same source does nothing. A Power Mode picked by hand
# therefore lasts until the next plug or unplug. /run is empty after a boot, so
# every boot acts once.
#
# The power-off is a transient systemd timer (systemd-run) set on WALL-CLOCK
# time with WakeSystem=true, so the RTC wakes the laptop to power it off. Not
# OnActiveSec: that is CLOCK_MONOTONIC, which stops while asleep (docs/DESIGN.md,
# "Traps"), so it would never come due. WakeSystem needs the system manager
# (systemd.timer(5)), which is why this is a root feature and not a user daemon.
# Waking the laptop cancels the timer, so the clock restarts at the next sleep.
#
# With --separate-fan (system/57-power-profiles.sh, the default) Performance and
# Power Saver move the CPU's energy preference only; the fan mode stays Fn+F's.
#
# VERIFY-ON-HW: which power_supply the Duo reports its charger as (an ACPI
# "Mains" supply is expected; the script falls back to the battery's status),
# and that the RTC wakes it from s2idle for the power-off.
#
# Opt in; the switch is kept across re-runs:
#   --power-follows-ac          install (Performance on AC, Power Saver on battery)
#   --battery-shutdown MIN      power off after MIN minutes asleep on battery
#                               (0 = never); implies --power-follows-ac
#   --no-power-follows-ac       remove everything above
# Neither flag: whatever is installed stays as it is, values included. Edits to
# /etc/zenduo/power.conf are kept too.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

require_root

SCRIPT_DST=/usr/local/sbin/zenduo-power
UNIT_DIR=/etc/systemd/system
UNITS=(zenduo-power.service zenduo-power-sleep.service zenduo-power-resume.service)
RULE_DST=/etc/udev/rules.d/71-zenduo-power.rules
CONF_DST=/etc/zenduo/power.conf

remove_feature() {
  local unit present=0
  for unit in "${UNITS[@]}"; do [ -e "$UNIT_DIR/$unit" ] && present=1; done
  if [ -e "$SCRIPT_DST" ] || [ -e "$RULE_DST" ] || [ -e "$CONF_DST" ]; then present=1; fi
  if [ "$present" = 0 ]; then
    log "power-follows-ac disabled (--no-power-follows-ac)"
    return 0
  fi
  log "power-follows-ac disabled — removing it"
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY RUN: would remove $SCRIPT_DST ${UNITS[*]} $RULE_DST $CONF_DST"
    mark_change
    return 0
  fi
  systemctl stop zenduo-sleep-poweroff.timer >/dev/null 2>&1 || true
  systemctl disable "${UNITS[@]}" >/dev/null 2>&1 || true
  for unit in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$unit"; done
  rm -f "$SCRIPT_DST" "$RULE_DST" "$CONF_DST" /run/zenduo/power-source /run/zenduo/poweroff-at
  rmdir /etc/zenduo 2>/dev/null || true
  systemctl daemon-reload
  udevadm control --reload || true
  mark_change
}

# KEY DEFAULT: the value already in $CONF_DST, so a re-run keeps the owner's edits.
kept_value() {
  local key="$1" val="$2" line k v
  if [ -r "$CONF_DST" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in '#'*|'') continue ;; *=*) ;; *) continue ;; esac
      k="${line%%=*}" v="${line#*=}"
      k="${k//[[:space:]]/}" v="${v//[[:space:]]/}"
      v="${v%\"}" v="${v#\"}"
      [ "$k" = "$key" ] && val="$v"
    done < "$CONF_DST"
  fi
  printf '%s\n' "$val"
}

want="${ZENDUO_POWER_FOLLOWS_AC:-}"
minutes="${ZENDUO_BATTERY_SHUTDOWN:-}"
if [ -n "$minutes" ]; then
  if ! [[ "$minutes" =~ ^[0-9]+$ ]] || [ "$minutes" -gt 1440 ]; then
    die "--battery-shutdown must be 0-1440 minutes"
  fi
  [ -n "$want" ] || want=1
fi
if [ -z "$want" ]; then
  if [ -e "$UNIT_DIR/${UNITS[0]}" ]; then
    want=1
  else
    log "power-follows-ac not installed (opt in: --power-follows-ac, --battery-shutdown MIN)"
    exit 0
  fi
fi
if [ "$want" != 1 ]; then
  remove_feature
  exit 0
fi

SYSTEMCTL="$(command -v systemctl)" || die "systemctl not found — this feature needs systemd"

[ -n "$minutes" ] || minutes="$(kept_value SHUTDOWN_AFTER_SLEEP_MIN 0)"
ac_profile="$(kept_value AC_PROFILE performance)"
battery_profile="$(kept_value BATTERY_PROFILE power-saver)"

tmp_script="$(mktemp)"
trap 'rm -f "$tmp_script"' EXIT

cat > "$tmp_script" <<'POWER'
#!/usr/bin/env bash
# zenduo-power — GNOME's Power Mode follows the charger, and a laptop left
# asleep on battery powers off instead of draining.
#
# Installed by linux-on-zenbook-duo's system/58-power-follows-ac.sh; that file
# says why it is built this way. Run by three units:
#   zenduo-power.service          at boot, and on every charger or battery change (udev)
#   zenduo-power-sleep.service    just before the laptop goes to sleep
#   zenduo-power-resume.service   just after it wakes up
#
#   zenduo-power            act if the power source changed since the last run
#   zenduo-power --reset    act as if it had (after editing /etc/zenduo/power.conf)
#   zenduo-power --sleeping on battery, set the power-off for SHUTDOWN_AFTER_SLEEP_MIN from now
#   zenduo-power --resumed  woken early: cancel it; woken at or after it: power off
#   zenduo-power --deadline the timer fired: power off if still on battery
#   zenduo-power --status   what it sees, and what is scheduled
set -euo pipefail

CONF="${ZENDUO_POWER_CONF:-/etc/zenduo/power.conf}"
SUPPLY_DIR="${ZENDUO_POWER_SUPPLY_DIR:-/sys/class/power_supply}"
RUN_DIR="${ZENDUO_POWER_RUN_DIR:-/run/zenduo}"
STATE_FILE="$RUN_DIR/power-source"
DEADLINE_FILE="$RUN_DIR/poweroff-at"
SELF="${ZENDUO_POWER_SELF:-/usr/local/sbin/zenduo-power}"
OFF_UNIT=zenduo-sleep-poweroff

log() { printf 'zenduo-power: %s\n' "$*"; }

# KEY DEFAULT: the value in $CONF. Parsed, never sourced: root does not execute
# a config file.
conf_get() {
  local key="$1" val="$2" line k v
  if [ -r "$CONF" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in '#'*|'') continue ;; *=*) ;; *) continue ;; esac
      k="${line%%=*}" v="${line#*=}"
      k="${k//[[:space:]]/}" v="${v//[[:space:]]/}"
      v="${v%\"}" v="${v#\"}"
      [ "$k" = "$key" ] && val="$v"
    done < "$CONF"
  fi
  printf '%s\n' "$val"
}

# KEY DEFAULT: a profile power-profiles-daemon knows, or empty (leave it alone).
profile() {
  local p
  p="$(conf_get "$1" "$2")"
  case "$p" in
    performance|balanced|power-saver|'') printf '%s\n' "$p" ;;
    *) log "ignoring $1=$p (want performance, balanced, power-saver or empty)" >&2; echo "" ;;
  esac
}

sleep_minutes() {
  local m
  m="$(conf_get SHUTDOWN_AFTER_SLEEP_MIN 0)"
  if [[ "$m" =~ ^[0-9]+$ ]] && [ "$m" -le 1440 ]; then
    echo "$((10#$m))"
  else
    log "ignoring SHUTDOWN_AFTER_SLEEP_MIN=$m (want 0-1440)" >&2
    echo 0
  fi
}

read_attr() { cat "$1" 2>/dev/null || true; }

# ac | battery | unknown.
#   - any Mains supply online                 -> ac
#   - the laptop's battery charging           -> ac (a charger the kernel does
#                                                not report as Mains)
#   - a Mains supply exists, none online      -> battery
#   - no Mains at all: the battery discharging -> battery, otherwise ac
# "Not charging" (held at the charge limit) is on the charger. Batteries with
# scope Device are a keyboard's or a mouse's, not the laptop's.
power_source() {
  local d type mains=0 batteries=0 charging=0 discharging=0 status
  for d in "$SUPPLY_DIR"/*; do
    [ -r "$d/type" ] || continue
    type="$(read_attr "$d/type")"
    case "$type" in
      Mains)
        mains=1
        if [ "$(read_attr "$d/online")" = 1 ]; then echo ac; return 0; fi
        ;;
      Battery)
        [ "$(read_attr "$d/scope")" = Device ] && continue
        batteries=1
        status="$(read_attr "$d/status")"
        case "$status" in
          Charging) charging=1 ;;
          Discharging) discharging=1 ;;
        esac
        ;;
    esac
  done
  if [ "$charging" = 1 ]; then echo ac; return 0; fi
  if [ "$mains" = 1 ]; then echo battery; return 0; fi
  if [ "$batteries" = 0 ]; then echo unknown; return 0; fi
  if [ "$discharging" = 1 ]; then echo battery; else echo ac; fi
}

set_profile() {
  local want="$1" out
  if [ -z "$want" ]; then
    log "Power Mode left as it is"
    return 0
  fi
  if ! command -v powerprofilesctl >/dev/null 2>&1; then
    log "powerprofilesctl not found — Power Mode unchanged"
    return 0
  fi
  if out="$(powerprofilesctl set "$want" 2>&1)"; then
    log "Power Mode: $want"
  else
    log "could not set Power Mode $want: ${out:-no output}"
  fi
}

cancel_poweroff() {
  systemctl stop "$OFF_UNIT.timer" >/dev/null 2>&1 || true
  systemctl reset-failed "$OFF_UNIT.timer" "$OFF_UNIT.service" >/dev/null 2>&1 || true
  rm -f "$DEADLINE_FILE"
}

# Going to sleep on battery: a one-shot timer SHUTDOWN_AFTER_SLEEP_MIN from
# now, on wall-clock time (a monotonic one would stop while asleep), with
# WakeSystem=true so the RTC wakes the laptop for it. RemainAfterElapse=no
# unloads it once it fired, so the name is free for the next sleep.
arm_poweroff() {
  local minutes deadline
  cancel_poweroff
  if [ "$(power_source)" != battery ]; then
    return 0
  fi
  minutes="$(sleep_minutes)"
  if [ "$minutes" -le 0 ]; then
    log "asleep on battery, no power-off (SHUTDOWN_AFTER_SLEEP_MIN=0)"
    return 0
  fi
  deadline=$(( $(date +%s) + minutes * 60 ))
  mkdir -p "$RUN_DIR"
  printf '%s\n' "$deadline" > "$DEADLINE_FILE"
  systemd-run --quiet --unit="$OFF_UNIT" \
    --description="zenduo: power off after $minutes min asleep on battery" \
    --on-calendar="$(date -u -d "@$deadline" '+%Y-%m-%d %H:%M:%S') UTC" \
    --timer-property=AccuracySec=1s --timer-property=RemainAfterElapse=no \
    --timer-property=WakeSystem=true \
    "$SELF" --deadline
  log "asleep on battery: powering off at $(date -d "@$deadline" '+%H:%M') unless woken first"
}

# The timer fired, or the laptop woke at or after the deadline. A charger
# plugged in while it slept cancels it.
deadline_reached() {
  rm -f "$DEADLINE_FILE"
  if [ "$(power_source)" = battery ]; then
    log "asleep on battery for $(sleep_minutes) min — powering off"
    systemctl poweroff
  else
    log "the power-off time came, but the charger is in — staying on"
  fi
}

# Just woke up. The RTC wake for the power-off is a resume too, and this unit
# may run before the timer does, so it checks the time itself rather than
# cancelling a power-off that is due.
resumed() {
  local deadline
  deadline="$(read_attr "$DEADLINE_FILE")"
  if [[ "$deadline" =~ ^[0-9]+$ ]]; then
    if [ "$(date +%s)" -ge "$deadline" ]; then
      deadline_reached
      return 0
    fi
    log "woke before the power-off — cancelled"
    cancel_poweroff
  fi
  act 0
}

show_status() {
  local deadline
  echo "power source:    $(power_source)"
  echo "last acted on:   $(read_attr "$STATE_FILE")"
  if command -v powerprofilesctl >/dev/null 2>&1; then
    echo "Power Mode now:  $(powerprofilesctl get 2>/dev/null || echo '?')"
  fi
  echo "on the charger:  $(profile AC_PROFILE performance)"
  echo "on battery:      $(profile BATTERY_PROFILE power-saver)"
  echo "asleep on battery: power off after $(sleep_minutes) min (0 = never)"
  deadline="$(read_attr "$DEADLINE_FILE")"
  if [[ "$deadline" =~ ^[0-9]+$ ]]; then
    echo "power-off:       at $(date -d "@$deadline" '+%Y-%m-%d %H:%M')"
  else
    echo "power-off:       not scheduled (only set while asleep on battery)"
  fi
}

act() { # <ignore-last 0|1>
  local src last=""
  src="$(power_source)"
  if [ "$src" = unknown ]; then
    log "no charger or battery in $SUPPLY_DIR — nothing to do"
    return 0
  fi
  [ "$1" = 1 ] || last="$(read_attr "$STATE_FILE")"
  if [ "$src" = "$last" ]; then
    return 0
  fi
  case "$src" in
    ac)
      log "on the charger"
      set_profile "$(profile AC_PROFILE performance)"
      ;;
    battery)
      log "on battery"
      set_profile "$(profile BATTERY_PROFILE power-saver)"
      ;;
  esac
  # Written last: a run that failed half-way is retried by the next event.
  mkdir -p "$RUN_DIR"
  printf '%s\n' "$src" > "$STATE_FILE"
}

case "${1:-}" in
  "")         act 0 ;;
  --reset)    act 1 ;;
  --sleeping) arm_poweroff ;;
  --resumed)  resumed ;;
  --deadline) deadline_reached ;;
  --status)   show_status ;;
  *) echo "usage: zenduo-power [--reset | --sleeping | --resumed | --deadline | --status]" >&2; exit 2 ;;
esac
POWER

SLEEP_TARGETS="suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target"

read -r -d '' UNIT_MAIN <<EOF_UNIT || true
[Unit]
Description=zenduo: set the Power Mode for the charger state
Documentation=https://github.com/Toldry/linux-on-zenbook-duo
After=power-profiles-daemon.service

[Service]
Type=oneshot
ExecStart=$SCRIPT_DST

[Install]
WantedBy=multi-user.target
EOF_UNIT

# Before sleep.target: done before systemd-suspend.service puts the laptop to sleep.
read -r -d '' UNIT_SLEEP <<EOF_UNIT || true
[Unit]
Description=zenduo: arm the power-off for a laptop asleep on battery
Documentation=https://github.com/Toldry/linux-on-zenbook-duo
Before=sleep.target

[Service]
Type=oneshot
ExecStart=$SCRIPT_DST --sleeping

[Install]
WantedBy=sleep.target
EOF_UNIT

# After the sleep targets: started by them, it runs once the laptop has woken.
read -r -d '' UNIT_RESUME <<EOF_UNIT || true
[Unit]
Description=zenduo: on waking, cancel the power-off, or carry it out if it is due
Documentation=https://github.com/Toldry/linux-on-zenbook-duo
After=$SLEEP_TARGETS

[Service]
Type=oneshot
ExecStart=$SCRIPT_DST --resumed

[Install]
WantedBy=$SLEEP_TARGETS
EOF_UNIT

read -r -d '' RULE <<EOF_RULE || true
# zenduo — tell zenduo-power when the charger or the laptop's battery changes.
# Written by system/58-power-follows-ac.sh; --no-power-follows-ac removes it.
# A keyboard's or mouse's battery has POWER_SUPPLY_SCOPE=Device and is skipped.
SUBSYSTEM=="power_supply", ACTION=="change", ENV{POWER_SUPPLY_TYPE}=="Mains", RUN+="$SYSTEMCTL start --no-block ${UNITS[0]}"
SUBSYSTEM=="power_supply", ACTION=="change", ENV{POWER_SUPPLY_TYPE}=="Battery", ENV{POWER_SUPPLY_SCOPE}!="Device", RUN+="$SYSTEMCTL start --no-block ${UNITS[0]}"
EOF_RULE

read -r -d '' CONF <<EOF_CONF || true
# zenduo — GNOME's Power Mode follows the charger; a laptop asleep on battery
# powers off after a while. Written by system/58-power-follows-ac.sh. A re-run
# keeps the values below; --battery-shutdown MIN changes the last one.
# The profiles apply from the next plug or unplug; to apply now:
#   sudo zenduo-power --reset
#
# Power Mode on the charger and on battery: performance | balanced | power-saver,
# or empty to leave it alone.
AC_PROFILE=$ac_profile
BATTERY_PROFILE=$battery_profile
# Minutes ASLEEP on battery before the laptop wakes itself and powers off.
# Waking it first cancels this; the count restarts at the next sleep. 0 = never.
# When it falls asleep is GNOME's setting (Settings > Power > Automatic Suspend).
SHUTDOWN_AFTER_SLEEP_MIN=$minutes
EOF_CONF

changed=0

if cmp -s "$tmp_script" "$SCRIPT_DST" 2>/dev/null; then
  log "up to date: $SCRIPT_DST"
else
  log "installing $SCRIPT_DST"
  run install -o root -g root -m 0755 "$tmp_script" "$SCRIPT_DST"
  changed=1
fi
if install_conf "$UNIT_DIR/${UNITS[0]}" "$UNIT_MAIN"; then changed=1; fi
if install_conf "$UNIT_DIR/${UNITS[1]}" "$UNIT_SLEEP"; then changed=1; fi
if install_conf "$UNIT_DIR/${UNITS[2]}" "$UNIT_RESUME"; then changed=1; fi
if install_conf "$RULE_DST" "$RULE"; then changed=1; fi
if install_conf "$CONF_DST" "$CONF"; then changed=1; fi

if [ "$DRY_RUN" = 1 ]; then
  log "DRY RUN: would enable ${UNITS[*]} and apply the Power Mode now"
elif [ "$changed" = 1 ]; then
  systemctl daemon-reload
  udevadm control --reload || true
  systemctl enable "${UNITS[@]}" >/dev/null
  # --reset: act on the current source even if it is the one last seen, so new
  # profiles apply now.
  "$SCRIPT_DST" --reset || warn "zenduo-power --reset failed — see: journalctl -u ${UNITS[0]}"
  log "power-follows-ac: $ac_profile on the charger, $battery_profile on battery, power-off after $minutes min asleep on battery (0 = never)"
else
  systemctl enable "${UNITS[@]}" >/dev/null 2>&1 || true
  log "power-follows-ac already installed"
fi
