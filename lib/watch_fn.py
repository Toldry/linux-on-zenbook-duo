#!/usr/bin/env python3
"""watch-fn — make the Zenbook Duo keyboard's media keys actually do things.

Ground truth (docs/install/INSTALL-LOG.md, Round 10): mainline hid-asus has no entry for this
keyboard, so nothing in the kernel initializes it or handles its vendor codes.
After `kb-init` sends the ASUS handshake + OOBE-disable, the keyboard's media
layer switches on and the F-row emits ASUS vendor reports (report id 0x5a):
brightness-down = 5a 10, brightness-up = 5a 20, Fn-lock = 5a 4e, the dedicated
second-screen key = 5a 6a. Volume/mute arrive as standard consumer-page usages
that Linux already handles natively — no work needed there.

This daemon closes the loop:
  - watches for the keyboard's hidraw nodes (USB or Bluetooth; they come and go
    with docking/pairing) at 1 Hz, re-sending the init whenever the node set
    changes — the keyboard forgets hotkey mode on re-enumeration
  - reads the 0x5a vendor reports and dispatches actions:
      brightness     -> GNOME's own StepUp/StepDown D-Bus (OSD included)
      second-screen  -> `duo toggle` (bottom panel on/off)
      kbd-backlight  -> `duo kb-backlight` cycle 0..3
      mic-mute       -> `wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle`, then the
                        key's LED set to match (`duo mic-led`)
      fn-lock        -> `duo fn-lock toggle`: the F-row swap is host state
      fan-profile    -> `duo fan-profile next`: the BIOS fan mode, in MyASUS's
                        order, with its popup (Fn+F)
      emoji          -> `ibus emoji` (the picker GNOME's Ctrl+; opens)
      split-screen   -> `duo theme toggle` (F8_DARK_MODE=1), else `duo swap-screens`
                        as Windows does (bare F8)
      myasus         -> `duo gui --toggle`, the duo window (bare F12)
  - marks F7, which arrives as an ordinary Super+P typed in one report, so
    the Super+P shortcut (`duo super-p`) opens the terminal for F7 and GNOME's
    display switcher for a Super+P typed by hand (lib/hotkeys.py)
  - honors ~/.config/zenduo/fn-map.json (from `duo fn-map`) as overrides, so
    newly-captured codes map onto actions without touching this file
  - on Bluetooth, darkens the keyboard backlight after KB_BACKLIGHT_IDLE_OFF
    seconds without a key and brings it back on the next one (IdleOff)

Run via the duo-watch-fn systemd user service (installed by ./install.sh, or the home-manager module)
or by hand: `duo watch-fn`. Needs the udev uaccess rule for hidraw access.
"""

import json
import os
import select
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_init  # noqa: E402  (same directory; shares node discovery + handshake)
import kb_backlight  # noqa: E402  (same directory; owns the remembered level)
import dock  # noqa: E402  (same directory; keyboard dock state)
import uinput_keys  # noqa: E402  (same directory; virtual brightness keys)
import battery  # noqa: E402  (same directory; where the keyboard's 5a 3d report is kept)
import hotkeys  # noqa: E402  (same directory; tells F7 from a typed Super+P)

DUO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "bin", "duo-cli"))

VENDOR_REPORT_ID = 0x5A

