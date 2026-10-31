#!/usr/bin/env python3
"""Battery levels: the laptop's, and the detachable keyboard's.

The laptop is plain sysfs: `BAT0` (docs/HARDWARE.md) with `capacity` and
`status` from the ACPI battery driver.

The keyboard has three possible sources, tried in this order:

1. A kernel power_supply node of the keyboard (`hid-*-battery`), which
   hid-input creates for a HID device that declares a battery usage. Whether
   this keyboard declares one is **VERIFY-ON-HW**; none was in the
   2026-09-05 report.
2. UPower, which also carries what BlueZ reads from a Bluetooth LE battery
   service. Whether the keyboard offers one over Bluetooth is
   **VERIFY-ON-HW**.
3. The keyboard's own status report `5a 3d XX YY`, which watch-fn saves as
   it arrives (`save_status`). **GUESS, about 85 %**: XX is the battery
   percentage. Under Windows it came as `5a 3d 63 00` (99) when Windows
   said 99 %, and `5a 3d 64 00` docked (docs/HARDWARE.md, 2026-10-31).
   On Linux it came as `5a 3d 05 09` and `5a 3d 06 09` (2026-10-30), which
   would be 5 and 6 %, unconfirmed; a rising value with YY set may mean
   "charging", but that is a guess too. So the window says where a value
   came from and that it is unconfirmed.

A reading is only shown while the keyboard is reachable (on the pins or on
Bluetooth); otherwise the answer is "unknown". watch-fn clears the saved
report each time it initialises the keyboard, so a value shown is always
from the current connection.

watch-fn also pops up a warning as the keyboard drops through 20, 10, 5
and 1 % (LowAlarm), from whichever source has a value.

Usage: battery.py            print both, one line each
"""

import glob
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_init  # noqa: E402  (same directory; owns how the keyboard is found)

VENDOR_REPORT_ID = 0x5A
STATUS_CODE = 0x3D
STATUS_FILE = "kb-status"
POWER_SUPPLY = "/sys/class/power_supply"

# sysfs `status` of the ACPI battery, in words for the window.
LAPTOP_STATUS = {
    "Charging": "charging",
    "Discharging": "not charging, on battery",
    "Full": "full, on the charger",
    # What the battery says once it reaches charge_control_end_threshold.
    "Not charging": "not charging, on the charger (charge limit)",
}


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


# ── the laptop ────────────────────────────────────────────────────────────────

def laptop(root=POWER_SUPPLY):
    """(percent, status) of the first BAT* that reports a capacity, or None."""
    for bat in sorted(glob.glob(os.path.join(root, "BAT*"))):
        cap = _read(os.path.join(bat, "capacity"))
        if cap.isdigit():
            return int(cap), _read(os.path.join(bat, "status"))
    return None


def laptop_text(reading):
    if reading is None:
        return "no battery found"
    pct, status = reading
    words = LAPTOP_STATUS.get(status, status.lower() if status else "")
    return f"{pct} %, {words}" if words else f"{pct} %"


# ── the keyboard ──────────────────────────────────────────────────────────────

def status_path():
    """Where watch-fn keeps the last `5a 3d` report: runtime, not state, so
    it goes away with the session instead of outliving the connection."""
    base = (os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_STATE_HOME")
            or os.path.expanduser("~/.local/state"))
    return os.path.join(base, "zenduo", STATUS_FILE)


def save_status(data, now=None, path=None):
    """Remember a `5a 3d …` report (bytes) and when it came; best effort."""
    path = path or status_path()
    line = " ".join(f"{b:02x}" for b in data[:4])
    stamp = int(time.time() if now is None else now)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{line}\n{stamp}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def clear_status(path=None):
    try:
        os.remove(path or status_path())
    except OSError:
        pass


def load_status(path=None):
    """(report bytes, unix time) of the last saved report, or None."""
    lines = _read(path or status_path()).split("\n")
    if len(lines) < 2:
        return None
    try:
        data = bytes(int(x, 16) for x in lines[0].split())
        return data, int(lines[1])
    except ValueError:
        return None


def status_percent(data):
    """The guessed percentage in a `5a 3d XX …` report, or None."""
    if len(data) >= 3 and data[0] == VENDOR_REPORT_ID and data[1] == STATUS_CODE \
            and data[2] <= 100:
        return data[2]
    return None


def _is_keyboard(device_dir):
    """Whether a power_supply node belongs to the Duo keyboard: matched as
    kb_init matches its hidraw nodes (vendor id on any bus, or the name)."""
    text = _read(os.path.join(device_dir, "device", "uevent")).upper()
    return f":0000{kb_init.VID}:" in text or "ZENBOOK DUO" in text


# sysfs `status` (kernel) and UPower `state`, in the words the window uses.
CHARGE_WORDS = {
    "Charging": "charging", "charging": "charging",
    "Discharging": "not charging", "discharging": "not charging",
    "Not charging": "not charging", "pending-charge": "not charging",
    "Full": "full", "fully-charged": "full",
}


