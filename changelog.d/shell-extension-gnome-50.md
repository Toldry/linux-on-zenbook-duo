### Fixes

- the Zenbook Duo GNOME Shell extension now loads on GNOME 50 (Ubuntu
  26.04). GNOME refused it because its list of supported versions stopped
  at 49, so F8's window swap, the Fn-key popups and the Super+P display
  switcher did nothing there. Run `duo swap-screens --enable` (or
  `./install.sh`), then log out and back in
