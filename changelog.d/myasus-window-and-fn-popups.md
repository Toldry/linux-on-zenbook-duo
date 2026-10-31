### The window looks and is arranged like MyASUS

- `duo gui` (F12) has MyASUS's sidebar: Home, Device Settings, System
  Diagnosis, System Update and Settings, plus Screens for what ScreenXpert
  does. Device Settings has its three tabs, Power & Performance, Audio &
  Visual and Input Device Settings, with each setting as a card
- what MyASUS has and this project does not (Quick setting, Instant
  Full-Charge, Sleep Assistant, Memory Allocated to GPU, noise cancelling,
  Splendid, OLED Care, the ambient-light keyboard backlight, firmware
  updates, ASUS's own cloud pages) is shown greyed with the reason
- old `duo gui --page` names still work; `--page device/input` picks a tab
- run under a virtual display, not yet on the laptop

### Popups for Fn+Esc and Fn+F, and Fn+F sets only the fan mode

- Fn+Esc and Fn+F show GNOME's own popup ("Fn lock on", "Fan mode:
  Whisper"), as MyASUS shows one. The GNOME Shell extension that swaps the
  screens draws it and is renamed `duo@zenduo.github.io`; the installer
  replaces the old one
- Fn+F now sets only the BIOS fan mode (`duo fan-profile`, through the root
  helper's new `thermal` verb), as MyASUS does, instead of a
  power-profiles-daemon profile, which also changed the CPU's energy
  preference. Run `sudo ./install.sh --system` once for the helper
- Fan Profile and Function key are radio lists on Device Settings, as in
  MyASUS
- not yet run on the laptop