# code (second report byte) -> action. Codes confirmed on hardware are noted;
# the 0xc4/0xc5 pair is hid-asus's kbd-backlight mapping (unconfirmed here —
# run `duo fn-map` and the saved map overrides/extends this table).
DEFAULT_ACTIONS = {
    0x10: "brightness-down",   # confirmed: bare F5 over BT after kb-init
    0x20: "brightness-up",     # confirmed: bare F6
    0x4E: "fn-lock",           # confirmed: Fn+Esc
    0x6A: "second-screen",     # confirmed: dedicated key left of PrtSc
    0xC7: "kbd-backlight",     # confirmed: bare F4 (one key cycles levels)
    # The two below were seen unmapped in this machine's journal (2026-09-05:
    # 22 and 20 presses) and are the codes mainline hid-asus maps to
    # KEY_MICMUTE and KEY_EMOJI_PICKER (drivers/hid/hid-asus.c,
    # asus_input_mapping). VERIFY-ON-HW which keycap sends which — the legend
    # says Fn+F9 and Fn+F11.
    0x7C: "mic-mute",
    0x7E: "emoji",
    # Measured 2026-10-30 with `duo fn-probe`: three bare F8 presses sent
    # `5a 9c` then `5a 00` each, and nothing else. Bare F7 sends no vendor
    # report at all, only a typed Super+P in one report, which ChordWatch
    # tells from a hand-typed one (lib/hotkeys.py).
    0x9C: "split-screen",
    # Bare F12 carries the MyASUS legend. 0x86 is the code mainline hid-asus
    # maps to KEY_PROG1 with the comment "MyASUS key" (drivers/hid/hid-asus.c,
    # asus_input_mapping, read 2026-10-30). VERIFY-ON-HW that this keyboard
    # sends it: `duo fn-probe`, or `duo log` names any code with no action.
    0x86: "myasus",
    # Measured under Windows 2026-10-31 (docs/HARDWARE.md, "What MyASUS
    # does"): Fn+F sends 5a 9d and MyASUS steps the fan profile on it.
    0x9D: "fan-profile",
}

# Actions from a fn-map.json we know how to perform (everything else logged).
# The actions dispatch() acts on.
HANDLED = {"brightness-down", "brightness-up", "kbd-backlight", "second-screen",
           "mic-mute", "emoji", "fn-lock", "split-screen", "myasus", "fan-profile"}


KNOWN_ACTIONS = {"brightness-down", "brightness-up", "fn-lock", "second-screen",
                 "kbd-backlight", "mute", "volume-down", "volume-up",
                 "mic-mute", "display-switch", "split-screen", "camera-toggle",
                 "emoji", "myasus", "fan-profile"}


def log(msg):
    print(f"watch-fn: {msg}", flush=True)


def load_overrides():
    """fn-map.json entries (action -> report hex) folded into code -> action."""
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(cfg_home, "zenduo", "fn-map.json")
    table = dict(DEFAULT_ACTIONS)
    try:
        with open(path) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return table
    except (OSError, ValueError) as e:
        log(f"ignoring unreadable {path}: {e}")
        return table
    for action, info in doc.get("keys", {}).items():
        report = info.get("report", "")
        parts = report.split()
        if len(parts) >= 2 and parts[0] == "5a":
            try:
                table[int(parts[1], 16)] = action
            except ValueError:
                pass
    return table


