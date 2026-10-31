"""lib/fan_profile.py — Fn+F's fan mode, without the BIOS."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import fan_profile  # noqa: E402


class FanProfile(unittest.TestCase):
    def test_steps_in_myasus_order(self):
        # Windows capture 2026-10-31: Standard, Whisper, Performance.
        choices = ["quiet", "balanced", "performance"]
        self.assertEqual([fan_profile.next_profile(p, choices)
                          for p in ("balanced", "quiet", "performance", "", "low-power")],
                         ["quiet", "performance", "balanced", "balanced", "balanced"])

    def test_skips_what_the_kernel_does_not_offer(self):
        self.assertEqual(fan_profile.next_profile("balanced", ["balanced", "performance"]),
                         "performance")

    def test_writes_only_through_the_helper_never_power_profiles_daemon(self):
        calls = []

        def runner(argv, **_kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch("sys.stdout"):
            self.assertEqual(fan_profile.set_profile("quiet", runner), 0)
        self.assertEqual(calls, [["sudo", "-n", fan_profile.HELPER, "thermal", "quiet"]])

    def test_next_shows_the_myasus_name(self):
        files = {fan_profile.PROFILE: "balanced", fan_profile.CHOICES: "quiet balanced performance"}
        with mock.patch.object(fan_profile, "read", side_effect=files.get), \
                mock.patch.object(fan_profile, "set_profile", return_value=0) as setter, \
                mock.patch.object(fan_profile.shell_ext, "osd") as osd:
            self.assertEqual(fan_profile.main(["next"]), 0)
        setter.assert_called_once_with("quiet")
        osd.assert_called_once_with("power-profile-power-saver-symbolic", "Fan mode: Whisper")


if __name__ == "__main__":
    unittest.main()
