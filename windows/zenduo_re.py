r"""Shared pieces of the Windows reverse-engineering kit (docs/WINDOWS-RE.md).

Everything here is plain stdlib and runs on Linux too, which is the point: the
captures are made on Windows, but they are read, decoded and turned into Linux
equivalents by the same functions, and tests/test_windows_re.py checks those
functions without a Windows machine.

What lives here:
  * the capture session folder and its marker log (what the user said they
    were about to do, with a timestamp, so every trace can be cut by action);
  * the guided scenarios (which key to press or MyASUS switch to click, in
    which order) and the coloured step runner that walks them;
  * decoders for the two channels the ASUS software talks to the hardware on:
    the ATKACPI driver (DSTS/DEVS calls into the BIOS) and HID feature reports
    to the keyboard.

Where each constant comes from is said next to it. Nothing here was measured
on Windows yet: the tables are what Linux and other projects already know,
and the captures are how to confirm or correct them.
"""

import collections
import datetime
import json
import os
import struct
import sys
import threading
import time

# ── ATKACPI (the ASUS ACPI driver's device, \\.\ATKACPI) ─────────────────────
# Read 2026-10-30 from G-Helper app/AsusACPI.cs (GPL-3, read only, nothing
# copied): the ASUS software opens \\.\ATKACPI and calls DeviceIoControl with
# code 0x0022240C and a buffer of <u32 method><u32 arg length><args>. DEVS
# args are <u32 device id><u32 value>; DSTS args are <u32 device id>. Codes
# 0x222400 (register an event handle) and 0x222408 (fetch the event code)
# carry the hotkey/notification channel. That the *MyASUS* services on this
# Zenbook use the same driver is a guess until hook_asus.py sees it.
ATK_DEVICE = r"\\.\ATKACPI"
ATK_IOCTL_CALL = 0x0022240C
ATK_IOCTL_REGISTER_EVENT = 0x00222400
ATK_IOCTL_GET_EVENT = 0x00222408

# Method ids are four ASCII letters read little-endian. Read 2026-10-30 from
# include/linux/platform_data/x86/asus-wmi.h (the subset worth naming).
ATK_METHODS = {
    0x54494E49: "INIT",
    0x53545344: "DSTS",
    0x53564544: "DEVS",
    0x53545342: "BSTS",
    0x53544344: "DCTS",
    0x4E554653: "SFUN",
    0x59454B48: "HKEY",
    0x5446424B: "KBFT",
    0x43455053: "SPEC",
    0x5256534F: "OSVR",
    0x50564544: "DEVP",
    0x4E464741: "AGFN",
    0x444F4D51: "QMOD",
}

# Device ids, same source. The Linux name is what to grep for in asus-wmi.c
# (and, when the kernel exposes one, the sysfs knob that already drives it).
ATK_DEVICE_IDS = {
    0x00010011: "WLAN",
    0x00010013: "BLUETOOTH",
    0x00040017: "MICMUTE_LED",
    0x0004001C: "MUTE_LED",
    0x00050001: "ALS_ENABLE",
    0x00050011: "BACKLIGHT",
    0x00050012: "BRIGHTNESS",
    0x00050019: "PANEL_OD",
    0x0005001C: "PANEL_HD",
    0x0005002A: "SCREEN_AUTO_BRIGHTNESS",
    0x00050021: "KBD_BACKLIGHT",
    0x00050022: "LIGHT_SENSOR",
    0x0005002F: "OOBE",
    0x00050031: "SCREENPAD_POWER",
    0x00050032: "SCREENPAD_LIGHT",
    0x00060013: "CAMERA",
    0x00060062: "LID_FLIP",
    0x00060077: "LID_FLIP_ROG",
    0x00060078: "CAMERA_LED_NEG",
    0x00060079: "CAMERA_LED",
    0x00080013: "CARDREADER",
    0x00100011: "TOUCHPAD",
    0x00100012: "TOUCHPAD_LED",
    0x00100023: "FNLOCK",
    0x00110011: "THERMAL_CTRL",
    0x00110012: "FAN_CTRL",
    0x00110013: "CPU_FAN_CTRL",          # a live reading: see ATK_LIVE_IDS
    0x00110018: "FAN_BOOST_MODE",
    0x00110019: "THROTTLE_THERMAL_POLICY_VIVO",
    # Named from what MyASUS does with them (Windows capture, 2026-10-31),
    # not from asus-wmi.h, which has no name for them.
    0x00100021: "KEY_NOTIFY (MyASUS passes each vendor key code)",
    0x0012008E: "SLEEP_ASSISTANT ?",
    # GUESS (about 60 %) for "Memory Allocated to GPU"; asus-wmi.h names it.
    0x000600C1: "APU_MEM",
    0x00110024: "CPU_FAN_CURVE",
    0x00120012: "PROCESSOR_STATE",
    0x00120031: "LID_RESUME",
    0x00120057: "RSOC (battery charge limit)",
    0x00120063: "KBD_DOCK",
    0x0012006C: "CHARGE_MODE",
    0x00120075: "THROTTLE_THERMAL_POLICY",
    0x00120091: "KEYSTONE",
    0x001200A0: "PPT_PL2_SPPT",
    0x001200A3: "PPT_PL1_SPL",
    0x001200C1: "PPT_PL3_FPPT",
    0x001200E2: "MCU_POWERSAVE",
    0x00130022: "BOOT_SOUND",
}

