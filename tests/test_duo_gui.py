"""lib/duo_gui.py — what the window shows and decides, without GTK or a display.

Everything above make_app() is plain python on purpose: the window only lays
these answers out, so this is where a change in what it says is caught."""

import configparser
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import duo_gui as dg  # noqa: E402
import duo_model as dm  # noqa: E402


class Glance(unittest.TestCase):
    def glance(self, **kw):
        g = dm.Glance(model="UX8406MA", keyboard="docked",
                      panels=[("eDP-1", "connected", "enabled"), ("eDP-2", "connected", "disabled")],
                      backlights=[("intel_backlight", 60), ("card1-eDP-2-backlight", None)],
                      battery_now="80", laptop_battery=(57, "Charging"),
                      kb_battery="99 %, charging (Bluetooth)")
        for k, v in kw.items():
            setattr(g, k, v)
        return dict((key, value) for key, _title, value in dg.glance_rows(g))

    def test_the_rows(self):
        rows = self.glance()
        self.assertEqual(rows["top"], "on")
        self.assertEqual(rows["bottom"], "off")
        self.assertEqual(rows["battery"], "80 %")
        self.assertEqual(rows["laptop-battery"], "57 %, charging")
        self.assertEqual(rows["kb-battery"], "99 %, charging (Bluetooth)")
        self.assertEqual(rows["backlight"], "intel_backlight 60 %, card1-eDP-2-backlight ?")
        self.assertTrue(rows["keyboard"].startswith("docked"))
        self.assertNotIn("override", rows)

    def test_a_machine_that_is_not_a_duo(self):
        rows = self.glance(panels=[("eDP-1", "absent", "absent")], backlights=[], battery_now="")
        self.assertEqual(rows["top"], "not found")
        self.assertEqual(rows["bottom"], "?")
        self.assertEqual(rows["backlight"], "none")
        self.assertIn("not supported", rows["battery"])
        self.assertEqual(self.glance(laptop_battery=None)["laptop-battery"], "no battery found")
        self.assertEqual(self.glance(kb_battery="")["kb-battery"], "unknown")

    def test_a_manual_override_is_shown(self):
        self.assertIn("override", self.glance(override={"set": "both"}))

    def test_a_disconnected_panel_says_so(self):
        self.assertEqual(dg.panel_text("disconnected", "disabled"), "disconnected")


class Features(unittest.TestCase):
    def unit(self, **kw):
        f = next(x for x in dm.FEATURES if x.name == "watch-fn")
        return dm.Unit(f, load="loaded", **kw)

    def test_running_with_restarts(self):
        u = self.unit(active="active", sub="running", file_state="enabled", restarts=2)
        self.assertEqual(dg.feature_state(u), ("running, 2 restarts", "ok"))

    def test_failed_with_its_exit_status(self):
        u = self.unit(active="failed", result="exit-code", exec_status=3, file_state="enabled")
        self.assertEqual(dg.feature_state(u), ("failed (exit 3)", "err"))

    def test_not_reported(self):
        self.assertEqual(dg.feature_state(None), ("not reported", "off"))


