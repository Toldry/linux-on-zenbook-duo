# Changelog fragments

A pull request does not edit [`CHANGELOG.md`](../CHANGELOG.md) or
[`VERSION`](../VERSION). It adds one small file here instead, so that open
pull requests never touch the same lines and merging one never makes the
others conflict. After the merge, CI turns every file here into a dated
section of `CHANGELOG.md`, bumps `VERSION`, deletes the files, tags `vX.Y.Z`
and publishes a GitHub release.

## Does my change need one?

If someone running the project would notice it: a new feature, a changed
behaviour, a bug fix, a changed default, a removed command. Docs, tests, CI
and refactors do not, and a merge without a fragment makes no release.

## Name

`<slug>.md`: a few lower-case words joined by dashes, different from other
open pull requests', for example `keyboard-light-idle-off.md`.

## Content

One `###` heading in the same plain voice as the rest of `CHANGELOG.md`,
saying what changed for the person at the laptop, then bullets:

```markdown
### The keyboard light goes dark when you stop typing on Bluetooth

- detached, the keyboard's light now turns off after 30 s without a key
  press and comes back on the next key, as Windows does
  (`KB_BACKLIGHT_IDLE_OFF`; 0 = never). Docked, it stays lit
```

- Small fixes share a section: give each its own fragment headed
  `### Fixes`, and the release puts their bullets under one heading.
- A fragment may hold more than one `###` section; no `#` or `##` headings.
- Say how hardware behaviour was established, as everywhere else: "not yet
  run on the laptop" is fine to write.

The release lists fragments newest first, by the commit that added them.

## The version number

`VERSION` holds `MAJOR.MINOR.PATCH`. The Conventional Commit subjects merged
since the last release decide the bump (`tools/release.py level`):

| Commits since the last release | Bump |
| :--- | :--- |
| any `feat` | minor |
| anything else (`fix`, `perf`, …) | patch |
| a `!` (`feat(cli)!:`) or a `BREAKING CHANGE:` footer | major, but minor before 1.0 |

Before 1.0 the interfaces (`duo` subcommands, config keys, home-manager
options) are still allowed to change, so a breaking change does not reach
1.0 by accident ([PLAN.md, "Z. 1.0"](../docs/PLAN.md)). 1.0.0 is cut on
purpose: Actions → ci → Run workflow, with `1.0.0` as the version.

## Locally

```bash
make changelog                     # preview the next section and its version
python3 tools/release.py check     # validate the fragments (make test does too)
```

`duo version` on a git checkout says how far it is past the release:
`0.10.0+3.g1a2b3c4` is three commits on, `.dirty` has uncommitted edits.
