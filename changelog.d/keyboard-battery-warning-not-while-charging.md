### The keyboard low-battery warning stays quiet while it charges

- no popup while the keyboard sits on the pogo pins, which charge it
- no popup while the keyboard's own status report says it is charging,
  as long as the level is not falling: that report's charging flag is
  still a guess, so a level that drops warns anyway
