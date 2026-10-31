"""lib/watch_fn.py — what the hotkey daemon says and does, without a keyboard."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import dock  # noqa: E402
import uinput_keys  # noqa: E402
import watch_fn  # noqa: E402


class Recorder(watch_fn.Dispatcher):
    """A Dispatcher that records instead of running anything."""

    def __init__(self):
        super().__init__()
        self.spawned, self.ran = [], []

    def spawn(self, argv):
        self.spawned.append(argv)

    def run(self, argv):
        self.ran.append(argv)

    def virtual_keys(self):
        return None  # never create a real /dev/uinput device from a test

    def output(self, argv):
        self.ran.append(argv)
        return self.outputs.get(argv[0])

    outputs = {}


class FnRow(unittest.TestCase):
    def test_mainline_codes_for_mic_mute_and_emoji(self):
        # 2026-09-05: 5a 7c and 5a 7e arrived as "unmapped" 42 times between them.
        self.assertEqual(watch_fn.DEFAULT_ACTIONS[0x7C], "mic-mute")
        self.assertEqual(watch_fn.DEFAULT_ACTIONS[0x7E], "emoji")
        self.assertTrue({"mic-mute", "emoji"} <= watch_fn.KNOWN_ACTIONS)

    def test_mic_mute_toggles_the_default_source_and_sets_the_led(self):
        d = Recorder()
        d.outputs = {"wpctl": "Volume: 1.00 [MUTED]\n"}
        d.dispatch(0x7C, "mic-mute")
        self.assertEqual(d.ran[0], ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
        self.assertEqual(d.spawned, [[watch_fn.DUO, "mic-led", "on"]])
        d = Recorder()
        d.outputs = {"wpctl": "Volume: 1.00\n"}
        d.dispatch(0x7C, "mic-mute")
        self.assertEqual(d.spawned, [[watch_fn.DUO, "mic-led", "off"]])

    def test_fn_lock_toggles_the_host_state(self):
        # Windows, 2026-10-31: MyASUS answers 5a 4e with 5a d0 4e <state>.
        d = Recorder()
        d.dispatch(0x4E, "fn-lock")
        self.assertEqual(d.spawned, [[watch_fn.DUO, "fn-lock", "toggle"]])

    def test_fn_f_steps_the_fan_mode_not_the_power_profile(self):
        self.assertEqual(watch_fn.DEFAULT_ACTIONS[0x9D], "fan-profile")
        d = Recorder()
        d.dispatch(0x9D, "fan-profile")
        self.assertEqual(d.spawned, [[watch_fn.DUO, "fan-profile", "next"]])

    def test_reconnect_restores_fn_lock_and_the_mic_led(self):
        d = Recorder()
        d.outputs = {"wpctl": "Volume: 0.40 [MUTED]\n"}
        d.restore_state()
        self.assertEqual(d.spawned, [[watch_fn.DUO, "fn-lock", "--restore"],
                                     [watch_fn.DUO, "mic-led", "on"]])

    def test_emoji_raises_the_ibus_picker(self):
        d = Recorder()
        d.dispatch(0x7E, "emoji")
        self.assertEqual(d.spawned, [["ibus", "emoji"]])

    def test_f8_is_9c(self):
        # 2026-10-30, fn-probe: three bare F8 presses, three `5a 9c`.
        self.assertEqual(watch_fn.DEFAULT_ACTIONS[0x9C], "split-screen")
        self.assertNotIn(0x3D, watch_fn.DEFAULT_ACTIONS)  # a status report

    def test_unknown_action_runs_nothing(self):
        d = Recorder()
        d.dispatch(0x3D, "unmapped-0x3d")
        self.assertEqual((d.spawned, d.ran), ([], []))

    def test_an_unknown_code_is_logged_once_per_run(self):
        # 0x3d arrived 41 times in a day (2026-09-27) and every arrival put two
        # lines in the journal, which filled the screen's Recent problems.
        from unittest import mock
        lines = []
        d = Recorder()
        with mock.patch.object(watch_fn, "log", side_effect=lines.append):
            for _ in range(5):
                d.dispatch(0x3D, "unmapped-0x3d")
            d.dispatch(0x9C, "unmapped-0x9c")
            d.dispatch(0x10, "brightness-down")
        self.assertEqual(len([ln for ln in lines if "0x3d" in ln]), 1)
        self.assertEqual(len([ln for ln in lines if "0x9c" in ln]), 1)
        self.assertIn("key 5a 10 -> brightness-down", lines)
        self.assertEqual(d.unhandled, {"unmapped-0x3d": 5, "unmapped-0x9c": 1})

    def test_every_default_action_is_handled_or_known_unbound(self):
        for action in watch_fn.DEFAULT_ACTIONS.values():
            self.assertIn(action, watch_fn.KNOWN_ACTIONS)
        self.assertLessEqual(watch_fn.HANDLED, watch_fn.KNOWN_ACTIONS)

    def test_fn_map_override_wins_over_the_default(self):
        with __import__("tempfile").TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "zenduo"))
            with open(os.path.join(tmp, "zenduo", "fn-map.json"), "w") as f:
                f.write('{"keys": {"camera-toggle": {"report": "5a 3d"}, "emoji": {"report": "5a 7e"}}}')
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                table = watch_fn.load_overrides()
            finally:
                if old is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = old
        self.assertEqual(table[0x3D], "camera-toggle")
        self.assertEqual(table[0x7E], "emoji")
        self.assertEqual(table[0x10], "brightness-down", "defaults survive an override file")


class DarkMode(unittest.TestCase):
    def test_f8_toggles_dark_mode(self):
        from unittest import mock
        d = Recorder()
        with mock.patch.dict(os.environ, {"ZENDUO_F8_DARK_MODE": "1"}):
            d.dispatch(0x9C, "split-screen")
        self.assertEqual(d.spawned, [[watch_fn.DUO, "theme", "toggle"]])

    def test_f8_dark_mode_off_swaps_the_screens_as_windows_does(self):
        from unittest import mock
        d = Recorder()
        with mock.patch.dict(os.environ, {"ZENDUO_F8_DARK_MODE": "0"}):
            d.dispatch(0x9C, "split-screen")
        self.assertEqual(d.spawned, [[watch_fn.DUO, "swap-screens"]])


class Window(unittest.TestCase):
    def test_f12_is_the_myasus_code_mainline_names(self):
        # drivers/hid/hid-asus.c: `case 0x86: ... KEY_PROG1 ... /* MyASUS key */`.
        self.assertEqual(watch_fn.DEFAULT_ACTIONS[0x86], "myasus")
        self.assertIn("myasus", watch_fn.HANDLED)

    def test_f12_toggles_the_window_in_its_own_unit(self):
        from unittest import mock
        d = Recorder()
        with mock.patch.dict(os.environ, {"ZENDUO_F12_GUI": "1"}):
            d.dispatch(0x86, "myasus")
        self.assertEqual(d.spawned, [["systemd-run", "--user", "--collect", "--quiet",
                                      "--", watch_fn.DUO, "gui", "--toggle"]])
        self.assertTrue(watch_fn.DUO.endswith("/bin/duo-cli"))

    def test_f12_gui_off_runs_nothing(self):
        from unittest import mock
        d = Recorder()
        with mock.patch.dict(os.environ, {"ZENDUO_F12_GUI": "0"}):
            d.dispatch(0x86, "myasus")
        self.assertEqual(d.spawned, [])


class FakeKeys:
    def __init__(self, fail=False):
        self.tapped, self.fail = [], fail

    def tap(self, key):
        if self.fail:
            raise OSError(5, "Input/output error")
        self.tapped.append(key)


class Brightness(unittest.TestCase):
    """GNOME 49 dropped gsd-power's Screen interface; the keys must still work."""

    def recorder(self, failing, keys=None):
        d = Recorder()
        d.run = lambda argv: (d.ran.append(argv), argv[0] not in failing)[1]
        d.virtual_keys = lambda: keys
        return d

    def test_gnome_49_gets_a_real_key_press(self):
        keys = FakeKeys()
        d = self.recorder(failing=("gdbus",), keys=keys)
        d.dispatch(0x20, "brightness-up")
        d.dispatch(0x10, "brightness-down")
        self.assertEqual(keys.tapped, [uinput_keys.KEY_BRIGHTNESSUP,
                                       uinput_keys.KEY_BRIGHTNESSDOWN])
        # GNOME Shell sets both panels itself; no Mutter call, no sysfs sync.
        self.assertEqual(len(d.ran), 2)
        self.assertEqual(d.spawned, [])

    def test_a_failed_key_press_falls_back_to_mutter(self):
        from unittest import mock
        d = self.recorder(failing=("gdbus",), keys=FakeKeys(fail=True))
        with mock.patch.object(watch_fn, "log"):
            d.dispatch(0x20, "brightness-up")
        self.assertEqual(d.ran[1], [watch_fn.DUO, "brightness", "up"])

    def test_without_uinput_the_daemon_says_why_once(self):
        from unittest import mock
        d = Recorder()
        d.virtual_keys = watch_fn.Dispatcher.virtual_keys.__get__(d)
        lines = []
        with mock.patch.object(uinput_keys, "VirtualKeys",
                               side_effect=PermissionError(13, "Permission denied")), \
                mock.patch.object(watch_fn, "log", side_effect=lines.append):
            self.assertIsNone(d.virtual_keys())
            self.assertIsNone(d.virtual_keys())
        self.assertEqual(len(lines), 1)
        self.assertIn("Permission denied", lines[0])

    def test_gsd_power_handles_it_when_present(self):
        d = self.recorder(failing=())
        d.dispatch(0x20, "brightness-up")
        self.assertEqual(len(d.ran), 1)
        self.assertIn("org.gnome.SettingsDaemon.Power.Screen.StepUp", d.ran[0])
        self.assertEqual(d.spawned, [[watch_fn.DUO, "sync-backlight"]])

    def test_falls_back_to_mutter_without_gsd_power(self):
        d = self.recorder(failing=("gdbus",))
        d.dispatch(0x10, "brightness-down")
        self.assertEqual(d.ran[1], [watch_fn.DUO, "brightness", "down"])
        # Mutter already set both panels; a sysfs sync would hide it from GNOME.
        self.assertEqual(d.spawned, [])

    def test_a_failed_fallback_is_logged(self):
        from unittest import mock
        d = self.recorder(failing=("gdbus", watch_fn.DUO))
        with mock.patch.object(watch_fn, "log") as log:
            d.dispatch(0x20, "brightness-up")
        self.assertTrue(any("duo brightness up" in call.args[0]
                            for call in log.call_args_list))