# Codes with no action here are logged once per run. 0x3d arrived 41 times
# on 2026-09-27, docked and on Bluetooth, often every 30 s or so and right
# after the keyboard connected: a status report from the keyboard, not a key
# press (2026-10-30, fn-probe: `5a 3d 05 09`, `5a 3d 06 09` with no key
# touched — the third byte changes, so it carries a value). Two lines per
# arrival filled the screen's "Recent problems" with nothing else.
class Dispatcher:
    def __init__(self):
        self.children = []  # (Popen, argv), reaped by reap()
        self.unhandled = {}  # action -> times seen this run
        self.keys = None  # uinput_keys.VirtualKeys, made on first use
        self.keys_failed = False

    def virtual_keys(self):
        """The virtual brightness keys, or None when /dev/uinput is closed to us."""
        if self.keys is None and not self.keys_failed:
            try:
                self.keys = uinput_keys.VirtualKeys()
            except OSError as e:
                self.keys_failed = True
                log(f"no virtual brightness keys ({uinput_keys.UINPUT_PATH}: "
                    f"{e.strerror}); stepping through Mutter instead, without "
                    f"GNOME's OSD. `sudo ./install.sh --system` installs the "
                    f"udev rule that opens it")
        return self.keys

    def restore_backlight(self):
        """Put the keyboard backlight back to the remembered level.

        Docking, undocking and resuming all blank it in hardware, which is why
        it "went dark" on every transition even though the level was known.
        """
        if os.environ.get("ZENDUO_KB_BACKLIGHT_RESTORE", "1") != "1":
            return  # KB_BACKLIGHT_RESTORE=0 in zenduo.conf
        level = kb_backlight.read_level()
        if level:
            log(f"restoring keyboard backlight to level {level}")
        self.spawn([DUO, "kb-backlight", str(level)])

    def restore_state(self):
        """Re-send what the keyboard forgot on re-enumeration besides the light.

        Fn-lock is re-sent only when it is on (kb_state --restore), and the
        mic-mute LED is set from PipeWire's state, which outlives the keyboard.
        """
        self.spawn([DUO, "fn-lock", "--restore"])
        if self.mic_muted():
            self.spawn([DUO, "mic-led", "on"])

    def mic_muted(self):
        """True when the default source is muted; False when unknown."""
        out = self.output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"])
        return out is not None and "[MUTED]" in out

    def darken_backlight(self):
        # --apply: dark for now, but the remembered level stays the user's.
        self.spawn([DUO, "kb-backlight", "--apply", "0"])

    def wake_backlight(self):
        self.spawn([DUO, "kb-backlight", "--apply", str(kb_backlight.read_level())])

    def spawn(self, argv):
        try:
            # Capture rather than discard: a key that silently does nothing is
            # indistinguishable from a key that never arrived, which is exactly
            # the hole that made "the media keys stopped working" so hard to
            # place. reap() surfaces whatever the command complained about.
            # stdout is inherited: under the unit it is the journal, and `duo`
            # skips its own logger copy when it sees that, so each line of
            # "synced ... -> ..." / "enabled eDP-2" lands exactly once.
            self.children.append((subprocess.Popen(
                argv, stderr=subprocess.PIPE), argv))
        except OSError as e:
            log(f"failed to run {argv[0]}: {e}")

    def reap(self):
        still_running = []
        for proc, argv in self.children:
            if proc.poll() is None:
                still_running.append((proc, argv))
                continue
            if proc.returncode != 0:
                err = (proc.stderr.read() or b"").decode(errors="replace").strip()
                log(f"{' '.join(argv)} failed (rc={proc.returncode})"
                    + (f": {err.splitlines()[-1]}" if err else ""))
            proc.stderr.close()
        self.children = still_running

    def run(self, argv):
        """Run argv to completion; True when it exited 0."""
        try:
            return subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"failed to run {argv[0]}: {e}")
            return False

    def output(self, argv):
        """argv's stdout, or None when it failed or could not run."""
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"failed to run {argv[0]}: {e}")
            return None
        return done.stdout if done.returncode == 0 else None

    def gsd_screen(self, method):
        # GNOME settings-daemon's own brightness path: applies the change AND
        # shows the on-screen display, exactly like a natively-handled key.
        # Blocking, so the follow-up second-panel sync reads the updated level.
        # GNOME 49 dropped this interface (backlight moved into Mutter), so
        # False here is expected there and brightness() falls back.
        return self.run(["gdbus", "call", "--session",
                         "--dest", "org.gnome.SettingsDaemon.Power",
                         "--object-path", "/org/gnome/SettingsDaemon/Power",
                         "--method", f"org.gnome.SettingsDaemon.Power.Screen.{method}"])

    def brightness(self, method):
        if self.gsd_screen(method):
            # GNOME drives only the primary panel; mirror the new level onto the
            # bottom panel so both screens dim together when the keyboard is undocked.
            self.spawn([DUO, "sync-backlight"])
            return
        # GNOME 49+: press a real brightness key and let GNOME Shell step its
        # slider, set both panels and show its OSD (see uinput_keys).
        keys = self.virtual_keys()
        if keys is not None:
            try:
                keys.tap(uinput_keys.KEY_BRIGHTNESSUP if method == "StepUp"
                         else uinput_keys.KEY_BRIGHTNESSDOWN)
                return
            except OSError as e:
                log(f"virtual brightness key failed: {e.strerror}")
        # Last resort: `duo brightness` sets both panels through Mutter. No
        # sync-backlight after it: that writes sysfs behind Mutter's back, and
        # GNOME's slider then sticks at the bottom panel's stale level.
        direction = "up" if method == "StepUp" else "down"
        if not self.run([DUO, "brightness", direction]):
            log(f"brightness {direction} failed: run `duo brightness {direction}` "
                f"to see why")

    def dispatch(self, code, action):
        if action not in HANDLED:
            seen = self.unhandled.get(action, 0)
            self.unhandled[action] = seen + 1
            if not seen:
                log(f"key 5a {code:02x} -> {action}: nothing is bound to it; "
                    f"further ones are not logged until the daemon restarts")
            return
        log(f"key 5a {code:02x} -> {action}")
        if action == "brightness-down":
            self.brightness("StepDown")
        elif action == "brightness-up":
            self.brightness("StepUp")
        elif action == "kbd-backlight":
            # Cycle from what is actually remembered, not from a counter held
            # in this process. The keyboard blanks its backlight every time it
            # re-enumerates, and an in-memory counter kept climbing from the
            # pre-dock value — so after docking at level 2 the first press went
            # to 3 instead of restoring 2.
            nxt = (kb_backlight.read_level() + 1) % (kb_backlight.MAX_LEVEL + 1)
            self.spawn([DUO, "kb-backlight", str(nxt)])
        elif action == "second-screen":
            # Docked, the keyboard is lying on the bottom panel: turning it on
            # would light a screen nobody can see, and (because a hand-issued
            # layout pauses the dock policy) it would stay lit until the next
            # dock or undock. The key is only meaningful once the panel is free.
            if dock.keyboard_docked():
                log("second-screen key ignored — the keyboard is docked over "
                    "the bottom panel")
            else:
                self.spawn([DUO, "toggle"])
        elif action == "mic-mute":
            # PipeWire's own toggle on the default source. No OSD: GNOME draws
            # one only for key events, and these reports never become key
            # events (mainline has no entry for this keyboard, V8). Then the
            # key's LED, from the state PipeWire reports rather than a flip
            # of our own, so a mute set elsewhere is corrected on the next
            # press. MyASUS does the same with 5a d0 7c (HARDWARE.md).
            self.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
            self.spawn([DUO, "mic-led", "on" if self.mic_muted() else "off"])
        elif action == "emoji":
            # IBus ships the picker GNOME's Ctrl+; opens; `ibus emoji` raises
            # it. The unit sees WAYLAND_DISPLAY through the session's imported
            # environment, same as gdbus does for brightness.
            self.spawn(["ibus", "emoji"])
        elif action == "split-screen":
            # Under Windows F8 swaps the windows between the panels (the
            # owner, 2026-10-31); dark mode is this project's own extra.
            if os.environ.get("ZENDUO_F8_DARK_MODE", "1") == "1":
                self.spawn([DUO, "theme", "toggle"])
            else:
                self.spawn([DUO, "swap-screens"])
        elif action == "myasus":
            if os.environ.get("ZENDUO_F12_GUI", "1") != "1":
                return  # F12_GUI=0 in zenduo.conf
            # Its own unit, like the terminal: the window must outlive a
            # restart of this daemon. A second press reaches the open window
            # over D-Bus (duo gui is single-instance) and closes it.
            self.spawn(["systemd-run", "--user", "--collect", "--quiet",
                        "--", DUO, "gui", "--toggle"])
        elif action == "fn-lock":
            # The keyboard only reports the key; the host keeps the state and
            # sends it (5a d0 4e, as MyASUS does: HARDWARE.md).
            self.spawn([DUO, "fn-lock", "toggle"])
        elif action == "fan-profile":
            # The fan mode alone, as MyASUS sets it: not power-profiles-daemon,
            # whose profiles also move the CPU's energy preference.
            self.spawn([DUO, "fan-profile", "next"])


