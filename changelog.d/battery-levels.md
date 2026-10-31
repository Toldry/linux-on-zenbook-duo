### Overview shows the laptop and keyboard battery, and warns when the keyboard runs low

- `duo gui` and `duo` show the laptop battery and whether it is charging,
  from `BAT0` in sysfs
- they also show the detachable keyboard's battery and charging state, from
  a kernel battery node or UPower if either has one, otherwise from the
  keyboard's own `5a 3d` status report. That report's meaning is a guess
  (it matched Windows at 99 %), so the row says "unconfirmed". Not yet run
  on the laptop. "unknown" while the keyboard is not connected
- `watch-fn` pops up a warning as the keyboard battery drops to 20, 10, 5
  and 1 %, once per level, and not while the kernel or UPower says it is
  charging