class ReEnumeration(unittest.TestCase):
    """2026-09-16: five times in an evening the keyboard's nodes vanished and
    came back within 2 s; each time the daemon rescanned mid-teardown, sent
    the init to nodes that were gone, and logged "media keys are dead"."""

    def test_settled_nodes_waits_for_two_scans_that_agree(self):
        scans = iter([["/dev/hidraw5", "/dev/hidraw6"], ["/dev/hidraw4", "/dev/hidraw5", "/dev/hidraw6"],
                      ["/dev/hidraw4", "/dev/hidraw5", "/dev/hidraw6"], ["never"]])
        slept = []
        nodes = watch_fn.settled_nodes(lambda: next(scans), sleep=slept.append)
        self.assertEqual(nodes, ("/dev/hidraw4", "/dev/hidraw5", "/dev/hidraw6"))
        self.assertEqual(len(slept), 2)

    def test_settled_nodes_gives_up_after_its_tries(self):
        n = iter(range(100))
        nodes = watch_fn.settled_nodes(lambda: [f"/dev/hidraw{next(n)}"], sleep=lambda s: None, tries=3)
        self.assertEqual(nodes, ("/dev/hidraw3",))

    def test_the_first_attempts_are_not_an_alarm(self):
        early = watch_fn.failure_message(1, 1, 2)
        self.assertIn("waiting", early)
        self.assertNotIn("dead", early)
        denied = watch_fn.failure_message(13, 2, 4)
        self.assertIn("not accessible yet", denied)
        loud = watch_fn.failure_message(1, 3, 8)
        self.assertIn("dead", loud)
        self.assertIn("not confirmed", loud)
        self.assertIn("udev", watch_fn.failure_message(13, 3, 8))