# Ids whose DSTS answer moves by itself, so a before/after read cannot pin
# them on a step. CPU_FAN_CTRL changed after 37 of 80 steps in the
# 2026-10-31 captures, idle ones included: the fan speed, not a setting.
ATK_LIVE_IDS = {0x00110013}

# DSTS result bits, same header.
DSTS_STATUS_BIT = 0x00000001
DSTS_PRESENCE_BIT = 0x00010000
ATK_UNSUPPORTED = 0xFFFFFFFE


def atk_call_buffer(method, args):
    """The input buffer for ATK_IOCTL_CALL: <method><len(args)><args>."""
    return struct.pack("<II", method, len(args)) + bytes(args)


def decode_atk_call(in_bytes, out_bytes=b""):
    """Name an ATK_IOCTL_CALL. Returns a dict, never raises on short input."""
    result = {"kind": "atk"}
    if len(in_bytes) < 8:
        result["error"] = f"input of {len(in_bytes)} bytes is too short"
        return result
    method, length = struct.unpack_from("<II", in_bytes)
    args = in_bytes[8:8 + length]
    result["method"] = ATK_METHODS.get(method, f"0x{method:08x}")
    if len(args) >= 4:
        device = struct.unpack_from("<I", args)[0]
        result["device_id"] = f"0x{device:08x}"
        result["device"] = ATK_DEVICE_IDS.get(device, "unknown")
        if len(args) >= 8:
            value = struct.unpack_from("<I", args, 4)[0]
            result["value"] = f"0x{value:08x}"
        if len(args) > 8:
            result["extra_args"] = args[8:].hex()
    if len(out_bytes) >= 4:
        ret = struct.unpack_from("<I", out_bytes)[0]
        result["returned"] = f"0x{ret:08x}"
        if result["method"] == "DSTS":
            result["present"] = ret != ATK_UNSUPPORTED and bool(ret & DSTS_PRESENCE_BIT)
    return result


def linux_equivalent_atk(decoded):
    """The asus-wmi debugfs commands that repeat a decoded call on Linux.

    asus-wmi exposes the same two methods under
    /sys/kernel/debug/asus-nb-wmi/ (dev_id, ctrl_param, dsts, devs; read from
    drivers/platform/x86/asus-wmi.c). Root only, and DEVS writes BIOS state:
    replay it by hand, once, knowing what the device id is.
    """
    method = decoded.get("method")
    if method not in ("DSTS", "DEVS") or "device_id" not in decoded:
        return None
    base = "/sys/kernel/debug/asus-nb-wmi"
    lines = [f"echo {decoded['device_id']} | sudo tee {base}/dev_id"]
    if method == "DEVS":
        lines.append(f"echo {decoded.get('value', '0x0')} | sudo tee {base}/ctrl_param")
        lines.append(f"sudo cat {base}/devs")
    else:
        lines.append(f"sudo cat {base}/dsts")
    return lines


def atk_changes(before, after):
    """The device ids whose DSTS answer differs between two reads.

    before and after map device id (int) to the 32-bit answer (int, or None
    when the call failed). An id in only one read is not a change: capture.py
    adds ids as the hooks discover them, and their first read is a baseline.
    """
    changes = []
    for device in sorted(set(before) & set(after)):
        if before[device] != after[device]:
            changes.append({"device_id": f"0x{device:08x}",
                            "device": ATK_DEVICE_IDS.get(device, "unknown"),
                            "before": _hex32(before[device]),
                            "after": _hex32(after[device])})
    return changes


def _hex32(value):
    return "failed" if value is None else f"0x{value:08x}"


def describe_atk_change(change):
    return (f"{change['device']} ({change['device_id']}):"
            f" {change['before']} -> {change['after']}")


# ── HID ──────────────────────────────────────────────────────────────────────
ASUS_VID = 0x0B05
DUO_KEYBOARD_PID = 0x1B2C

# Windows HID class IOCTLs (hidclass.h): device type 0x0B. What HidD_SetFeature
# and friends turn into, so hook_asus.py can name them when an app calls
# DeviceIoControl directly instead of going through hid.dll.
HID_IOCTLS = {
    0x000B0191: "IOCTL_HID_SET_FEATURE",
    0x000B0192: "IOCTL_HID_GET_FEATURE",
    0x000B0195: "IOCTL_HID_SET_OUTPUT_REPORT",
    0x000B01A2: "IOCTL_HID_GET_INPUT_REPORT",
    0x000B01A0: "IOCTL_HID_GET_OUTPUT_REPORT",
    # Enumeration, not traffic: MyASUS asks for every collection's
    # descriptor when its pages open (seen 2026-10-31, over 1000 per minute
    # while the light-sensor page is up).
    0x000B01A8: "IOCTL_HID_GET_COLLECTION_INFORMATION",
    0x000B0193: "IOCTL_HID_GET_COLLECTION_DESCRIPTOR",
}
HID_ENUMERATION_IOCTLS = {0x000B01A8, 0x000B0193}

