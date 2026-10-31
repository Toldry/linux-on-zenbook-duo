#!/usr/bin/env python3
"""F7 opens a terminal; a Super+P typed by hand still opens GNOME's display
switcher.

Bare F7 does not send a key of its own: after kb-init the keyboard types a
literal Super+P on its ordinary keyboard interface (measured 2026-10-30 with
`duo watch-input`: KEY_LEFTMETA + KEY_P, scancodes 0x700e3 + 0x70013). Mutter
binds Super+P to `switch-monitor`, the switcher F7 used to raise, and GNOME
cannot tell F7 from a typed Super+P. So Super+P comes out of `switch-monitor`
and goes to a custom shortcut running `duo super-p`, which decides:

- F7 sends Super and P in ONE keyboard report (`02 08 00 13`, docs/HARDWARE.md,
  measured 2026-10-30 docked). A hand types Super first: one report with the
  modifier alone, then one with P. watch-fn reads those reports from the
  keyboard's hidraw nodes (ChordWatch) and leaves a mark when it sees the
  one-report kind (mark_f7).
- `duo super-p` waits briefly for that mark. Marked: `duo terminal`. Not
  marked (typed by hand, on any keyboard): GNOME's own display switcher,
  raised through the Shell extension, or `duo layout cycle` without it.

So without watch-fn F7 falls back to the switcher, never Super+P to the
terminal. Whether the Bluetooth report has the same shape is VERIFY-ON-HW;
if it does not, F7 on Bluetooth raises the switcher.

Everything is GNOME's own settings, written through the gsettings command.
Other entries in `switch-monitor` (XF86Display) and the user's own custom
shortcuts are kept.

Usage:
  hotkeys.py apply DUO_CLI   bind Super+P to `DUO_CLI super-p`
  hotkeys.py remove          give Super+P back to the display switcher
  hotkeys.py show            print what is bound now
  hotkeys.py super-p         what the shortcut runs: F7 or a typed Super+P
Exit: 0 done · 1 gsettings failed · 64 usage.
"""

import ast
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shell_ext  # noqa: E402  (same directory; the display switcher lives in the Shell)

DUO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "bin", "duo-cli"))

F7_ACCEL = "<Super>p"
MUTTER = "org.gnome.mutter.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_DIR = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
F7_PATH = CUSTOM_DIR + "zenduo-f7/"
CUSTOM = f"{MEDIA}.custom-keybinding:{F7_PATH}"
NAME = "F7 opens a terminal, Super+P the display switcher (zenduo)"
# Until 2026-10-30 F7 toggled dark mode under this path; apply and remove
# take it out, so an update does not leave two shortcuts on Super+P.
OLD_PATHS = (CUSTOM_DIR + "zenduo-theme-toggle/",)


def custom(path):
    return f"{MEDIA}.custom-keybinding:{path}"


class HotkeyError(Exception):
    pass


def to_gvariant(values):
    """['a', 'b'] as GVariant text; an empty list needs its type spelled out."""
    def q(s):
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "[" + ", ".join(q(v) for v in values) + "]" if values else "@as []"


def from_gvariant(text):
    text = text.strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        raise HotkeyError(f"unexpected gsettings value: {text}") from None
    if isinstance(value, str):
        return value
    return list(value)


def run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


class GSettings:
    def __init__(self, runner=run):
        self.runner = runner

    def call(self, *argv):
        rc, out = self.runner(["gsettings", *argv])
        if rc != 0:
            raise HotkeyError(f"gsettings {' '.join(argv[:3])} failed: {out.strip()}")
        return out

    def get(self, schema, key):
        return from_gvariant(self.call("get", schema, key))

    def set(self, schema, key, value):
        text = to_gvariant(value) if isinstance(value, list) else value
        self.call("set", schema, key, text)

    def reset(self, schema, key):
        self.call("reset", schema, key)


def is_f7(accel):
    return accel.lower().replace("<mod4>", "<super>") == F7_ACCEL.lower()


def apply(store, duo_cli):
    """Bind F7 to the terminal; True when anything had to change."""
    changed = False
    switch = store.get(MUTTER, "switch-monitor")
    kept = [a for a in switch if not is_f7(a)]
    if kept != switch:
        store.set(MUTTER, "switch-monitor", kept)
        changed = True
    paths = store.get(MEDIA, "custom-keybindings")
    kept = [p for p in paths if p not in OLD_PATHS]
    if F7_PATH not in kept:
        kept.append(F7_PATH)
    if kept != paths:
        store.set(MEDIA, "custom-keybindings", kept)
        changed = True
    for old in OLD_PATHS:
        if old in paths:
            for key in ("name", "command", "binding"):
                store.reset(custom(old), key)
    want = {"name": NAME, "command": f"{duo_cli} super-p", "binding": F7_ACCEL}
    for key, value in want.items():
        if store.get(CUSTOM, key) != value:
            store.set(CUSTOM, key, value)
            changed = True
    return changed


