### Fn+F's fan mode no longer moves GNOME's Power Mode

- the fan mode (Standard, Whisper, Performance) and GNOME's Power Mode are
  now two separate settings, as MyASUS's fan mode and Windows' power mode
  are: the system install starts power-profiles-daemon with
  `--block-driver=platform_profile`, so Power Mode only sets the CPU's
  energy preference and the fan mode is Fn+F's alone.
  `--no-separate-fan` links them again. Seen linked on the laptop
  (Ubuntu 26.04.1, daemon 0.30); the separation is not yet run on it
