### Releases, a version per merge, and `duo version` on a checkout

- every merge that changes what the project does is now a release: CI writes
  its `CHANGELOG.md` section, bumps `VERSION`, tags `vX.Y.Z` and publishes a
  GitHub release with the same notes, so `duo report` names a version you
  can look up
- on a git checkout (`./install.sh --dev`), `duo version` and `duo doctor`
  add how far the code is past that release, for example `0.10.0+3.g1a2b3c4`,
  and `.dirty` for uncommitted edits
