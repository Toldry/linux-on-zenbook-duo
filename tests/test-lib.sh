#!/usr/bin/env bash
# tests/test-lib.sh — behavioural tests for system/lib.sh, lib/conf.sh and
# the argument plumbing of install.sh / uninstall.sh.
#
# Run with `make test`, or `sudo make test` to include the tests that need root
# (install_conf writes root-owned files). CI runs it under sudo so nothing is
# skipped. Deliberately no test framework: this has to run on a fresh machine
# before anything is installed, so bash and coreutils are the whole dependency
# list.
#
# Why it exists: `cmd | grep -q` under pipefail returns 141 on a big enough
# writer, so a successful match reads as a failure. shellcheck passes on it,
# the script exits 0 and logs a plausible reason. That bug shipped twice in the
# repo this one was split from. These are tests of the helpers it lived in.

set -uo pipefail   # NOT -e: an assertion failure must be recorded, not fatal

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=../system/lib.sh
source ./system/lib.sh
# shellcheck source=../lib/conf.sh
source ./lib/conf.sh
set +e

PASSED=0 FAILED=0 SKIPPED=0
ok()   { PASSED=$((PASSED + 1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
no()   { FAILED=$((FAILED + 1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }
skip() { SKIPPED=$((SKIPPED + 1)); printf '  \033[33m–\033[0m %s (skipped: %s)\n' "$1" "$2"; }
group(){ printf '\n\033[1m%s\033[0m\n' "$1"; }
is()   { if [ "$2" = "$3" ]; then ok "$1"; else no "$1 — want '$3', got '$2'"; fi; }
succeeds() { local name="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else no "$name — expected exit 0, got $?"; fi; }
fails()    { local name="$1"; shift; if "$@" >/dev/null 2>&1; then no "$name — expected non-zero, got 0"; else ok "$name"; fi; }

# ── out_matches ──────────────────────────────────────────────────────────────
group "out_matches"
succeeds "matches a plain substring"            out_matches $'alpha\nbeta\ngamma' beta
fails    "reports no match"                     out_matches $'alpha\nbeta' delta
succeeds "-E extended regex"                    out_matches $'  Candidate: 1.1.2-3' -E '^[[:space:]]+Candidate: [^([:space:]]'
fails    "-E rejects Candidate: (none)"         out_matches $'  Candidate: (none)' -E '^[[:space:]]+Candidate: [^([:space:]]'
succeeds "-x whole-line match"                  out_matches $'docker\nsudo' -x docker
fails    "empty text never matches"             out_matches '' -E '.'

group "out_matches — the pipefail/SIGPIPE regression"
big="$(seq 1 200000)"
succeeds "matches line 1 of 200k without SIGPIPE" out_matches "$big" -E '^1$'
succeeds "matches the last line too"              out_matches "$big" -E '^200000$'
naive_pipeline_status() { ( set -euo pipefail; printf '%s\n' "$big" | grep -qE '^1$' ); echo $?; }
naive="$(naive_pipeline_status)"
# What the naive pipeline returns depends on the parent's SIGPIPE disposition:
#   141  printf was killed by SIGPIPE (a terminal, a plain shell)
#     1  SIGPIPE is ignored, so the write fails with EPIPE and printf exits 1.
#        GitHub's runner starts every job that way (measured 2026-09-05: the
#        first two CI runs on main failed here with "returned 1").
# Both are the hazard — a successful match reported as failure. Only 0 means
# this bash/grep never hit it, which makes the helper merely unnecessary here.
case "$naive" in
  141) ok "the naive 'printf | grep -q' pipeline returns 141 — SIGPIPE (why this helper exists)" ;;
  1)   ok "the naive 'printf | grep -q' pipeline returns 1 — EPIPE with SIGPIPE ignored (why this helper exists)" ;;
  0)   skip "naive pipeline fails under pipefail" "this bash/grep did not hit SIGPIPE or EPIPE; helper is still correct" ;;
  *)   no "naive pipeline returned $naive — expected 141, 1 or 0" ;;
esac

# ── feature_on / hardware ────────────────────────────────────────────────────
group "feature_on"
unset ZENDUO_TESTFEAT
succeeds "unset + default 1 is on"   feature_on TESTFEAT 1
fails    "unset + default 0 is off"  feature_on TESTFEAT 0
ZENDUO_TESTFEAT=1; succeeds "=1 is on"                    feature_on TESTFEAT 0
ZENDUO_TESTFEAT=0; fails    "=0 is off even with default 1" feature_on TESTFEAT 1
# shellcheck disable=SC2034  # read by feature_on through indirect expansion
ZENDUO_TESTFEAT=yes; fails  "anything but 1 is off"       feature_on TESTFEAT 1
unset ZENDUO_TESTFEAT

group "is_duo_hardware"
dmi="$(mktemp)"
printf 'Zenbook Duo UX8406MA_UX8406MA\n' > "$dmi"
ZENDUO_DMI_PRODUCT="$dmi" succeeds "matches the UX8406MA" is_duo_hardware
printf 'ROG Zephyrus G14\n' > "$dmi"
ZENDUO_DMI_PRODUCT="$dmi" fails "rejects another ASUS" is_duo_hardware
ZENDUO_DMI_PRODUCT=/nonexistent fails "rejects a machine with no DMI" is_duo_hardware
rm -f "$dmi"

# ── GRUB helpers on a scratch file ───────────────────────────────────────────
group "ensure_grub_param / remove_grub_param"
GRUB_FILE="$(mktemp)"
printf 'GRUB_DEFAULT=0\nGRUB_CMDLINE_LINUX_DEFAULT="quiet splash"\n' > "$GRUB_FILE"
# shellcheck disable=SC2034  # both are read inside lib.sh
DRY_RUN=0 GRUB_CHANGED=0
ensure_grub_param i915.enable_psr=0 >/dev/null
is "adds the param"            "$(grep GRUB_CMDLINE_LINUX_DEFAULT "$GRUB_FILE")" 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash i915.enable_psr=0"'
is "flags the change"          "$GRUB_CHANGED" 1
GRUB_CHANGED=0
ensure_grub_param i915.enable_psr=0 >/dev/null
is "does not add it twice"     "$(grep -c 'enable_psr' "$GRUB_FILE")" 1
is "no change flagged when present" "$GRUB_CHANGED" 0
remove_grub_param i915.enable_psr=0 >/dev/null
is "removes the param"         "$(grep GRUB_CMDLINE_LINUX_DEFAULT "$GRUB_FILE")" 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"'
printf 'GRUB_CMDLINE_LINUX_DEFAULT=""\n' > "$GRUB_FILE"
ensure_grub_param i915.enable_psr=0 >/dev/null
is "adds to an empty cmdline without a leading space" "$(cat "$GRUB_FILE")" 'GRUB_CMDLINE_LINUX_DEFAULT="i915.enable_psr=0"'
rm -f "$GRUB_FILE"
GRUB_FILE=/etc/default/grub

# ── ensure_line ──────────────────────────────────────────────────────────────
group "ensure_line"
tmpf="$(mktemp)"
printf 'existing\n' > "$tmpf"
ensure_line "$tmpf" 'added' >/dev/null 2>&1
is "appends a missing line"        "$(grep -c '^added$' "$tmpf")" "1"
ensure_line "$tmpf" 'added' >/dev/null 2>&1
is "does not append it twice"      "$(grep -c '^added$' "$tmpf")" "1"
rm -f "$tmpf"

# ── install_conf ─────────────────────────────────────────────────────────────
group "install_conf"
if [ "$(id -u)" != 0 ]; then
  skip "install_conf writes a new file"    "needs root (run: sudo make test)"
  skip "install_conf is idempotent"        "needs root"
  skip "install_conf honours DRY_RUN"      "needs root"
else
  confdir="$(mktemp -d)"
  target="$confdir/nested/dir/test.conf"
  DRY_RUN=0
  if install_conf "$target" "first" >/dev/null 2>&1; then ok "install_conf writes a new file (returns 0 = changed)"; else no "install_conf writes a new file"; fi
  is "content is correct" "$(cat "$target" 2>/dev/null)" "first"
  if install_conf "$target" "first" >/dev/null 2>&1; then no "install_conf is idempotent (should return 1)"; else ok "install_conf is idempotent (returns 1 = unchanged)"; fi
  if install_conf "$target" "second" >/dev/null 2>&1; then ok "install_conf rewrites when content differs"; else no "install_conf rewrites when content differs"; fi
  DRY_RUN=1
  install_conf "$confdir/dry.conf" "x" >/dev/null 2>&1
  is "DRY_RUN writes nothing" "$([ -e "$confdir/dry.conf" ] && echo created || echo absent)" "absent"
  # shellcheck disable=SC2034  # read by install_conf inside lib.sh
  DRY_RUN=0
  rm -rf "$confdir"
fi

# ── conf.sh ──────────────────────────────────────────────────────────────────
group "conf_get / conf_set (lib/conf.sh)"
ZENDUO_CONF="$(mktemp -d)/zenduo.conf"
export ZENDUO_CONF
is "missing file -> built-in default"     "$(conf_get APPLY_METHOD)" "temporary"
is "missing file -> empty default"        "$(conf_get BATTERY_LIMIT)" ""
is "explicit default wins over built-in"  "$(conf_get BATTERY_LIMIT 80)" "80"
cat > "$ZENDUO_CONF" <<'CONF'
# a comment
APPLY_METHOD=persistent   # trailing comment
BATTERY_LIMIT = 75
BACKLIGHT_TARGET="card1-eDP-2-backlight"
DOCK_POLICY=0; rm -rf /
CONF
is "reads a plain value"                  "$(conf_get APPLY_METHOD)" "persistent"
is "tolerates spaces around ="            "$(conf_get BATTERY_LIMIT)" "75"
is "strips surrounding quotes"            "$(conf_get BACKLIGHT_TARGET)" "card1-eDP-2-backlight"
is "rejects a value with shell metacharacters (falls back to default)" "$(conf_get DOCK_POLICY 2>/dev/null)" "1"
conf_set BATTERY_LIMIT 80
is "conf_set rewrites in place"           "$(conf_get BATTERY_LIMIT)" "80"
is "conf_set keeps one line for the key"  "$(grep -c '^BATTERY_LIMIT' "$ZENDUO_CONF")" "1"
conf_set NEW_KEY value
is "conf_set appends an unknown key"      "$(conf_get NEW_KEY)" "value"
# "/" is a legal value character (conf_get's allow-list) and was also the sed
# delimiter, so this used to fail with "unknown option to `s'".
conf_set BACKLIGHT_TARGET card1/eDP-2
is "conf_set accepts a value with a slash" "$(conf_get BACKLIGHT_TARGET)" "card1/eDP-2"
fails "conf_set refuses metacharacters"   conf_set APPLY_METHOD 'x;y'
rm -f "$ZENDUO_CONF"
conf_set APPLY_METHOD temporary
is "conf_set creates the file from defaults" "$(grep -c '^APPLY_METHOD=temporary' "$ZENDUO_CONF")" "1"
rm -rf "$(dirname "$ZENDUO_CONF")"

# ── the root helper's argument handling ──────────────────────────────────────
# The helper is the only code that runs as root, so every verb has to refuse
# before it writes. The content validation of login-layout (element whitelist,
# size, ownership) needs a root write target and is exercised by installing;
# what is testable here is that no argument shape reaches the write at all.
group "zenduo-helper arguments"
helper_rc() { ( unset SUDO_UID; "$@" ) >/dev/null 2>&1; echo $?; }
is "no verb is a usage error (64)"              "$(helper_rc ./helper/zenduo-helper)" 64
is "an unknown verb is a usage error"           "$(helper_rc ./helper/zenduo-helper bogus)" 64
is "backlight with no device is a usage error"  "$(helper_rc ./helper/zenduo-helper backlight)" 64
is "batlimit out of range is a usage error"     "$(helper_rc ./helper/zenduo-helper batlimit 5)" 64
is "power with an unknown profile is a usage error" "$(helper_rc ./helper/zenduo-helper power turbo none 60)" 64
is "power with minutes out of range is a usage error" "$(helper_rc ./helper/zenduo-helper power performance power-saver 1441)" 64
is "power with a missing value is a usage error"   "$(helper_rc ./helper/zenduo-helper power performance power-saver)" 64
is "login-layout outside sudo is refused"       "$(helper_rc ./helper/zenduo-helper login-layout)" 64
is "login-layout rejects a non-numeric uid"     "$(SUDO_UID=nobody ./helper/zenduo-helper login-layout >/dev/null 2>&1; echo $?)" 64
# 4294967295 is (uid_t)-1: getent never resolves it, on any machine.
is "login-layout needs a uid with a home"       "$(SUDO_UID=4294967295 ./helper/zenduo-helper login-layout >/dev/null 2>&1; echo $?)" 66

# ── the amp reporter, extracted from the script that installs it ─────────────
# Its verdict feeds `duo doctor`. A user outside the adm group gets no kernel
# journal, and counting zero hits in nothing used to read as "clean".
group "duo-cs35l41-check"
chk_dir="$(mktemp -d)"
sed -n "/^cat > \"\$tmp_check\" <<'CHECK'\$/,/^CHECK\$/p" system/46-speaker-amp.sh | sed '1d;$d' > "$chk_dir/duo-cs35l41-check"
chmod +x "$chk_dir/duo-cs35l41-check"
is "the reporter was extracted" "$(grep -c '^PATTERN=' "$chk_dir/duo-cs35l41-check")" 1
fake_kernel_journal() { # <fail|clean|bad>: what `journalctl -k -b` will do
  printf '#!/usr/bin/env bash\ncase %s in fail) exit 1 ;; clean) echo "usb 1-1: new device" ;; bad) echo "cs35l41-hda: Failed waiting for CS35L41_PUP_DONE_MASK: -110" ;; esac\n' "$1" > "$chk_dir/journalctl"
  chmod +x "$chk_dir/journalctl"
}
check_rc() { ( PATH="$chk_dir:$PATH" duo-cs35l41-check >/dev/null 2>&1 ); echo $?; }
fake_kernel_journal fail;  is "an unreadable kernel journal is 'cannot tell' (2), not clean" "$(check_rc)" 2
fake_kernel_journal clean; is "a clean boot exits 0"                                        "$(check_rc)" 0
fake_kernel_journal bad;   is "the PUP_DONE timeout exits 1"                                "$(check_rc)" 1
rm -rf "$chk_dir"

# ── nix_managed ──────────────────────────────────────────────────────────────
group "nix_managed"
nm_dir="$(mktemp -d)"
touch "$nm_dir/plain.service"
ln -s /nix/store/0000000000000000000000000000000-home-manager-files/.config/systemd/user/duo-watch-fn.service "$nm_dir/hm.service"
ln -s "$nm_dir/plain.service" "$nm_dir/local-link.service"
fails    "a regular file is not managed"            nix_managed "$nm_dir/plain.service"
succeeds "a symlink into /nix/store is managed"     nix_managed "$nm_dir/hm.service"
fails    "a symlink elsewhere is not managed"       nix_managed "$nm_dir/local-link.service"
fails    "a missing path is not managed"            nix_managed "$nm_dir/none.service"
rm -rf "$nm_dir"

# ── systemd unit templates ────────────────────────────────────────────────────
# Without SyslogIdentifier the daemons' stdout is filed under the executable's
# name ("duo") and `duo log` (-t zenduo) showed none of it (2026-09-05: 600
# watch-fn lines under "duo", zero under "zenduo"). The home-manager module is
# covered by the flake's hm-units check.
group "systemd unit templates"
for f in systemd/user/duo-*.service; do
  is "$(basename "$f") logs under the zenduo identifier" "$(grep -c '^SyslogIdentifier=zenduo$' "$f")" 1
done

# ── the audio files the installer ships ──────────────────────────────────────
# One definition each, read by WirePlumber and systemd, so a typo in the key
# would install cleanly and do nothing.
group "audio config files"
is "the WirePlumber 0.4 policy turns the headset switch off" \
   "$(grep -c '^bluetooth_policy.policy\["media-role.use-headset-profile"\] = false$' config/wireplumber/11-zenduo-bluetooth-stereo.lua)" 1
is "the WirePlumber 0.5 setting says the same" \
   "$(grep -c '^  bluetooth.autoswitch-to-headset-profile = false$' config/wireplumber/zenduo-bluetooth-stereo.conf)" 1
is "the rtkit drop-in asks rtkit before the service starts" \
   "$(grep -c '^ExecStartPre=-busctl --system --quiet --timeout=10 call org.freedesktop.RealtimeKit1 ' config/systemd/user/10-zenduo-rtkit.conf)" 1
is "the rtkit drop-in is a [Service] section" "$(grep -c '^\[Service\]$' config/systemd/user/10-zenduo-rtkit.conf)" 1

# ── zenduo-power, extracted from the script that installs it ────────────────
# A fake /sys/class/power_supply and stubs for every command with an effect,
# each of which records how it was called. The power-off is armed only when the
# laptop goes to sleep on battery, on wall time (OnCalendar, never OnActiveSec:
# that clock stops while asleep), and waking early cancels it.
group "zenduo-power"
pw_dir="$(mktemp -d)"
mkdir -p "$pw_dir/bin" "$pw_dir/sys" "$pw_dir/run"
sed -n "/^cat > \"\$tmp_script\" <<'POWER'\$/,/^POWER\$/p" system/58-power-follows-ac.sh | sed '1d;$d' > "$pw_dir/zenduo-power"
chmod +x "$pw_dir/zenduo-power"
is "the decider was extracted" "$(grep -c '^OFF_UNIT=' "$pw_dir/zenduo-power")" 1
for stub in powerprofilesctl systemd-run systemctl; do
  printf '#!/usr/bin/env bash\necho "%s $*" >> "%s/calls"\n' "$stub" "$pw_dir" > "$pw_dir/bin/$stub"
done
# date: the real one, except that `date +%s` answers $FAKE_NOW when it is set.
# shellcheck disable=SC2016  # the $1 and $@ belong to the stub
printf '#!/usr/bin/env bash\nif [ "$1" = +%%s ] && [ -n "${FAKE_NOW:-}" ]; then echo "$FAKE_NOW"; exit; fi\nexec %s "$@"\n' "$(command -v date)" > "$pw_dir/bin/date"
chmod +x "$pw_dir/bin/"*
supply() { # <name> <type> <attr=value>...
  local d="$pw_dir/sys/$1" kv
  mkdir -p "$d"
  echo "$2" > "$d/type"
  shift 2
  for kv in "$@"; do echo "${kv#*=}" > "$d/${kv%%=*}"; done
}
pw_conf() { printf 'AC_PROFILE=performance\nBATTERY_PROFILE=power-saver\nSHUTDOWN_AFTER_SLEEP_MIN=%s\n' "$1" > "$pw_dir/power.conf"; }
pw() { # run zenduo-power; prints the calls it made
  : > "$pw_dir/calls"
  ( PATH="$pw_dir/bin:$PATH" ZENDUO_POWER_CONF="$pw_dir/power.conf" ZENDUO_POWER_SUPPLY_DIR="$pw_dir/sys" \
    ZENDUO_POWER_RUN_DIR="$pw_dir/run" ZENDUO_POWER_SELF=/x/zenduo-power "$pw_dir/zenduo-power" "$@" >/dev/null 2>&1 )
  cat "$pw_dir/calls"
}
calls_match() { out_matches "$1" "${@:2}"; }

pw_conf 60
supply AC0 Mains online=1
supply BAT0 Battery status=Not\ charging scope=System
supply hid-kbd-battery Battery status=Discharging scope=Device
c="$(pw)"
succeeds "on the charger at the charge limit: Performance"      calls_match "$c" -x 'powerprofilesctl set performance'
fails    "a keyboard's discharging battery is not the laptop's" calls_match "$c" 'power-saver'
is "the same source again does nothing" "$(pw)" ""
fails    "asleep on the charger: no power-off"                  calls_match "$(pw --sleeping)" 'systemd-run'

echo 0 > "$pw_dir/sys/AC0/online"; echo Discharging > "$pw_dir/sys/BAT0/status"
c="$(pw)"
succeeds "unplugged: Power Saver"                               calls_match "$c" -x 'powerprofilesctl set power-saver'
fails    "unplugged and awake: no power-off, however long"      calls_match "$c" 'systemd-run'

c="$(pw --sleeping)"
succeeds "asleep on battery: a power-off that wakes the laptop" calls_match "$c" -E '^systemd-run .*--unit=zenduo-sleep-poweroff .*WakeSystem=true /x/zenduo-power --deadline$'
succeeds "the power-off is set on wall time, in UTC"            calls_match "$c" -E -- '--on-calendar=[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} UTC '
fails    "never a monotonic timer (it stops while asleep)"      calls_match "$c" -e '--on-active' -e 'OnActiveSec'
deadline="$(cat "$pw_dir/run/poweroff-at")"
left=$(( deadline - $(date +%s) ))
succeeds "the power-off is 60 minutes away" test "$left" -ge 3590 -a "$left" -le 3600

c="$(FAKE_NOW=$((deadline - 600)) pw --resumed)"
succeeds "woken 10 minutes early: the power-off is cancelled"   calls_match "$c" -x 'systemctl stop zenduo-sleep-poweroff.timer'
fails    "and the laptop stays on"                              calls_match "$c" -x 'systemctl poweroff'
is "and nothing is left armed" "$(cat "$pw_dir/run/poweroff-at" 2>/dev/null)" ""

pw --sleeping >/dev/null
deadline="$(cat "$pw_dir/run/poweroff-at")"
succeeds "woken by the RTC at the deadline: powers off"        calls_match "$(FAKE_NOW="$deadline" pw --resumed)" -x 'systemctl poweroff'
succeeds "the timer itself firing on battery: powers off"      calls_match "$(pw --deadline)" -x 'systemctl poweroff'
echo 1 > "$pw_dir/sys/AC0/online"
fails    "the timer firing with the charger plugged in meanwhile: stays on" calls_match "$(pw --deadline)" 'poweroff'
echo 0 > "$pw_dir/sys/AC0/online"

c="$(pw --reset)"
succeeds "--reset acts on an unchanged source"                  calls_match "$c" -x 'powerprofilesctl set power-saver'

echo Charging > "$pw_dir/sys/BAT0/status"
succeeds "a charging battery is on the charger, whatever Mains says" calls_match "$(pw)" -x 'powerprofilesctl set performance'

rm -rf "$pw_dir/sys/AC0"; echo Discharging > "$pw_dir/sys/BAT0/status"
succeeds "no Mains supply: a discharging battery is on battery" calls_match "$(pw)" -x 'powerprofilesctl set power-saver'
pw_conf 0
fails    "SHUTDOWN_AFTER_SLEEP_MIN=0 arms nothing"              calls_match "$(pw --sleeping)" 'systemd-run'
printf 'AC_PROFILE=performance\nBATTERY_PROFILE=turbo; rm -rf /\nSHUTDOWN_AFTER_SLEEP_MIN=abc\n' > "$pw_dir/power.conf"
c="$(pw --reset)"
fails    "a bad profile is not passed on"                       calls_match "$c" 'powerprofilesctl'
fails    "a bad minute count arms nothing"                      calls_match "$(pw --sleeping)" 'systemd-run'
rm -rf "$pw_dir/sys/"* "$pw_dir/run/"*
is "no supplies at all: nothing is done" "$(pw)" ""
is "an unknown verb is a usage error" "$( (ZENDUO_POWER_SUPPLY_DIR="$pw_dir/sys" "$pw_dir/zenduo-power" --bogus >/dev/null 2>&1); echo $?)" 2
rm -rf "$pw_dir"

# ── install.sh: flags must survive the runuser round trip ────────────────────
# As root, install.sh re-runs itself under runuser for the user half. Its
# parser starts from the defaults, so the flags have to travel as arguments:
# 2026-09-05 `sudo ./install.sh --dry-run` ran the user half FOR REAL and
# --battery-limit / --speaker-dsp / --prefix never reached it.
group "install.sh argument plumbing"
plan()  { ZENDUO_INSTALL_PLAN=1 ./install.sh "$@" 2>/dev/null; }
field() { printf '%s\n' "$1" | sed -n "s/^$2=//p"; }
p="$(plan --dry-run --dev --prefix /opt/z --no-watch-fn --watch-rotation --battery-limit 80 --apply-method persistent --speaker-dsp --no-audio-buffer-floor --no-bluetooth-stereo --no-audio-realtime --user bob)"
is "--user NAME names the target account"     "$(field "$p" TARGET_USER)" bob
is "--user NAME keeps both halves"            "$(field "$p" DO_SYSTEM),$(field "$p" DO_USER)" "1,1"
is "--dry-run is parsed"                      "$(field "$p" DRY_RUN)" 1
is "system flags exclude the user-only ones"  "$(field "$p" SYSTEM_FLAGS)" "--dry-run --dev --prefix /opt/z"
fwd="$(field "$p" USER_FLAGS)"
is "every user-half flag is forwarded"        "$fwd" "--dry-run --prefix /opt/z --no-watch-fn --watch-rotation --battery-limit 80 --apply-method persistent --speaker-dsp --no-audio-buffer-floor --no-bluetooth-stereo --no-audio-realtime"
# The round trip the root path performs: re-parse exactly what it forwards.
# shellcheck disable=SC2086  # word-splitting the forwarded flags is the point
c="$(plan $fwd --user)"
is "child: user half only"                    "$(field "$c" DO_SYSTEM),$(field "$c" DO_USER)" "0,1"
is "child: dry run survives"                  "$(field "$c" DRY_RUN)" 1
is "child: --prefix survives"                 "$(field "$c" PREFIX)" /opt/z
is "child: --no-watch-fn survives"            "$(field "$c" WATCH_FN)" 0
is "child: --watch-rotation survives"         "$(field "$c" WATCH_ROTATION)" 1
is "child: --battery-limit survives"          "$(field "$c" BATTERY_LIMIT)" 80
is "child: --apply-method survives"           "$(field "$c" APPLY_METHOD)" persistent
is "child: --no-audio-buffer-floor survives"  "$(field "$c" AUDIO_FLOOR)" 0
# The floor is a fix, not a taste: it is on unless it is turned off.
is "the audio buffer floor defaults on"       "$(field "$(plan)" AUDIO_FLOOR)" 1
is "child: --no-bluetooth-stereo survives"    "$(field "$c" BT_STEREO)" 0
is "child: --no-audio-realtime survives"      "$(field "$c" AUDIO_RT)" 0
is "bluetooth stereo defaults on"             "$(field "$(plan)" BT_STEREO)" 1
is "audio realtime defaults on"               "$(field "$(plan)" AUDIO_RT)" 1
is "child: --speaker-dsp survives"            "$(field "$c" SPEAKER_DSP)" 1
is "no flags forwards nothing"                "$(field "$(plan)" USER_FLAGS)" ""
is "--user alone means the user half only"    "$(field "$(plan --user)" DO_SYSTEM)" 0
is "--system alone skips the user half"       "$(field "$(plan --system)" DO_USER)" 0
is "--no-separate-fan goes to the system half" "$(field "$(plan --no-separate-fan)" SYSTEM_FLAGS)" "--no-separate-fan"
is "--battery-shutdown goes to the system half with its value" "$(field "$(plan --power-follows-ac --battery-shutdown 60)" SYSTEM_FLAGS)" "--power-follows-ac --battery-shutdown 60"
fails "--battery-shutdown out of range is refused" plan --battery-shutdown 2000
fails "--battery-shutdown needs a number"          plan --battery-shutdown soon
fails "--battery-limit out of range is refused" plan --battery-limit 10
fails "--apply-method never is refused"        plan --apply-method never
fails "an unknown flag is refused"             plan --bogus
succeeds "--help exits 0"                      ./install.sh --help
# The help is the header comment; a fixed line range printed code once the
# header grew past it (2026-09-16: six lines of bash after the last flag).
is "--help prints the header only, no code"   "$(./install.sh --help | grep -c 'set -euo')" 0

group "uninstall.sh argument plumbing"
is "--prefix without a value is a usage error (64), not an unbound variable" "$(./uninstall.sh --prefix >/dev/null 2>&1; echo $?)" 64
is "an unknown flag is a usage error (64)"     "$(./uninstall.sh --bogus >/dev/null 2>&1; echo $?)" 64
succeeds "--help exits 0"                      ./uninstall.sh --help
is "--help prints the header only, no code"   "$(./uninstall.sh --help | grep -c 'set -euo')" 0

group "bin/duo-cli argument plumbing"
is "help exits 0, not 64"                     "$(./bin/duo-cli --help >/dev/null 2>&1; echo $?)" 0
is "help goes to stdout"                      "$(./bin/duo-cli help 2>/dev/null | grep -c '^usage:')" 1
is "an unknown command is a usage error (64)" "$(./bin/duo-cli bogus >/dev/null 2>&1; echo $?)" 64
is "a usage error goes to stderr, not stdout" "$(./bin/duo-cli bogus 2>/dev/null | grep -c '^usage:')" 0

# ── summary ──────────────────────────────────────────────────────────────────
printf '\n%s\n' "────────────────────────────────────────"
printf '%d passed, %d failed, %d skipped\n' "$PASSED" "$FAILED" "$SKIPPED"
[ "$FAILED" -eq 0 ] || exit 1
printf '\033[32mall good\033[0m\n'
