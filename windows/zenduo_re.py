r"""Shared pieces of the Windows reverse-engineering kit (docs/WINDOWS-RE.md).

Everything here is plain stdlib and runs on Linux too, which is the point: the
captures are made on Windows, but they are read, decoded and turned into Linux
equivalents by the same functions, and tests/test_windows_re.py checks those
functions without a Windows machine.

What lives here:
  * the capture session folder and its marker log (what the user said they
    were about to do, with a timestamp, so every trace can be cut by action);
  * the guided scenarios: which MyASUS toggles to flip, in which order;
  * decoders for the two channels the ASUS software talks to the hardware on:
    the ATKACPI driver (DSTS/DEVS calls into the BIOS) and HID feature reports
    to the keyboard.

Where each constant comes from is said next to it. Nothing here was measured
on Windows yet: the tables are what Linux and other projects already know,
and the captures are how to confirm or correct them.
"""

import datetime
import json
import os
import struct
import sys
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
    0x00110013: "CPU_FAN_CTRL",
    0x00110018: "FAN_BOOST_MODE",
    0x00110019: "THROTTLE_THERMAL_POLICY_VIVO",
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
}

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
]

# Vendor input codes (report 0x5a, second byte). Same sources; "?" = not
# confirmed on this unit (watch_fn.py says which).
KNOWN_VENDOR_CODES = {
    0x10: "brightness down",
    0x20: "brightness up",
    0x3D: "status report, not a key (meaning unknown)",
    0x4E: "Fn-lock",
    0x6A: "second-screen key",
    0x7C: "mic mute ?",
    0x7E: "emoji ?",
    0x86: "MyASUS key ?",
    0x9C: "bare F8 (split screen)",
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
    """Append-only JSON lines, flushed per line so a crash loses nothing."""

    def __init__(self, path):
        self.path = path
        self._file = open(path, "a", encoding="utf-8")

    def write(self, record):
        record.setdefault("ts", time.time())
        self._file.write(json.dumps(record, sort_keys=True) + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


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


# Each step names one thing to do in MyASUS (or on the keyboard), so a capture
# holds one change per marker. Order matters: undo each change in the next
# step so the machine ends as it started. Labels that say "(USB)" or "(BT)"
# need the keyboard docked or detached and paired.
SCENARIOS = {
    "attach": [
        "baseline: touch nothing for 10 s",
        "(USB) dock the keyboard and wait 10 s",
        "(USB) undock the keyboard and wait 10 s",
        "(BT) turn the keyboard's Bluetooth on and wait until it connects",
        "(BT) dock again and wait 10 s",
        "sleep the laptop (Start > Power > Sleep), wake it, wait 10 s",
    ],
    "keyboard": [
        "baseline: touch nothing for 10 s",
        "press the backlight key (F4) once, wait 3 s",
        "press the backlight key (F4) once, wait 3 s",
        "press the backlight key (F4) once, wait 3 s",
        "press the backlight key (F4) until it is off",
        # Menu names read from MyASUS 4.0.81.0 screenshots, 2026-10-30.
        "MyASUS > Device Settings > Input Device Settings > Keyboard backlight"
        " sleep mode: pick 'While on battery or on AC power' and a different"
        " idle time",
        "same place: put the sleep mode and idle time back as they were",
        "same tab, Automatic keyboard backlight: switch Smart Switch (light off"
        " over Bluetooth) to its other state",
        "same place: switch Smart Switch back",
        "same place: switch Auto-brighten to its other state",
        "same place: switch Auto-brighten back",
        "same place: switch Auto-dim to its other state",
        "same place: switch Auto-dim back",
        "cover the light sensor (probably next to the camera above the top screen)"
        " with a finger for 10 s, then uncover it (Auto-brighten/Auto-dim at work)",
        "press Fn+Esc (Fn-lock) once",
        "press Fn+Esc (Fn-lock) again",
        "same tab, Function key: switch between 'Locked Fn key' and 'Normal Fn"
        " key'",
        "same place: switch the Function key setting back",
        "press F9 once",
        "press F11 once",
        "press F12 (MyASUS key) once",
        "press the second-screen key (left of PrtSc) once",
        "press the second-screen key again",
        "leave the keyboard alone for 60 s (status reports 0x3d)",
    ],
    "power": [
        "baseline: touch nothing for 10 s",
        "MyASUS: set the fan profile to Whisper (or Quiet)",
        "MyASUS: set the fan profile to Performance",
        "MyASUS: set the fan profile to Standard",
        "MyASUS: set the battery charging mode to Balanced (stops at 80 %)",
        "MyASUS: set the battery charging mode to Full capacity",
        "MyASUS: set the battery charging mode back to what it was",
        "Windows Settings > System > Power: set Power mode to Best power efficiency",
        "Windows Settings > System > Power: set Power mode to Best performance",
        "Windows Settings > System > Power: set Power mode back to Balanced",
    ],
    "display": [
        "baseline: touch nothing for 10 s",
        "MyASUS: in OLED care, switch each option to its other state",
        "MyASUS: in OLED care, switch each option back",
        "MyASUS: pick a different Splendid colour mode",
        "MyASUS: pick the Splendid colour mode you had before",
        "MyASUS: switch flicker-free dimming to its other state",
        "MyASUS: switch flicker-free dimming back",
    ],
    "audio": [
        "baseline: touch nothing for 10 s",
        "MyASUS or the audio app: pick a different speaker sound mode or preset",
        "pick the sound mode you had before",
        "switch the microphone's AI noise cancelation to its other state",
        "switch the microphone's AI noise cancelation back",
    ],
}


def run_markers(log, scenario=None, out=sys.stdout, inp=sys.stdin):
    """Record markers until the user types q. Returns how many were written.

    With a scenario, each step is printed and Enter marks "about to do it";
    `s` skips a step. Without one, whatever the user types is the label.
    """
    written = 0
    steps = SCENARIOS.get(scenario, []) if scenario else []
    log.write({"type": "marker", "label": "capture started"})
    if steps:
        print(f"Scenario '{scenario}': {len(steps)} steps. Enter = about to do"
              " the step, s = skip, q = stop.", file=out)
        for index, step in enumerate(steps, 1):
            print(f"\n[{index}/{len(steps)}] {step}", file=out)
            answer = inp.readline()
            if not answer or answer.strip().lower() == "q":
                break
            if answer.strip().lower() == "s":
                continue
            log.write({"type": "marker", "label": step, "step": index})
            written += 1
            print("   marked; do it now, then press Enter for the next step.", file=out)
        print("\nScenario done. Press Enter to stop the capture.", file=out)
        inp.readline()
    else:
        print("Type what you are about to do and press Enter (q to stop).", file=out)
        for line in inp:
            label = line.strip()
            if label.lower() == "q":
                break
            if label:
                log.write({"type": "marker", "label": label})
                written += 1
    log.write({"type": "marker", "label": "capture stopped"})
    return written


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