HANDSHAKE_TEXT = b"ASUS Tech.Inc."

# What Linux already sends, from lib/kb_init.py and docs/HARDWARE.md. A capture
# that shows Windows sending something else is a finding.
KNOWN_FEATURE_PREFIXES = [
    (bytes([0x5A]) + HANDSHAKE_TEXT, "hotkey handshake (kb_init, from hid-asus)"),
    (bytes([0x5D]) + HANDSHAKE_TEXT, "handshake on report 0x5d (kb_init)"),
    (bytes([0x5E]) + HANDSHAKE_TEXT, "handshake on report 0x5e (kb_init)"),
    (bytes([0x5A, 0x05, 0x20, 0x31, 0x00, 0x08]), "OOBE disable 1/4 (kb_init, guessed)"),
    (bytes([0x5A, 0xBA, 0xC5, 0xC4]), "keyboard backlight; next byte is the level"),
    (bytes([0x5A, 0xD0, 0x8F, 0x01]), "OOBE disable 3/4 (kb_init, guessed)"),
    (bytes([0x5A, 0xD0, 0x85, 0xFF]), "OOBE disable 4/4 (kb_init, guessed)"),
    # What MyASUS sends (Windows capture, 2026-10-31, same bytes over USB and
    # Bluetooth). The state byte follows the prefix: 1 on, 0 off.
    (bytes([0x5A, 0xD0, 0x4E]), "Fn-lock state, set by the host"),
    (bytes([0x5A, 0xD0, 0x7C]), "mic-mute LED, set by the host"),
    (bytes([0x5A, 0xF4, 0x6B]), "touchpad toggle (the keyboard answers with Ctrl+Win+F24)"),
]
STATE_PREFIXES = {bytes([0x5A, 0xD0, 0x4E]), bytes([0x5A, 0xD0, 0x7C])}

# Vendor input codes (report 0x5a, second byte). Same sources; "?" = not
# confirmed on this unit (watch_fn.py says which).
KNOWN_VENDOR_CODES = {
    0x10: "brightness down",
    0x20: "brightness up",
    0x3D: "status report, not a key (third byte: battery %? GUESS)",
    0x4E: "Fn-lock",
    0x6A: "second-screen key",
    0x7C: "mic mute (F9)",
    0x7E: "emoji (F11)",
    0x86: "MyASUS key (F12)",
    0x9C: "bare F8 (split screen)",
    0x9D: "Fn+F (fan profile)",
    0xC7: "keyboard backlight key",
}


def describe_feature(data):
    """A one-line meaning for a feature report payload (report id first)."""
    data = bytes(data)
    stripped = data.rstrip(b"\x00")
    for prefix, meaning in KNOWN_FEATURE_PREFIXES:
        if stripped.startswith(prefix.rstrip(b"\x00")):
            if prefix[:4] == bytes([0x5A, 0xBA, 0xC5, 0xC4]) and len(data) > 4:
                return f"{meaning} (level {data[4]})"
            if prefix in STATE_PREFIXES:
                return f"{meaning}: {'on' if len(data) > 3 and data[3] else 'off'}"
            return meaning
    return "not in the known table: new"


def describe_vendor_input(data):
    """Meaning of a vendor input report (0x5a, code, ...), or None."""
    data = bytes(data)
    if len(data) < 2 or data[0] != 0x5A:
        return None
    if data[1] == 0x00:
        return "key release"
    return KNOWN_VENDOR_CODES.get(data[1], f"unknown vendor code 0x{data[1]:02x}")


# Keyboard usages (HID Usage Tables 1.4, page 0x07) that carry what was typed:
# letters, digits, Enter, Esc, Backspace, Tab, space, punctuation, and the
# keypad. The rest (modifiers, F1-F24, arrows, PrtSc) say which shortcut was
# sent, which is what a capture is for.
TYPING_USAGES = set(range(0x04, 0x39)) | set(range(0x54, 0x64))
MODIFIER_NAMES = ["Ctrl", "Shift", "Alt", "Win", "RCtrl", "RShift", "RAlt", "RWin"]


def is_key_report(data):
    """A boot-protocol keyboard report: modifiers, a zero byte, six usages."""
    return len(data) == 8 and data[1] == 0 and data[0] != 0x5A


def scrub_keystrokes(data):
    """The report with the typed keys blanked; anything else unchanged.

    The USB capture sees every key pressed on the docked keyboard, passwords
    included. Captures are meant to be shared, so they keep the shortcut
    (Ctrl+Win+F24) and lose the text.
    """
    data = bytes(data)
    if not is_key_report(data):
        return data
    return data[:2] + bytes(0 if u in TYPING_USAGES else u for u in data[2:])


