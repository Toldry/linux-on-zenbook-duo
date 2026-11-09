### The Power Mode can follow the charger, and the laptop can power off on battery

- `sudo ./install.sh --system --power-follows-ac` sets GNOME's Power Mode to
  Performance when the charger is plugged in and to Power Saver when it is
  pulled, as Windows keeps one power mode for each. A mode picked by hand
  lasts until the next plug or unplug. The two profiles are in
  `/etc/zenduo/power.conf`
- `--battery-shutdown MIN` also powers the laptop off after MIN minutes on
  battery, waking it from suspend to do so, with a notification five minutes
  before. Plugging in cancels it. `sudo zenduo-power --status` shows what is
  scheduled. Both are opt in and kept across re-runs until
  `--no-power-follows-ac`. Tested with a fake charger only, not yet run on the
  laptop
