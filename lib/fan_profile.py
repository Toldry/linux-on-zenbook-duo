#!/usr/bin/env python3
"""The fan mode Fn+F steps through, set the way MyASUS sets it.

MyASUS writes one BIOS setting, THROTTLE_THERMAL_POLICY_VIVO (0x00110019):
Standard 0, Whisper 1, Performance 2, stepped in that order on Fn+F
(measured under Windows 2026-10-31, docs/HARDWARE.md). On Linux asus-wmi
puts that same setting behind /sys/firmware/acpi/platform_profile, so this
writes only that file, through the root helper. It does not go through
power-profiles-daemon: a profile there also moves the CPU's
energy_performance_preference, which MyASUS's fan modes leave alone. If
power-profiles-daemon notices the change and follows it with the CPU too is
**VERIFY-ON-HW** (`duo fan-profile`, then read
/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference).

Which platform_profile name asus-wmi maps to which BIOS number on a Vivo id
(quiet = Whisper is its table's meaning) is **VERIFY-ON-HW** as well.

Usage: fan_profile.py [show|next|standard|whisper|performance]
  next is what Fn+F runs: the next mode, and the popup MyASUS shows.
Exit: 0 ok · 1 no platform_profile or the helper refused · 64 usage.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shell_ext  # noqa: E402  (same directory; the popup)

PROFILE = "/sys/firmware/acpi/platform_profile"
CHOICES = "/sys/firmware/acpi/platform_profile_choices"
HELPER = "/usr/local/sbin/zenduo-helper"

# MyASUS's names and Fn+F order, each with its platform_profile value.
MODES = [("Standard", "balanced"), ("Whisper", "quiet"), ("Performance", "performance")]
ICONS = {"balanced": "power-profile-balanced-symbolic",
         "quiet": "power-profile-power-saver-symbolic",
         "performance": "power-profile-performance-symbolic"}


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def mode_name(profile):
    return next((name for name, value in MODES if value == profile), profile or "unknown")


def next_profile(current, choices):
    """The value after `current` in MyASUS's order, skipping ones the kernel
    does not offer; Standard when `current` is not one of them."""
    order = [value for _name, value in MODES if value in choices] or ["balanced"]
    if current not in order:
        return order[0]
    return order[(order.index(current) + 1) % len(order)]


def set_profile(value, runner=subprocess.run):
    try:
        done = runner(["sudo", "-n", HELPER, "thermal", value],
                      capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"fan-profile: cannot run the helper: {e}", file=sys.stderr)
        return 1
    if done.returncode != 0:
        print(f"fan-profile: the helper refused ({done.stderr.strip() or done.returncode}); "
              "is it installed? sudo ./install.sh --system", file=sys.stderr)
        return 1
    print(f"fan-profile: {mode_name(value)} ({value})")
    return 0


def main(argv):
    arg = argv[0] if len(argv) == 1 else ("show" if not argv else "")
    current = read(PROFILE)
    if arg == "show":
        print(f"{mode_name(current)} ({current or 'no platform_profile'})")
        return 0 if current else 1
    if not current and arg:
        print(f"fan-profile: {PROFILE} is missing (asus-wmi not loaded?)", file=sys.stderr)
        return 1
    by_name = {name.lower(): value for name, value in MODES}
    if arg == "next":
        target = next_profile(current, read(CHOICES).split())
    elif arg in by_name:
        target = by_name[arg]
    else:
        print(__doc__, file=sys.stderr)
        return 64
    rc = set_profile(target)
    if rc == 0 and arg == "next":
        shell_ext.osd(ICONS.get(target, "dialog-information-symbolic"),
                      f"Fan mode: {mode_name(target)}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
