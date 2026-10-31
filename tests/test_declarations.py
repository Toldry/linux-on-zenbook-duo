"""Settings and features are declared in several files; they must agree.

lib/duo_model.py's KNOBS and FEATURES are the reference. A knob or a feature
added there, or anywhere else, without the others is the drift this catches:
F8_DARK_MODE shipped (PR #11) with no home-manager option, so a
home-manager machine could not set it.

Parsed as text, so the test needs neither Nix nor bash.
"""

import glob
import os
import re
import sys
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "lib"))
import duo_model as dm  # noqa: E402


def read(relative_path):
    with open(os.path.join(REPO, relative_path), encoding="utf-8") as handle:
        return handle.read()


def assignments(text):
    """{KEY: value} of the KEY=value lines in text."""
    return dict(re.findall(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))


def conf_sh_defaults():
    body = re.search(r"<<'DEFAULTS'\n(.*?)\nDEFAULTS", read("lib/conf.sh"), re.DOTALL)
    return assignments(body.group(1))


def home_manager():
    return read("nix/home-manager.nix")


def home_manager_conf_keys():
    body = re.search(r"confText = ''\n(.*?)\n  '';", home_manager(), re.DOTALL)
    return set(assignments(body.group(1)))


def home_manager_daemon_env():
    body = re.search(r"daemonEnv = \[(.*?)\];", home_manager(), re.DOTALL)
    return set(re.findall(r'"ZENDUO_([A-Z0-9_]+)=', body.group(1)))


def duo_cli_exports():
    """Knobs bin/duo-cli hands to the daemons as ZENDUO_* environment."""
    return set(re.findall(r"^export ZENDUO_([A-Z0-9_]+)=.*conf_get \1\)",
                          read("bin/duo-cli"), re.MULTILINE))


def knobs_read_from_environment():
    """Knobs a python daemon reads as ZENDUO_<KEY>, not through duo-cli."""
    keys = {knob.key for knob in dm.KNOBS}
    found = set()
    for path in glob.glob(os.path.join(REPO, "lib", "*.py")):
        with open(path, encoding="utf-8") as handle:
            found.update(re.findall(r"ZENDUO_([A-Z0-9_]+)", handle.read()))
    return found & keys


class Knobs(unittest.TestCase):
    def setUp(self):
        self.keys = {knob.key for knob in dm.KNOBS}

    def test_conf_sh_has_a_default_for_every_knob(self):
        self.assertEqual(set(conf_sh_defaults()), self.keys)

    def test_the_example_lists_every_knob_with_the_same_default(self):
        example = assignments(read("config/zenduo.conf.example"))
        self.assertEqual(set(example), self.keys)
        self.assertEqual(example, conf_sh_defaults())

    def test_home_manager_writes_every_knob(self):
        self.assertEqual(home_manager_conf_keys(), self.keys)

    def test_every_knob_a_daemon_reads_is_exported(self):
        """A daemon under systemd sees only its environment, not the file."""
        read_by_daemons = knobs_read_from_environment()
        self.assertLessEqual(read_by_daemons, duo_cli_exports())
        self.assertEqual(duo_cli_exports(), home_manager_daemon_env())


class Features(unittest.TestCase):
    def setUp(self):
        self.names = {f.name for f in dm.FEATURES if f.scope == "user"}

    def test_duo_cli(self):
        listed = re.search(r'^USER_FEATURES="([^"]*)"', read("bin/duo-cli"), re.MULTILINE)
        self.assertEqual(set(listed.group(1).split()), self.names)

    def test_unit_templates(self):
        units = {os.path.basename(p)[len("duo-"):-len(".service")]
                 for p in glob.glob(os.path.join(REPO, "systemd", "user", "duo-*.service"))}
        self.assertEqual(units, self.names)

    def test_install_sh(self):
        switched = set(re.findall(r"set_feature ([a-z-]+) ", read("install.sh")))
        self.assertEqual(switched, self.names)

    def test_home_manager(self):
        services = set(re.findall(r"^\s+duo-([a-z-]+) = ", home_manager(), re.MULTILINE))
        self.assertEqual(services, self.names)


if __name__ == "__main__":
    unittest.main()
