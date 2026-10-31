#!/usr/bin/env python3
"""Keyboard state the host keeps: Fn-lock and the mic-mute LED.

Measured under Windows on 2026-10-31 (docs/HARDWARE.md, "What MyASUS does"):
the keyboard only reports Fn+Esc (`5a 4e`) and F9 (`5a 7c`); MyASUS answers
with a feature report that sets the state, `5a d0 4e <1|0>` for Fn-lock and
`5a d0 7c <1|0>` for the mic-mute LED, docked and over Bluetooth alike. The
MyASUS "Function key" option sends the same Fn-lock report and nothing else.
That the keyboard swaps its F-row on that report, rather than only lighting
the Esc LED, is what MyASUS implies; **VERIFY-ON-HW** with `duo fn-probe`.

The keyboard forgets on every re-enumeration (dock, undock, resume), as it
forgets hotkey mode, so the Fn-lock choice is remembered here and re-sent by
watch-fn after each kb-init (--restore).

Usage: kb_state.py fn-lock [on|off|toggle|--show|--restore]
       kb_state.py mic-led on|off
Exit codes: as kb_backlight.py (0 ok · 1 no device · 13 permission · 64 usage).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_backlight  # noqa: E402  (same directory; owns the report sender)

FN_LOCK = [0x5A, 0xD0, 0x4E]
MIC_LED = [0x5A, 0xD0, 0x7C]


def fn_lock_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "zenduo", "fn-lock")


def read_fn_lock():
    """The remembered Fn-lock, False when nothing was saved.

    False is the state hotkey mode starts in: after kb-init the bare F-keys
    are the media keys (HARDWARE.md, "Hotkey mode"), as under Windows with
    MyASUS's report at 0.
    """
    try:
        with open(fn_lock_path()) as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def save_fn_lock(on):
    path = fn_lock_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("1\n" if on else "0\n")
        os.replace(tmp, path)
    except OSError:
        pass  # best effort, like the backlight level


def report(prefix, on):
    return prefix + [1 if on else 0]


def set_fn_lock(on):
    rc = kb_backlight.send_vendor_report(report(FN_LOCK, on),
                                         f"Fn-lock {'on' if on else 'off'}", "kb_state")
    if rc == 0:
        save_fn_lock(on)
    return rc


def set_mic_led(on):
    return kb_backlight.send_vendor_report(report(MIC_LED, on),
                                           f"mic-mute LED {'on' if on else 'off'}",
                                           "kb_state")


def main(argv):
    if argv[:1] == ["fn-lock"]:
        arg = argv[1] if len(argv) == 2 else "--show"
        if arg == "--show":
            print("on" if read_fn_lock() else "off")
            return 0
        if arg == "--restore":
            # Off is what a freshly initialised keyboard already is: nothing
            # to send, and one write fewer over Bluetooth, where refusals hide.
            return set_fn_lock(True) if read_fn_lock() else 0
        if arg in ("on", "off", "toggle"):
            on = (not read_fn_lock()) if arg == "toggle" else arg == "on"
            return set_fn_lock(on)
    if argv[:1] == ["mic-led"] and len(argv) == 2 and argv[1] in ("on", "off"):
        return set_mic_led(argv[1] == "on")
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