class AbsentKeyboard(unittest.TestCase):
    """2026-09-05: the keyboard died on USB (enumerated, "can't set config",
    no hidraw nodes) and watch-fn went silent for five hours because the
    read-error path reset its node set without ever reporting what it found."""

    def setUp(self):
        self.saved = (dock.keyboard_docked, dock.keyboard_usb_configured)

    def tearDown(self):
        dock.keyboard_docked, dock.keyboard_usb_configured = self.saved

    def fake(self, docked, configured):
        dock.keyboard_docked = lambda: docked
        dock.keyboard_usb_configured = lambda dev=None: configured

    def test_undocked_is_waiting(self):
        self.fake(False, None)
        self.assertIn("undocked", watch_fn.absent_reason())

    def test_dead_usb_link_is_named(self):
        self.fake(True, False)
        msg = watch_fn.absent_reason()
        self.assertIn("pogo pins", msg)
        self.assertIn("re-seated", msg)

    def test_docked_and_configured_but_no_nodes_is_still_waiting(self):
        # A working link with no hidraw nodes yet (udev still binding): not the
        # dead-link message, which would send the user to re-seat a fine keyboard.
        self.fake(True, True)
        self.assertNotIn("re-seated", watch_fn.absent_reason())


if __name__ == "__main__":
    unittest.main()