def idle_off_seconds():
    """KB_BACKLIGHT_IDLE_OFF from the environment; 0 turns idle-off off."""
    try:
        return max(0, int(os.environ.get("ZENDUO_KB_BACKLIGHT_IDLE_OFF", "30")))
    except ValueError:
        return 30


class IdleOff:
    """Darken the keyboard backlight while nobody types on a Bluetooth keyboard.

    Nothing else turns it off on Linux: GNOME's idle dimming only reaches
    backlights the kernel exposes as an LED (UPower's KbdBacklight), and this
    one is a vendor HID report only watch-fn sends (kb_backlight.py, V8). And
    restore_backlight() turns it back on after every reconnect and resume. The
    keyboard then drained its battery several times a day, where it lasted
    days under Windows (user report, 2026-10-30: the backlight stayed lit with
    the keyboard detached and idle). ASUS rates it at more than 24 h with the
    backlight off (https://www.asus.com/support/faq/1051888/). Docked the
    keyboard runs on the laptop's power, so this only acts on Bluetooth.
    Measured 2026-10-30: detached and idle, it went dark after 30 s, so the
    keyboard sends no unprompted report besides the vendor 0x3d (any other
    one would count as typing and keep it lit). Battery life is unmeasured.
    """

    def __init__(self, timeout):
        self.timeout = timeout
        self.enabled = False
        self.dark = False
        self.last_key = 0.0

    def connected(self, now, bluetooth):
        """The keyboard was just initialised, and restore_backlight() lit it."""
        self.enabled = bluetooth and self.timeout > 0
        self.dark = False
        self.last_key = now

    def key(self, now):
        """A key was pressed; True when that should bring the backlight back."""
        self.last_key = now
        woke, self.dark = self.dark, False
        return woke

    def due(self, now, level_of):
        """True once per idle stretch: the moment to darken the backlight.
        level_of is only read when the timeout has passed (it reads a file)."""
        if (not self.enabled or self.dark
                or now - self.last_key < self.timeout or level_of() == 0):
            return False
        self.dark = True
        return True


