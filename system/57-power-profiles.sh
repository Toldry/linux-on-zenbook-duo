#!/usr/bin/env bash
# 57-power-profiles.sh — keep the fan mode and GNOME's Power Mode apart.
#
# On Windows these are two settings: MyASUS's fan mode (the BIOS thermal
# policy, Standard/Whisper/Performance) and Windows' power mode. On Linux the
# fan mode is /sys/firmware/acpi/platform_profile, and power-profiles-daemon,
# which owns GNOME's Power Mode menu, both drives it and follows it: picking a
# fan mode moved the Power Mode, and the reverse (seen 2026-10-31 on the
# author's machine, Ubuntu 26.04.1, power-profiles-daemon 0.30).
#
# The daemon can be told to leave a driver alone: --block-driver=platform_profile
# (read from power-profiles-daemon's own --help; the script checks the binary
# offers it before using it). Power Mode then only sets the CPU's
# energy_performance_preference through intel_pstate, and the fan mode is
# `duo fan-profile` / Fn+F alone. Whether Performance stays in GNOME's menu with
# only the CPU driver: VERIFY-ON-HW.
#
# Switch: --no-separate-fan (ZENDUO_SEPARATE_FAN=0). Default on.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

require_root

DROPIN=/etc/systemd/system/power-profiles-daemon.service.d/50-zenduo-separate-fan.conf
UNIT=power-profiles-daemon.service

restart_daemon() {
  run systemctl daemon-reload
  if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
    run systemctl restart "$UNIT"
  fi
}

if ! feature_on SEPARATE_FAN 1; then
  if [ -e "$DROPIN" ]; then
    log "fan mode separation disabled — removing $DROPIN"
    run rm -f "$DROPIN"
    restart_daemon
  else
    log "fan mode separation disabled (--no-separate-fan)"
  fi
  exit 0
fi

# The unit's own ExecStart names the binary; these are where distros put it.
PPD_BIN=""
for candidate in /usr/libexec/power-profiles-daemon \
                 /usr/lib/power-profiles-daemon/power-profiles-daemon \
                 /usr/lib/power-profiles-daemon; do
  if [ -f "$candidate" ] && [ -x "$candidate" ]; then PPD_BIN="$candidate"; break; fi
done
if [ -z "$PPD_BIN" ]; then
  log "power-profiles-daemon not installed — nothing ties the fan mode to a power mode"
  exit 0
fi

help_text="$("$PPD_BIN" --help 2>&1 || true)"
if ! out_matches "$help_text" -e '--block-driver'; then
  warn "$PPD_BIN has no --block-driver (needs 0.20 or later): the fan mode and Power Mode stay linked"
  exit 0
fi

read -r -d '' BODY <<EOF_DROPIN || true
# zenduo — keep the BIOS fan mode (platform_profile) out of GNOME's Power Mode,
# as on Windows. Written by system/57-power-profiles.sh; --no-separate-fan removes it.
[Service]
ExecStart=
ExecStart=$PPD_BIN --block-driver=platform_profile
EOF_DROPIN

if install_conf "$DROPIN" "$BODY"; then
  restart_daemon
fi