class Knobs(unittest.TestCase):
    def test_every_knob_gets_a_row(self):
        kinds = {k.key: dg.knob_kind(k, ["intel_backlight"]) for k in dm.KNOBS}
        self.assertEqual(kinds["F12_GUI"], "switch")
        self.assertEqual(kinds["APPLY_METHOD"], "choice")
        self.assertEqual(kinds["BACKLIGHT_SOURCE"], "choice")
        self.assertEqual(kinds["BATTERY_LIMIT"], "entry")
        self.assertEqual(kinds["KB_BACKLIGHT_IDLE_OFF"], "choice")

    def test_idle_off_is_picked_from_plain_durations(self):
        knob = dm.KNOB_BY_KEY["KB_BACKLIGHT_IDLE_OFF"]
        choices = dg.knob_choices(knob, ["intel_backlight"])
        self.assertIn("30", choices)  # the default must be on offer
        self.assertTrue(all(dm.validate(knob, c) == "" for c in choices))
        self.assertEqual([dg.choice_label(c, knob) for c in ("0", "30", "60", "300", "45")],
                         ["Never", "After 30 seconds", "After 1 minute", "After 5 minutes",
                          "After 45 seconds"])

    def test_a_backlight_is_typed_when_the_kernel_lists_none(self):
        knob = dm.KNOB_BY_KEY["BACKLIGHT_SOURCE"]
        self.assertEqual(dg.knob_kind(knob, []), "entry")

    def test_an_empty_choice_means_automatic(self):
        target = dm.KNOB_BY_KEY["BACKLIGHT_TARGET"]
        self.assertEqual(dg.knob_choices(target, ["a", "b"]), ["", "a", "b"])
        self.assertEqual(dg.choice_label(""), "(automatic)")
        method = dm.KNOB_BY_KEY["APPLY_METHOD"]
        self.assertEqual(dg.knob_choices(method, ["a"]), ["temporary", "persistent"])

    def test_every_knob_has_words_and_one_place(self):
        groups = {title for title, _desc in dg.KNOB_GROUPS} | {dg.EXTRAS[0]}
        for k in dm.KNOBS:
            self.assertIn(k.key, dg.KNOB_TEXT, k.key)
            if dg.knob_page(k) == "device":
                # A card carries it; exactly one, or the row would be built twice.
                self.assertEqual(sum(c.how == f"knob:{k.key}" for c in dg.CARDS), 1, k.key)
            else:
                self.assertIn(dg.knob_section(k), groups, k.key)

    def test_what_windows_does_not_do_is_on_its_own_tab(self):
        k = dm.KNOB_BY_KEY
        self.assertEqual({key for key in k if dg.knob_page(k[key]) == "extras"},
                         {"F7_TERMINAL", "F8_DARK_MODE"})
        self.assertEqual(dg.knob_page(k["F12_GUI"]), "device")
        self.assertEqual(dg.page_target("extras"), ("settings", "extras"))

    def test_a_knob_without_words_still_shows(self):
        knob = dm.Knob("NEW_KNOB", "bool", "something new")
        self.assertEqual(dg.knob_text(knob), (dg.KNOB_GROUPS[0][0], "NEW_KNOB", "something new"))
        self.assertEqual(dg.knob_page(knob), "screens")


class MyAsusLayout(unittest.TestCase):
    """The window is laid out like MyASUS (owner's screenshots, 2026-10-30)."""

    def test_sidebar_keeps_myasus_order_and_greys_what_linux_lacks(self):
        names = [e[0] for e in dg.SIDEBAR if e]
        self.assertEqual(names[:2], ["home", "device"])
        self.assertEqual(names[-1], "settings")
        for entry in dg.SIDEBAR:
            if entry and entry[0] not in dg.PAGES:
                self.assertTrue(entry[3], f"{entry[0]} is greyed without a reason")

    def test_device_settings_has_myasus_tabs_and_cards(self):
        self.assertEqual([title for _t, title in dg.TABS["device"]],
                         ["Power & Performance", "Audio & Visual", "Input Device Settings"])
        for tab, _title in dg.TABS["device"]:
            self.assertTrue(dg.cards(tab), tab)
        titles = {c.title for c in dg.CARDS}
        for seen in ("Fan Profile", "Battery Care Mode", "Memory Allocated to GPU",
                     "Sleep Assistant", "Function key", "Smart Switch",
                     "Keyboard backlight sleep mode", "Automatic keyboard backlight"):
            self.assertIn(seen, titles)

    def test_what_is_not_done_is_shown_greyed_with_a_reason(self):
        for card in dg.CARDS:
            self.assertEqual(card.available, not card.why, card.title)
            self.assertTrue(card.desc, card.title)
        self.assertTrue(dg.QUICK_SETTING[2] and dg.SYSTEM_UPDATE[2])
        self.assertTrue(all(why for *_rest, why in dg.SCREENS_TODO))

    def test_every_card_drives_something_the_window_knows(self):
        known = {"fan-mode", "fn-key", "kb-level", "touchpad"}
        for card in dg.CARDS:
            if card.how is None:
                continue
            kind, _, name = card.how.partition(":")
            if kind == "knob":
                self.assertIn(name, dm.KNOB_BY_KEY)
            elif kind == "feature":
                self.assertIn(name, {f.name for f in dm.FEATURES})
            else:
                self.assertIn(card.how, known)

    def test_fan_modes_are_myasus_names_over_platform_profile(self):
        self.assertEqual([title for _k, title, _v, _d in dg.FAN_MODES],
                         ["Standard", "Whisper", "Performance"])
        self.assertEqual(dg.fan_mode_of("quiet"), "whisper")
        self.assertEqual(dg.fan_mode_of(""), "")

    def test_home_tiles(self):
        g = dm.Glance(fan_mode="performance", keyboard="docked")
        tiles = {key: value for key, _t, value, _i in dg.home_tiles(g)}
        self.assertEqual(tiles["fan"], "Performance")
        self.assertEqual(tiles["keyboard"], "docked")

    def test_page_names_old_and_new(self):
        self.assertEqual(dg.page_target("device/input"), ("device", "input"))
        self.assertEqual(dg.page_target("doctor"), ("diagnosis", "tool"))
        self.assertEqual(dg.page_target("overview"), ("home", ""))
        for bad in ("device/nowhere", "glidex", "nowhere"):
            with self.assertRaises(ValueError):
                dg.page_target(bad)


