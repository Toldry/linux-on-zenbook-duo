# Windows parity

What the machine does under Windows, whether it does it here, and how.
✅ verified on hardware · 🧪 implemented, not yet verified or partial ·
📝 planned (with the plan phase) · ✖ out of scope.

## Displays

| Windows | Here | Status |
|---|---|---|
| Bottom screen off when the keyboard is docked, on when lifted | `duo watch-displays` — converges the layout on keyboard poll, Mutter `MonitorsChanged` and logind resume | ✅ |
| Win+P: laptop only, external only, duplicate, extend | GNOME's own Super+P, or `duo layout laptop\|external\|mirror\|extend\|cycle`; the daemon governs the bottom panel only, so either survives | ✅ |
| The layout comes back per monitor set: same screens, positions, scales and primary as last time those monitors were connected | Recorded into GNOME's own `~/.config/monitors.xml` by `duo watch-displays`, restored by Mutter before anything is drawn (`REMEMBER_LAYOUT=1`) | 🧪 2026-09-12 |
| The lock screen appears on the monitor you were using | Same memory: Mutter restores the layout before the shield is drawn | 🧪 2026-09-12 |
| The sign-in screen appears there too | `duo layout login` (automatic after a change) installs the same layouts for GDM, via the root helper | 🧪 2026-09-12 |
| Second-screen key toggles the bottom panel | `5a 6a` → `duo toggle`; ignored while docked (the panel is under the keyboard) | ✅ |
| Brightness keys drive both panels | GNOME's own StepUp/StepDown (with OSD) then the bottom panel is synced. GNOME 49+ has no StepUp/StepDown, so watch-fn presses a virtual brightness key and GNOME handles it natively (slider and OSD); `duo brightness` through Mutter is the fallback without /dev/uinput | ✅ |
| Auto-rotate: tent, book, portrait | `duo watch-rotation` logs orientation | 🧪 → phase O |
| Touch and pen land on the right panel | `watch-displays` pins each digitizer at start; `duo set-tablet-mapping` by hand (4-value GNOME setting, connector included) | 🧪 written 2026-09-27, not yet applied → phase P |
| No OLED flicker | `i915.enable_psr=0` | ✅ |
| ScreenXpert app launcher / window-throw between panels | — | 📝 phase U |
| Six-finger virtual keyboard gesture | GNOME's on-screen keyboard appears when no keyboard is present | ✖ (phase U covers the useful part) |

## Keyboard

| Windows | Here | Status |
|---|---|---|
| Typing over USB (docked) and Bluetooth (detached) | kernel | ✅ |
| Fn-row: brightness down/up | `watch-fn` | ✅ USB + BT |
| Fn-row: keyboard backlight cycle | `watch-fn` → `duo kb-backlight` | ✅ |
| Fn-row: volume, mute | native consumer-page usages | ✅ |
| Fn-row: Fn-lock (Fn+Esc) | `watch-fn` → `duo fn-lock toggle` sends `5a d0 4e <1/0>` as MyASUS does, with a "Fn lock on/off" popup; remembered and re-sent after re-init | 🧪 2026-10-31, from a Windows capture; not yet run on Linux |
| Fn-row: mic mute (F9), emoji (F11) | `watch-fn` → `wpctl set-mute … toggle` then the key's LED (`duo mic-led`, `5a d0 7c`), `ibus emoji` | keycaps ✅ 2026-10-31 (Windows capture); LED 🧪 not yet run on Linux |
| Fn-row: F7 (types Super+P) | opens a terminal: `duo hotkeys apply` moves Super+P from GNOME's switcher to `duo super-p`, which opens the terminal when watch-fn saw F7's one-report Super+P and GNOME's switcher for a typed one (`F7_TERMINAL`) | 🧪 2026-10-30; telling F7 from a typed Super+P is written 2026-10-31, not yet run on the laptop, and on Bluetooth **VERIFY-ON-HW** |
| Fn-row: F8 (`5a 9c`): Windows swaps the windows between the panels | `F8_DARK_MODE=0`: `duo swap-screens` asks the `duo@zenduo.github.io` GNOME Shell extension to move every window to the other panel; the default, `1`, toggles dark mode instead (Extras tab) | 🧪 2026-10-31, Windows behaviour reported by the owner; not yet run on GNOME |
| Fn-row: F12, MyASUS (`5a 86`) | `watch-fn` opens and closes the duo window, `duo gui --toggle` (`F12_GUI`) | 🧪 2026-10-30; code ✅ 2026-10-31 (Windows capture) |
| Fn+F: fan profile (`5a 9d`) with MyASUS's popup | `watch-fn` → `duo fan-profile next`: `platform_profile` (asus-wmi's name for the BIOS fan mode) through the root helper, Standard → Whisper → Performance, then a GNOME popup. Not power-profiles-daemon, whose profiles also move the CPU's energy preference | 🧪 2026-10-31, from a Windows capture; mapping to the BIOS modes and whether power-profiles-daemon follows VERIFY-ON-HW |
| Fn-row: camera | not attributed yet (`5a 3d` is a status report, not a key) | 📝 phase Q |
| Keyboard backlight level remembered across dock/undock/sleep | state file + restore on re-init | ✅ |
| Wi-Fi keeps working when the keyboard comes off | kernel ≥ 6.11 | ✅ |
| Palm rejection while typing | libinput quirk + disable-while-typing | ✅ |
| Bluetooth pairing | F10 long-press recipe (documented; nothing to automate) | ✅ |