def counts_as_typing(data, actions):
    """Whether a report from the keyboard means somebody touched it.

    Unmapped vendor codes do not: 0x3d arrives every 30 s or so on its own
    (docs/HARDWARE.md, measured 2026-09-27) and would keep the backlight lit.
    """
    if len(data) >= 2 and data[0] == VENDOR_REPORT_ID:
        return data[1] in actions
    return bool(data)


def absent_reason():
    """The line logged when no hidraw node of the keyboard exists.

    The pogo link can be present and dead at the same time
    (dock.keyboard_usb_configured): the device enumerated but the kernel could
    not configure it, so no hidraw node ever appears and no re-init helps.
    Say so, because every other signal — "docked", bottom panel off — looks
    normal, and the daemon otherwise falls silent (2026-09-05: five hours).
    """
    if dock.keyboard_docked() and dock.keyboard_usb_configured() is False:
        return ("keyboard is on the pogo pins but its USB link failed to configure "
                "(kernel: \"can't set config\") — no typing or media keys over USB "
                "until it is lifted off and re-seated")
    return "no keyboard (undocked / BT off) — waiting"


def settled_nodes(scan, sleep=time.sleep, tries=4, interval=0.5):
    """The keyboard's node set once two scans in a row agree.

    A re-enumerating keyboard shows a shrinking, then growing, node set for a
    second or two. Acting on the first scan after a node was lost sent the
    handshake to nodes that were gone by the time they were opened, and every
    such event (2026-09-16: five in an evening) was logged as "media keys are
    dead" and then confirmed fine 2 s later. After `tries` disagreeing scans
    the last one is returned anyway; the open errors catch what is left.
    """
    current = tuple(sorted(scan()))
    for _ in range(tries):
        sleep(interval)
        again = tuple(sorted(scan()))
        if again == current:
            return current
        current = again
    return current


