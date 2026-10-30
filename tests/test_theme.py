"""theme: which settings a light/dark switch writes. The store is a dict;
nothing here runs gsettings."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import theme  # noqa: E402


class FakeStore:
    def __init__(self, **values):
        self.values = {k.replace("_", "-"): v for k, v in values.items()}
        self.writes = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value


def quiet(argv, store):
    devnull = open(os.devnull, "w")
    old, sys.stdout = sys.stdout, devnull
    try:
        return theme.main(argv, store)
    finally:
        sys.stdout = old
        devnull.close()


class ThemeTest(unittest.TestCase):
    def test_toggle_from_light_goes_dark_with_the_yaru_accent_kept(self):
        store = FakeStore(color_scheme="default", gtk_theme="Yaru-blue")
        self.assertEqual(quiet(["toggle"], store), 0)
        self.assertEqual(store.writes, [("color-scheme", "prefer-dark"),
                                        ("gtk-theme", "Yaru-blue-dark")])

    def test_toggle_from_dark_writes_default_not_prefer_light(self):
        store = FakeStore(color_scheme="prefer-dark", gtk_theme="Yaru-dark")
        quiet(["toggle"], store)
        self.assertEqual(store.writes, [("color-scheme", "default"),
                                        ("gtk-theme", "Yaru")])

    def test_prefer_light_counts_as_light(self):
        store = FakeStore(color_scheme="prefer-light", gtk_theme="Yaru")
        quiet(["toggle"], store)
        self.assertEqual(store.values["color-scheme"], "prefer-dark")

    def test_a_theme_that_is_not_yaru_is_left_alone(self):
        store = FakeStore(color_scheme="default", gtk_theme="Adwaita")
        quiet(["dark"], store)
        self.assertEqual(store.writes, [("color-scheme", "prefer-dark")])

    def test_setting_the_style_it_already_has_does_not_rewrite_the_theme(self):
        store = FakeStore(color_scheme="prefer-dark", gtk_theme="Yaru-dark")
        quiet(["dark"], store)
        self.assertEqual(store.writes, [("color-scheme", "prefer-dark")])

    def test_unknown_verb_is_usage(self):
        self.assertEqual(theme.main(["purple"], FakeStore()), 64)


if __name__ == "__main__":
    unittest.main()
