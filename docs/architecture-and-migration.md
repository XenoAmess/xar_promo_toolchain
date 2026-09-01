# Architecture and migration

## Scope and current boundary

`xar_promo_toolchain` is the reusable metadata, evidence-retention, producer-neutral capture receipt, configurable evidence-role, claims-binding, TTS, layout, media-process, pipeline, audit, review, and offline-export foundation for promotional-video pipelines. Its ten-command CLI covers the project/run lifecycle plus dependency-injected plan/build, automated audit, pending human review, explicit sign-off, and local release-bundle export. Projects still own their adapter, preset, `PipelineComposer`, executables, and content policy; the package is not a recorder, OCR engine, timeline editor, or publication service.

This is a standalone repository. The `tools/` and `mod_zhongguo_style/` paths
used in the migration notes below are historical paths in the CK3 integration
repository ([`XenoAmess/ck3_eternal_recurrence`](https://github.com/XenoAmess/ck3_eternal_recurrence));
they are not required parent directories for this package. The checked-in
standalone integration sample is `examples/zhongguo-361-iteration1/`.

This distinction is important: a valid manifest, a preserved MP4, or a command acknowledgement is evidence about that individual operation. None of them, by itself, means that a video passed content review or was published.

## Three-layer architecture

```text
project preset (story, voice, languages, duration, release policy)
                         |
                         v
domain adapter (for example, verified CK3/Vivhite capture marks and clean spans)
                         |
                         v
generic package (config/run models, capture/evidence contracts, hashes, retention,
                 TTS/audio planning, claims/review binding, sign-off)
```

The dependency direction is downward. Generic code must not import a CK3 project, and the CK3 adapter must not import a ZhongGuo-specific preset.

| Layer | Current home | Owns | Must not own |
| --- | --- | --- | --- |
| Generic package | `src/xar_promo/`, including `capture.py`, `evidence.py`, `claims.py`, and `tts/` | `ProjectConfig`, `RunManifest`, typed runlog, per-run config snapshots, content-addressed artifacts, producer-neutral capture receipts, configurable evidence roles, structural claim/review bindings, provider-neutral TTS, storyboard/source/media/layout primitives, dependency-injected pipeline execution, audio planning, automated-audit records, pending review packages, explicit sign-off, and offline allowlisted export | CK3/STS2 process automation, product claims, brand copy, a fixed voice/language pair, project composition, OCR execution, recording, or publication credentials |
| CK3 adapter | `src/xar_promo/adapters/ck3/` | Read-only verification and projection of a CK3 capture bundle: report, evidence index, raw capture, ordered marks, independently evidenced clean spans, and an end-of-operation source-integrity check | OCR, capture orchestration, repairing a failed run, choosing a story, or deciding whether a particular mod claim is sufficiently demonstrated |
| Project preset | `src/xar_promo/presets/`, a standard checked-in `ProjectConfig`, and the project's legacy wrappers during migration | Chapter order, narration, subtitles, visual identity, required CK3 marks/spans, real-character policy, voice, duration limit, humor/tone, and release gates | Changes to generic schemas or CK3 evidence semantics merely to accommodate one campaign |

The first concrete preset is `xar_promo.presets.zhongguo_361_phase2`. It reads a
project-supplied `ProjectConfig` (the CK3 integration repository currently keeps
the canonical file at `mod_zhongguo_style/promo/phase2-promo-project.json`),
derives CK3 span/mark requirements from configured chapters, fixes the
`zh-CN-XiaoxiaoNeural` request, and validates the sequel's duration,
historical-character provenance, and clean-UI attestations. It deliberately
returns a non-release-ready capture candidate while the project-specific live
matrix and full-duration human review remain outstanding. Rendering and the
established release workflow still enter through the integration repository's
`mod_zhongguo_style/tools/` during migration.

The acceptance runner exposes a separate `--phase2-promo-capture` producer mode for
that preset.  It uses the eight-span contract in
`schemas/phase2-capture-contract-v1.schema.json`; the legacy `--promo-capture`
route remains phase-one-only.  Until a real phase-two visual choreography is
registered, the new mode fails before preflight/CK3/FFmpeg with a typed RED.  A
static contract or MCP-only phase-two run cannot be promoted to video footage.
The future integration point is the runner's
`register_phase2_promo_capture_producer(producer)` callable; it owns the
gameplay choreography, starts the recorder only after the HUD is visible, and
must emit all eight mapped clean spans before the preset can consume the run.

When the phase-two capture is produced by the frozen seed runner, the project
builder can bind its `--seed-preflight-report` to the runner's
`--preflight-only` `preflight.json`.  The project layer verifies the report's
schema, GREEN/no-launch invariants, immutable checks, and report-to-artifact
root self-consistency, then records the report's exact bytes and SHA-256 in the
candidate provenance (and preserves it as a raw run artifact).  The later
capture may be a sibling attempt: when its timeline exposes the frozen source
commit or clean-source/tree hash, those shared values are compared strictly;
older timelines may supply the same identity through the capture root's
GREEN `report.json` runtime projection (`cell.runtime_tree_before_sha256` and
`product_runtime_manifest.tree_sha256`), which is read-only and hash-bound;
conflicting sources are rejected.  If neither source exposes a shared value,
the candidate carries a typed `capture_identity_unbound` blocker.
The report remains a required capture artifact even when the timeline already
has a matching identity: a missing `report.json` keeps the binding explicitly
`unbound` because the CK3 adapter cannot verify a capture without that report.
This is an upstream input gate only; it does not promote a capture to live
evidence or replace the runtime matrix and human review gates.

## Project configuration and run evidence are separate

One checked-in `promo-project.json` describes intent. Each attempt receives its own `runs/<run-id>/run-manifest.json`, an immutable content-addressed snapshot of the then-current config, and evidence directories.

### `ProjectConfig`

`ProjectConfig` contains repeatable, reviewable authoring intent:

- stable project identity and title;
- selected adapter and preset identifiers;
- narration and subtitle locales;
- duration constraint;
- chapters, cues, and their declared artifact references.

It does not contain a capture result, output hash, audit result, or reviewer decision. A change to narration, chapter order, policy, or constraints changes the project-config bytes and therefore produces a different binding for subsequent runs.

### `RunManifest`

`RunManifest` is a per-attempt append-only evidence ledger. It binds the exact run-local project-config snapshot by relative path, byte count, and SHA-256, then records:

- ordered phase history;
- immutable raw and derived artifact identities;
- ordered audit records;
- ordered human sign-offs bound to exact artifact bytes;
- the artifact-retention policy for the run.

"Append-only" describes the logical record. A command may atomically replace the current JSON representation in order to append a row, but it must not rewrite an earlier fact into a different fact. Before mutation, the previous manifest bytes belong in content-addressed manifest history. Sequence numbers are ordered and gap-free, and a failed attempt stays a failed attempt rather than being edited into GREEN.

The separation prevents two common ambiguities:

1. Changing the checked-in plan cannot invalidate or silently reinterpret evidence captured for an older run; `start-run` snapshots the changed plan for a new run.
2. Retrying one phase cannot erase which bytes were captured, rendered, audited, or reviewed in an earlier attempt.

At version 0.2, `plan`, `build`, and `audit` call the same generic handlers while
capture receipts, configurable evidence roles, structural claims, and optional
multi-track audio are available to project composers. `plan` is strictly
read-only; `build` executes a project-composed invocation and retains both
successful and failed attempt material; `audit` preserves evidence/report bytes
and appends only a typed automated result. A required `--composer
MODULE:ATTRIBUTE` keeps project composition outside the generic schema, while
adapter and preset IDs resolve through standard entry-point groups. Existing
project wrappers remain valid migration entry points and may delegate
incrementally.

## Process-material retention

The v1 run policy is `append-only-content-addressed` with distinct raw, derived, and manifest-history directories. `preserve_process_material` is required to be true.

The retention rule covers more than the final MP4. Keep, when produced:

- original recordings, screenshots, evidence indexes, capture reports, and timelines;
- narration scripts, TTS requests, returned audio, provider metadata, and cache entries;
- subtitle source and rendered subtitle files;
- chapter segments, intermediate renders, concat inputs, logs, sidecars, and audit samples;
- failed or superseded attempts and the reports explaining their status;
- final deliverables, their byte counts and SHA-256 values, and reviewer sign-offs;
- the run-local snapshot of the exact project config used by that attempt;
- every prior run-manifest preimage saved by a manifest mutation.

Preservation copies a source into immutable storage; it does not move, truncate, or delete the source. Reusing an identical SHA-256 may reuse the stored bytes, but it must not collapse distinct semantic records or distinct run attempts. Clean-up and publication are separate, explicitly authorized operations outside this package.

## CK3 adapter contract

`xar_promo.adapters.ck3.load_capture_bundle(...)` consumes an existing capture-artifact root and performs read-only verification. The current adapter requires the producer's GREEN report and evidence index, a timeline that identifies real CK3 footage after the gameplay HUD, explicit loading-screen exclusion, a raw recording whose bytes match the index, ordered start/stop marks, and independently hash-bound begin/end evidence for every projected clean span.

A project supplies `required_span_ids` and `required_mark_labels`; this keeps campaign vocabulary in the preset. The adapter returns typed paths and timeline projections only after the evidence closes. `CaptureBundle.verify_unchanged()` can be called after a long pipeline to detect mutation of any load-time source record; it never uses OCR to guess missing state, mutates producer artifacts, or deletes rejected attempts.

The adapter does not launch CK3 or record the desktop. Those operations remain responsibilities of the CK3 acceptance/capture runner, which is a producer upstream of this toolchain.

## Version 0.2 generic contracts (prepared, not yet tagged)

The 0.2 contract extraction is deliberately domain-neutral. A producer writes a
`CaptureReceipt` containing exact source-file bindings, a media time base and
duration, ordered marks, named clean spans, and evidence references. The generic
capture loader validates relative paths, byte counts, SHA-256 values, ordering,
positive span durations, and the declared overlap policy. It can re-check the
same files with `verify_unchanged()` after composition. It does not launch a
recorder, execute OCR, inspect a game API, or classify a span as natural/staged.

Evidence plan/bundle v2 keeps the v1 wire shape compatible while making required
roles configurable per sample. A caller can require `frame.begin`,
`tooltip.ocr`, `state.before`, or any other portable role; xAR verifies role
presence and producer/artifact/hash closure only. The meaning and validator for
each role remain in the project adapter. The CK3 phase-two schema is retained as
its own legacy format and must not be extended with another project's fields.

The implementation keeps the legacy `build_sampling_plan` and
`write_evidence_bundle` entry points intact. Integrations that need configurable
roles use `build_sampling_plan_v2`, `validate_sampling_plan_v2`,
`write_sampling_plan_v2`, `write_evidence_bundle_v2`, and the corresponding
v2-aware loaders. This is a data-contract version, not a new CLI command.

The claims/review sidecar binds a stable claim ID to evidence artifact IDs,
validator metadata, and a result, and can carry a structured checklist response.
This is a referential-integrity contract, not a truth engine: xAR never decides
whether a product statement is true and never turns an automated result into a
human sign-off.

The audio planner accepts typed stems and deterministic operations (trim, delay,
gain, fade, and ducking) and records the resulting media command plan. A project
still owns voice, language, music selection, rights, loudness policy, and artistic
mix decisions. The legacy single-audio renderer and `PipelineComposer` ABI remain
valid during migration.

The new schema resources are packaged in both wheel and sdist:

```text
xar_promo/schemas/capture-receipt-v1.schema.json
xar_promo/schemas/evidence-plan-v2.schema.json
xar_promo/schemas/evidence-bundle-v2.schema.json
xar_promo/schemas/claims-review-v1.schema.json
```

`install-contract-v1.json` is updated with this resource list and the 0.2.0
artifact names. The schema filename remains v1 where the capture wire contract
is new but stable; the historical CK3 phase-two filename is unchanged for
compatibility.

## Example boundary: a game-specific integration

For a game project such as Vivhite, the integration repository owns the
producer, preset, and semantic validator. It may add a sidecar with shot IDs,
game version, state/action receipts, overlay policy, natural/staged status,
card or mechanic claims, bilingual script, and release metadata. Its adapter
projects only the verified files, marks, spans, and evidence roles into the
generic contracts above.

The generic package must not import the game API, know card or HP rules, start
Vulkan/OBS, hide game overlays, or decide that a mechanic was demonstrated.
Conversely, the integration must reuse xAR's retention, audio plan, audit,
review, sign-off, and export primitives instead of maintaining a parallel
implementation. This keeps the same capture/evidence boundary useful for CK3,
gameplay, software-demo, and other future projects.

## Legacy-wrapper migration

The current, working production path remains authoritative during migration:

- `tools/build_full_agent_showcase.py` is the older reusable showcase builder;
- `mod_zhongguo_style/tools/build_promo_video.py` layers ZhongGuo-specific rendering and release policy over that builder;
- `mod_zhongguo_style/tools/prepare_promo_release_manifest.py` projects a verified capture into the existing release-candidate manifest;
- `mod_zhongguo_style/tools/export_promo_script.py` and `validate_promo_video.py` keep their existing export and audit interfaces.

Migration is incremental:

1. Freeze the existing command lines, manifests, output/sidecar shapes, and regression tests as the compatibility baseline.
2. Introduce `ProjectConfig` plus a new run per attempt. Bind, rather than overwrite, the legacy manifest and sidecars as artifacts while both formats coexist.
3. Route already-extracted concerns through the package: content-addressed retention, cached TTS requests, read-only CK3 capture verification, and the isolated ZhongGuo phase-two preset.
4. Delegate suitable wrapper stages to the package's frozen storyboard/source/media/pipeline/audit/review interfaces after byte, visual, and CLI parity tests cover each legacy surface. The wrappers keep translating their existing arguments and remain responsible for project policy.
5. Deprecate a legacy entry point only after every repository caller has moved, equivalent output has been demonstrated, retained historical projects still validate, and the replacement has a documented rollback path.

This is not a big-bang rewrite. Existing wrappers remain callable, and existing process assets remain where they are. A compatibility wrapper may delegate to package code, but it must preserve exit-code meaning, offline validation behavior, source immutability, and existing output records. It must never relabel generated cards as gameplay or turn a missing project gate into a generic-package GREEN.

## Non-goals

Version 0.2 still does not attempt to:

- replace the CK3/STS2 automation or capture runner;
- infer game state or content claims with OCR;
- provide a timeline editor, nonlinear editor, or general media-asset manager;
- guess adapter/preset factory signatures, hide project composition inside the generic schema, or provide a one-size-fits-all editing policy;
- embed ZhongGuo narration, real-character rules, `zh-CN-XiaoxiaoNeural`, bilingual layout, or the 20-minute cap in the generic core or CK3 adapter instead of its project preset;
- approve a video automatically or manufacture a human sign-off;
- delete legacy manifests, failed takes, caches, or intermediate material;
- upload to Steam Workshop, a video platform, or any other external service.

## Validation, release, and publication boundaries

The following gates are deliberately separate:

| Gate | Meaning | Does not mean |
| --- | --- | --- |
| Structure/authoring validation | Configuration and currently required references are well-formed for continued authoring | The video exists or is release-ready |
| Release-profile validation | The package's release metadata requirements are satisfied for the checked artifact set | Project-specific visual/content audit passed |
| Project audit | A project-owned validator checked its actual policy, media, subtitles, and evidence | A human approved the result |
| Human sign-off | A named reviewer recorded a decision against exact preserved bytes | The artifact was uploaded |
| Publication | An explicitly authorized external workflow uploaded the approved bytes | Earlier process material may be deleted |

Package publication to a Python index, a mod release to Steam Workshop, and publication of a promotional video are three different release operations. None is performed by `xar-promo`. A future publisher must require explicit authority and must consume the exact approved artifact identity; it must not rebuild or substitute bytes after sign-off.
