### Fixes

- F7 opens the terminal again. The check that told it from a typed Super+P
  relied on F7 sending both keys in one report, which a timed capture showed
  it does not: it sends the same reports a hand does, only faster. watch-fn
  now calls a P that follows Super within 30 ms F7 (measured on Bluetooth:
  under 9 ms for F7, 67 ms or more by hand); restart it with
  `systemctl --user restart duo-watch-fn` after updating
