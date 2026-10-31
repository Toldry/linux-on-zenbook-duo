### Super+P is the display switcher again; only F7 opens a terminal

- F7 types Super+P, so binding F7 to a terminal also took Super+P away from
  GNOME's display switcher. `watch-fn` now tells them apart (F7 sends Super
  and P in one keyboard report; a hand sends Super first): F7 opens the
  terminal, a Super+P typed by hand raises GNOME's switcher, through the
  Zenbook Duo GNOME Shell extension
- to get it on an existing install: `./install.sh` again, or
  `duo hotkeys apply`, `duo swap-screens --enable` and
  `systemctl --user restart duo-watch-fn`, then log out and back in
- written from the 2026-10-30 capture, not yet run on the laptop; over
  Bluetooth F7 may still raise the switcher until that is checked
