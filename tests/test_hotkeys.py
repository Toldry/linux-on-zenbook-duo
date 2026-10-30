"""hotkeys: what binding F7 to the theme toggle writes into GNOME's settings,
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
    def test_apply_moves_super_p_from_the_switcher_to_the_toggle(self):
        store = FakeStore(paths=[MINE])
        self.assertTrue(hk.apply(store, DUO))
        self.assertEqual(store.get(hk.MUTTER, "switch-monitor"), ["XF86Display"])
        self.assertEqual(store.get(hk.MEDIA, "custom-keybindings"), [MINE, hk.THEME_PATH])
        self.assertEqual(store.get(hk.CUSTOM, "command"), f"{DUO} theme toggle")
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