class Toasts(unittest.TestCase):
    def test_only_a_failure_pops_up(self):
        self.assertFalse(dg.show_result(dm.Result(True, "duo top", "")))
        self.assertFalse(dg.show_result(dm.Result(True, "duo top", "enabled eDP-2")))
        self.assertTrue(dg.show_result(dm.Result(False, "duo top", "refused")))

    def test_a_daemon_knob_offers_a_restart(self):
        ok, bad = dm.Result(True, "c"), dm.Result(False, "c")
        self.assertEqual(dg.after_knob("F12_GUI", "0", ok), ("restart", "watch-fn"))
        self.assertIsNone(dg.after_knob("F12_GUI", "0", bad))
        self.assertIsNone(dg.after_knob("BATTERY_LIMIT", "80", ok))


class Journal(unittest.TestCase):
    def test_lines_and_errors_only(self):
        now = 1_800_000_000
        entries = [dm.Entry(now - 5, "keyboard initialised", unit="duo-watch-fn.service"),
                   dm.Entry(now - 1, "apply failed", unit="duo-watch-displays.service")]
        text = dg.journal_text(entries, now=now)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertIn("watch-fn", text)
        only = dg.journal_text(entries, errors_only=True, now=now)
        self.assertEqual(only.splitlines()[0].split()[1:], ["watch-displays", "apply", "failed"])

    def test_nothing_to_show_says_why(self):
        self.assertIn("No journal", dg.journal_text([], error="No journal files were found."))
        self.assertEqual(dg.journal_text([]), "Nothing logged yet.")

    def test_it_keeps_the_newest_lines(self):
        entries = [dm.Entry(1_800_000_000 + i, f"line {i}") for i in range(10)]
        self.assertEqual(dg.journal_text(entries, limit=3).splitlines()[-1].split()[-1], "9")
        self.assertEqual(len(dg.journal_text(entries, limit=3).splitlines()), 3)


class Toggle(unittest.TestCase):
    def test_f12_closes_only_the_focused_window(self):
        self.assertTrue(dg.toggle_closes(visible=True, active=True))
        self.assertFalse(dg.toggle_closes(visible=True, active=False))
        self.assertFalse(dg.toggle_closes(visible=False, active=False))

    def test_arguments(self):
        self.assertEqual(dg.parse_args([]), (False, ""))
        self.assertEqual(dg.parse_args(["--toggle", "--page", "logs"]), (True, "logs"))
        for bad in (["--page"], ["--page", "nowhere"], ["--bogus"]):
            with self.assertRaises(ValueError):
                dg.parse_args(bad)

    def test_about_is_a_page_like_the_others(self):
        self.assertEqual(dg.parse_args(["--page", "about"]), (False, "about"))
        self.assertTrue(all(url.startswith(dg.REPO_URL) for _t, _s, url in dg.ABOUT_LINKS))

    def test_bad_arguments_are_a_usage_error_before_any_gtk(self):
        self.assertEqual(dg.main(["--bogus"]), 64)


class Launcher(unittest.TestCase):
    """The desktop file: GNOME groups the window under it by name."""

    PATH = os.path.join(ROOT, "config", dg.APP_ID + ".desktop")

    def test_it_is_named_after_the_application_id(self):
        self.assertTrue(os.path.exists(self.PATH), self.PATH)

    def test_exec_is_what_install_and_nix_rewrite(self):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(self.PATH)
        entry = cp["Desktop Entry"]
        # install.sh and nix/package.nix both replace exactly this prefix.
        self.assertEqual(entry["Exec"], "/usr/local/bin/duo-cli gui")
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Terminal"], "false")


if __name__ == "__main__":
    unittest.main()
