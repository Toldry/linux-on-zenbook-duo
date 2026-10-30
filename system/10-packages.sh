#!/usr/bin/env bash
# 10-packages.sh — runtime dependencies for the duo tooling:
#   usbutils          lsusb, used by humans debugging attach/detach
#   inotify-tools     inotifywait for event-driven waits (watch-backlight)
#   iio-sensor-proxy  monitor-sensor / D-Bus accelerometer for rotation
#   python3 + gi      GObject introspection — the Mutter DisplayConfig D-Bus client
#   GTK 4, libadwaita the typelibs `duo gui` (the window F12 opens) is drawn with
#   evtest            watch decoded evdev key events (Fn/media-key mapping)
#   pciutils          lspci, for `duo doctor`
# No pyusb: the keyboard-backlight fallback talks to /dev/hidraw* directly
# (lib/kb_backlight.py), which never detaches the kernel driver.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./lib.sh

require_root

case "$(pkg_manager)" in
  apt)
    apt_update
    ensure_pkg usbutils inotify-tools iio-sensor-proxy python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 evtest pciutils
    ;;
  dnf)
    ensure_pkg usbutils inotify-tools iio-sensor-proxy python3 python3-gobject gtk4 libadwaita evtest pciutils
    ;;
  pacman)
    ensure_pkg usbutils inotify-tools iio-sensor-proxy python python-gobject gtk4 libadwaita evtest pciutils
    ;;
  *)
    warn "unknown distro — make sure these are installed: usbutils inotify-tools iio-sensor-proxy python3 python3-gi (PyGObject) GTK 4 + libadwaita typelibs evtest pciutils"
    ;;
esac

# The one dependency that cannot be papered over: without PyGObject there is no
# display control at all. Check the SYSTEM interpreter, which is the one `duo`
# pins for its gi-using scripts (a Nix/pyenv python on PATH is not it).
PYGI="${DUO_PYGI:-/usr/bin/python3}"
if [ -x "$PYGI" ] && "$PYGI" -c 'import gi' >/dev/null 2>&1; then
  log "PyGObject available to $PYGI"
else
  warn "PyGObject (python3-gi) is not importable by $PYGI — display commands will not work until it is"
fi
# The window is optional (the terminal screen has everything), so a missing
# typelib is a warning, not a failure.
if [ -x "$PYGI" ] && "$PYGI" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' >/dev/null 2>&1; then
  log "GTK 4 + libadwaita available to $PYGI (duo gui)"
else
  warn "GTK 4 / libadwaita typelibs not importable by $PYGI — duo gui (F12) will not open; duo in a terminal still works"
fi