def failure_message(rc, failures, delay):
    """What to say when the handshake did not land, scaled to how long it has
    been failing: the first attempts after a dock routinely fail (the udev ACL
    lands a moment after the nodes appear), so they are not an alarm yet."""
    if failures < 3:
        what = ("hidraw is not accessible yet" if rc == 13
                else "waiting for the keyboard to echo the handshake")
        return f"{what} (attempt {failures}); retrying in {delay}s"
    if rc == 13:
        return (f"hidraw is not accessible (attempt {failures}): the udev rule is missing "
                f"or not applied (sudo ./install.sh --system, then re-seat the keyboard); "
                f"retrying in {delay}s")
    return (f"hotkey mode not confirmed (attempt {failures}): the media keys are dead until "
            f"the keyboard echoes the handshake. Re-seat it, or share `duo doctor`; "
            f"retrying in {delay}s")


def slept_since(last):
    """(suspended?, new marks) — CLOCK_BOOTTIME counts suspend, CLOCK_MONOTONIC
    does not, so the gap between them is exactly the time spent asleep.

    The keyboard forgets hotkey mode whenever it loses power, and a resume can
    hand back the very same hidraw node names — so watching the node set alone
    misses it, and the media layer stays dead until a physical re-dock. That is
    the "worked yesterday, dead after a few sleeps" failure. No D-Bus needed:
    two clock reads tell us the machine was away.
    """
    mono, boot = time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)
    if last is None:
        return False, (mono, boot)
    slept = (boot - last[1]) - (mono - last[0])
    return slept > 1.0, (mono, boot)


BATTERY_CHECK_SECONDS = 60


def check_kb_battery(alarm, notify=battery.notify):
    """Pop up a warning when the keyboard battery drops through a low level.

    Not while the kernel or UPower says it is charging; the 5a 3d report's
    charging state is only a guess, so it never silences a warning.
    """
    reading = battery.keyboard_reading(battery.load_status(), battery.keyboard_sysfs(),
                                       battery.keyboard_upower())
    if reading is None:
        return None
    pct, state, source = reading
    if state == "charging" and source != "report":
        return None
    level = alarm.update(pct)
    if level is not None:
        shown = notify(pct, source)
        log(f"keyboard battery at {pct} % ({source}): below {level} %"
            + ("" if shown else "; no popup (notify-send or the session bus missing)"))
    return level


