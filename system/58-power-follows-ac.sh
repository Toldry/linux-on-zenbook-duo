#!/usr/bin/env bash
# 58-power-follows-ac.sh — GNOME's Power Mode follows the charger, and the
# laptop powers off after a while unplugged.
#
# The owner asked for Performance on the charger, Power Saver on battery, and a
# full power-off after Y minutes on battery (2026-11-09). None of that is a
# setting on Ubuntu 26.04:
#
#   - power-profiles-daemon keeps whatever profile was picked last and never
#     looks at the charger. GNOME's only automatic switch is "Automatic Power
#     Saver", which acts at LOW battery, not on unplugging.
#   - GNOME's "Automatic Suspend on battery" (sleep-inactive-battery-timeout)
#     counts idle time, not time since unplugging. That one is fine as it is,
#     so suspend is left to GNOME; this script does not suspend anything.
#   - Nothing in GNOME, logind or UPower powers off after time on battery;
#     UPower's CriticalPowerAction fires at a battery percentage.
#   - suspend-then-hibernate would come closest, but Ubuntu's kernel refuses to
#     hibernate under Secure Boot (lockdown), which the owner's machine has on
#     (`mokutil --sb-state`, 2026-11-09). Hence power-off, not hibernate.
#
# What is installed, and why each piece:
#
#   /usr/local/sbin/zenduo-power        decides; extracted and tested by tests/test-lib.sh
#   /etc/zenduo/power.conf              the two profiles and the minutes, parsed, never sourced
#   zenduo-power.service (oneshot)      runs it at boot and after every resume
#   /etc/udev/rules.d/71-zenduo-power.rules
#                                       starts the service on a charger or battery change
#
# It acts on EDGES: the source it last acted on is kept in /run, so a run that
# finds the same source does nothing. A Power Mode picked by hand therefore lasts
# until the next plug or unplug, and a battery uevent every few minutes costs a
# file read. /run is empty after a boot, so every boot acts once.
#
# The power-off is a transient systemd timer (systemd-run) set on WALL-CLOCK
# time with WakeSystem=true, so the RTC wakes the laptop from suspend to power it
# off. Not OnActiveSec: that is CLOCK_MONOTONIC, which stops while suspended
# (docs/DESIGN.md, "Traps"), so "60 minutes" would mean 60 minutes awake.
# WakeSystem needs the system manager (systemd.timer(5)), which is why this is a
# root feature and not a user daemon. A second timer, WARN_MIN before, puts up a
# desktop notification. Plugging in cancels both.
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
#   --battery-shutdown MIN      power off after MIN minutes on battery (0 = never);
#                               implies --power-follows-ac
#   --no-power-follows-ac       remove everything above
# Neither flag: whatever is installed stays as it is, values included. Edits to
# /etc/zenduo/power.conf are kept too.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

require_root

SCRIPT_DST=/usr/local/sbin/zenduo-power
UNIT_NAME=zenduo-power.service
UNIT_DST=/etc/systemd/system/$UNIT_NAME
RULE_DST=/etc/udev/rules.d/71-zenduo-power.rules
CONF_DST=/etc/zenduo/power.conf

