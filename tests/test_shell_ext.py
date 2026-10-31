"""lib/shell_ext.py and the GNOME Shell extension it calls, without GNOME."""

import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))
import shell_ext  # noqa: E402

EXTENSION = os.path.join(ROOT, "gnome-shell", shell_ext.UUID)


class Fake:
    """A runner that answers each argv[:3] with a canned (rc, output)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        return self.answers.get(tuple(argv[:3]), (0, ""))


class Swap(unittest.TestCase):
    def test_reads_the_number_gdbus_prints(self):
        self.assertEqual(shell_ext.parse_moved("(uint32 3,)\n"), 3)
        self.assertIsNone(shell_ext.parse_moved("()"))

    def test_calls_the_method_the_extension_exports(self):
        fake = Fake({("gdbus", "call", "--session"): (0, "(uint32 2,)")})
        self.assertEqual(shell_ext.swap(fake), 0)
        argv = fake.calls[0]
        self.assertIn(shell_ext.OBJECT_PATH, argv)
        self.assertIn(f"{shell_ext.INTERFACE}.SwapScreens", argv)
        with open(os.path.join(EXTENSION, "extension.js")) as f:
            source = f.read()
        self.assertIn(f"'{shell_ext.OBJECT_PATH}'", source)
        self.assertIn(f'<interface name="{shell_ext.INTERFACE}">', source)
        for method in ("SwapScreens", "ShowOsd"):
            self.assertIn(f'<method name="{method}">', source)

    def test_a_missing_extension_says_how_to_enable_it(self):
        fake = Fake({("gdbus", "call", "--session"):
                     (1, "Error: GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: ...")})
        with mock.patch("sys.stderr") as err:
            self.assertEqual(shell_ext.swap(fake), 1)
        self.assertIn("--enable", "".join(c.args[0] for c in err.write.call_args_list))


class Osd(unittest.TestCase):
    def test_shows_the_popup_through_the_extension(self):
        fake = Fake({})
        self.assertEqual(shell_ext.osd("input-keyboard-symbolic", "Fn lock on", fake), 0)
        self.assertEqual(fake.calls[0][-3:], [f"{shell_ext.INTERFACE}.ShowOsd",
                                              "input-keyboard-symbolic", "Fn lock on"])
        self.assertEqual(len(fake.calls), 1)

    def test_falls_back_to_a_notification_and_never_fails(self):
        fake = Fake({("gdbus", "call", "--session"): (1, "UnknownObject"),
                     ("notify-send", "--transient", "--app-name=Zenbook Duo"): (1, "")})
        with mock.patch("sys.stdout"):
            self.assertEqual(shell_ext.osd("x", "Fan mode: Whisper", fake), 0)
        self.assertEqual(fake.calls[1][0], "notify-send")


class Enable(unittest.TestCase):
    def test_adds_the_uuid_once_and_drops_the_old_name(self):
        self.assertEqual(shell_ext.with_uuid("@as []"), repr([shell_ext.UUID]))
        self.assertEqual(shell_ext.with_uuid("['a@b']\n"), repr(["a@b", shell_ext.UUID]))
        self.assertIsNone(shell_ext.with_uuid(repr([shell_ext.UUID])))
        self.assertEqual(shell_ext.with_uuid(repr(["swap-screens@zenduo.github.io", "a@b"])),
                         repr(["a@b", shell_ext.UUID]))

    def test_copies_the_extension_and_writes_the_list(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), \
                mock.patch("sys.stdout"):
            fake = Fake({("gsettings", "get", "org.gnome.shell"): (0, "['other@x']")})
            self.assertEqual(shell_ext.enable(fake), 0)
            target = os.path.join(tmp, "gnome-shell", "extensions", shell_ext.UUID)
            self.assertTrue(os.path.isfile(os.path.join(target, "metadata.json")))
        self.assertEqual(fake.calls[-1],
                         ["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                          repr(["other@x", shell_ext.UUID])])


class Metadata(unittest.TestCase):
    def test_metadata_names_its_own_directory(self):
        with open(os.path.join(EXTENSION, "metadata.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["uuid"], shell_ext.UUID)
        # GNOME 45 changed extensions to ES modules; this one is written as one.
        self.assertTrue(all(int(v) >= 45 for v in meta["shell-version"]))
        with open(os.path.join(EXTENSION, "extension.js")) as f:
            self.assertTrue(re.search(r"^export default class", f.read(), re.M))


if __name__ == "__main__":
    unittest.main()
