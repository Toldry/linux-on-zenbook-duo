### The keyboard light starts off on Bluetooth and dims over time

- detached, the keyboard's light now starts off every time the keyboard
  connects; F4 turns it on for that connection, and docked the light comes
  back at your usual level as before
- a lit light on Bluetooth steps one level dimmer every 10 minutes until it
  is off, and F4 starts the count again
- measured on the keyboard: the light uses about 29 % of its battery an hour,
  against about 1 % an hour without it
- `KB_BACKLIGHT_BT_DARK` (1) and `KB_BACKLIGHT_DIM_MINUTES` (10, 0 = never)
  turn either part off, also from `duo gui`