remove_feature() {
  if [ ! -e "$UNIT_DST" ] && [ ! -e "$SCRIPT_DST" ] && [ ! -e "$RULE_DST" ] && [ ! -e "$CONF_DST" ]; then
    log "power-follows-ac disabled (--no-power-follows-ac)"
    return 0
  fi
  log "power-follows-ac disabled — removing it"
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY RUN: would remove $SCRIPT_DST $UNIT_DST $RULE_DST $CONF_DST"
    mark_change
    return 0
  fi
  systemctl stop zenduo-battery-poweroff.timer zenduo-battery-poweroff-warn.timer >/dev/null 2>&1 || true
  systemctl disable "$UNIT_NAME" >/dev/null 2>&1 || true
  rm -f "$SCRIPT_DST" "$UNIT_DST" "$RULE_DST" "$CONF_DST" /run/zenduo/power-source
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
  if [ -e "$UNIT_DST" ]; then
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

DUO_USER="$(target_user)" || die "cannot determine the target user — run via sudo from your own account, or pass --user NAME"
id "$DUO_USER" >/dev/null 2>&1 || die "user '$DUO_USER' does not exist on this machine"
SYSTEMCTL="$(command -v systemctl)" || die "systemctl not found — this feature needs systemd"

[ -n "$minutes" ] || minutes="$(kept_value SHUTDOWN_AFTER_MIN 0)"
ac_profile="$(kept_value AC_PROFILE performance)"
battery_profile="$(kept_value BATTERY_PROFILE power-saver)"

tmp_script="$(mktemp)"
tmp_unit="$(mktemp)"
trap 'rm -f "$tmp_script" "$tmp_unit"' EXIT

cat > "$tmp_script" <<'POWER'
#!/usr/bin/env bash
# zenduo-power — GNOME's Power Mode follows the charger, and the laptop powers
# off after a while unplugged.
#
# Installed by linux-on-zenbook-duo's system/58-power-follows-ac.sh; that file
# says why it is built this way. zenduo-power.service runs it at boot, after
# every resume and, through a udev rule, on every charger or battery change.
#
#   zenduo-power            act if the power source changed since the last run
#   zenduo-power --reset    act as if it had (after editing /etc/zenduo/power.conf)
#   zenduo-power --warn N   tell the desktop user the power-off is N minutes away
#   zenduo-power --status   what it sees, and what is scheduled
set -euo pipefail

CONF="${ZENDUO_POWER_CONF:-/etc/zenduo/power.conf}"
SUPPLY_DIR="${ZENDUO_POWER_SUPPLY_DIR:-/sys/class/power_supply}"
STATE_FILE="${ZENDUO_POWER_STATE:-/run/zenduo/power-source}"
SELF="${ZENDUO_POWER_SELF:-/usr/local/sbin/zenduo-power}"
OFF_UNIT=zenduo-battery-poweroff
WARN_UNIT=zenduo-battery-poweroff-warn
WARN_MIN=5
DUO_USER='@TARGET_USER@'

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

shutdown_minutes() {
  local m
  m="$(conf_get SHUTDOWN_AFTER_MIN 0)"
  if [[ "$m" =~ ^[0-9]+$ ]] && [ "$m" -le 1440 ]; then
    echo "$((10#$m))"
  else
    log "ignoring SHUTDOWN_AFTER_MIN=$m (want 0-1440)" >&2
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
  local unit
  for unit in "$OFF_UNIT" "$WARN_UNIT"; do
    systemctl stop "$unit.timer" >/dev/null 2>&1 || true
    systemctl reset-failed "$unit.timer" "$unit.service" >/dev/null 2>&1 || true
  done
}

# <unit> <epoch seconds> <description> <command...>: a one-shot timer at that
# wall-clock moment. RemainAfterElapse=no unloads it once it fired, so the name
# is free for the next unplug.
at_time() {
  local unit="$1" epoch="$2" description="$3"
  shift 3
  systemd-run --quiet --unit="$unit" --description="$description" \
    --on-calendar="$(date -u -d "@$epoch" '+%Y-%m-%d %H:%M:%S') UTC" \
    --timer-property=AccuracySec=1s --timer-property=RemainAfterElapse=no \
    "$@"
}

schedule_poweroff() {
  local minutes="$1" deadline
  if [ "$minutes" -le 0 ]; then
    log "no power-off on battery (SHUTDOWN_AFTER_MIN=0)"
    return 0
  fi
  if systemctl is-active --quiet "$OFF_UNIT.timer" 2>/dev/null; then
    log "power-off already scheduled"
    return 0
  fi
  cancel_poweroff
  deadline=$(( $(date +%s) + minutes * 60 ))
  at_time "$OFF_UNIT" "$deadline" "zenduo: power off after $minutes min on battery" \
    --timer-property=WakeSystem=true "$(command -v systemctl)" poweroff
  log "on battery: powering off at $(date -d "@$deadline" '+%H:%M') unless the charger comes back"
  if [ "$minutes" -gt "$WARN_MIN" ]; then
    at_time "$WARN_UNIT" $((deadline - WARN_MIN * 60)) "zenduo: warn of the power-off on battery" \
      "$SELF" --warn "$WARN_MIN"
  fi
}

# Best effort: the journal line is the record, the popup is a courtesy.
warn_user() {
  local minutes="$1" uid bus
  log "powering off in $minutes min unless the charger comes back"
  uid="$(id -u "$DUO_USER" 2>/dev/null)" || return 0
  bus="/run/user/$uid/bus"
  [ -S "$bus" ] || return 0
  command -v notify-send >/dev/null 2>&1 || return 0
  runuser -u "$DUO_USER" -- env "DBUS_SESSION_BUS_ADDRESS=unix:path=$bus" \
    notify-send -u critical -a "Battery" "Powering off in $minutes minutes" \
      "The laptop has been unplugged for a while. Plug in the charger to cancel, or save your work." \
    >/dev/null 2>&1 || true
}

show_status() {
  echo "power source:    $(power_source)"
  echo "last acted on:   $(read_attr "$STATE_FILE")"
  if command -v powerprofilesctl >/dev/null 2>&1; then
    echo "Power Mode now:  $(powerprofilesctl get 2>/dev/null || echo '?')"
  fi
  echo "on the charger:  $(profile AC_PROFILE performance)"
  echo "on battery:      $(profile BATTERY_PROFILE power-saver), power off after $(shutdown_minutes) min (0 = never)"
  if systemctl is-active --quiet "$OFF_UNIT.timer" 2>/dev/null; then
    echo "power-off:       $(systemctl show -p NextElapseUSecRealtime --value "$OFF_UNIT.timer" 2>/dev/null)"
  else
    echo "power-off:       not scheduled"
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
      cancel_poweroff
      set_profile "$(profile AC_PROFILE performance)"
      ;;
    battery)
      log "on battery"
      [ "$1" = 1 ] && cancel_poweroff
      set_profile "$(profile BATTERY_PROFILE power-saver)"
      schedule_poweroff "$(shutdown_minutes)"
      ;;
  esac
  # Written last: a run that failed half-way is retried by the next event.
  mkdir -p "$(dirname "$STATE_FILE")"
  printf '%s\n' "$src" > "$STATE_FILE"
}

