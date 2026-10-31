"""lib/kb_state.py — Fn-lock and the mic-mute LED, without a keyboard."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import kb_state  # noqa: E402


class KbState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name})
        self.env.start()
        self.sent = []
        self.send = mock.patch.object(
            kb_state.kb_backlight, "send_vendor_report",
            side_effect=lambda payload, what, tag: self.sent.append(payload) or 0)
        self.send.start()

    def tearDown(self):
        self.send.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_reports_are_what_myasus_sends(self):
        # Windows capture 2026-10-31: 5a d0 4e 01 / 00 and 5a d0 7c 01 / 00.
        kb_state.main(["fn-lock", "on"])
        kb_state.main(["mic-led", "off"])
        self.assertEqual(self.sent, [[0x5A, 0xD0, 0x4E, 1], [0x5A, 0xD0, 0x7C, 0]])

    def test_toggle_is_remembered(self):
        self.assertFalse(kb_state.read_fn_lock())
        kb_state.main(["fn-lock", "toggle"])
        self.assertTrue(kb_state.read_fn_lock())
        kb_state.main(["fn-lock", "toggle"])
        self.assertFalse(kb_state.read_fn_lock())
        self.assertEqual([p[3] for p in self.sent], [1, 0])

    def test_restore_sends_only_when_on(self):
        self.assertEqual(kb_state.main(["fn-lock", "--restore"]), 0)
        self.assertEqual(self.sent, [])
        kb_state.save_fn_lock(True)
        kb_state.main(["fn-lock", "--restore"])
        self.assertEqual(self.sent, [[0x5A, 0xD0, 0x4E, 1]])

    def test_a_failed_send_keeps_the_old_state(self):
        self.send.stop()
        with mock.patch.object(kb_state.kb_backlight, "send_vendor_report", return_value=1):
            self.assertEqual(kb_state.main(["fn-lock", "on"]), 1)
        self.send.start()
        self.assertFalse(kb_state.read_fn_lock())

    def test_usage(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(kb_state.main(["mic-led"]), 64)
            self.assertEqual(kb_state.main(["fn-lock", "sideways"]), 64)


if __name__ == "__main__":
    unittest.main()