def main():
    # Line-buffer stdout so kb_init's prints reach the journal when they happen.
    # Block-buffered through a pipe they sat unflushed for 37 minutes, arriving
    # stamped with a much later event and pointing diagnosis at the wrong time.
    sys.stdout.reconfigure(line_buffering=True)
    actions = load_overrides()
    dispatcher = Dispatcher()
    idle = IdleOff(idle_off_seconds())
    fds = {}
    # None = "state unknown, say what you find": the first pass and every
    # rescan after a lost node log either branch below, so a keyboard that
    # vanished (undock, BT off, or a USB link that died) is written down.
    # An empty tuple means "no nodes, already reported".
    known = None
    clocks = None
    failures, retry_at = 0, 0.0
    alarm, battery_at = battery.LowAlarm(), 0.0
    chords = hotkeys.ChordWatch()
    log(f"started (actions: {len(actions)} codes; map overrides honored)")
    while True:
        dispatcher.reap()
        woke, clocks = slept_since(clocks)
        if woke:
            log("resumed from sleep — re-sending the keyboard handshake")
            known = None  # it lost hotkey mode while the machine was off — or died
        nodes = tuple(sorted(kb_init.keyboard_hidraw_nodes()))
        if nodes != known and time.monotonic() >= retry_at:
            for fd in list(fds):
                os.close(fd)
            fds = {}
            nodes = settled_nodes(kb_init.keyboard_hidraw_nodes)
            if nodes:
                log(f"keyboard present on {' '.join(nodes)} — sending init")
                rc = kb_init.send_handshake(hint=False, verbose=False)
                vanished = []
                for n in nodes:
                    try:
                        fds[os.open(n, os.O_RDONLY | os.O_NONBLOCK)] = n
                    except FileNotFoundError:
                        vanished.append(n)
                    except OSError as e:
                        log(f"cannot read {n}: {e}")
                if rc == 2 or vanished:
                    # The nodes went away under us: the keyboard is still
                    # re-enumerating, or just left. That is not a handshake
                    # failure, so it neither counts nor alarms; look again.
                    log("keyboard nodes changed while sending the init (re-enumerating?); rescanning")
                    for fd in list(fds):
                        os.close(fd)
                    fds = {}
                    known = None
                    retry_at = time.monotonic() + 1.0
                    continue
                # Only remember this node set once the init AND at least one open
                # actually succeeded. Docking races udev: the hidraw nodes appear
                # a moment before the uaccess ACL lands, so the first attempt can
                # fail with EACCES — committing `known` there would wedge the
                # daemon in the 2 s idle loop until a physical re-dock.
                #
                # send_handshake now only reports success when the keyboard
                # echoes the handshake back, so a failure here means the media
                # layer really is off and retrying is the right thing. Back off
                # while doing so: the OOBE sequence shares a prefix with the
                # keyboard-backlight report, and re-sending it every couple of
                # seconds forever would poke the device far harder than intended.
                if rc == 0 and fds:
                    known = nodes
                    # A new connection: the last one's battery report is stale.
                    battery.clear_status()
                    failures, retry_at = 0, 0.0
                    # The keyboard is back and initialised: give it its
                    # backlight back rather than leaving it dark until the
                    # user presses the key.
                    dispatcher.restore_backlight()
                    dispatcher.restore_state()
                    idle.connected(time.monotonic(),
                                   any(kb_init.over_bluetooth(n) for n in nodes))
                else:
                    failures += 1
                    delay = min(60, 2 ** min(failures, 6))
                    retry_at = time.monotonic() + delay
                    if failures in (1, 3) or failures % 10 == 0:
                        log(failure_message(rc, failures, delay))
            else:
                # The pogo link can be present and dead at the same time
                # (dock.keyboard_usb_configured): the device enumerated but
                # the kernel could not configure it, so no hidraw node ever
                # appears and no amount of re-init helps. Say so, because
                # every other signal ("docked", bottom panel off) looks normal.
                log(absent_reason())
                known = nodes
                failures, retry_at = 0, 0.0
        if not fds:
            time.sleep(2)
            continue
        ready, _, _ = select.select(list(fds), [], [], 2.0)
        for fd in ready:
            try:
                data = os.read(fd, 64)
            except OSError:
                os.close(fd)
                node = fds.pop(fd, None)
                if known is not None:  # the first lost node says it; the rest are the same event
                    log(f"lost {node}; rescanning")
                known = None  # rescan, and report whichever state is found
                continue
            is_vendor = len(data) >= 2 and data[0] == VENDOR_REPORT_ID
            if chords.is_f7(fds.get(fd), data):
                hotkeys.mark_f7()  # `duo super-p` reads it: terminal, not the switcher
            if counts_as_typing(data, actions) and idle.key(time.monotonic()):
                dispatcher.wake_backlight()
                if is_vendor and actions.get(data[1]) == "kbd-backlight":
                    continue  # the press that wakes the backlight only wakes it
            if is_vendor and data[1] == battery.STATUS_CODE:
                battery.save_status(data)  # the window reads it: lib/battery.py
                battery_at = 0.0  # check it now
            if is_vendor and data[1] != 0:
                code = data[1]
                dispatcher.dispatch(code, actions.get(code, f"unmapped-0x{code:02x}"))
        if time.monotonic() >= battery_at:
            battery_at = time.monotonic() + BATTERY_CHECK_SECONDS
            check_kb_battery(alarm)
        # After the reads, so a key that arrived during the select counts.
        if idle.due(time.monotonic(), kb_backlight.read_level):
            dispatcher.darken_backlight()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nwatch-fn: stopped", file=sys.stderr)
        sys.exit(0)
