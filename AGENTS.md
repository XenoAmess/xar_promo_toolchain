# XAR Promo Toolchain

This directory is the standalone repository for `xar-promo-toolchain`.  It is
not a CK3 mod and must remain usable without a checkout of the parent
`ck3_eternal_recurrence` repository.

## Scope and release channel

- The package, CLI, schemas, tests, documentation, and examples in this
  repository are the complete standalone deliverable.
- Publish releases to the GitHub repository
  `XenoAmess/xar_promo_toolchain`; do not publish to PyPI unless the owner
  explicitly requests that channel.
- Release tags use `v<version>` (for example `v0.1.0`).  Build the wheel and
  sdist from the tagged commit, run `twine check`, and attach SHA-256 sums to
  the GitHub Release.
- A package release is a toolchain release.  It does not claim that a CK3
  capture, a live review, or a finished promotional film exists unless a
  repository-local artifact explicitly proves it.

## Development

- On every machine, immediately after reading this file and before beginning
  repository work, run `python scripts/ensure_suno_engineer_skill.py` (or
  `py -3 scripts/ensure_suno_engineer_skill.py` on Windows). This idempotently
  installs the required `suno-engineer` Codex skill from pinned upstream commit
  `b1b11a67e8ce5dd07691b68cf736cff8e4c55c87` when it is missing and verifies
  exact file hashes when it is present. Do not continue after a conflict or
  verification failure. The skill is a Codex authoring dependency, not a
  Python package/runtime dependency; see `docs/music-authoring.md`.
- Requires Python 3.11 or newer.  Install editable code with
  `python -m pip install -e .` and optional rendering/TTS extras only when
  needed (`.[visual,tts]`).
- Run the focused suite with `python -m unittest discover -s tests`; use
  `python -O -m unittest discover -s tests` for the optimized-path check.
- Build with `python -m build`; inspect the sdist allowlist and package data
  before publishing.  Keep generated schema files and release metadata in
  sync with their source generators.
- `examples/zhongguo-361-iteration1/` is an offline capability demonstration
  copied from the first ZhongGuo iteration.  It is intentionally separate
  from any phase-two project and must not be described as a completed live
  video.
- Parent projects may use `XAR_PROMO_TOOLCHAIN_SOURCE` to point at a local
  checkout during development.  Normal consumers should use the published
  GitHub Release artifact.

## Git workflow

- Keep commits focused and use concise English commit messages.
- After a requested work package is complete, commit and push the standalone
  repository unless the owner gives a different instruction.
- Do not add parent-repository paths, CK3 caches, credentials, generated build
  directories, or large untracked media to the release by accident.
