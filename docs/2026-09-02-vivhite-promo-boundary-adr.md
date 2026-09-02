# ADR: Generic media contracts versus Vivhite production logic

Date: 2026-09-02

Status: Accepted

## Decision

The xAR package owns project-neutral media provenance and deterministic
orchestration only. It may validate bytes, paths, hashes, time, roles, claims
references, audio plans, technical probes, review responses, and deliverable
identity. It must not interpret Slay the Spire 2 state, card mechanics, OCR
content, renderer/window details, or the truth of a project claim.

The Vivhite repository owns the STS2 adapter and its capture contract, OBS/game
producer, semantic validators, storyboard and claim text, narration/subtitle
content, asset policy, and Workshop lifecycle. The adapter projects a verified
project receipt into xAR's generic `CaptureReceipt` and supplies opaque project
metadata only after its own identity checks.

## Reuse test for future extraction

A capability may move into xAR only when all of these are true:

1. Its public API contains no Vivhite, STS2, card, HP, Brain, or Vulkan domain
   terms.
2. At least two independent media projects have a concrete reuse case.
3. Its validation is limited to structure, bytes, time, and references; domain
   truth remains an injected project validator.
4. It does not require a recorder, OCR engine, full NLE, platform account, or
   game-specific transport.
5. Existing v0.1 CLI, `RunManifest`, and `PipelineComposer` call signatures
   remain compatible.

STS2 transport remains outside the core until a second independent consumer
exists. If that happens, extract a separate plugin rather than adding STS2
semantics to this package.

## Current implementation boundary

The reusable modules are `xar_promo.capture`, configurable evidence roles,
audio mix planning (including explicit `normalize` control), claim bindings,
review checklist structures, technical audit, and export provenance. The
Vivhite-specific modules remain in its repository and are consumed through its
adapter.

The local `0.2.0` wheel/sdist have been built and tested from the current
working tree. Publication/tagging is intentionally a separate release action;
the Vivhite run records the exact executable and package provenance it used.
