#!/usr/bin/env python3
"""Open the default terminal: what F7 runs (through `duo terminal`).

xdg-terminal-exec is the freedesktop launcher that honours the user's chosen
terminal; the rest are GNOME's own, newest first, for systems without it.

Usage:
  terminal.py          open a terminal window
Exit: 0 opened · 1 no terminal found (or it could not start) · 64 usage.
"""

import os
import shutil
import sys

TERMINALS = (["xdg-terminal-exec"], ["ptyxis", "--new-window"], ["kgx"],
             ["gnome-terminal"])


def terminal_argv(which=None):
    which = which or shutil.which
    for argv in TERMINALS:
        if which(argv[0]):
            return argv
    return None


def main(argv):
    if argv:
        print(__doc__.split("Usage:")[1].rstrip(), file=sys.stderr)
        return 64
    found = terminal_argv()
    if found is None:
        print("terminal: none found (looked for "
              + ", ".join(t[0] for t in TERMINALS) + ")", file=sys.stderr)
        return 1
    try:
        os.execvp(found[0], found)
    except OSError as e:
        print(f"terminal: cannot run {found[0]}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
