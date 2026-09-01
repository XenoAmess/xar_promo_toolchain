# Generic capture, evidence, audio, and claims contracts

Version 0.2 adds reusable contracts for projects that already have a capture
producer. It does not turn `xar-promo` into a recorder, OCR engine, nonlinear
editor, or publishing client.

The boundary is intentional:

```text
project producer/adapter
  -> generic capture receipt and evidence-role checks
  -> preserve exact bytes
  -> project composer and generic audio/render planning
  -> technical audit
  -> project semantic audit
  -> human review/sign-off
```

The generic package proves how bytes and references fit together. A project
proves what the captured product means.

## Capture receipt

`xar_promo.capture` defines the producer-neutral `CaptureReceipt` contract. A
receipt records a producer identity, one or more exact source-file bindings,
the media time base and duration, ordered marks, named clean spans, and
references to preserved evidence artifacts. Every file is bound by a relative
project path, byte count, and SHA-256. `verify_unchanged()` can be called after
long-running composition to detect source mutation.

The bundled `capture-receipt-v1.schema.json` describes the portable shape. It
does not contain product vocabulary. In particular, fields such as `natural`,
`staged`, `hud_ready`, `game_state`, card names, or loading-screen policy are
project-side extensions and must be kept in a hash-bound adapter sidecar.

Marks are ordered and spans must have positive duration and remain inside the
declared media duration. A project may choose whether overlapping spans are
allowed; a preset should make that policy explicit. The generic validator does
not decide whether a span is a good shot, whether a UI is acceptable, or whether
the producer captured a real rather than staged interaction.

The existing CK3 phase-two contract remains unchanged. It is a ZhongGuo/CK3
compatibility format, not a template for another project. A CK3 adapter may
project its legacy bundle into the generic receipt; existing v1 readers and
fixtures remain supported.

## Evidence roles (v2)

Evidence plans and bundles can declare their required roles per sample instead
of assuming the historical fixed `frame` and `ocr` pair. The v2 resources are:

- `evidence-plan-v2.schema.json`
- `evidence-bundle-v2.schema.json`

Roles are portable identifiers. The generic layer checks that the declared
roles, producer metadata, artifact paths, byte counts, and SHA-256 values close
without mutation. It does not execute OCR, inspect a game API, or infer a
semantic result from an image.

The legacy `build_sampling_plan`/`write_evidence_bundle` APIs remain available.
Use `build_sampling_plan_v2`, `validate_sampling_plan_v2`,
`write_sampling_plan_v2`, and `write_evidence_bundle_v2` when a project needs
per-sample role requirements or producer overrides; v2 loaders dispatch by the
document's `format_version`.

For example, a game integration may declare:

```text
frame.begin
frame.end
tooltip.ocr
state.before
state.after
action.receipt
runtime.manifest
```

Those names are still only references at the xAR layer. The integration owns
the validator that explains whether a state transition demonstrates its claim.
Evidence produced outside the project root, stale files, duplicate role IDs,
or changed source bytes remain RED and are never repaired in place.

## Claims and review binding

`claims-review-v1.schema.json` describes a structural sidecar for claims and a
review checklist. `ClaimBinding` links a stable `claim_id` to evidence artifact
IDs and a validator identity/result; `ReviewChecklist` and `ReviewResponse`
carry human row-level responses. Each response also carries
`checklist_sha256`, the canonical SHA-256 of the normalized checklist
document; matching `checklist_id` values alone are not sufficient, so changing
a row, required flag, deliverable binding, or metadata invalidates the
response. A pre-hash v1 response can still be parsed for archival inspection,
but validation classifies it as `legacy_unbound` and never accepts it as an
approval; create a new response with the intended checklist's explicit digest
before passing it to `write_review_response` (the writer never auto-binds an
untrusted legacy record).
The generic checks are limited to:

- unique and portable IDs;
- references that exist in the current run;
- exact artifact byte/hash bindings;
- recorded validator metadata;
- exact checklist content/hash binding (in addition to its stable ID);
- a review response bound to the exact deliverable bytes.

The claim text, truth criteria, acceptable wording, and semantic validator are
project-owned. A generic `pass` result never creates a human sign-off, and a
review checklist never substitutes for watching the exact deliverable.

## Multi-track audio

Projects may describe narration, gameplay, BGM, and SFX as typed audio stems.
The generic audio planner supports source binding, trim, delay, gain, fades,
and deterministic ducking, plus the output sample-rate/channel contract. The
channel contract is deliberately limited to the standard layouts from mono
through 7.1 (1..8 channels); each delayed stem is normalized to its explicit
layout before `adelay`, so every output channel receives the declared delay on
the older FFmpeg builds used by supported integrations. Unsupported channel
counts are rejected during planning rather than silently downmixed. The
planner records the resulting FFmpeg invocation/filter graph for auditability.

The project still chooses the music, licensing, language, voice, loudness
policy, and artistic mix. The legacy single-audio render path remains valid;
projects can migrate to the multi-track planner without changing the stable
`PipelineComposer` callable ABI.

## Project integration checklist

An integration should:

1. produce and retain raw media and sidecars before invoking xAR;
2. validate its own domain fields and project-specific claims;
3. project valid spans and evidence roles into generic types;
4. preserve all successful, failed, and superseded attempts;
5. pass a fresh, byte-bound deliverable through technical audit, semantic audit,
   human review, explicit sign-off, and offline export.

It must not add game-specific fields to the generic schemas, import a game API
from the generic package, or copy the generic retention and sign-off logic into
the project repository.

## Installation and package data

All JSON schemas under `xar_promo/schemas/` are included by the wheel and source
distribution. The installed `install-contract-v1.json` lists the exact
resources and can be queried with `importlib.resources`. A 0.2.0 release is
prepared in source metadata; a GitHub release is created only by the tagged
release workflow after the complete compatibility and packaging checks pass.