case "${1:-}" in
  "")       act 0 ;;
  --reset)  act 1 ;;
  --warn)   warn_user "${2:-$WARN_MIN}" ;;
  --status) show_status ;;
  *) echo "usage: zenduo-power [--reset | --warn MINUTES | --status]" >&2; exit 2 ;;
esac
POWER

# The username is fixed at install time, as in 46-speaker-amp.sh.
sed -i "s|@TARGET_USER@|$DUO_USER|" "$tmp_script"

cat > "$tmp_unit" <<'UNIT'
[Unit]
Description=zenduo: set the Power Mode for the charger state, power off after a while unplugged
Documentation=https://github.com/Toldry/linux-on-zenbook-duo
# After the sleep targets: started by them, it runs once the laptop has resumed.
After=power-profiles-daemon.service suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/zenduo-power

[Install]
WantedBy=multi-user.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
UNIT

read -r -d '' RULE <<EOF_RULE || true
# zenduo — tell zenduo-power when the charger or the laptop's battery changes.
# Written by system/58-power-follows-ac.sh; --no-power-follows-ac removes it.
# A keyboard's or mouse's battery has POWER_SUPPLY_SCOPE=Device and is skipped.
SUBSYSTEM=="power_supply", ACTION=="change", ENV{POWER_SUPPLY_TYPE}=="Mains", RUN+="$SYSTEMCTL start --no-block $UNIT_NAME"
SUBSYSTEM=="power_supply", ACTION=="change", ENV{POWER_SUPPLY_TYPE}=="Battery", ENV{POWER_SUPPLY_SCOPE}!="Device", RUN+="$SYSTEMCTL start --no-block $UNIT_NAME"
EOF_RULE

read -r -d '' CONF <<EOF_CONF || true
# zenduo — GNOME's Power Mode follows the charger; power off after a while unplugged.
# Written by system/58-power-follows-ac.sh. A re-run keeps the values below;
# --battery-shutdown MIN changes the last one. Read on every plug and unplug;
# to apply an edit now: sudo zenduo-power --reset
#
# Power Mode on the charger and on battery: performance | balanced | power-saver,
# or empty to leave it alone.
AC_PROFILE=$ac_profile
BATTERY_PROFILE=$battery_profile
# Minutes on battery before the laptop powers off, waking it from suspend if it
# has to. 0 = never. Suspending while idle is GNOME's setting (Settings > Power).
SHUTDOWN_AFTER_MIN=$minutes
EOF_CONF

changed=0

if cmp -s "$tmp_script" "$SCRIPT_DST" 2>/dev/null; then
  log "up to date: $SCRIPT_DST"
else
  log "installing $SCRIPT_DST"
  run install -o root -g root -m 0755 "$tmp_script" "$SCRIPT_DST"
  changed=1
fi
if cmp -s "$tmp_unit" "$UNIT_DST" 2>/dev/null; then
  log "up to date: $UNIT_DST"
else
  log "installing $UNIT_DST"
  run install -o root -g root -m 0644 "$tmp_unit" "$UNIT_DST"
  changed=1
fi
if install_conf "$RULE_DST" "$RULE"; then
  changed=1
fi
if install_conf "$CONF_DST" "$CONF"; then
  changed=1
fi

if [ "$DRY_RUN" = 1 ]; then
  log "DRY RUN: would enable $UNIT_NAME and apply it now"
elif [ "$changed" = 1 ]; then
  systemctl daemon-reload
  udevadm control --reload || true
  systemctl enable "$UNIT_NAME" >/dev/null
  # --reset: act on the current source even if it is the one last seen, so new
  # values apply now and a pending power-off is rescheduled with them.
  "$SCRIPT_DST" --reset || warn "zenduo-power --reset failed — see: journalctl -u $UNIT_NAME"
  log "power-follows-ac: $ac_profile on the charger, $battery_profile on battery, power-off after $minutes min (0 = never)"
else
  systemctl enable "$UNIT_NAME" >/dev/null 2>&1 || true
  log "power-follows-ac already installed"
fi