## Audio

| Windows | Here | Status |
|---|---|---|
| Speaker voicing (harman/kardon APO) | `duo speaker-dsp`: EasyEffects chain | ✅ Nix EE 8; 🧪 apt EE 7 (preset ships, autoload manual) → phase S |
| Amps always initialised | reporter + power-off recovery; root cause is Windows Fast Startup | ✅ (as good as it can be from Linux) |
| Bluetooth headset stays in stereo during a call (Windows keeps "Headset" and "Headphones" apart) | WirePlumber policy file, on by default (`zenduo.bluetoothStereo`) | ✅ measured 2026-09-20 |
| The audio server keeps realtime priority | drop-in that waits for rtkit (`zenduo.audioRealtime`) | ✅ measured 2026-09-20 |
| Microphone processing | — | 📝 phase S |

## Power

| Windows | Here | Status |
|---|---|---|
| Battery charge limit (MyASUS) | `duo bat-limit`, re-applied at login | ✅ |
| Performance / balanced / quiet profiles | `platform_profile` exists; no `duo` verb yet | 📝 phase T |
| Standby drain comparable to Windows | s2idle; unmeasured | 📝 phase T |
| Keyboard battery level (Windows Settings > Bluetooth) | Overview row in `duo` / `duo gui`; `watch-fn` pops up a warning at 20, 10, 5 and 1 % | 🧪 source is the `5a 3d` report unless the kernel or UPower has one: **VERIFY-ON-HW** |
| Keyboard backlight goes dark when idle on Bluetooth, so the keyboard battery lasts days | `watch-fn` idle-off (`KB_BACKLIGHT_IDLE_OFF`, 30 s) | ✅ goes dark, measured 2026-10-30; battery life unmeasured |
| Hibernate | — | ✖ |

## Everything else

| Windows | Here | Status |
|---|---|---|
| Windows Hello (IR face) | — | ✖ (Howdy unreliable; revisit after 1.0) |
| NPU | — | ✖ |
| BIOS updates | MyASUS or EZ-Flash from USB; never from Linux | ✖ by design |
| A settings app | `duo`: the screen, with every feature, knob, check and log (`duo-cli features` / `duo-cli config` underneath) | 🧪 2026-09-17 |
| A settings window | `duo gui` (F12, or Zenbook Duo in the app grid): GTK 4 + libadwaita over the same model as the screen; laid out like MyASUS (sidebar, Device Settings' three tabs, a card per setting), with what is not done here shown greyed and why; what Windows does not do (F7 terminal, F8 dark mode) sits on Settings → Extras | 🧪 2026-10-30, run under a virtual display, not yet on hardware |