def status_charging(data):
    """The guessed charging state in a `5a 3d XX YY` report, or "".

    **GUESS, about 60 %**: YY is nonzero while it charges. On Linux the
    report rose 05 -> 06 with YY = 09 (2026-10-30); under Windows it read
    `63 00` on Bluetooth and `64 00` (full) docked (docs/HARDWARE.md).
    """
    if status_percent(data) is None or len(data) < 4:
        return ""
    return "charging" if data[3] else "not charging"


def _is_keyboard(device_dir):
    """Whether a power_supply node belongs to the Duo keyboard: matched as
    kb_init matches its hidraw nodes (vendor id on any bus, or the name)."""
    text = _read(os.path.join(device_dir, "device", "uevent")).upper()
    return f":0000{kb_init.VID}:" in text or "ZENBOOK DUO" in text


def keyboard_sysfs(root=POWER_SUPPLY):
    """(percent, charge state) from a kernel HID battery node of the
    keyboard, or None. The state is "" when the node has no `status`."""
    for node in sorted(glob.glob(os.path.join(root, "hid-*"))):
        cap = _read(os.path.join(node, "capacity"))
        if cap.isdigit() and _is_keyboard(node):
            return int(cap), CHARGE_WORDS.get(_read(os.path.join(node, "status")), "")
    return None


def parse_upower(text):
    """(percent, charge state) of the Duo keyboard in `upower --dump`, or None."""
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.strip().partition(":")
            if sep:
                fields.setdefault(key.strip(), value.strip())
        model = fields.get("model", "").upper()
        pct = fields.get("percentage", "").rstrip("%").strip()
        if "ZENBOOK DUO" in model or ("ASUS" in model and "keyboard" in block):
            try:
                return round(float(pct)), CHARGE_WORDS.get(fields.get("state", ""), "")
            except ValueError:
                continue
    return None


def keyboard_upower(run=subprocess.run):
    if not shutil.which("upower"):
        return None
    try:
        out = run(["upower", "--dump"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_upower(out or "")


def keyboard_reachable():
    return any(True for _ in kb_init.keyboard_hidraw_nodes())


def keyboard_reading(status=None, sysfs=None, upower=None):
    """(percent, charge state, source) from the best source, or None.
    source is "kernel", "bluetooth" or "report" (the unconfirmed guess)."""
    if sysfs is not None:
        return sysfs[0], sysfs[1], "kernel"
    if upower is not None:
        return upower[0], upower[1], "bluetooth"
    pct = status_percent(status[0]) if status else None
    if pct is None:
        return None
    return pct, status_charging(status[0]), "report"


def keyboard_text(reachable, sysfs=None, upower=None, status=None, now=None):
    """What the window says about the keyboard battery."""
    if not reachable:
        return "unknown (the keyboard is not connected)"
    reading = keyboard_reading(status, sysfs, upower)
    if reading is None:
        return "unknown (the keyboard has not reported it yet)"
    pct, state, source = reading
    text = f"{pct} %, {state or 'charging state unknown'}"
    if source == "bluetooth":
        return text + " (Bluetooth)"
    if source == "report":
        age = max(0, int((time.time() if now is None else now) - status[1]))
        ago = "just now" if age < 60 else f"{age // 60} min ago"
        return text + f" (keyboard's own report, {ago}; unconfirmed)"
    return text


# ── the low-battery popup ─────────────────────────────────────────────────────

LOW_LEVELS = (20, 10, 5, 1)   # asked for by the owner, 2026-10-31
REARM_ABOVE = 3               # points over a level before it can warn again


class LowAlarm:
    """Which low-battery warning is due, once per level on the way down.

    A drop past several levels at once (a first reading of 4 %) warns once,
    for the lowest. A level warns again only after the battery has climbed
    REARM_ABOVE points past it, so a reading that wobbles 10/11/10 does not
    pop up every time.
    """

    def __init__(self, levels=LOW_LEVELS):
        self.levels = sorted(levels, reverse=True)
        self.warned = set()

    def update(self, pct):
        for level in list(self.warned):
            if pct > level + REARM_ABOVE:
                self.warned.discard(level)
        crossed = [lv for lv in self.levels if pct <= lv and lv not in self.warned]
        if not crossed:
            return None
        self.warned.update(lv for lv in self.levels if pct <= lv)
        return min(crossed)


def low_message(pct, source):
    """(urgency, title, body) of the popup."""
    title = f"Keyboard battery at {pct} %"
    body = "Dock the keyboard on the laptop or plug it in to charge it."
    if source == "report":
        body += (" (From the keyboard's own status report, which is not confirmed"
                 " to be its battery level yet.)")
    return ("critical" if pct <= 5 else "normal"), title, body


def notify(pct, source, run=subprocess.run):
    """Show the popup; False when it could not be shown (no notify-send,
    no session bus). Never raises: a popup must not take watch-fn down."""
    if not shutil.which("notify-send"):
        return False
    urgency, title, body = low_message(pct, source)
    try:
        run(["notify-send", "-u", urgency, "-a", "Zenbook Duo", "-i", "battery-caution",
             title, body], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def main(argv):
    if argv:
        print(__doc__.rsplit("Usage:", 1)[1].strip(), file=sys.stderr)
        return 64
    print("laptop:  ", laptop_text(laptop()))
    print("keyboard:", keyboard_text(keyboard_reachable(), keyboard_sysfs(),
                                     keyboard_upower(), load_status()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
