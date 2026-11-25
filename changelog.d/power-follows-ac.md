### The Power Mode can follow the charger, and a laptop left asleep on battery powers off

- `sudo ./install.sh --system --power-follows-ac` sets GNOME's Power Mode to
  Performance when the charger is plugged in and to Power Saver when it is
  pulled, as Windows keeps one power mode for each. A mode picked by hand
  lasts until the next plug or unplug. The two profiles are in
  `/etc/zenduo/power.conf`
- `--battery-shutdown MIN` also powers the laptop off once it has been asleep
  on battery for MIN minutes: it wakes itself to do it, so a forgotten laptop
  does not drain flat. Waking it first cancels this, so it never interrupts
  work, and the count starts again at the next sleep. A charger plugged in
  meanwhile keeps it on. When it goes to sleep is still GNOME's Automatic
  Suspend. Hibernate would be the better end, but Ubuntu refuses it under
  Secure Boot. `sudo zenduo-power --status` shows what is set. Both are opt
  in and kept across re-runs until `--no-power-follows-ac`. Tested with a fake
  charger only, not yet run on the laptop
- the window's Device Settings → Power & Performance has both: a Power Mode
  card (on the charger, on battery) and the Sleep Assistant card, which
  MyASUS uses for the same job. `duo power` and `duo power set AC BATTERY
  MINUTES` do the same from a terminal, through one new root-helper verb
  that only rewrites the three values in `/etc/zenduo/power.conf`
- the window's list of system features now says whether each is installed;
  it showed nothing for them before
