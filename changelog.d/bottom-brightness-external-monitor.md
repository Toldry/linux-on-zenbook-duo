### Fixes

- the brightness keys move the bottom screen again after it comes on with an
  external monitor connected. GNOME could tie the bottom screen's slider to
  the top screen's backlight while the panel was still switching on, so the
  sliders moved and the bottom screen did not. Whenever the bottom screen
  comes on, `watch-displays` now checks which backlight GNOME drives and, if
  it is the wrong one, re-applies the layout as it is so GNOME looks again.
  `duo brightness check` does the same by hand. Re-applying the layout fixed
  it on the laptop (2026-11-16); the automatic check is not yet run there