def describe_key_report(data):
    """'Ctrl+Win+F24', 'typing (hidden)' or 'all keys up' for a key report."""
    data = bytes(data)
    if not is_key_report(data):
        return None
    names = [name for bit, name in enumerate(MODIFIER_NAMES) if data[0] >> bit & 1]
    hidden = False
    for usage in data[2:]:
        if 0x3A <= usage <= 0x45:
            names.append(f"F{usage - 0x39}")
        elif 0x68 <= usage <= 0x73:
            names.append(f"F{usage - 0x68 + 13}")
        elif usage in TYPING_USAGES:
            hidden = True
        elif usage:
            names.append(f"usage 0x{usage:02x}")
    if hidden:
        names.append("typing (hidden)")
    return "keys: " + "+".join(names) if names else "keys: all up"


def decode_ioctl(code, in_bytes, out_bytes):
    """Decode any DeviceIoControl the hooks saw. Unknown codes stay raw."""
    if code == ATK_IOCTL_CALL:
        return decode_atk_call(in_bytes, out_bytes)
    if code == ATK_IOCTL_GET_EVENT:
        event = struct.unpack_from("<I", out_bytes)[0] if len(out_bytes) >= 4 else None
        return {"kind": "atk-event",
                "event": None if event is None else f"0x{event:x}"}
    if code == ATK_IOCTL_REGISTER_EVENT:
        return {"kind": "atk-register-event"}
    if code in HID_IOCTLS:
        name = HID_IOCTLS[code]
        # The report travels in whichever buffer is not empty: hid.dll passes
        # it as the "output" buffer for the METHOD_*_DIRECT codes.
        data = in_bytes or out_bytes
        decoded = {"kind": "hid", "ioctl": name, "data": data.hex()}
        if "FEATURE" in name and data:
            decoded["meaning"] = describe_feature(data)
        return decoded
    return {"kind": "other", "ioctl": f"0x{code:08x}"}


# ── Sessions and markers ─────────────────────────────────────────────────────
# Every tool writes into captures/<stamp>-<tool>/ next to this file unless told
# otherwise, and every interactive tool records markers the same way, so
# analyze.py can line up a USB capture, a hook log and a WMI trace by action.

def session_dir(tool, base=None, stamp=None):
    base = base or os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
    stamp = stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(base, f"{stamp}-{tool}")
    os.makedirs(path, exist_ok=True)
    return path


class JsonLog:
    """Append-only JSON lines, flushed per line so a crash loses nothing.

    Several capture threads write to one log, hence the lock.
    """

    def __init__(self, path):
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record):
        record.setdefault("ts", time.time())
        with self._lock:
            if self._file.closed:       # a capture thread outliving the run
                return
            self._file.write(json.dumps(record, sort_keys=True) + "\n")
            self._file.flush()

    def close(self):
        with self._lock:
            self._file.close()


class EventLog:
    """events.log: one readable line per captured event, kept off the screen.

    The screen shows the steps; this file has the detail (follow it live with
    `Get-Content events.log -Wait` in a second window, if wanted). It also
    counts events by kind, so each step can end with what it caught.
    """

    def __init__(self, path):
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._step = collections.Counter()
        self.total = collections.Counter()

    def add(self, kind, text):
        """Log one line; kind is what to count it as, or None for a note."""
        with self._lock:
            if self._file.closed:
                return
            self._file.write(f"{time.strftime('%H:%M:%S')} {text}\n")
            self._file.flush()
            if kind:
                self._step[kind] += 1
                self.total[kind] += 1

    def take_counts(self):
        """The counts since the last call."""
        with self._lock:
            counts, self._step = self._step, collections.Counter()
        return counts

    def close(self):
        with self._lock:
            self._file.close()