class BacklightIdleOff(unittest.TestCase):
    """2026-10-30: detached and idle, the backlight stayed lit and the keyboard
    needed charging several times a day; under Windows it lasted days."""

    def connected(self, timeout=30, bluetooth=True):
        idle = watch_fn.IdleOff(timeout)
        idle.connected(100.0, bluetooth)
        return idle

    def test_goes_dark_once_after_the_timeout(self):
        idle = self.connected()
        self.assertFalse(idle.due(129.0, lambda: 2))
        self.assertTrue(idle.due(130.0, lambda: 2))
        self.assertFalse(idle.due(200.0, lambda: 2))  # already dark

    def test_the_next_key_wakes_it_and_restarts_the_clock(self):
        idle = self.connected()
        idle.due(130.0, lambda: 2)
        self.assertTrue(idle.key(140.0))
        self.assertFalse(idle.key(141.0))  # lit again: an ordinary key
        self.assertFalse(idle.due(170.0, lambda: 2))
        self.assertTrue(idle.due(171.0, lambda: 2))

    def test_typing_keeps_it_lit(self):
        idle = self.connected()
        for t in range(101, 200, 10):
            self.assertFalse(idle.key(float(t)))
            self.assertFalse(idle.due(t + 5.0, lambda: 2))

    def test_never_acts_docked_disabled_or_already_off(self):
        self.assertFalse(self.connected(bluetooth=False).due(1000.0, lambda: 2))
        self.assertFalse(self.connected(timeout=0).due(1000.0, lambda: 2))
        level_read = []
        idle = self.connected()
        self.assertFalse(idle.due(1000.0, lambda: level_read.append(1) or 0))
        self.assertEqual(level_read, [1])
        self.assertFalse(idle.key(1001.0))  # nothing was darkened, nothing to wake

    def test_level_is_only_read_once_the_timeout_passed(self):
        idle = self.connected()
        idle.due(110.0, lambda: self.fail("read the level before the timeout"))

    def test_status_reports_are_not_typing(self):
        actions = dict(watch_fn.DEFAULT_ACTIONS)
        self.assertFalse(watch_fn.counts_as_typing(bytes([0x5A, 0x3D, 0, 0]), actions))
        self.assertTrue(watch_fn.counts_as_typing(bytes([0x5A, 0xC7, 0, 0]), actions))
        self.assertTrue(watch_fn.counts_as_typing(bytes([0x01, 0, 0, 0x04]), actions))
        self.assertFalse(watch_fn.counts_as_typing(b"", actions))

    def test_timeout_from_the_environment(self):
        from unittest import mock
        for value, expected in (("45", 45), ("0", 0), ("-3", 0), ("soon", 30)):
            with mock.patch.dict(os.environ, {"ZENDUO_KB_BACKLIGHT_IDLE_OFF": value}):
                self.assertEqual(watch_fn.idle_off_seconds(), expected)

    def test_dark_and_wake_never_touch_the_remembered_level(self):
        from unittest import mock
        d = Recorder()
        with mock.patch.object(watch_fn.kb_backlight, "read_level", return_value=2):
            d.darken_backlight()
            d.wake_backlight()
        self.assertEqual(d.spawned, [[watch_fn.DUO, "kb-backlight", "--apply", "0"],
                                     [watch_fn.DUO, "kb-backlight", "--apply", "2"]])


class KeyboardBattery(unittest.TestCase):
    def test_a_low_reading_pops_up_once(self):
        from unittest import mock
        shown = []

        def notify(pct, source):
            shown.append((pct, source))
            return True

        b = watch_fn.battery
        with mock.patch.object(b, "load_status", return_value=(bytes([0x5A, 0x3D, 0x09, 0x00]), 0)), \
                mock.patch.object(b, "keyboard_sysfs", return_value=None), \
                mock.patch.object(b, "keyboard_upower", return_value=None):
            alarm = b.LowAlarm()
            self.assertEqual(watch_fn.check_kb_battery(alarm, notify), 10)
            self.assertIsNone(watch_fn.check_kb_battery(alarm, notify))
        self.assertEqual(shown, [(9, "report")])

    def test_no_popup_while_the_kernel_says_it_charges(self):
        from unittest import mock
        b = watch_fn.battery
        with mock.patch.object(b, "load_status", return_value=None), \
                mock.patch.object(b, "keyboard_sysfs", return_value=(4, "charging")), \
                mock.patch.object(b, "keyboard_upower", return_value=None):
            self.assertIsNone(watch_fn.check_kb_battery(b.LowAlarm(), lambda *_: True))
