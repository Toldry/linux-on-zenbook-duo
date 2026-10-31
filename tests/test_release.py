"""tools/release.py and `duo-cli version` — the version and the changelog."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(REPO, "tools"))
import release  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


def git(root, *args, date=None):
    env = dict(os.environ, **GIT_ENV)
    if date is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{date} +0000"
    return subprocess.run(["git", "-C", root, *args], env=env, check=True,
                          capture_output=True, text=True).stdout


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


class BumpLevel(unittest.TestCase):
    def test_feat_is_minor_fix_is_patch(self):
        self.assertEqual(release.bump_level(["fix(cli): x", "docs: y"]),
                         "patch")
        self.assertEqual(release.bump_level(["fix: x", "feat(gui): y"]),
                         "minor")

    def test_breaking_marks(self):
        self.assertEqual(release.bump_level(["feat(cli)!: drop x"]), "major")
        self.assertEqual(release.bump_level(["fix!: x"]), "major")
        self.assertEqual(
            release.bump_level(["fix: x\n\nBREAKING CHANGE: key renamed"]),
            "major")

    def test_not_conventional_is_patch(self):
        self.assertEqual(release.bump_level(["Update README", ""]), "patch")
        # A "!" or "feat" later in the subject is not a type
        self.assertEqual(release.bump_level(["Fix the feat: thing!"]),
                         "patch")


class NextVersion(unittest.TestCase):
    def test_bumps(self):
        self.assertEqual(release.next_version("0.9.0", "patch"), "0.9.1")
        self.assertEqual(release.next_version("0.9.3", "minor"), "0.10.0")
        self.assertEqual(release.next_version("1.2.3", "major"), "2.0.0")

    def test_breaking_before_one_is_minor(self):
        """1.0 is a decision (docs/PLAN.md, "Z. 1.0"), never a side effect."""
        self.assertEqual(release.next_version("0.9.4", "major"), "0.10.0")

    def test_bad_input(self):
        with self.assertRaises(release.ReleaseError):
            release.next_version("dev", "patch")
        with self.assertRaises(release.ReleaseError):
            release.next_version("0.9.0", "none")


class Fragments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_sections_and_code_blocks(self):
        sections = release.parse_sections(
            "### A\n\n- one\n\n```\n## not a heading\n### nor this\n```\n"
            "### B\n- two\n", "f")
        self.assertEqual([heading for heading, _ in sections], ["A", "B"])
        self.assertIn("## not a heading", sections[0][1])

    def test_rejects(self):
        for text in ("- no heading\n", "", "### Empty\n\n",
                     "### A\n- x\n## Release\n", "# Title\n### A\n- x\n"):
            with self.subTest(text=text), \
                    self.assertRaises(release.ReleaseError):
                release.parse_sections(text, "f")

    def test_names(self):
        write(self.tmp, "Bad_Name.md", "### A\n- x\n")
        with self.assertRaises(release.ReleaseError):
            release.check_fragment(os.path.join(self.tmp, "Bad_Name.md"))
        write(self.tmp, "fine-name-2.md", "### A\n- x\n")
        release.check_fragment(os.path.join(self.tmp, "fine-name-2.md"))

    def test_newest_first_and_shared_headings_merge(self):
        write(self.tmp, "README.md", "not a fragment")
        write(self.tmp, ".hidden", "not a fragment")
        write(self.tmp, "old-fix.md", "### Fixes\n\n- old fix\n")
        write(self.tmp, "feature.md", "### A feature\n\n- does a thing\n")
        write(self.tmp, "new-fix.md", "### Fixes\n\n- new fix\n")
        stamps = {"old-fix.md": 1, "feature.md": 2, "new-fix.md": 3}
        sections = release.collect_fragments(
            self.tmp, added_at=lambda path: stamps[os.path.basename(path)])
        self.assertEqual(sections, [("Fixes", "- new fix\n- old fix"),
                                    ("A feature", "- does a thing")])

    def test_committed_fragments_are_valid(self):
        """The same check CI runs: a broken fragment fails before merge."""
        directory = os.path.join(REPO, "changelog.d")
        for path in release.fragment_paths(directory):
            with self.subTest(fragment=os.path.basename(path)):
                release.check_fragment(path)


CHANGELOG = """# Changelog

Intro.

## Unreleased

### Written by hand

- before changelog.d/

## 0.9.0 — 2026-09-05

