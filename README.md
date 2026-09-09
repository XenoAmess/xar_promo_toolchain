# XAR Promo Toolchain

`xar-promo-toolchain` is a reusable foundation for promotional-video pipelines. Version `0.2.0` adds producer-neutral capture receipts, configurable evidence roles, structural claim/review bindings, and multi-track audio planning; version `0.2.1` fixes delayed-stem fade ordering so narration remains audible after an output delay. The package retains separate project/run contracts, immutable process-material retention, provider-neutral TTS, dependency-injected media orchestration, automated-audit and human-review boundaries, and offline release-bundle export. A project supplies its own adapter, preset, and `PipelineComposer`; this is an orchestration toolchain, not a recorder, OCR engine, timeline editor, or upload service.

The real CLI has ten commands:

- `init` creates `promo-project.json` and the first bound run;
- `start-run` creates another run with an immutable snapshot of the current config;
- `validate` checks a native config/run or a legacy showcase v1 manifest;
- `preserve` copies bytes into content-addressed run storage and appends an artifact;
- `signoff` records a named human decision bound to exact preserved bytes;
- `plan` composes and validates a pipeline attempt with zero writes;
- `build` runs a composed pipeline and preserves GREEN or RED material;
- `audit` creates and appends an automated audit without human approval;
- `review` plans or creates a pending human-review package;
- `export` validates or creates an offline, allowlisted release bundle.

There is no `publish` or upload command. `audit`, `review`, and `signoff` are intentionally separate operations.

### 0.2.0 status

