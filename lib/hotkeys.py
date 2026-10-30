#!/usr/bin/env python3
"""F7 toggles dark mode instead of opening GNOME's display switcher.

Bare F7 does not send a key of its own: after kb-init the keyboard types a
literal Super+P on its ordinary keyboard interface (measured 2026-10-30 with
`duo watch-input`: KEY_LEFTMETA + KEY_P, scancodes 0x700e3 + 0x70013). Mutter
binds Super+P to `switch-monitor`, which is the switcher F7 used to raise.
So the key can only be rebound where GNOME reads it: Super+P comes out of
`switch-monitor` and goes to a custom shortcut running `duo theme toggle`.
A Super+P typed by hand does the same — GNOME cannot tell the two apart —
and `duo layout cycle` still steps the display layouts.

Everything is GNOME's own settings, written through the gsettings command.
Other entries in `switch-monitor` (XF86Display) and the user's own custom
shortcuts are kept.

Usage:
  hotkeys.py apply DUO_CLI   bind F7 (Super+P) to `DUO_CLI theme toggle`
  hotkeys.py remove          give Super+P back to the display switcher
  hotkeys.py show            print what is bound now
Exit: 0 done · 1 gsettings failed · 64 usage.
"""

import ast
import subprocess
import sys

F7_ACCEL = "<Super>p"
MUTTER = "org.gnome.mutter.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_DIR = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
THEME_PATH = CUSTOM_DIR + "zenduo-theme-toggle/"
CUSTOM = f"{MEDIA}.custom-keybinding:{THEME_PATH}"
NAME = "Toggle dark mode (zenduo, F7)"


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
    """Bind F7 to the theme toggle; True when anything had to change."""
    changed = False
    switch = store.get(MUTTER, "switch-monitor")
    kept = [a for a in switch if not is_f7(a)]
    if kept != switch:
        store.set(MUTTER, "switch-monitor", kept)
        changed = True
    paths = store.get(MEDIA, "custom-keybindings")
    if THEME_PATH not in paths:
        store.set(MEDIA, "custom-keybindings", paths + [THEME_PATH])
        changed = True
    want = {"name": NAME, "command": f"{duo_cli} theme toggle", "binding": F7_ACCEL}
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
    if THEME_PATH in paths:
        store.set(MEDIA, "custom-keybindings", [p for p in paths if p != THEME_PATH])
    for key in ("name", "command", "binding"):
        store.reset(CUSTOM, key)


def show(store):
    bound = THEME_PATH in store.get(MEDIA, "custom-keybindings")
    command = store.get(CUSTOM, "command") if bound else ""
    switch = store.get(MUTTER, "switch-monitor")
    if bound and not any(is_f7(a) for a in switch):
        return f"F7 (Super+P): {command}"
    if any(is_f7(a) for a in switch):
        return "F7 (Super+P): GNOME's display switcher"
    return "F7 (Super+P): unbound"


def main(argv, store=None):
    if not argv or argv[0] not in ("apply", "remove", "show") \
            or (argv[0] == "apply") != (len(argv) == 2) or len(argv) > 2:
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        return 64
    store = store or GSettings()
    try:
        if argv[0] == "apply":
            changed = apply(store, argv[1])
            print("hotkeys: F7 toggles dark mode" + ("" if changed else " (up to date)"))
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
