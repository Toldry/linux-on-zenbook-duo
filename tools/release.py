#!/usr/bin/env python3
"""tools/release.py — the version number and CHANGELOG.md, kept by CI.

Nobody bumps VERSION or edits CHANGELOG.md by hand. A pull request adds a
fragment to changelog.d/ instead (changelog.d/README.md says how); after a
merge to main, the `release` job in .github/workflows/ci.yml runs

    level    which part of the version the merged commits call for
    release  fragments -> a dated CHANGELOG section, VERSION bumped,
             fragments deleted; prints the new version
    notes V  the CHANGELOG section of version V, for the GitHub release

and commits, tags `vX.Y.Z` and publishes a GitHub release. Locally,
`make changelog` (the `draft` command) previews the next section and `check`
validates the fragments; `make test` runs the same check.

The last commit that changed VERSION is the previous release: tags are not
needed to find it, so a clone without tags computes the same thing.

stdlib only, like everything else in the project.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files in changelog.d/ that are not fragments
NOT_FRAGMENTS = {"README.md"}
FRAGMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*\.md$")
# `type(scope)!: subject`; the scope and the `!` are optional
CONVENTIONAL_SUBJECT = re.compile(
    r"^(?P<type>[a-zA-Z]+)(\([^)]*\))?(?P<breaking>!)?:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
RELEASE_COMMIT_PREFIX = "chore(release):"
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
UNRELEASED_HEADING = re.compile(r"^## +Unreleased\s*$", re.IGNORECASE)
LEVELS = ("none", "patch", "minor", "major")


class ReleaseError(Exception):
    pass


# --- version arithmetic ----------------------------------------------------

def bump_level(commit_messages):
    """The SemVer part these full commit messages (subject + body) call for.

    A breaking change (`feat!:`, `fix(cli)!:`, a `BREAKING CHANGE:` footer)
    is major, any `feat` is minor, everything else, including a subject that
    is not a Conventional Commit, is patch.
    """
    level = "patch"
    for message in commit_messages:
        stripped = message.strip()
        subject = stripped.splitlines()[0] if stripped else ""
        match = CONVENTIONAL_SUBJECT.match(subject)
        breaking = match is not None and match.group("breaking") is not None
        if breaking or BREAKING_FOOTER.search(message):
            return "major"
        if match and match.group("type").lower() == "feat":
            level = "minor"
    return level


def next_version(current, level):
    """Bump `current` by `level`. Before 1.0 a breaking change is a minor bump.

    docs/PLAN.md, "Z. 1.0": the interfaces (duo subcommands, config keys,
    home-manager options) are frozen only at 1.0, so until then a break is
    expected and must not make 1.0 by accident. 1.0.0 is cut on purpose, with
    `release --version 1.0.0` (the workflow's manual run takes it as input).
    """
    match = VERSION_PATTERN.match(current.strip())
    if not match:
        raise ReleaseError(f"VERSION is {current.strip()!r}, not X.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    if level == "major" and major == 0:
        level = "minor"
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"no version bump for level {level!r}")


# --- fragments -------------------------------------------------------------

def fragment_paths(directory):
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name) for name in os.listdir(directory)
        if name not in NOT_FRAGMENTS and not name.startswith(".")
        and os.path.isfile(os.path.join(directory, name)))


def parse_sections(text, where):
    """Split Markdown into [(heading, body)] on `### ` headings.

    Raises ReleaseError for text before the first heading, a `#`/`##`
    heading (it would break CHANGELOG.md's structure) or an empty section.
    """
    sections = []
    heading, body = None, []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("### "):
            if heading is not None:
                sections.append((heading, body))
            heading, body = line[4:].strip(), []
            continue
        if not in_code and re.match(r"^#{1,2} ", line):
            raise ReleaseError(
                f"{where}: only ### headings belong in a changelog entry, "
                f"found {line.strip()!r}")
        if heading is None:
            if line.strip():
                raise ReleaseError(
                    f"{where}: must start with a '### ' heading, "
                    f"found {line.strip()!r}")
            continue
        body.append(line)
    if heading is not None:
        sections.append((heading, body))
    if not sections:
        raise ReleaseError(f"{where}: empty")
    out = []
    for heading, lines in sections:
        body = "\n".join(lines).strip("\n")
        if not heading or not body.strip():
            raise ReleaseError(f"{where}: section {heading!r} has no text")
        out.append((heading, body))
    return out


def check_fragment(path):
    name = os.path.basename(path)
    if not FRAGMENT_NAME.match(name):
        raise ReleaseError(
            f"changelog.d/{name}: name it like 'keyboard-light-idle-off.md' "
            "(lower case, digits and dashes, .md)")
    with open(path, encoding="utf-8") as handle:
        return parse_sections(handle.read(), f"changelog.d/{name}")


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", root, *args],
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def added_at(path):
    """Unix time of the commit that added `path`; a file not yet committed
    is the newest of all (so a local draft shows it on top)."""
    directory = os.path.dirname(os.path.abspath(path))
    result = git(directory, "log", "--diff-filter=A", "--format=%ct", "-1",
                 "--", os.path.basename(path), check=False)
    stamp = result.stdout.strip()
    return int(stamp) if result.returncode == 0 and stamp else sys.maxsize


def collect_fragments(directory, added_at=added_at):
    """[(heading, body)] of every fragment, newest first, as the changelog
    reads; fragments that share a heading (`### Fixes`) become one section."""
    paths = fragment_paths(directory)
    parsed = [(path, check_fragment(path)) for path in paths]
    parsed.sort(key=lambda item: os.path.basename(item[0]))
    parsed.sort(key=lambda item: added_at(item[0]), reverse=True)
    merged, order = {}, []
    for _path, sections in parsed:
        for heading, body in sections:
            if heading not in merged:
                merged[heading] = []
                order.append(heading)
            merged[heading].append(body)
    return [(heading, "\n".join(merged[heading])) for heading in order]


# --- CHANGELOG.md ----------------------------------------------------------

def split_changelog(text):
    """(head, unreleased_body, rest).

    head ends just before the first `## ` heading; a `## Unreleased` section
    found there is taken out and its body returned, so that hand-written
    entries from before changelog.d/ existed, or from a branch that still
    adds them, are folded into the next release instead of lost.
    """
    lines = text.splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines)
                  if line.startswith("## ")), len(lines))
    head = "".join(lines[:first])
    if first < len(lines) and UNRELEASED_HEADING.match(lines[first]):
        end = next((i for i in range(first + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        return head, "".join(lines[first + 1:end]).strip("\n"), \
            "".join(lines[end:])
    return head, "", "".join(lines[first:])


def render_section(version, date, fragment_sections, unreleased_body):
    parts = [f"## {version} — {date}"]
    for heading, body in fragment_sections:
        parts.append(f"### {heading}\n\n{body}")
    if unreleased_body:
        parts.append(unreleased_body)
    return "\n\n".join(parts) + "\n"


def section_of(text, version):
    """The body of `## <version> — …`, without its heading."""
    lines = text.splitlines()
    heading = re.compile(rf"^## +{re.escape(version)}(\s|$)")
    start = next((i for i, line in enumerate(lines) if heading.match(line)),
                 None)
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end]).strip("\n") + "\n"


# --- the repository --------------------------------------------------------

class Repo:
    def __init__(self, root=ROOT):
        self.root = root
        self.version_file = os.path.join(root, "VERSION")
        self.changelog = os.path.join(root, "CHANGELOG.md")
        self.fragments = os.path.join(root, "changelog.d")

    def version(self):
        with open(self.version_file, encoding="utf-8") as handle:
            return handle.read().strip()

    def changelog_text(self):
        with open(self.changelog, encoding="utf-8") as handle:
            return handle.read()

    def pending(self):
        """(fragment_sections, unreleased_body) waiting for a release."""
        _head, unreleased, _rest = split_changelog(self.changelog_text())
        return collect_fragments(self.fragments), unreleased

    def commit_messages_since_release(self):
        """Non-merge commit messages after the last commit that changed
        VERSION, minus earlier release commits."""
        base = git(self.root, "log", "-1", "--format=%H", "--",
                   "VERSION").stdout.strip()
        revisions = f"{base}..HEAD" if base else "HEAD"
        log = git(self.root, "log", "--no-merges", "--format=%B%x00",
                  revisions).stdout
        messages = (message.strip() for message in log.split("\0"))
        return [message for message in messages
                if message and not message.startswith(RELEASE_COMMIT_PREFIX)]

    def level(self):
        fragments, unreleased = self.pending()
        if not fragments and not unreleased:
            return "none"
        return bump_level(self.commit_messages_since_release())

    def draft(self, version=None, date=None):
        fragments, unreleased = self.pending()
        if version is None:
            level = self.level()
            if level == "none":
                return None
            version = next_version(self.version(), level)
        date = date or datetime.date.today().isoformat()
        return version, render_section(version, date, fragments, unreleased)

    def release(self, version=None, date=None):
        """Write the release; returns the new version, or None if nothing is
        pending and no version was forced."""
        if version is not None and not VERSION_PATTERN.match(version):
            raise ReleaseError(f"--version {version!r} is not X.Y.Z")
        drafted = self.draft(version, date)
        if drafted is None:
            return None
        version, section = drafted
        head, _unreleased, rest = split_changelog(self.changelog_text())
        text = head.rstrip("\n") + "\n\n" + section
        if rest:
            text += "\n" + rest
        with open(self.changelog, "w", encoding="utf-8") as handle:
            handle.write(text)
        with open(self.version_file, "w", encoding="utf-8") as handle:
            handle.write(version + "\n")
        for path in fragment_paths(self.fragments):
            os.remove(path)
        return version


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tools/release.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="validate changelog.d/ fragments")
    commands.add_parser("level", help="print none, patch, minor or major")
    draft = commands.add_parser("draft", help="preview the next section")
    release = commands.add_parser("release", help="write the next release")
    for sub in (draft, release):
        sub.add_argument("--version", help="X.Y.Z instead of the computed one")
        sub.add_argument("--date", help="YYYY-MM-DD instead of today (UTC)")
    notes = commands.add_parser("notes", help="print a version's section")
    notes.add_argument("version")
    args = parser.parse_args(argv)
    repo = Repo()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        if args.command == "check":
            for path in fragment_paths(repo.fragments):
                check_fragment(path)
            split_changelog(repo.changelog_text())
            print(f"{len(fragment_paths(repo.fragments))} fragment(s) ok")
        elif args.command == "level":
            print(repo.level())
        elif args.command == "draft":
            drafted = repo.draft(args.version, args.date or today)
            if drafted is None:
                print("nothing to release: changelog.d/ is empty",
                      file=sys.stderr)
            else:
                print(drafted[1], end="")
        elif args.command == "release":
            version = repo.release(args.version, args.date or today)
            if version is None:
                print("nothing to release: changelog.d/ is empty",
                      file=sys.stderr)
                return 1
            print(version)
        elif args.command == "notes":
            print(section_of(repo.changelog_text(), args.version), end="")
    except ReleaseError as error:
        print(f"release.py: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