First.
"""


class Changelog(unittest.TestCase):
    def test_split_with_unreleased(self):
        head, unreleased, rest = release.split_changelog(CHANGELOG)
        self.assertEqual(head, "# Changelog\n\nIntro.\n\n")
        self.assertEqual(unreleased,
                         "### Written by hand\n\n- before changelog.d/")
        self.assertTrue(rest.startswith("## 0.9.0 — 2026-09-05\n"))

    def test_split_without_unreleased(self):
        text = "# Changelog\n\n## 0.9.0 — 2026-09-05\n\nFirst.\n"
        self.assertEqual(release.split_changelog(text),
                         ("# Changelog\n\n", "",
                          "## 0.9.0 — 2026-09-05\n\nFirst.\n"))

    def test_section_of(self):
        self.assertEqual(release.section_of(CHANGELOG, "0.9.0"), "First.\n")
        with self.assertRaises(release.ReleaseError):
            release.section_of(CHANGELOG, "0.9")


class EndToEnd(unittest.TestCase):
    """A throwaway repository through two releases, as CI would run them."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        git(self.root, "init", "-q", "-b", "main")
        write(self.root, "VERSION", "0.9.0\n")
        write(self.root, "CHANGELOG.md", CHANGELOG)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "chore: start", date=1000)
        self.repo = release.Repo(self.root)

    def commit(self, message, date, files):
        for name, text in files.items():
            write(self.root, name, text)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", message, date=date)

    def test_release_cycle(self):
        # The hand-written Unreleased section alone is something to release
        self.commit("fix(cli): a fix", 2000, {"README": "x"})
        self.assertEqual(self.repo.level(), "patch")
        self.commit("feat(gui): a feature", 3000,
                    {"changelog.d/feature.md": "### A feature\n\n- new\n"})
        self.commit("fix(gui): fix it", 4000,
                    {"changelog.d/later-fix.md": "### Fixes\n\n- fixed\n"})
        self.assertEqual(self.repo.level(), "minor")

        self.assertEqual(self.repo.release(date="2026-11-01"), "0.10.0")
        self.assertEqual(self.repo.version(), "0.10.0")
        self.assertEqual(release.fragment_paths(self.repo.fragments), [])
        text = self.repo.changelog_text()
        self.assertTrue(text.startswith(
            "# Changelog\n\nIntro.\n\n## 0.10.0 — 2026-11-01\n\n"
            "### Fixes\n\n- fixed\n\n### A feature\n\n- new\n\n"
            "### Written by hand\n"), text)
        self.assertNotIn("Unreleased", text)
        self.assertIn("\n## 0.9.0 — 2026-09-05\n", text)
        self.assertIn("### A feature", release.section_of(text, "0.10.0"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "chore(release): 0.10.0", date=5000)

        # Nothing pending right after a release, whatever the commits say
        self.commit("feat: no fragment", 6000, {"README": "y"})
        self.assertEqual(self.repo.level(), "none")
        self.assertIsNone(self.repo.release())
        # Commits since the release count, also those without a fragment
        self.commit("fix: small", 7000,
                    {"changelog.d/small.md": "### Fixes\n\n- small\n"})
        self.assertEqual(self.repo.level(), "minor")
        self.assertEqual(self.repo.release(date="2026-11-02"), "0.11.0")

    def test_forced_version(self):
        self.commit("fix: x", 2000,
                    {"changelog.d/x.md": "### Fixes\n\n- x\n"})
        self.assertEqual(self.repo.release("1.0.0", "2026-12-01"), "1.0.0")
        self.assertIn("## 1.0.0 — 2026-12-01", self.repo.changelog_text())
        with self.assertRaises(release.ReleaseError):
            self.repo.release("v1.0", "2026-12-01")


class DuoVersion(unittest.TestCase):
    """`duo-cli version` on a git checkout names the exact commit."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("bin/duo-cli", "lib/conf.sh",
                     "config/zenduo.conf.example"):
            os.makedirs(os.path.join(self.root, os.path.dirname(name)),
                        exist_ok=True)
            shutil.copy2(os.path.join(REPO, name),
                         os.path.join(self.root, name))
        write(self.root, "VERSION", "0.10.0\n")
        self.env = dict(os.environ, **GIT_ENV,
                        HOME=self.root, XDG_CONFIG_HOME=self.root)

    def version(self):
        return subprocess.run(
            ["bash", os.path.join(self.root, "bin/duo-cli"), "version"],
            env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_plain_without_git(self):
        self.assertEqual(self.version(), "0.10.0")

    def test_checkout_past_the_release(self):
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "chore(release): 0.10.0")
        self.assertEqual(self.version(), "0.10.0")
        write(self.root, "README", "x")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "fix: x")
        sha = git(self.root, "rev-parse", "--short", "HEAD").strip()
        self.assertEqual(self.version(), f"0.10.0+1.g{sha}")
        write(self.root, "README", "edited")
        self.assertEqual(self.version(), f"0.10.0+1.g{sha}.dirty")


if __name__ == "__main__":
    unittest.main()
