"""hotkeys: what binding F7 to the terminal writes into GNOME's settings,
and what undoing it puts back. The store is a dict; nothing runs gsettings."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import hotkeys as hk  # noqa: E402

DUO = "/usr/local/bin/duo-cli"
MINE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"


class FakeStore:
    def __init__(self, switch=("<Super>p", "XF86Display"), paths=()):
        self.values = {(hk.MUTTER, "switch-monitor"): list(switch),
                       (hk.MEDIA, "custom-keybindings"): list(paths)}
        self.writes = []

    def get(self, schema, key):
        return self.values.get((schema, key), "")

    def set(self, schema, key, value):
        self.writes.append((schema, key, value))
        self.values[(schema, key)] = value

    def reset(self, schema, key):
        self.values.pop((schema, key), None)


class Hotkeys(unittest.TestCase):
    def test_apply_moves_super_p_from_the_switcher_to_the_terminal(self):
        store = FakeStore(paths=[MINE])
        self.assertTrue(hk.apply(store, DUO))
        self.assertEqual(store.get(hk.MUTTER, "switch-monitor"), ["XF86Display"])
        self.assertEqual(store.get(hk.MEDIA, "custom-keybindings"), [MINE, hk.F7_PATH])
        self.assertEqual(store.get(hk.CUSTOM, "command"), f"{DUO} super-p")
        self.assertEqual(store.get(hk.CUSTOM, "binding"), "<Super>p")
        self.assertIn(DUO, hk.show(store))

    def test_a_second_apply_writes_nothing(self):
        store = FakeStore()
        hk.apply(store, DUO)
        store.writes.clear()
        self.assertFalse(hk.apply(store, DUO))
        self.assertEqual(store.writes, [])

    def test_the_mod4_spelling_counts_as_super_p(self):
        store = FakeStore(switch=["<Mod4>P"])
        hk.apply(store, DUO)
        self.assertEqual(store.get(hk.MUTTER, "switch-monitor"), [])

    def test_remove_puts_the_switcher_back_and_keeps_other_shortcuts(self):
        store = FakeStore(paths=[MINE])
        hk.apply(store, DUO)
        hk.remove(store)
        self.assertEqual(store.get(hk.MUTTER, "switch-monitor"), ["<Super>p", "XF86Display"])
        self.assertEqual(store.get(hk.MEDIA, "custom-keybindings"), [MINE])
        self.assertEqual(hk.show(store), "F7 (Super+P): GNOME's display switcher")

    def test_apply_replaces_the_old_dark_mode_shortcut(self):
        # Until 2026-10-30 F7 toggled dark mode under another path.
        old = hk.OLD_PATHS[0]
        store = FakeStore(switch=["XF86Display"], paths=[MINE, old])
        store.values[(hk.custom(old), "binding")] = "<Super>p"
        self.assertTrue(hk.apply(store, DUO))
        self.assertEqual(store.get(hk.MEDIA, "custom-keybindings"), [MINE, hk.F7_PATH])
        self.assertNotIn((hk.custom(old), "binding"), store.values)

    def test_remove_also_takes_the_old_shortcut_out(self):
        old = hk.OLD_PATHS[0]
        store = FakeStore(switch=["XF86Display"], paths=[old, MINE])
        hk.remove(store)
        self.assertEqual(store.get(hk.MEDIA, "custom-keybindings"), [MINE])
        self.assertEqual(store.get(hk.MUTTER, "switch-monitor"), ["<Super>p", "XF86Display"])

    def test_gvariant_round_trip(self):
        self.assertEqual(hk.to_gvariant([]), "@as []")
        self.assertEqual(hk.from_gvariant("@as []"), [])
        self.assertEqual(hk.from_gvariant(hk.to_gvariant(["<Super>p", "it's"])),
                         ["<Super>p", "it's"])
        self.assertEqual(hk.from_gvariant("'prefer-dark'\n"), "prefer-dark")

    def test_apply_needs_the_duo_cli_path(self):
        self.assertEqual(hk.main(["apply"], FakeStore()), 64)


if __name__ == "__main__":
    unittest.main()


class ChordWatch(unittest.TestCase):
    """F7 types Super and P in one report; a hand types Super first."""

    F7 = bytes([0x02, 0x08, 0x00, 0x13, 0, 0, 0, 0])
    SUPER = bytes([0x02, 0x08, 0x00, 0x00, 0, 0, 0, 0])
    RELEASED = bytes([0x02, 0x00, 0x00, 0x00, 0, 0, 0, 0])

    def test_f7_is_one_report(self):
        chords = hk.ChordWatch()
        self.assertTrue(chords.is_f7("/dev/hidraw1", self.F7))
        self.assertFalse(chords.is_f7("/dev/hidraw1", self.RELEASED))
        self.assertTrue(chords.is_f7("/dev/hidraw1", self.F7))   # pressed again

    def test_a_typed_super_p_is_not_f7(self):
        chords = hk.ChordWatch()
        self.assertFalse(chords.is_f7("/dev/hidraw1", self.SUPER))
        self.assertFalse(chords.is_f7("/dev/hidraw1", self.F7))

    def test_nodes_and_report_ids_are_tracked_apart(self):
        chords = hk.ChordWatch()
        chords.is_f7("/dev/hidraw1", self.SUPER)
        self.assertTrue(chords.is_f7("/dev/hidraw2", self.F7))

    def test_other_reports_are_ignored(self):
        chords = hk.ChordWatch()
        self.assertFalse(chords.is_f7("n", bytes([0x5A, 0x08, 0x00, 0x13])))  # vendor
        self.assertFalse(chords.is_f7("n", bytes([0x02, 0x08, 0x01, 0x13])))  # not a key report
        self.assertFalse(chords.is_f7("n", bytes([0x02, 0x08])))              # too short
        self.assertFalse(chords.is_f7("n", bytes([0x02, 0x02, 0x00, 0x13])))  # Shift+P


class Mark(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "zenduo", hk.MARK_FILE)

    def test_a_fresh_mark_is_taken_once(self):
        hk.mark_f7(now=100.0, path=self.path)
        self.assertTrue(hk.take_f7_mark(self.path, wait=0, clock=lambda: 100.2))
        self.assertFalse(hk.take_f7_mark(self.path, wait=0, clock=lambda: 100.3))

    def test_a_stale_mark_does_not_count(self):
        hk.mark_f7(now=100.0, path=self.path)
        self.assertFalse(hk.take_f7_mark(self.path, wait=0, clock=lambda: 105.0))

    def test_it_waits_for_a_late_mark(self):
        now = [100.0]

        def sleep(seconds):
            now[0] += seconds
            if now[0] >= 100.1:
                hk.mark_f7(now=100.05, path=self.path)
        self.assertTrue(hk.take_f7_mark(self.path, wait=0.25, clock=lambda: now[0],
                                        sleep=sleep))

    def test_no_mark_gives_up(self):
        now = [100.0]

        def sleep(seconds):
            now[0] += seconds
        self.assertFalse(hk.take_f7_mark(self.path, wait=0.25, clock=lambda: now[0],
                                         sleep=sleep))


class SuperP(unittest.TestCase):
    class Done:
        returncode = 0

    def run_super_p(self, f7, shell_rc=0):
        ran, called = [], []

        def runner(argv):
            ran.append(argv)
            return self.Done()

        def call(method):
            called.append(method)
            return shell_rc, "" if shell_rc == 0 else "UnknownMethod"
        hk.super_p(DUO, take=lambda: f7, call=call, runner=runner)
        return ran, called

    def test_f7_opens_the_terminal(self):
        self.assertEqual(self.run_super_p(True), ([[DUO, "terminal"]], []))

    def test_a_typed_super_p_opens_the_switcher(self):
        self.assertEqual(self.run_super_p(False), ([], ["ShowDisplaySwitcher"]))

    def test_without_the_extension_it_steps_the_layout(self):
        ran, _called = self.run_super_p(False, shell_rc=1)
        self.assertEqual(ran, [[DUO, "layout", "cycle"]])