def count_summary(counts):
    """'2 ATK calls, 1 HID write' from a Counter of kinds (singular names)."""
    if not counts:
        return "nothing captured"
    return ", ".join(f"{n} {kind}{'' if n == 1 else 's'}"
                     for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def read_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass        # a half-written last line after a kill
    return records


# ── Guided scenarios ─────────────────────────────────────────────────────────
# Each step names one thing to do, so a capture holds one change per marker,
# and undoes it in a later step so the machine ends as it started. A step is
# one of three kinds:
#   * an action: the marker is written when the step is shown, the user does
#     it and presses Enter;
#   * a keyboard state (needs=...): it ends by itself once Windows sees the
#     keyboard in that state (when the tool can tell), or on Enter;
#   * a hands-off wait (wait=seconds): a countdown, nothing to press.
# Menu names are as MyASUS 4.0.81.0 shows them (screenshots, 2026-10-30): all
# under MyASUS > Device Settings, whose "tabs" only scroll one long page.

Step = collections.namedtuple("Step", "do hint needs wait")


def step(do, hint="", needs=None, wait=0):
    return Step(do, hint, needs, wait)


# What each needs= value asks of the keyboard, as the screen says it.
KEYBOARD_STATES = {
    "docked": "DOCKED: lying on the lower screen, on its pins (USB)",
    "off-dock": "OFF the lower screen",
    "bluetooth": "OFF the lower screen and CONNECTED over Bluetooth",
}

OPEN_INPUT_SETTINGS = step(
    "Open MyASUS, click Device Settings on the left, then click 'Input Device"
    " Settings' in the row along the top.",
    "That row only scrolls the page: every setting below is on the same page.")
HANDS_OFF_BASELINE = step("Hands off: touch nothing.", wait=10)
PAIRING_HINT = (
    "Usually a few seconds after lifting it. If it never connects, slide the"
    " switch on the keyboard's left edge on. Never paired with Windows? Hold F10"
    " for 4-5 s until its light flashes blue, then Settings > Bluetooth &"
    " devices > Add device.")


def _keyboard_steps(transport):
    """The keyboard run, the same over USB and Bluetooth except where noted."""
    if transport == "docked":
        start = [step("Put the keyboard on the lower screen so it snaps onto its"
                      " pins. Leave it there until this run ends.",
                      "This step ends by itself once Windows sees the keyboard"
                      " on USB.", needs="docked")]
    else:
        start = [step("Lift the keyboard off the lower screen and put it on the"
                      " desk. Leave it off until this run ends.", needs="off-dock"),
                 step("Wait for the keyboard to connect over Bluetooth.",
                      PAIRING_HINT, needs="bluetooth")]
    steps = start + [
        HANDS_OFF_BASELINE,
        step("Press F4 once, without Fn. (backlight 1 of 4)",
             "F4 has the keyboard-backlight icon; the light goes up one level."
             " If nothing changes, hold Fn while pressing F4 here and in every"
             " F-key step below."),
        step("Press F4 once. (backlight 2 of 4)"),
        step("Press F4 once. (backlight 3 of 4)"),
        step("Press F4 once. (backlight 4 of 4)",
             "If the backlight is not off now, press F4 until it is, then Enter."),
        step("Hold Fn, press Esc once, let go of both. (Fn-lock 1 of 2)",
             "This swaps what the bare F-keys do."),
        step("Hold Fn, press Esc once more, let go. (Fn-lock 2 of 2, back as"
             " it was)"),
        step("Press F8 once.",
             "If it opens anything, close it before pressing Enter."),
        step("Press F9 twice, about a second apart.",
             "Probably microphone mute: two presses leave it as it was."),
        step("Press F11 once.",
             "If a panel opens (emoji picker?), close it with Esc, then Enter."),
        step("Press F12 once.",
             "The MyASUS key: MyASUS opening or coming to the front is expected."),
        step("Press the second-screen key once. (1 of 2)",
             "The key just left of PrtSc, with a two-screen icon."),
        step("Press the second-screen key once more. (2 of 2, back as it was)",
             "Wait until both screens show the desktop again, then Enter."),
        OPEN_INPUT_SETTINGS,
        step("Touchpad: click its switch once, so it reads Lock.",
             "The touchpad stops working until the next step; use the touch"
             " screen to click."),
        step("Touchpad: click its switch again, so it reads Unlock."),
        step("Function key: click the option that is NOT selected now ('Locked"
             " Fn key' or 'Normal Fn key')."),
        step("Function key: click the option that was selected before."),
        step("Automatic keyboard backlight: click the Auto-brighten switch once."),
        step("Auto-brighten: click its switch again (back as it was)."),
        step("Automatic keyboard backlight: click the Auto-dim switch once."),
        step("Auto-dim: click its switch again (back as it was)."),
    ]
    if transport == "bluetooth":
        steps += [
            step("Automatic keyboard backlight: click the Smart Switch switch once.",
                 "Smart Switch turns the backlight off over Bluetooth."),
            step("Smart Switch: click its switch again (back as it was)."),
        ]
    steps += [
        step("Press F4 until the keyboard backlight is at its brightest."),
        step("Cover the light sensor with a fingertip for 10 s, then uncover it.",
             "GUESS: the sensor is in the bezel above the top screen, next to"
             " the camera. Press Enter after uncovering it."),
        step("Keyboard backlight sleep mode: in the drop-down pick 'While on"
             " battery or on AC power', and pick the shortest time offered.",
             "Note both old values first: a later step puts them back."),
        step("Hands off the keyboard and the touchpad until the countdown ends.",
             "The backlight should go off by itself when the time runs out."
             " If the shortest time offered is over 1 minute, press s to skip.",
             wait=90),
        step("Keyboard backlight sleep mode: put both values back as they were."),
    ]
    if transport == "bluetooth":
        steps.append(step(
            "Open Windows Settings > Bluetooth & devices and note the keyboard's"
            " battery percentage.",
            "Write it down for the report: it may explain the 3d status code."))
    steps.append(step("Hands off: touch nothing until the countdown ends.",
                      "The keyboard sends a status report (code 3d) now and then;"
                      " this waits for one.", wait=60))
    return steps


def _setting_pair(where, what, hint=""):
    """Flip a two-state setting, then flip it back: two steps."""
    return [step(f"{where}: click the {what} switch once.", hint),
            step(f"{where}: click the {what} switch again (back as it was).")]


SCENARIOS = {
    "keyboard-docked": {
        "title": "Keyboard over USB (docked)",
        "keyboard": "docked",
        "steps": _keyboard_steps("docked"),
    },
    "keyboard-bluetooth": {
        "title": "Keyboard over Bluetooth (detached)",
        "keyboard": "bluetooth",
        "steps": _keyboard_steps("bluetooth"),
    },
    "attach": {
        "title": "Docking, undocking, Bluetooth and sleep",
        "keyboard": None,
        "steps": [
            step("Put the keyboard on the lower screen so it snaps onto its pins.",
                 needs="docked"),
            HANDS_OFF_BASELINE,
            step("Lift the keyboard off the lower screen and put it on the desk.",
                 needs="off-dock"),
            step("Wait for the keyboard to connect over Bluetooth.", PAIRING_HINT,
                 needs="bluetooth"),
            HANDS_OFF_BASELINE,
            step("Put the keyboard back on the lower screen.", needs="docked"),
            HANDS_OFF_BASELINE,
            step("Sleep the laptop: Start > Power > Sleep. 10 s after the screens"
                 " go dark, press the power button, sign in, then press Enter.",
                 "This window keeps counting after the wake; that is fine."),
            HANDS_OFF_BASELINE,
        ],
    },
    "power": {
        "title": "Fan profile, battery care, sleep assistant, power mode",
        "keyboard": None,
        "steps": [
            step("Plug the charger in and leave it in for this run.",
                 "Performance mode is offered only on the charger."),
            step("Open MyASUS, click Device Settings on the left, then click"
                 " 'Power & Performance' in the row along the top."),
            HANDS_OFF_BASELINE,
            step("Fan Profile: click Whisper mode."),
            step("Fan Profile: click Performance mode."),
            step("Fan Profile: click Standard mode."),
            step("Hold Fn, press F once, let go of both. (fan 1 of 3)",
                 "A pop-up or MyASUS shows the new fan profile."),
            step("Hold Fn, press F once. (fan 2 of 3)"),
            step("Hold Fn, press F once. (fan 3 of 3)",
                 "That should be Standard again. If MyASUS shows another mode,"
                 " click Standard mode, then Enter."),
            *_setting_pair("Battery Care Mode", "'Battery Care Mode'"),
            step("Battery Care Mode: click the 'Instant Full-Charge Mode' switch"
                 " so it is on."),
            step("Battery Care Mode: click the 'Instant Full-Charge Mode' switch"
                 " so it is off."),
            *_setting_pair("Sleep Assistant", "Sleep Assistant"),
            step("Windows Settings > System > Power & battery > Power mode: pick"
                 " Best power efficiency."),
            step("Power mode: pick Best performance."),
            step("Power mode: pick Balanced (or what you had)."),
        ],
    },
    "display": {
        "title": "Refresh rate, OLED care, dimming, Splendid",
        "keyboard": None,
        "steps": [
            step("Open MyASUS, click Device Settings on the left, then click"
                 " 'Audio & Visual' in the row along the top."),
            HANDS_OFF_BASELINE,
            step("Display refresh rate: pick Fixed refresh rate, 60 Hz, on Panel 1."),
            step("Display refresh rate: put Panel 1 back (Adaptive, or what you had)."),
            *_setting_pair("ASUS OLED Care", "Pixel refresh"),
            step("OLED Flicker-Free Dimming, Panel 1: drag the slider to 50.",
                 "Note where it was first."),
            step("OLED Flicker-Free Dimming, Panel 1: drag it back to where it was."),
            step("OLED Flicker-Free Dimming: pick Panel 2, drag the slider to 50."),
            step("OLED Flicker-Free Dimming, Panel 2: drag it back to where it was."),
            step("Splendid, Panel 1: click Vivid."),
            step("Splendid, Panel 1: click Eye Care."),
            step("Splendid, Panel 1: click E-Reading."),
            step("Splendid, Panel 1: click Manual and move its slider once."),
            step("Splendid, Panel 1: click Normal (or what you had)."),
            step("Splendid: pick a different Color Gamut.", "Note the old one first."),
            step("Splendid: put Color Gamut back."),
            step("Target Mode: switch it on."),
            step("Target Mode: switch it off."),
            *_setting_pair("Tru2Life", "Tru2Life"),
        ],
    },
    "audio": {
        "title": "Sound modes, volume booster, AI noise canceling",
        "keyboard": None,
        "steps": [
            step("Open MyASUS, click Device Settings on the left, then click"
                 " 'Audio & Visual' in the row along the top.",
                 "Note the current Sound Mode and microphone mode first."),
            HANDS_OFF_BASELINE,
            step("Sound Modes: click Dynamic."),
            step("Sound Modes: click Music."),
            step("Sound Modes: click Voice."),
            step("Sound Modes: click the mode you had before."),
            step("Volume Booster: switch it on."),
            step("Volume Booster: switch it off."),
            step("AI Noise-Canceling Speaker: switch it on."),
            step("AI Noise-Canceling Speaker: switch it off."),
            step("AI Noise-Canceling Microphone: click Authentic Recording (AI off)."),
            step("AI Noise-Canceling Microphone: click Single-presenter Mode."),
            step("AI Noise-Canceling Microphone: click the mode you had before."),
        ],
    },
}


# ── The step runner ──────────────────────────────────────────────────────────

class Paint:
    """ANSI colour, on only for a console that shows it (and not NO_COLOR)."""

    CODES = {"step": "30;43;1", "do": "96;1", "hint": "37", "ok": "92",
             "warn": "91;1", "info": "90", "keys": "93"}

    def __init__(self, out):
        self.on = (hasattr(out, "isatty") and out.isatty()
                   and "NO_COLOR" not in os.environ and _enable_vt())

    def __call__(self, kind, text):
        return f"\x1b[{self.CODES[kind]}m{text}\x1b[0m" if self.on else text


def _enable_vt():
    """Turn on escape-code handling in a Windows console (on by default elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)                 # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING (wincon.h)
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


class ConsoleKeys:
    """Single key presses, with a timeout, from the console.

    get() returns "enter", a lower-case character, or None when nothing was
    pressed in time. Windows reads keys as they are pressed (msvcrt); other
    systems read whole lines, so a letter needs Enter after it there.
    """

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin

    def get(self, timeout=None):
        if os.name == "nt":
            import msvcrt
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    char = msvcrt.getwch()
                    if char in ("\x00", "\xe0"):        # arrows, F-keys: two codes
                        msvcrt.getwch()
                        continue
                    if char == "\x03":
                        raise KeyboardInterrupt
                    return "enter" if char in "\r\n" else char.lower()
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                time.sleep(0.05)
        if timeout is not None:
            import select
            if not select.select([self.stream], [], [], timeout)[0]:
                return None
        line = self.stream.readline()
        if not line:
            return "q"
        line = line.strip().lower()
        return line[:1] if line else "enter"

    def line(self):
        return self.stream.readline()


def parse_step_selection(text, count):
    """'5', '5-7,12' or '20-' -> sorted step numbers (1-based) within 1..count."""
    chosen = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        first, dash, last = part.partition("-")
        try:
            low = int(first)
            high = (int(last) if last else count) if dash else low
        except ValueError:
            raise ValueError(f"'{part}' is not a step number or range like 5-7")
        if not 1 <= low <= high <= count:
            raise ValueError(f"'{part}' is outside steps 1-{count}")
        chosen.update(range(low, high + 1))
    if not chosen:
        raise ValueError("no steps chosen")
    return sorted(chosen)


STEPS_HELP = ("run only these steps of the scenario, by the numbers it shows:"
              " 5, 5-7,12 or 20- (from 20 to the end)")


def selected_steps(args):
    """The --steps choice of a parsed command line, or None for all steps."""
    if not getattr(args, "steps", None):
        return None
    if not args.scenario:
        sys.exit("--steps needs --scenario")
    try:
        return parse_step_selection(args.steps, len(SCENARIOS[args.scenario]["steps"]))
    except ValueError as error:
        sys.exit(f"--steps: {error}")


def run_steps(log, scenario=None, keys=None, out=None, detect=None, after_step=None,
              only=None):
    """Walk a scenario, writing one marker per step. Returns the steps done.

    only lists the step numbers to run (parse_step_selection); default all.
    detect() names the keyboard's state ("docked", "bluetooth", "none") or
    returns None when it cannot tell; with it, keyboard-state steps end by
    themselves. after_step(index) is called when a step ends and may return a
    line to show (what was captured during it). Without a scenario, whatever
    the user types is the label. The keys are Enter (done), s (skip), r (redo
    the step before this one) and q (stop).
    """
    out = out or sys.stdout
    keys = keys or ConsoleKeys()
    paint = Paint(out)
    log.write({"type": "marker", "label": "capture started", "scenario": scenario})
    done = 0
    if not scenario:
        print(paint("do", "Type what you are about to do and press Enter (q to stop)."),
              file=out)
        while True:
            label = keys.line()
            if not label or label.strip().lower() == "q":
                break
            if label.strip():
                log.write({"type": "marker", "label": label.strip()})
                done += 1
        log.write({"type": "marker", "label": "capture stopped"})
        return done

    plan = SCENARIOS[scenario]
    steps = plan["steps"]
    print(paint("step", f" {plan['title']}: {len(steps)} steps "), file=out)
    if plan["keyboard"]:
        print(paint("warn", "For this whole run the keyboard must be "
                    f"{KEYBOARD_STATES[plan['keyboard']]}."), file=out)
    print(paint("keys", "Keys: Enter = step done · s = skip step · r = redo the step"
                " before · q = stop"), file=out)
    order = only or list(range(1, len(steps) + 1))
    if only:
        print(paint("info", "Only steps " + ", ".join(map(str, only))), file=out)
    position = 0
    redo = False
    while position <= len(order):
        if position == len(order):
            # A last chance to redo the final step before the capture stops.
            print("\n  " + paint("keys", "Enter = finish · r = redo the last step"),
                  file=out, end="", flush=True)
            key = _until_key(keys)
            print(file=out)
            if key == "r":
                position, redo = position - 1, True
                continue
            break
        index = order[position]
        current = steps[index - 1]
        tag = f"  [keyboard: {plan['keyboard']}]" if plan["keyboard"] else ""
        again = "  (again)" if redo else ""
        print("\n" + paint("step", f" STEP {index}/{len(steps)} ")
              + paint("info", tag + again), file=out)
        print("  " + paint("do", current.do), file=out)
        if current.hint:
            print("  " + paint("hint", current.hint), file=out)
        marker = {"type": "marker", "label": current.do, "step": index,
                  "scenario": scenario}
        if redo:
            marker["redo"] = True
        log.write(marker)
        redo = False
        if current.needs:
            outcome = _wait_for_state(current.needs, keys, out, paint, detect)
        elif plan["keyboard"] and detect and not _state_matches(
                plan["keyboard"], detect() or plan["keyboard"]):
            # The keyboard moved mid-run: hold this step until it is back.
            print("  " + paint("warn", "The keyboard is not where this run needs it."),
                  file=out)
            outcome = _wait_for_state(plan["keyboard"], keys, out, paint, detect)
            if outcome == "enter":
                outcome = _action(current, keys, out, paint)
        else:
            outcome = _action(current, keys, out, paint)
        if outcome == "q":
            break
        if outcome == "r":
            if position == 0:
                print("  " + paint("info", "no step before this one; this one again"),
                      file=out)
            else:
                position -= 1
            redo = True
            continue
        position += 1
        if outcome == "s":
            log.write({"type": "marker", "label": f"skipped: {current.do}",
                       "step": index, "skipped": True, "scenario": scenario})
            print("  " + paint("info", "skipped"), file=out)
            continue
        done += 1
        summary = after_step(index) if after_step else None
        print("  " + paint("ok", "✓ " + (summary or "done")), file=out)
    print("\n" + paint("step", " Scenario finished "), file=out)
    log.write({"type": "marker", "label": "capture stopped"})
    return done


def _action(current, keys, out, paint):
    """A step the user does: a hands-off countdown, or Enter when done."""
    if current.wait:
        return _countdown(current.wait, keys, out, paint)
    print("  " + paint("keys", "Enter when done"), file=out, end="", flush=True)
    key = _until_key(keys)
    print(file=out)
    return key


def _until_key(keys):
    while True:
        key = keys.get()
        if key in ("enter", "s", "r", "q"):
            return key


def _countdown(seconds, keys, out, paint):
    """A hands-off wait. Enter ends it early, s skips it, r goes back, q stops."""
    for remaining in range(seconds, 0, -1):
        print("\r  " + paint("keys", f"hands off: {remaining:>3} s left "), file=out,
              end="", flush=True)
        key = keys.get(1.0)
        if key in ("enter", "s", "r", "q"):
            print(file=out)
            return key
    print(file=out)
    return "enter"


def _wait_for_state(needs, keys, out, paint, detect):
    """Until the keyboard is in the state the step asks for, or Enter."""
    want = KEYBOARD_STATES[needs]
    if detect is None:
        print("  " + paint("keys", f"Enter once the keyboard is {want}"), file=out,
              end="", flush=True)
        key = _until_key(keys)
        print(file=out)
        return key
    shown = False
    while True:
        state = detect()
        if state is not None and _state_matches(needs, state):
            print(("\n" if shown else "") + "  " + paint("ok", f"seen: keyboard {state}"),
                  file=out)
            return "enter"
        if not shown:
            seen = state or "unknown"
            print("  " + paint("warn", f"Waiting for the keyboard to be {want}")
                  + paint("info", f" (now: {seen}; Enter if it already is)"),
                  file=out, end="", flush=True)
            shown = True
        key = keys.get(1.0)
        if key in ("enter", "s", "r", "q"):
            print(file=out)
            return key


def _state_matches(needs, state):
    if needs == "off-dock":
        return state != "docked"
    return state == needs


def keyboard_transport(paths):
    """'docked', 'bluetooth' or 'none', from the HID interface paths Windows lists.

    The ids are from docs/HARDWARE.md: 0b05:1b2c on the pogo pins (USB),
    0b05:1b2d over Bluetooth. How Windows spells them in a path is read from
    the Windows HID enumerator naming (USB: "vid_0b05&pid_1b2c"; Bluetooth:
    "..._vid&00020b05_pid&1b2d..." after the HID service GUID); that this
    keyboard's paths look so is VERIFY-ON-HW, which is why every keyboard
    step also ends on Enter.
    """
    lowered = [p.lower() for p in paths]
    if any("vid_0b05&pid_1b2c" in p for p in lowered):
        return "docked"
    if any("0b05_pid&1b2d" in p or "pid_1b2d" in p
           or (is_bluetooth_path(p) and "0b05" in p) for p in lowered):
        return "bluetooth"
    return "none"


def is_bluetooth_path(path):
    """A HID path under the Bluetooth HID (Classic) or HOGP (LE) service."""
    path = path.lower()
    return "{00001124-" in path or "{00001812-" in path


def label_for(ts, markers):
    """The label of the last marker at or before ts ('' before the first)."""
    label = ""
    for marker in markers:
        if marker.get("ts", 0) <= ts:
            label = marker.get("label", "")
        else:
            break
    return label


def require_windows(tool):
    if os.name != "nt":
        sys.exit(f"{tool}: this one runs on Windows (see docs/WINDOWS-RE.md).")
