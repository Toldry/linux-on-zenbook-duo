#!/usr/bin/env python3
"""Light or dark style for the GNOME session, the way Settings → Appearance
switches it.

Two settings move together. `color-scheme` is what libadwaita/GTK4 apps and
gnome-shell follow. Ubuntu's Appearance panel also swaps the legacy
`gtk-theme` between Yaru-<accent> and Yaru-<accent>-dark, because GTK3 apps
ignore `color-scheme` (read from gnome-control-center's Ubuntu patch
"ubuntu-appearance", VERIFY-ON-HW on GNOME 50). A theme that is not Yaru is
the user's and is left alone.

"Light" is written as `default`, not `prefer-light`: `default` is what GNOME's
own quick-settings toggle writes, and apps that predate `prefer-light` read it
as light.

Usage:
  theme.py [show]      print light or dark
  theme.py dark|light  set it
  theme.py toggle      the other one (what F7 is bound to)
Exit: 0 done · 1 gsettings failed · 64 usage.
"""

import subprocess
import sys

IFACE = "org.gnome.desktop.interface"
DARK_SUFFIX = "-dark"


def run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


class ThemeError(Exception):
    pass


class GSettings:
    def __init__(self, runner=run):
        self.runner = runner

    def get(self, key):
        rc, out = self.runner(["gsettings", "get", IFACE, key])
        if rc != 0:
            raise ThemeError(f"gsettings get {IFACE} {key} failed: {out.strip()}")
        return out.strip().strip("'")

    def set(self, key, value):
        rc, out = self.runner(["gsettings", "set", IFACE, key, value])
        if rc != 0:
            raise ThemeError(f"gsettings set {IFACE} {key} failed: {out.strip()}")


def current(store):
    return "dark" if store.get("color-scheme") == "prefer-dark" else "light"


def yaru_variant(gtk_theme, style):
    """The Yaru theme name for `style`, or None when the theme is not Yaru."""
    if not gtk_theme.startswith("Yaru"):
        return None
    base = gtk_theme[:-len(DARK_SUFFIX)] if gtk_theme.endswith(DARK_SUFFIX) else gtk_theme
    return base + DARK_SUFFIX if style == "dark" else base


def apply(store, style):
    store.set("color-scheme", "prefer-dark" if style == "dark" else "default")
    gtk_theme = store.get("gtk-theme")
    wanted = yaru_variant(gtk_theme, style)
    if wanted is not None and wanted != gtk_theme:
        store.set("gtk-theme", wanted)


def main(argv, store=None):
    verb = argv[0] if argv else "show"
    if verb not in ("show", "dark", "light", "toggle") or len(argv) > 1:
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        return 64
    store = store or GSettings()
    try:
        style = current(store)
        if verb == "show":
            print(style)
            return 0
        if verb == "toggle":
            verb = "light" if style == "dark" else "dark"
        apply(store, verb)
    except ThemeError as e:
        print(f"theme: {e}", file=sys.stderr)
        return 1
    print(verb)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