Version 0.2.0 is released on GitHub with generic capture receipts,
evidence-plan/bundle v2, structural claims and review sidecars, and multi-track
audio planning. The canonical wheel, source distribution, and `SHA256SUMS` are
the assets in the [v0.2.0 GitHub Release](https://github.com/XenoAmess/xar_promo_toolchain/releases/tag/v0.2.0);
the package is intentionally not published to PyPI or another public Python
index. The release was cut from annotated tag `v0.2.0`; that tag is currently
not cryptographically signed, so consumers requiring signed-tag provenance
must apply their own additional trust gate.

## Release artifact handoff

The release workflow builds and checks the wheel and source distribution,
validates the sdist contents, performs isolated installs of both artifacts,
writes `SHA256SUMS`, and uploads the checked files as a GitHub Actions artifact.
For a `v<version>` tag it also publishes those exact files as a GitHub Release;
manual workflow runs remain artifact-only. The historical `v0.2.0` tag run
passed the workflow; the current `v0.2.1` tag run passed it as well and
published the checked bytes in the v0.2.1 release below. The workflow does not
publish to PyPI or another package index, upload media, or start a game/recorder. See
[Installation and cross-machine delivery](docs/installation.md) for the exact
artifact, reproducibility, and trust boundaries.

### 0.2.1 status

Version 0.2.1 is the patch release for delayed-stem fade ordering. It is cut
from the `v0.2.1` tag and is published as the [v0.2.1 GitHub
Release](https://github.com/XenoAmess/xar_promo_toolchain/releases/tag/v0.2.1)
after the same packaging and compatibility workflow passed. The release is not
published to PyPI; consumers should verify its `SHA256SUMS` and pin the exact
artifact. The published artifact digests are:

```text
f8de0711415e7fce2bf07a34d3db4edc0593f32ba1cb61034946665e27014621  xar_promo_toolchain-0.2.1-py3-none-any.whl
56a712c03078a67a856f861d8c619dcdbd17967a4f26645c9c6e1643d14e4ad5  xar_promo_toolchain-0.2.1.tar.gz
```

## Install and inspect

Python 3.11 or later is required.

For Windows, Linux, macOS, wheel/sdist/source/editable installs, optional
dependencies, offline wheelhouses, verification, upgrades, and uninstall, see
[Installation and cross-machine delivery](docs/installation.md). Automation can
read the installed machine contract at
`xar_promo/schemas/install-contract-v1.json` through `importlib.resources`.
Codex contributors must also run the idempotent, repository-pinned
`scripts/ensure_suno_engineer_skill.py` bootstrap described in
[Music authoring environment](docs/music-authoring.md) once per machine.

```powershell
py -m pip install -e .
py -m xar_promo --help
py -m xar_promo init --help
py -m xar_promo start-run --help
py -m xar_promo validate --help
py -m xar_promo preserve --help
py -m xar_promo signoff --help
py -m xar_promo plan --help
py -m xar_promo build --help
py -m xar_promo audit --help
py -m xar_promo review --help
py -m xar_promo export --help
```

The core has no required dependency outside the standard library. Install only the optional slice an integration needs:

```powershell
py -m pip install -e ".[tts]"     # edge-tts 7.2.8
py -m pip install -e ".[visual]"  # Pillow 12.3.0
py -m pip install -e ".[render]"  # Pillow 12.3.0
```

`edge-tts` is loaded only for live Edge synthesis. FFmpeg and ffprobe are external executables and are not Python dependencies.

## Project and run lifecycle

Create authoring intent and a first run without overwriting existing files:

```powershell
xar-promo init C:\work\my-promo `
  --project-id my-promo `
  --title "My Promo" `
  --run-id capture-001 `
  --narration-locale zh-CN `
  --subtitle-locale zh-CN `
  --subtitle-locale en

xar-promo validate C:\work\my-promo\promo-project.json
xar-promo validate C:\work\my-promo\runs\capture-001\run-manifest.json
```

The `init` defaults `adapter=generic` and `preset=default` are neutral scaffold identifiers, not bundled production components. Before `plan`, `build`, or `audit`, edit the checked-in config to the IDs supplied by an installed integration plugin. Plugins publish callables through the `xar_promo.adapters` and `xar_promo.presets` Python entry-point groups.

After changing the config, start a new run. An older run keeps its original content-addressed config snapshot.

```powershell
xar-promo start-run C:\work\my-promo\promo-project.json --run-id render-001
```

`promo-project.json` owns intent: project identity, adapter/preset IDs, locales, constraints, chapters, and cues. `runs/<id>/run-manifest.json` is an append-only evidence ledger containing a config snapshot binding, typed phase history, immutable artifacts, typed automated audits, and explicit human sign-offs. Every mutation archives the prior manifest bytes before atomic replacement.

## Capture, evidence, audio, and claims boundary

Version 0.2.0 keeps capture production and product meaning outside the generic
package. A project or external recorder emits a hash-bound
`xar-promo-capture-receipt`; xAR validates its producer metadata, source bytes,
ordered marks, clean spans, and evidence references. It does not launch a game,
record a window, run OCR, or decide whether a span demonstrates a claim.

Evidence plans can use v2 roles per sample instead of the historical fixed
`frame`/`ocr` pair. A project may therefore bind roles such as
`state.before`, `state.after`, or `action.receipt` while keeping their meaning
in a project-owned validator. `claims-review-v1` provides structural
`claim_id`/evidence/validator bindings and review checklists; it never converts
an automated result into a human approval.

The multi-track audio planner accepts typed narration, gameplay, BGM, and SFX
stems with trim, delay, gain, fade, and deterministic ducking. Music choice,
rights, voice, language, loudness policy, and aesthetic decisions remain in the
project composer. The old single-audio render API and `PipelineComposer` ABI
remain compatible.

See [Generic capture, evidence, audio, and claims contracts](docs/capture-evidence-audio-claims.md)
for the schema resources, migration rules, and integration boundary.

## Plan and build

The project provides a callable implementing the documented `PipelineComposer` ABI:

```powershell
xar-promo plan C:\work\my-promo\promo-project.json `
  --workdir C:\work\my-promo\attempts\plan-001 `
  --composer my_project.promo:compose `
  --validate-only

xar-promo build C:\work\my-promo\runs\render-001\run-manifest.json `
  --workdir C:\work\my-promo\attempts\render-001 `
  --composer my_project.promo:compose
```

`plan` is always read-only, whether or not the explicit `--validate-only` marker is present. It does not create the workdir, invoke TTS/FFmpeg, or mutate a run. `build` requires a native run and preserves complete outputs plus partials, stdout/stderr, and diagnostics from a failed attempt before appending typed phases.

The composer is loaded with strict `MODULE:ATTRIBUTE` syntax. The CLI passes resolved adapter and preset factories to it without invoking them or guessing their signatures. The composer owns concrete media dependencies and executable selection.

## Audit, review, sign-off, and export

`review --probe` accepts a byte-bound v1 envelope, not bare ffprobe JSON. Create
it from a live ffprobe invocation with the public producer; the command audit,
stdout, stderr, and envelope are retained at caller-selected paths:

```python
from pathlib import Path

from xar_promo import probe_and_write_bound_media

probe_and_write_bound_media(
    r"C:\tools\ffprobe.exe",
    Path(r"C:\exports\promo-final.mp4"),
    output_path=Path(r"C:\exports\promo-final.bound-probe.json"),
    audit_directory=Path(r"C:\exports\probe-audit"),
)
```

The envelope records the exact deliverable byte count and SHA-256 and embeds
the live ffprobe result. Bare, stale, or differently bound probes are rejected,
including in zero-write `--plan-only` mode.

```powershell
xar-promo audit C:\work\my-promo\runs\render-001\run-manifest.json `
  --subject-artifact-id promo-final `
  --evidence-bundle artifacts\evidence-bundle.json `
  --report artifacts\automated-audit.json `
  --report-artifact-id automated-audit-001

xar-promo review C:\exports\promo-final.mp4 `
  --storyboard C:\exports\storyboard.json `
  --probe C:\exports\promo-final.bound-probe.json `
  --output-directory C:\exports\review `
  --audit-directory C:\exports\review-audit `
  --ffmpeg C:\tools\ffmpeg.exe `
  --plan-only
```

`--plan-only` is optional preflight and is not review evidence. Run the real review command without that flag to extract frames and create the pending package:

```powershell
xar-promo review C:\exports\promo-final.mp4 `
  --storyboard C:\exports\storyboard.json `
  --probe C:\exports\promo-final.bound-probe.json `
  --output-directory C:\exports\review `
  --audit-directory C:\exports\review-audit `
  --ffmpeg C:\tools\ffmpeg.exe
```

The named human must then review the exact deliverable bytes in full at 1× speed and explicitly state an `approved` or `rejected` decision. Only after that statement may it be recorded:

```powershell
xar-promo signoff `
  --run-manifest C:\work\my-promo\runs\render-001\run-manifest.json `
  --artifact-id promo-final `
  --reviewer "Reviewer Name" `
  --decision approved

xar-promo export C:\work\my-promo\runs\render-001\run-manifest.json `
  C:\work\my-promo\dist\release-001 `
  --policy C:\work\my-promo\release-export-policy.json `
  --validate-only
```

`audit` appends only an automated result. `review` remains `pending-human-review`; neither a plan nor a generated review package is approval. Only `signoff` records the named reviewer's explicit decision. Release-profile validation requires ready chapters and a preserved deliverable whose latest explicit sign-off is approved. `export --validate-only` and `--dry-run` are no-write preflights; normal export copies a strict allowlist into a new local directory. Export never contacts a network service.

## Retention layout

```text
my-promo/
|-- promo-project.json
`-- runs/
    `-- render-001/
        |-- run-manifest.json
        `-- artifacts/
            |-- project-config/sha256/...
            |-- raw/sha256/...
            |-- derived/sha256/...
            `-- manifest-history/sha256/...
```

Preservation copies; it never moves, edits, or deletes source material. Failed and superseded attempts remain process evidence.

## Reusable layers

- `src/xar_promo/` contains models, validation, typed runlog operations, retention, capture/evidence/claims contracts, TTS, storyboard, visual-source, media/layout, pipeline, review, audit, and export primitives.
- `src/xar_promo/adapters/` contains domain adapters. The CK3 adapter verifies an existing capture bundle without OCR interpretation or mutation.
- `src/xar_promo/presets/` isolates project policy from the generic core and adapters.

See [Architecture and migration](docs/architecture-and-migration.md), [CLI and manifest v1](docs/cli-and-manifest-v1.md), [Pipeline composition](docs/pipeline-composition.md), [Human review and sign-off](docs/review-and-human-signoff.md), [Export and release bundles](docs/export-and-release-bundle.md), [Migrating existing builders](docs/migrating-existing-builders.md), and [Troubleshooting](docs/troubleshooting.md).

## Test

```powershell
py -m unittest discover -s tests -v
py -O -m unittest discover -s tests -v
```

The project is licensed under GPL-3.0-only.
