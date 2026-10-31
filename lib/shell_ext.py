#!/usr/bin/env python3
"""Calls into the GNOME Shell extension in gnome-shell/ (duo@zenduo.github.io).

Under Wayland only GNOME Shell may move another program's window, and since
GNOME 41 only the Shell itself may show its volume-style popup, so both live
in a small extension that offers them over D-Bus and this calls it.

  swap   swap the windows between the two panels, what F8 does under Windows
         (reported by the owner, 2026-10-31; what exactly moves there, such
         as minimised windows, is not measured)
  osd    the popup MyASUS shows for Fn+Esc and Fn+F; without the extension
         it falls back to a short notification

Usage:
  shell_ext.py swap               prints how many windows moved
  shell_ext.py osd ICON LABEL     ICON is a themed icon name
  shell_ext.py --enable           copy the extension into ~/.local/share and turn it on
Exit: 0 done · 1 the extension is not running or gdbus failed · 64 usage.
"""

import ast
import os
import shutil
import subprocess
import sys

UUID = "duo@zenduo.github.io"
OLD_UUIDS = ("swap-screens@zenduo.github.io",)   # its name before the popups
OBJECT_PATH = "/io/github/zenduo/Shell"
INTERFACE = "io.github.zenduo.Shell"
SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "gnome-shell", UUID)

NOT_RUNNING = ("the Zenbook Duo GNOME Shell extension is not running. Run "
               "`duo swap-screens --enable`, then log out and back in "
               "(GNOME loads a new extension only at login)")


def run(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


def call(method, args=(), runner=run):
    """(rc, output) of one method on the extension's object."""
    return runner(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                   "--object-path", OBJECT_PATH, "--method", f"{INTERFACE}.{method}",
                   *args])


def not_running(output):
    return any(s in output for s in ("UnknownMethod", "UnknownObject", "No such interface"))


def parse_moved(reply):
    """gdbus prints "(uint32 3,)"; the number, or None if it is not that."""
    text = reply.strip().strip("(),")
    if text.startswith("uint32 "):
        text = text[len("uint32 "):]
    return int(text) if text.isdigit() else None


def swap(runner=run):
    rc, out = call("SwapScreens", runner=runner)
    if rc != 0:
        print("swap-screens: " + (NOT_RUNNING if not_running(out) else
                                  f"gdbus failed: {out.strip()}"), file=sys.stderr)
        return 1
    moved = parse_moved(out)
    if moved == 0:
        # Docked, the bottom panel is off: there is no second panel to swap with.
        print("swap-screens: nothing to swap (is the bottom panel on?)")
    else:
        print(f"swap-screens: moved {moved if moved is not None else '?'} windows")
    return 0


def osd(icon, label, runner=run):
    rc, out = call("ShowOsd", [icon, label], runner=runner)
    if rc == 0:
        return 0
    # A popup is a courtesy: without the extension say it as a notification
    # that goes away by itself, and never fail the key press over it.
    rc, _ = runner(["notify-send", "--transient", "--app-name=Zenbook Duo",
                    f"--icon={icon}", label])
    if rc != 0:
        print(f"osd: {label} (no popup: {out.strip().splitlines()[-1] if out.strip() else '?'})")
    return 0


def with_uuid(gsettings_value, uuid=UUID, drop=OLD_UUIDS):
    """The enabled-extensions value with `uuid` in and `drop` out, or None if
    that is what it already is.

    gsettings prints a GVariant: "['a@b', 'c@d']", or "@as []" when empty.
    """
    text = gsettings_value.strip()
    if text.startswith("@as "):
        text = text[len("@as "):]
    current = ast.literal_eval(text) if text else []
    wanted = [u for u in current if u not in drop]
    if uuid not in wanted:
        wanted.append(uuid)
    return None if wanted == current else repr(wanted)


def extensions_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "gnome-shell", "extensions")


def enable(runner=run, source=SOURCE):
    target = os.path.join(extensions_dir(), UUID)
    if os.path.islink(target):
        print(f"shell extension: {target} is a link (home-manager): left as it is")
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        print(f"shell extension: copied to {target}")
    for old in OLD_UUIDS:
        stale = os.path.join(extensions_dir(), old)
        if os.path.isdir(stale) and not os.path.islink(stale):
            shutil.rmtree(stale)
            print(f"shell extension: removed the old {stale}")
    # `gnome-extensions enable` refuses an extension the running Shell has
    # not loaded yet, which a freshly copied one never is under Wayland, so
    # the list is written directly and the Shell picks it up at next login.
    rc, out = runner(["gsettings", "get", "org.gnome.shell", "enabled-extensions"])
    if rc != 0:
        print(f"shell extension: cannot read enabled-extensions: {out.strip()}", file=sys.stderr)
        return 1
    value = with_uuid(out)
    if value is None:
        print("shell extension: already enabled")
        return 0
    rc, out = runner(["gsettings", "set", "org.gnome.shell", "enabled-extensions", value])
    if rc != 0:
        print(f"shell extension: cannot enable: {out.strip()}", file=sys.stderr)
        return 1
    print("shell extension: enabled; it starts at the next login")
    return 0


def main(argv):
    if argv in ([], ["swap"]):
        return swap()
    if argv == ["--enable"]:
        return enable()
    if len(argv) == 3 and argv[0] == "osd":
        return osd(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
