#!/usr/bin/env python3
"""Swap the windows between the two panels, as F8 does under Windows.

Under Wayland no outside program may move another's window, so the move is
done inside GNOME Shell by the extension in gnome-shell/ (one D-Bus method,
nothing on screen) and this only calls it. That F8 swaps the windows under
Windows was reported by the owner on 2026-10-31; what exactly moves there
(minimised windows, dialogs) is not measured.

Usage:
  swap_screens.py            swap; prints how many windows moved
  swap_screens.py --enable   copy the extension into ~/.local/share and turn it on
Exit: 0 done · 1 the extension is not running or gdbus failed · 64 usage.
"""

import ast
import os
import shutil
import subprocess
import sys

UUID = "swap-screens@zenduo.github.io"
OBJECT_PATH = "/io/github/zenduo/SwapScreens"
METHOD = "io.github.zenduo.SwapScreens.Swap"
SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "gnome-shell", UUID)

NOT_RUNNING = ("swap-screens: the GNOME Shell extension is not running. Run "
               "`duo swap-screens --enable`, then log out and back in "
               "(GNOME loads a new extension only at login)")


def run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


def parse_moved(reply):
    """gdbus prints "(uint32 3,)"; the number, or None if it is not that."""
    text = reply.strip().strip("(),")
    if text.startswith("uint32 "):
        text = text[len("uint32 "):]
    return int(text) if text.isdigit() else None


def swap(runner=run):
    rc, out = runner(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                      "--object-path", OBJECT_PATH, "--method", METHOD])
    if rc != 0:
        unknown = "UnknownMethod" in out or "UnknownObject" in out or "No such interface" in out
        print(NOT_RUNNING if unknown else f"swap-screens: gdbus failed: {out.strip()}",
              file=sys.stderr)
        return 1
    moved = parse_moved(out)
    if moved == 0:
        # Docked, the bottom panel is off: there is no second panel to swap with.
        print("swap-screens: nothing to swap (is the bottom panel on?)")
    else:
        print(f"swap-screens: moved {moved if moved is not None else '?'} windows")
    return 0


def with_uuid(gsettings_value, uuid=UUID):
    """The enabled-extensions value with `uuid` added, or None if it is there.

    gsettings prints a GVariant: "['a@b', 'c@d']", or "@as []" when empty.
    """
    text = gsettings_value.strip()
    if text.startswith("@as "):
        text = text[len("@as "):]
    current = ast.literal_eval(text) if text else []
    if uuid in current:
        return None
    return repr(current + [uuid])


def install_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "gnome-shell", "extensions", UUID)


def enable(runner=run, source=SOURCE):
    target = install_dir()
    if os.path.islink(target):
        print(f"swap-screens: {target} is a link (home-manager): left as it is")
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        print(f"swap-screens: extension copied to {target}")
    # `gnome-extensions enable` refuses an extension the running Shell has
    # not loaded yet, which a freshly copied one never is under Wayland, so
    # the list is written directly and the Shell picks it up at next login.
    rc, out = runner(["gsettings", "get", "org.gnome.shell", "enabled-extensions"])
    if rc != 0:
        print(f"swap-screens: cannot read enabled-extensions: {out.strip()}", file=sys.stderr)
        return 1
    value = with_uuid(out)
    if value is None:
        print("swap-screens: already enabled")
        return 0
    rc, out = runner(["gsettings", "set", "org.gnome.shell", "enabled-extensions", value])
    if rc != 0:
        print(f"swap-screens: cannot enable: {out.strip()}", file=sys.stderr)
        return 1
    print("swap-screens: enabled; it starts at the next login")
    return 0


def main(argv):
    if not argv:
        return swap()
    if argv == ["--enable"]:
        return enable()
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