def remove(store):
    """Undo apply(); Super+P goes back to the front of `switch-monitor`."""
    switch = store.get(MUTTER, "switch-monitor")
    if not any(is_f7(a) for a in switch):
        store.set(MUTTER, "switch-monitor", [F7_ACCEL] + switch)
    paths = store.get(MEDIA, "custom-keybindings")
    ours = (F7_PATH,) + OLD_PATHS
    if any(p in paths for p in ours):
        store.set(MEDIA, "custom-keybindings", [p for p in paths if p not in ours])
    for path in ours:
        for key in ("name", "command", "binding"):
            store.reset(custom(path), key)


def show(store):
    bound = F7_PATH in store.get(MEDIA, "custom-keybindings")
    command = store.get(CUSTOM, "command") if bound else ""
    switch = store.get(MUTTER, "switch-monitor")
    if bound and not any(is_f7(a) for a in switch):
        return f"F7 (Super+P): {command}"
    if any(is_f7(a) for a in switch):
        return "F7 (Super+P): GNOME's display switcher"
    return "F7 (Super+P): unbound"


# ── telling F7 from a typed Super+P ──────────────────────────────────────────

VENDOR_REPORT_ID = 0x5A
LEFT_GUI = 0x08        # modifier bit of a boot-style keyboard report
USAGE_P = 0x13         # HID keyboard page usage of P
MARK_FILE = "f7-pressed"
MARK_FRESH = 1.0       # seconds after F7 that its mark still counts
MARK_WAIT = 0.25       # how long super-p waits for watch-fn's mark


class ChordWatch:
    """Which Super+P reports came from F7: modifier and P in one report.

    Keyboard reports are [report id, modifiers, reserved, keys...]. Fed every
    report of every keyboard hidraw node; remembers the modifiers each
    (node, report id) last sent, so P arriving with Super already held is a
    hand, and P arriving together with Super is the key.
    """

    def __init__(self):
        self.modifiers = {}

    def is_f7(self, node, data):
        if len(data) < 4 or data[0] == VENDOR_REPORT_ID or data[2] != 0:
            return False
        key = (node, data[0])
        before = self.modifiers.get(key, 0)
        self.modifiers[key] = data[1]
        return bool(data[1] & LEFT_GUI) and USAGE_P in data[3:] \
            and not before & LEFT_GUI


def mark_path():
    """Runtime, not state: a mark must not outlive the session."""
    base = (os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_STATE_HOME")
            or os.path.expanduser("~/.local/state"))
    return os.path.join(base, "zenduo", MARK_FILE)


def mark_f7(now=None, path=None):
    """watch-fn: F7 was just pressed. Wall time, read back with wall time."""
    path = path or mark_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{time.time() if now is None else now}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def take_f7_mark(path=None, wait=MARK_WAIT, clock=time.time, sleep=time.sleep):
    """True if F7 was pressed within MARK_FRESH s; the mark is used up.

    watch-fn and GNOME see the key at the same moment, but this runs only
    after GNOME has started it, so the mark is usually there already; the
    wait covers a busy watch-fn.
    """
    path = path or mark_path()
    deadline = clock() + wait
    while True:
        try:
            with open(path) as f:
                stamp = float(f.read().strip() or "0")
        except (OSError, ValueError):
            stamp = None
        if stamp is not None and abs(clock() - stamp) <= MARK_FRESH:
            try:
                os.remove(path)
            except OSError:
                pass
            return True
        if clock() >= deadline:
            return False
        sleep(0.02)


def super_p(duo_cli=DUO, take=take_f7_mark, call=shell_ext.call, runner=subprocess.run):
    """What Super+P does: the terminal for F7, the display switcher otherwise."""
    if take():
        return runner([duo_cli, "terminal"]).returncode
    rc, out = call("ShowDisplaySwitcher")
    if rc == 0:
        return 0
    # No extension (or one from before this method): step the layouts, which
    # is what a quick Super+P does in GNOME too.
    print("super-p: no display switcher from the Shell extension "
          f"({out.strip().splitlines()[-1] if out.strip() else '?'}); "
          "stepping the layout instead", file=sys.stderr)
    return runner([duo_cli, "layout", "cycle"]).returncode


def main(argv, store=None):
    if argv == ["super-p"]:
        return super_p()
    if not argv or argv[0] not in ("apply", "remove", "show") \
            or (argv[0] == "apply") != (len(argv) == 2) or len(argv) > 2:
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        return 64
    store = store or GSettings()
    try:
        if argv[0] == "apply":
            changed = apply(store, argv[1])
            print("hotkeys: F7 opens a terminal, a typed Super+P the display switcher"
                  + ("" if changed else " (up to date)"))
        elif argv[0] == "remove":
            remove(store)
            print("hotkeys: F7 opens the display switcher again")
        else:
            print(show(store))
    except HotkeyError as e:
        print(f"hotkeys: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
