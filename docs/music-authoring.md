# Music authoring environment

`suno-engineer` is the required Codex skill for designing or refining Suno
V5/V5.5 prompts used by promotional-video projects. It is an authoring
dependency, not a Python dependency of `xar-promo-toolchain`, and is therefore
installed into the user's Codex home rather than a project virtual environment.

The required upstream source is
[`skills/suno-engineer/SKILL.md`](https://github.com/bitwize-music-studio/claude-ai-music-skills/blob/main/skills/suno-engineer/SKILL.md)
from `bitwize-music-studio/claude-ai-music-skills`, pinned by this repository to
commit `b1b11a67e8ce5dd07691b68cf736cff8e4c55c87`.

## Automatic per-machine bootstrap

Run the repository bootstrap once at the beginning of work on every machine.
It is idempotent: an exact installed copy exits successfully without network
access; a missing copy is downloaded from the pinned GitHub commit, installed
under `$CODEX_HOME/skills/suno-engineer` (or `~/.codex/skills/suno-engineer`),
and verified by SHA-256.

```powershell
# Windows PowerShell
py -3 scripts/ensure_suno_engineer_skill.py
```

```sh
# Linux or macOS
python3 scripts/ensure_suno_engineer_skill.py
```

For an offline/read-only preflight, use `--check`:

```sh
python3 scripts/ensure_suno_engineer_skill.py --check
```

The check exits `0` only when the exact pinned files are present. A missing
installation exits `1`. An existing installation with different or additional
files exits `2`; the bootstrap deliberately leaves those local bytes untouched
instead of silently replacing a customized skill. Resolve that conflict, then
run the bootstrap again. After a first installation, begin a new Codex turn or
session so skill discovery can refresh.

The pinned installation contains `SKILL.md` and `genre-practices.md`. The
bootstrap never installs or restores the former AceDataCloud `suno-music`
skill. For instrumental soundtrack work, upstream `suno-engineer` explicitly
does not require its `lyric-writer` prerequisite.

## Capability boundary

Installing the skill supplies prompt-engineering instructions only. It does
not create a Suno account, grant service credentials, install a Suno MCP
server, purchase generation credits, or prove that generated audio is fit for
release. The project still has to retain the exact prompt/settings, generated
audio, selection decision, edits, attribution or rights record, media probes,
automated audit, and byte-bound human sign-off required by its own workflow.

Do not embed this Codex-home bootstrap into package import, wheel installation,
`xar-promo` runtime commands, or CI test collection. Those paths must remain
offline and free of writes outside their declared target. `AGENTS.md` is the
machine-start contract for Codex workers; this document is the human and
automation reference.
