"""lib/terminal.py — which terminal F7 opens."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import terminal  # noqa: E402


class Terminal(unittest.TestCase):
    def test_xdg_terminal_exec_wins_when_present(self):
        self.assertEqual(terminal.terminal_argv(lambda name: True), ["xdg-terminal-exec"])
        self.assertIsNone(terminal.terminal_argv(lambda name: False))

    def test_the_first_gnome_terminal_found(self):
        found = {"ptyxis", "gnome-terminal"}
        self.assertEqual(terminal.terminal_argv(found.__contains__), ["ptyxis", "--new-window"])

    def test_arguments_are_a_usage_error(self):
        self.assertEqual(terminal.main(["x"]), 64)


if __name__ == "__main__":
    unittest.main()
