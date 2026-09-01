"""Deterministic evidence plans and hash-bound external evidence bundles.

This module deliberately does not extract frames or run OCR.  It records which
external adapter/tool is expected to produce each artifact, then binds the
files that adapter produced by byte count and SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ArtifactError, ManifestError
from .model import SourceRecord


# ``FORMAT_VERSION`` is intentionally kept at v1.  A number of consumers (and
# the v1 audit report) import this name as the legacy format marker.  Plan and
# bundle v2 use explicit constants below so adding the configurable-role
# contract does not silently change the old API's output.
FORMAT_VERSION = 1
PLAN_V2_FORMAT_VERSION = 2
BUNDLE_V2_FORMAT_VERSION = 2
# Friendly aliases for callers that want to feature-detect the new contract.
V2_FORMAT_VERSION = 2
FORMAT_VERSION_V2 = 2
EVIDENCE_PLAN_FORMAT_VERSION = 2
EVIDENCE_BUNDLE_FORMAT_VERSION = 2
PLAN_KIND = "xar_promo_evidence_plan"
BUNDLE_KIND = "xar_promo_evidence_bundle"
TIMESTAMP_QUANTUM = Decimal("0.000001")
REQUIRED_EVIDENCE_ROLES = ("frame", "ocr")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRODUCER_FIELDS = {
    "adapter_id",
    "tool",
    "tool_version",
    "operation",
    "execution",
}


class EvidenceError(ArtifactError):
    """Evidence is absent, stale, or does not satisfy its declared plan."""


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{context} must be an object")
    return value


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{context} must be an array")
    return value


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{context} must be a non-empty string")
    return value.strip()


def _identifier(value: Any, context: str) -> str:
    result = _string(value, context)
    if _IDENTIFIER.fullmatch(result) is None:
        raise EvidenceError(
            f"{context} must be a portable identifier containing only "
            "letters, digits, '.', '_' or '-'"
        )
    return result


def _only_keys(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if extras:
        raise EvidenceError(f"{context} has unsupported fields: {', '.join(extras)}")
    if missing:
        raise EvidenceError(f"{context} is missing fields: {', '.join(missing)}")


def _decimal(value: Any, context: str) -> Decimal:
    if isinstance(value, bool):
        raise EvidenceError(f"{context} must be a finite non-negative number")
    try:
        result = Decimal(str(value)).quantize(TIMESTAMP_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise EvidenceError(f"{context} must be a finite non-negative number") from exc
    if not result.is_finite() or result < 0:
        raise EvidenceError(f"{context} must be a finite non-negative number")
    return result


def _timestamp(value: Decimal) -> str:
    return f"{value:.6f}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise EvidenceError(f"could not hash artifact {path}: {exc}") from exc
    return digest.hexdigest().upper()


def _read_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise EvidenceError(f"{context} was not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(
            f"invalid JSON in {context}: {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise EvidenceError(f"could not read {context} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{context} JSON root must be an object")
    return value


def _write_new_json(path: Path, value: Mapping[str, Any], context: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise EvidenceError(f"refusing to overwrite existing {context}: {path}") from exc
    except OSError as exc:
        raise EvidenceError(f"could not write {context} {path}: {exc}") from exc


def normalize_external_producer(value: Any, context: str = "producer") -> dict[str, str]:
    """Validate metadata for a tool invocation performed outside this module."""

    row = _object(value, context)
    _only_keys(row, _PRODUCER_FIELDS, context)
    result = {key: _string(row.get(key), f"{context}.{key}") for key in sorted(_PRODUCER_FIELDS)}
    if result["execution"] != "external":
        raise EvidenceError(f"{context}.execution must be 'external'")
    return result


def _normalize_role(value: Any, context: str) -> str:
    """Normalize a v2 role while keeping role names portable.

    v1 accepted any non-empty role string in submissions.  That behaviour is
    intentionally left untouched; v2 role names are identifiers so they can
    safely be used as map keys by adapters from different projects.
    """

    return _identifier(value, context)


def _normalize_roles(
    value: Any,
    context: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)):
        raise EvidenceError(f"{context} must be an array")
    if isinstance(value, list):
        roles = value
    else:
        try:
            roles = list(value)
        except TypeError as exc:
            raise EvidenceError(f"{context} must be an array") from exc
    normalized = [_normalize_role(item, f"{context}[{index}]") for index, item in enumerate(roles)]
    if len(normalized) != len(set(normalized)):
        raise EvidenceError(f"{context} contains duplicate roles")
    if not normalized and not allow_empty:
        raise EvidenceError(f"{context} must contain at least one role")
    # Role order is not semantic.  Sorting makes plans independent of the
    # order in which a project adapter assembled its role list.
    return sorted(normalized)


def _normalize_producer_map(
    value: Any,
    context: str,
    *,
    allow_empty: bool = False,
) -> dict[str, dict[str, str]]:
    """Normalize a dynamic role -> producer mapping used by v2 plans."""

    row = _object(value, context)
    if not row and not allow_empty:
        raise EvidenceError(f"{context} must contain at least one producer")
    result: dict[str, dict[str, str]] = {}
    for role, producer in row.items():
        normalized_role = _normalize_role(role, f"{context} role")
        if normalized_role in result:
            raise EvidenceError(f"{context} contains duplicate role {normalized_role}")
        result[normalized_role] = normalize_external_producer(
            producer, f"{context}.{normalized_role}"
        )
    return {role: result[role] for role in sorted(result)}


def _coalesce_alias(
    first: Any,
    second: Any,
    *,
    context: str,
) -> Any:
    """Select two backwards-compatible keyword aliases without ambiguity."""

    if first is not None and second is not None:
        raise EvidenceError(f"{context} was provided more than once")
    return first if first is not None else second


def make_source_record(
    path: Path,
    *,
    project_root: Path,
    artifact_id: str,
    collection: str,
    role: str,
    label: str,
    media_type: str | None = None,
    source_name: str | None = None,
) -> SourceRecord:
    """Create a core ``SourceRecord`` bound to existing bytes under a project root."""

    root = project_root.resolve()
    canonical = path.resolve()
    if not canonical.is_file():
        raise EvidenceError(f"artifact was not found: {canonical}")
    try:
        relative = canonical.relative_to(root).as_posix()
    except ValueError as exc:
        raise EvidenceError(f"artifact must be inside project root {root}: {canonical}") from exc
    payload: dict[str, Any] = {
        "id": artifact_id,
        "collection": collection,
        "role": role,
        "path": relative,
        "label": label,
        "bytes": canonical.stat().st_size,
        "sha256": _sha256_file(canonical),
    }
    if media_type is not None:
        payload["media_type"] = media_type
    if source_name is not None:
        payload["source_name"] = source_name
    try:
        return SourceRecord.from_mapping(payload, artifact_id)
    except ManifestError as exc:
        raise EvidenceError(str(exc)) from exc


def verify_source_record(
    value: SourceRecord | Mapping[str, Any],
    *,
    project_root: Path,
    context: str = "artifact",
) -> SourceRecord:
    """Verify that a record still names the exact file bytes it was bound to."""

    try:
        # Reparse typed inputs as well.  ``SourceRecord`` is a frozen data
        # class without a post-init validator, so a caller can otherwise
        # construct one that bypasses the portable-path and digest checks.
        record = (
            SourceRecord.from_mapping(value.to_dict(), context)
            if isinstance(value, SourceRecord)
            else SourceRecord.from_mapping(value, context)
        )
    except ManifestError as exc:
        raise EvidenceError(str(exc)) from exc
    # Resolve the candidate before touching it and prove that symlinks (or
    # junctions on Windows) cannot redirect a portable in-root spelling to an
    # arbitrary external file.  ``SourceRecord`` rejects ``..`` segments, but
    # lexical validation alone is insufficient when a project contains a
    # symlinked artifact path.
    root = project_root.resolve()
    path = (root / Path(record.path)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EvidenceError(
            f"{context} resolves outside project root {root}: {record.path}"
        ) from exc
    if not path.is_file():
        raise EvidenceError(f"{context} was not found: {path}")
    if path.stat().st_size != record.bytes:
        raise EvidenceError(f"{context} byte count does not match its binding")
    if _sha256_file(path) != record.sha256:
        raise EvidenceError(f"{context} SHA-256 does not match its binding")
    return record


def bind_external_artifact(
    path: Path,
    *,
    project_root: Path,
    artifact_id: str,
    collection: str,
    role: str,
    label: str,
    media_type: str,
    producer: Mapping[str, Any],
    source_name: str | None = None,
) -> dict[str, Any]:
    """Record bytes plus the external adapter/tool that produced them."""

    return {
        "artifact": make_source_record(
            path,
            project_root=project_root,
            artifact_id=artifact_id,
            collection=collection,
            role=role,
            label=label,
            media_type=media_type,
            source_name=source_name,
        ).to_dict(),
        "producer": normalize_external_producer(producer),
    }


def verify_external_binding(
    value: Any,
    *,
    project_root: Path,
    context: str,
) -> dict[str, Any]:
    row = _object(value, context)
    _only_keys(row, {"artifact", "producer"}, context)
    record = verify_source_record(row["artifact"], project_root=project_root, context=f"{context}.artifact")
    return {
        "artifact": record.to_dict(),
        "producer": normalize_external_producer(row["producer"], f"{context}.producer"),
    }


def deterministic_timestamps(start_seconds: Any, end_seconds: Any, interval_seconds: Any) -> list[str]:
    """Return stable six-decimal samples, always including both endpoints."""

    start = _decimal(start_seconds, "start_seconds")
    end = _decimal(end_seconds, "end_seconds")
    interval = _decimal(interval_seconds, "interval_seconds")
    if interval <= 0:
        raise EvidenceError("interval_seconds must be greater than zero")
    if end < start:
        raise EvidenceError("end_seconds must be greater than or equal to start_seconds")
    result = [start]
    cursor = start + interval
    while cursor < end:
        result.append(cursor)
        cursor += interval
    if result[-1] != end:
        result.append(end)
    return [_timestamp(item) for item in result]


def _normalize_chapters(
    chapters: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(chapters):
        row = _object(raw, f"chapters[{index}]")
        _only_keys(row, {"id", "kind", "source", "start_seconds", "end_seconds"}, f"chapters[{index}]")
        chapter_id = _identifier(row["id"], f"chapters[{index}].id")
        kind = _string(row["kind"], f"chapters[{index}].kind")
        if kind not in {"video", "still"}:
            raise EvidenceError(f"chapters[{index}].kind must be video or still")
        start = _decimal(row["start_seconds"], f"chapters[{index}].start_seconds")
        end = _decimal(row["end_seconds"], f"chapters[{index}].end_seconds")
        if end < start:
            raise EvidenceError(f"chapters[{index}] ends before it starts")
        result.append(
            {
                "id": chapter_id,
                "kind": kind,
                "source": verify_external_binding(
                    row["source"], project_root=project_root, context=f"chapters[{index}].source"
                ),
                "start_seconds": _timestamp(start),
                "end_seconds": _timestamp(end),
            }
        )
    result.sort(key=lambda item: item["id"])
    ids = [item["id"] for item in result]
    if len(ids) != len(set(ids)):
        raise EvidenceError("chapters contains duplicate ids")
    if not result:
        raise EvidenceError("chapters must contain at least one chapter")
    return result


def _assemble_plan(
    chapters: list[dict[str, Any]],
    interval: Decimal,
    frame_producer: dict[str, str],
    ocr_producer: dict[str, str],
) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, set[str]]] = {}
    for chapter in chapters:
        artifact = chapter["source"]["artifact"]
        timestamps = (
            [_timestamp(_decimal(chapter["start_seconds"], "chapter start"))]
            if chapter["kind"] == "still"
            else deterministic_timestamps(chapter["start_seconds"], chapter["end_seconds"], interval)
        )
        for timestamp in timestamps:
            key = (artifact["sha256"], timestamp)
            group = groups.setdefault(key, {"chapter_ids": set(), "source_artifact_ids": set()})
            group["chapter_ids"].add(chapter["id"])
            group["source_artifact_ids"].add(artifact["id"])
    ordered = sorted(groups.items(), key=lambda item: (item[0][0], Decimal(item[0][1])))
    samples = []
    for index, ((source_sha256, timestamp), group) in enumerate(ordered, start=1):
        samples.append(
            {
                "id": f"sample-{index:06d}",
                "source_sha256": source_sha256,
                "timestamp_seconds": timestamp,
                "chapter_ids": sorted(group["chapter_ids"]),
                "source_artifact_ids": sorted(group["source_artifact_ids"]),
            }
        )
    return {
        "format_version": FORMAT_VERSION,
        "kind": PLAN_KIND,
        "sampling": {
            "interval_seconds": _timestamp(interval),
            "timestamp_precision_decimal_places": 6,
            "endpoint_policy": "include-start-and-end",
            "deduplication_key": ["source_sha256", "timestamp_seconds"],
            "required_roles": list(REQUIRED_EVIDENCE_ROLES),
            "external_producers": {
                "frame": frame_producer,
                "ocr": ocr_producer,
            },
        },
        "chapters": chapters,
        "samples": samples,
    }


def _assemble_sample_rows(
    chapters: list[dict[str, Any]], interval: Decimal
) -> list[dict[str, Any]]:
    """Build the deterministic sample rows shared by plan v1 and v2."""

    groups: dict[tuple[str, str], dict[str, set[str]]] = {}
    for chapter in chapters:
        artifact = chapter["source"]["artifact"]
        timestamps = (
            [_timestamp(_decimal(chapter["start_seconds"], "chapter start"))]
            if chapter["kind"] == "still"
            else deterministic_timestamps(
                chapter["start_seconds"], chapter["end_seconds"], interval
            )
        )
        for timestamp in timestamps:
            key = (artifact["sha256"], timestamp)
            group = groups.setdefault(
                key, {"chapter_ids": set(), "source_artifact_ids": set()}
            )
            group["chapter_ids"].add(chapter["id"])
            group["source_artifact_ids"].add(artifact["id"])
    ordered = sorted(groups.items(), key=lambda item: (item[0][0], Decimal(item[0][1])))
    return [
        {
            "id": f"sample-{index:06d}",
            "source_sha256": source_sha256,
            "timestamp_seconds": timestamp,
            "chapter_ids": sorted(group["chapter_ids"]),
            "source_artifact_ids": sorted(group["source_artifact_ids"]),
        }
        for index, ((source_sha256, timestamp), group) in enumerate(ordered, start=1)
    ]


def _sample_spec_keys(sample: Mapping[str, Any], index: int) -> tuple[Any, ...]:
    """Return accepted lookup keys for a per-sample v2 option.

    The stable sample id is the primary key.  The source/timestamp key makes it
    possible for an adapter to provide requirements before IDs are assigned,
    while the integer and decimal-string indexes are convenient for small
    programmatic callers.
    """

    return (
        sample["id"],
        index,
        str(index),
        f"{sample['source_sha256']}@{sample['timestamp_seconds']}",
    )


def _resolve_per_sample_spec(
    spec: Any,
    samples: list[dict[str, Any]],
    *,
    context: str,
    default: Any,
    normalize: Any,
) -> list[Any]:
    """Resolve a sequence/mapping/callback into one normalized value per sample."""

    if spec is None:
        return [default for _ in samples]
    if callable(spec):
        return [
            normalize(spec(dict(sample)), f"{context}[{index}]")
            for index, sample in enumerate(samples)
        ]
    if isinstance(spec, Mapping):
        by_key: dict[Any, Any] = {}
        for raw_key, raw_value in spec.items():
            matched = [
                index
                for index, sample in enumerate(samples)
                if raw_key in _sample_spec_keys(sample, index)
            ]
            if len(matched) != 1:
                raise EvidenceError(f"{context} contains unknown sample key {raw_key!r}")
            index = matched[0]
            if index in by_key:
                raise EvidenceError(f"{context} contains duplicate sample assignment")
            by_key[index] = normalize(raw_value, f"{context}[{raw_key!r}]")
        return [by_key.get(index, default) for index in range(len(samples))]
    if isinstance(spec, (str, bytes, bytearray)):
        raise EvidenceError(f"{context} must be a mapping, sequence, or callback")
    try:
        values = list(spec)
    except TypeError as exc:
        raise EvidenceError(f"{context} must be a mapping, sequence, or callback") from exc
    if len(values) != len(samples):
        raise EvidenceError(
            f"{context} must contain exactly {len(samples)} values (one per sample)"
        )
    return [normalize(value, f"{context}[{index}]") for index, value in enumerate(values)]


def _normalize_sample_roles(value: Any, context: str) -> list[str]:
    return _normalize_roles(value, context)


def _normalize_sample_producers(
    value: Any, context: str
) -> dict[str, dict[str, str]] | None:
    # ``None`` in a per-sample sequence/callback means “use the plan-level
    # producer map”.  Supporting that explicit value keeps a sequence-shaped
    # integration ergonomic without weakening the validation of actual maps.
    if value is None:
        return None
    return _normalize_producer_map(value, context, allow_empty=True)


def _assemble_plan_v2(
    chapters: list[dict[str, Any]],
    interval: Decimal,
    required_roles: list[str],
    external_producers: dict[str, dict[str, str]],
    *,
    sample_roles: Any = None,
    sample_producers: Any = None,
) -> dict[str, Any]:
    samples = _assemble_sample_rows(chapters, interval)
    role_values = _resolve_per_sample_spec(
        sample_roles,
        samples,
        context="sample_required_roles",
        default=list(required_roles),
        normalize=_normalize_sample_roles,
    )
    producer_values = _resolve_per_sample_spec(
        sample_producers,
        samples,
        context="sample_external_producers",
        default=None,
        normalize=_normalize_sample_producers,
    )
    normalized_samples: list[dict[str, Any]] = []
    for index, (sample, roles, overrides) in enumerate(
        zip(samples, role_values, producer_values)
    ):
        if not roles:
            raise EvidenceError(f"samples[{index}].required_roles must contain at least one role")
        effective_producers = dict(external_producers)
        if overrides is not None:
            unknown = sorted(set(overrides) - set(roles))
            if unknown:
                raise EvidenceError(
                    f"samples[{index}].external_producers contains roles not required by sample: "
                    + ", ".join(unknown)
                )
            effective_producers.update(overrides)
        missing = sorted(set(roles) - set(effective_producers))
        if missing:
            raise EvidenceError(
                f"samples[{index}] has no producer for required roles: {', '.join(missing)}"
            )
        normalized = dict(sample)
        normalized["required_roles"] = roles
        if overrides is not None:
            normalized["external_producers"] = overrides
        normalized_samples.append(normalized)
    return {
        "format_version": PLAN_V2_FORMAT_VERSION,
        "kind": PLAN_KIND,
        "sampling": {
            "interval_seconds": _timestamp(interval),
            "timestamp_precision_decimal_places": 6,
            "endpoint_policy": "include-start-and-end",
            "deduplication_key": ["source_sha256", "timestamp_seconds"],
            "required_roles": required_roles,
            "external_producers": external_producers,
        },
        "chapters": chapters,
        "samples": normalized_samples,
    }


def build_sampling_plan_v2(
    chapters: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
    interval_seconds: Any,
    required_roles: Iterable[str] | Mapping[Any, Any] | None = None,
    external_producers: Mapping[str, Mapping[str, Any]] | None = None,
    role_producers: Mapping[str, Mapping[str, Any]] | None = None,
    producers: Mapping[str, Mapping[str, Any]] | None = None,
    sample_roles: Any = None,
    sample_required_roles: Any = None,
    sample_producers: Any = None,
    sample_external_producers: Any = None,
    frame_producer: Mapping[str, Any] | None = None,
    ocr_producer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v2 sampling plan with configurable evidence roles.

    ``required_roles`` is the default role list.  Every generated sample gets
    an explicit ``required_roles`` list in the canonical plan.  Per-sample
    values may be supplied as a sequence (parallel to the generated samples),
    a mapping keyed by sample id/index/source+timestamp, or a callback receiving
    the sample row.  ``external_producers`` is a role -> producer map; a
    sample may provide a strict override through ``sample_producers``.

    The aliases (``role_producers``, ``producers``, ``sample_required_roles``
    and ``sample_external_producers``) make the contract easier to consume from
    adapters while resolving ambiguity eagerly.  The legacy v1 builder remains
    unchanged and should be used when only frame/OCR evidence is needed.
    """

    producer_map = _coalesce_alias(
        external_producers,
        _coalesce_alias(role_producers, producers, context="v2 producer map"),
        context="v2 producer map",
    )
    sample_role_spec = _coalesce_alias(
        sample_roles,
        sample_required_roles,
        context="v2 sample role requirements",
    )
    sample_producer_spec = _coalesce_alias(
        sample_producers,
        sample_external_producers,
        context="v2 sample producer requirements",
    )

    # A mapping passed as required_roles is a useful shorthand for callers
    # whose requirements are entirely sample-specific.
    if isinstance(required_roles, Mapping):
        if sample_role_spec is not None:
            raise EvidenceError(
                "required_roles mapping cannot be combined with sample role requirements"
            )
        sample_role_spec = required_roles
        required_roles = []

    if producer_map is None:
        producer_payload: dict[str, Mapping[str, Any]] = {}
        if frame_producer is not None:
            producer_payload["frame"] = frame_producer
        if ocr_producer is not None:
            producer_payload["ocr"] = ocr_producer
        producer_map = producer_payload
    normalized_producers = _normalize_producer_map(
        producer_map, "external_producers", allow_empty=True
    )

    if required_roles is None:
        default_roles = list(normalized_producers)
        if not default_roles and sample_role_spec is None:
            default_roles = list(REQUIRED_EVIDENCE_ROLES)
    else:
        default_roles = _normalize_roles(required_roles, "required_roles", allow_empty=True)
    if not normalized_producers and default_roles:
        raise EvidenceError("external_producers must cover every default required role")
    missing_defaults = sorted(set(default_roles) - set(normalized_producers))
    if missing_defaults:
        raise EvidenceError(
            "external_producers has no producer for required roles: "
            + ", ".join(missing_defaults)
        )

    interval = _decimal(interval_seconds, "interval_seconds")
    if interval <= 0:
        raise EvidenceError("interval_seconds must be greater than zero")
    normalized_chapters = _normalize_chapters(chapters, project_root=project_root)
    return _assemble_plan_v2(
        normalized_chapters,
        interval,
        default_roles,
        normalized_producers,
        sample_roles=sample_role_spec,
        sample_producers=sample_producer_spec,
    )


def build_sampling_plan(
    chapters: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
    interval_seconds: Any,
    frame_producer: Mapping[str, Any],
    ocr_producer: Mapping[str, Any],
) -> dict[str, Any]:
    """Build deterministic metadata; this function does no media processing."""

    interval = _decimal(interval_seconds, "interval_seconds")
    if interval <= 0:
        raise EvidenceError("interval_seconds must be greater than zero")
    normalized = _normalize_chapters(chapters, project_root=project_root)
    return _assemble_plan(
        normalized,
        interval,
        normalize_external_producer(frame_producer, "frame_producer"),
        normalize_external_producer(ocr_producer, "ocr_producer"),
    )


def _validate_sampling_plan_v1(value: Any, *, project_root: Path) -> dict[str, Any]:
    row = _object(value, "evidence plan")
    _only_keys(row, {"format_version", "kind", "sampling", "chapters", "samples"}, "evidence plan")
    if (
        isinstance(row["format_version"], bool)
        or row["format_version"] != FORMAT_VERSION
        or row["kind"] != PLAN_KIND
    ):
        raise EvidenceError("evidence plan must declare xar_promo_evidence_plan format v1")
    sampling = _object(row["sampling"], "sampling")
    _only_keys(
        sampling,
        {
            "interval_seconds",
            "timestamp_precision_decimal_places",
            "endpoint_policy",
            "deduplication_key",
            "required_roles",
            "external_producers",
        },
        "sampling",
    )
    if sampling["timestamp_precision_decimal_places"] != 6:
        raise EvidenceError("sampling timestamp precision must be six decimal places")
    if sampling["endpoint_policy"] != "include-start-and-end":
        raise EvidenceError("sampling endpoint policy is unsupported")
    if sampling["deduplication_key"] != ["source_sha256", "timestamp_seconds"]:
        raise EvidenceError("sampling deduplication key is unsupported")
    if sampling["required_roles"] != list(REQUIRED_EVIDENCE_ROLES):
        raise EvidenceError("sampling must require frame and ocr evidence")
    producers = _object(sampling["external_producers"], "sampling.external_producers")
    _only_keys(producers, set(REQUIRED_EVIDENCE_ROLES), "sampling.external_producers")
    interval = _decimal(sampling["interval_seconds"], "sampling.interval_seconds")
    if interval <= 0:
        raise EvidenceError("sampling.interval_seconds must be greater than zero")
    normalized = _normalize_chapters(_array(row["chapters"], "chapters"), project_root=project_root)
    expected = _assemble_plan(
        normalized,
        interval,
        normalize_external_producer(producers["frame"], "sampling.external_producers.frame"),
        normalize_external_producer(producers["ocr"], "sampling.external_producers.ocr"),
    )
    if row != expected:
        raise EvidenceError("evidence plan does not match its deterministic sampling metadata")
    return expected


def _validate_sampling_plan_v2(value: Any, *, project_root: Path) -> dict[str, Any]:
    """Validate and canonicalize a configurable-role v2 sampling plan."""

    row = _object(value, "evidence plan")
    _only_keys(row, {"format_version", "kind", "sampling", "chapters", "samples"}, "evidence plan")
    if (
        isinstance(row["format_version"], bool)
        or row["format_version"] != PLAN_V2_FORMAT_VERSION
        or row["kind"] != PLAN_KIND
    ):
        raise EvidenceError("evidence plan must declare xar_promo_evidence_plan format v2")
    sampling = _object(row["sampling"], "sampling")
    _only_keys(
        sampling,
        {
            "interval_seconds",
            "timestamp_precision_decimal_places",
            "endpoint_policy",
            "deduplication_key",
            "required_roles",
            "external_producers",
        },
        "sampling",
    )
    if sampling["timestamp_precision_decimal_places"] != 6:
        raise EvidenceError("sampling timestamp precision must be six decimal places")
    if sampling["endpoint_policy"] != "include-start-and-end":
        raise EvidenceError("sampling endpoint policy is unsupported")
    if sampling["deduplication_key"] != ["source_sha256", "timestamp_seconds"]:
        raise EvidenceError("sampling deduplication key is unsupported")
    default_roles = _normalize_roles(
        sampling["required_roles"], "sampling.required_roles", allow_empty=True
    )
    producers = _normalize_producer_map(
        sampling["external_producers"], "sampling.external_producers", allow_empty=True
    )
    missing_defaults = sorted(set(default_roles) - set(producers))
    if missing_defaults:
        raise EvidenceError(
            "sampling.external_producers has no producer for required roles: "
            + ", ".join(missing_defaults)
        )
    interval = _decimal(sampling["interval_seconds"], "sampling.interval_seconds")
    if interval <= 0:
        raise EvidenceError("sampling.interval_seconds must be greater than zero")
    normalized_chapters = _normalize_chapters(
        _array(row["chapters"], "chapters"), project_root=project_root
    )
    expected_samples = _assemble_sample_rows(normalized_chapters, interval)
    raw_samples = _array(row["samples"], "samples")
    if len(raw_samples) != len(expected_samples):
        raise EvidenceError("evidence plan samples do not match deterministic sampling metadata")
    normalized_samples: list[dict[str, Any]] = []
    for index, (raw, expected) in enumerate(zip(raw_samples, expected_samples)):
        sample = _object(raw, f"samples[{index}]")
        sample_allowed = {
            "id",
            "source_sha256",
            "timestamp_seconds",
            "chapter_ids",
            "source_artifact_ids",
            "required_roles",
            "external_producers",
        }
        sample_required = sample_allowed - {"external_producers"}
        sample_extras = sorted(set(sample) - sample_allowed)
        sample_missing = sorted(sample_required - set(sample))
        if sample_extras:
            raise EvidenceError(
                f"samples[{index}] has unsupported fields: {', '.join(sample_extras)}"
            )
        if sample_missing:
            raise EvidenceError(
                f"samples[{index}] is missing fields: {', '.join(sample_missing)}"
            )
        # The sample identity/timestamp/source projection is still generated by
        # xAR.  Only role requirements and producer overrides are project data.
        for key in ("id", "source_sha256", "timestamp_seconds", "chapter_ids", "source_artifact_ids"):
            if sample.get(key) != expected[key]:
                raise EvidenceError(
                    f"samples[{index}].{key} does not match deterministic sampling metadata"
                )
        roles = _normalize_roles(
            sample["required_roles"], f"samples[{index}].required_roles"
        )
        has_override = "external_producers" in sample
        overrides = (
            _normalize_producer_map(
                sample["external_producers"],
                f"samples[{index}].external_producers",
                allow_empty=True,
            )
            if has_override
            else None
        )
        effective = dict(producers)
        if overrides is not None:
            unknown = sorted(set(overrides) - set(roles))
            if unknown:
                raise EvidenceError(
                    f"samples[{index}].external_producers contains roles not required by sample: "
                    + ", ".join(unknown)
                )
            effective.update(overrides)
        missing = sorted(set(roles) - set(effective))
        if missing:
            raise EvidenceError(
                f"samples[{index}] has no producer for required roles: {', '.join(missing)}"
            )
        normalized_sample = dict(expected)
        normalized_sample["required_roles"] = roles
        if overrides is not None:
            normalized_sample["external_producers"] = overrides
        normalized_samples.append(normalized_sample)
    expected_plan = {
        "format_version": PLAN_V2_FORMAT_VERSION,
        "kind": PLAN_KIND,
        "sampling": {
            "interval_seconds": _timestamp(interval),
            "timestamp_precision_decimal_places": 6,
            "endpoint_policy": "include-start-and-end",
            "deduplication_key": ["source_sha256", "timestamp_seconds"],
            "required_roles": default_roles,
            "external_producers": producers,
        },
        "chapters": normalized_chapters,
        "samples": normalized_samples,
    }
    if row != expected_plan:
        raise EvidenceError("evidence plan does not match its deterministic sampling metadata")
    return expected_plan


def validate_sampling_plan(value: Any, *, project_root: Path) -> dict[str, Any]:
    """Validate either the legacy v1 or configurable-role v2 plan."""

    row = _object(value, "evidence plan")
    version = row.get("format_version")
    if version == FORMAT_VERSION:
        return _validate_sampling_plan_v1(row, project_root=project_root)
    if version == PLAN_V2_FORMAT_VERSION:
        return _validate_sampling_plan_v2(row, project_root=project_root)
    raise EvidenceError("evidence plan format_version is unsupported")


def validate_sampling_plan_v2(value: Any, *, project_root: Path) -> dict[str, Any]:
    """Validate only a v2 plan (explicit helper for adapter code)."""

    row = _object(value, "evidence plan")
    if (
        isinstance(row.get("format_version"), bool)
        or row.get("format_version") != PLAN_V2_FORMAT_VERSION
    ):
        raise EvidenceError("evidence plan must declare xar_promo_evidence_plan format v2")
    return _validate_sampling_plan_v2(row, project_root=project_root)


def write_sampling_plan(
    path: Path,
    chapters: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
    interval_seconds: Any,
    frame_producer: Mapping[str, Any],
    ocr_producer: Mapping[str, Any],
) -> dict[str, Any]:
    plan = build_sampling_plan(
        chapters,
        project_root=project_root,
        interval_seconds=interval_seconds,
        frame_producer=frame_producer,
        ocr_producer=ocr_producer,
    )
    _write_new_json(path, plan, "evidence plan")
    return plan


def write_sampling_plan_v2(
    path: Path,
    chapters: Iterable[Mapping[str, Any]],
    *,
    project_root: Path,
    interval_seconds: Any,
    required_roles: Iterable[str] | Mapping[Any, Any] | None = None,
    external_producers: Mapping[str, Mapping[str, Any]] | None = None,
    role_producers: Mapping[str, Mapping[str, Any]] | None = None,
    producers: Mapping[str, Mapping[str, Any]] | None = None,
    sample_roles: Any = None,
    sample_required_roles: Any = None,
    sample_producers: Any = None,
    sample_external_producers: Any = None,
    frame_producer: Mapping[str, Any] | None = None,
    ocr_producer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and immutably write a configurable-role v2 sampling plan."""

    plan = build_sampling_plan_v2(
        chapters,
        project_root=project_root,
        interval_seconds=interval_seconds,
        required_roles=required_roles,
        external_producers=external_producers,
        role_producers=role_producers,
        producers=producers,
        sample_roles=sample_roles,
        sample_required_roles=sample_required_roles,
        sample_producers=sample_producers,
        sample_external_producers=sample_external_producers,
        frame_producer=frame_producer,
        ocr_producer=ocr_producer,
    )
    _write_new_json(path, plan, "evidence plan")
    return plan


def load_sampling_plan(path: Path, *, project_root: Path) -> dict[str, Any]:
    return validate_sampling_plan(_read_json_object(path, "evidence plan"), project_root=project_root)


def load_sampling_plan_v2(path: Path, *, project_root: Path) -> dict[str, Any]:
    """Load and validate only a format v2 sampling plan."""

    return validate_sampling_plan_v2(
        _read_json_object(path, "evidence plan"), project_root=project_root
    )


def _plan_sample_roles(plan: Mapping[str, Any], sample: Mapping[str, Any]) -> tuple[str, ...]:
    if plan["format_version"] == FORMAT_VERSION:
        return REQUIRED_EVIDENCE_ROLES
    return tuple(sample["required_roles"])


def _plan_sample_producer(
    plan: Mapping[str, Any], sample: Mapping[str, Any], role: str
) -> dict[str, str]:
    sampling = plan["sampling"]
    producers = sampling["external_producers"]
    overrides = sample.get("external_producers", {})
    producer = overrides.get(role, producers.get(role))
    if producer is None:
        raise EvidenceError(
            f"sample {sample['id']} has no producer for required role {role}"
        )
    return normalize_external_producer(producer, f"sample {sample['id']} producer {role}")


def write_evidence_bundle(
    path: Path,
    *,
    project_root: Path,
    plan_path: Path,
    submissions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind externally generated frame/OCR files to every planned sample."""

    plan = load_sampling_plan(plan_path, project_root=project_root)
    plan_record = make_source_record(
        plan_path,
        project_root=project_root,
        artifact_id="evidence-plan",
        collection="derived",
        role="evidence-plan",
        label="Deterministic evidence sampling plan",
        media_type="application/json",
    )
    expected = {
        (sample["id"], role)
        for sample in plan["samples"]
        for role in _plan_sample_roles(plan, sample)
    }
    samples_by_id = {sample["id"]: sample for sample in plan["samples"]}
    supplied: dict[tuple[str, str], dict[str, Any]] = {}
    for index, raw in enumerate(submissions):
        row = _object(raw, f"submissions[{index}]")
        _only_keys(row, {"sample_id", "role", "path", "media_type", "producer"}, f"submissions[{index}]")
        sample_id = _identifier(row["sample_id"], f"submissions[{index}].sample_id")
        role = _string(row["role"], f"submissions[{index}].role")
        key = (sample_id, role)
        if key not in expected:
            raise EvidenceError(f"unexpected evidence submission for {sample_id}/{role}")
        if key in supplied:
            raise EvidenceError(f"duplicate evidence submission for {sample_id}/{role}")
        producer = normalize_external_producer(row["producer"], f"submissions[{index}].producer")
        sample = samples_by_id[sample_id]
        required_producer = _plan_sample_producer(plan, sample, role)
        if producer != required_producer:
            raise EvidenceError(f"producer metadata does not match the plan for role {role}")
        artifact_path = Path(row["path"])
        supplied[key] = {
            "sample_id": sample_id,
            "role": role,
            "binding": bind_external_artifact(
                artifact_path,
                project_root=project_root,
                artifact_id=f"{sample_id}-{role}",
                collection="derived",
                role=role,
                label=f"{sample_id} {role} evidence",
                media_type=_string(row["media_type"], f"submissions[{index}].media_type"),
                producer=producer,
            ),
        }
    missing = sorted(expected - set(supplied))
    if missing:
        formatted = ", ".join(f"{sample_id}/{role}" for sample_id, role in missing)
        raise EvidenceError(f"required evidence is missing: {formatted}")
    bundle = {
        "format_version": plan["format_version"],
        "kind": BUNDLE_KIND,
        "plan": plan_record.to_dict(),
        "entries": [supplied[key] for key in sorted(supplied)],
    }
    _write_new_json(path, bundle, "evidence bundle")
    return bundle


def write_evidence_bundle_v2(
    path: Path,
    *,
    project_root: Path,
    plan_path: Path,
    submissions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write a v2 bundle, rejecting accidental use of a legacy plan."""

    plan = load_sampling_plan(plan_path, project_root=project_root)
    if plan["format_version"] != PLAN_V2_FORMAT_VERSION:
        raise EvidenceError("write_evidence_bundle_v2 requires a format v2 plan")
    return write_evidence_bundle(
        path,
        project_root=project_root,
        plan_path=plan_path,
        submissions=submissions,
    )


def load_evidence_bundle(path: Path, *, project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reverify the bundle, its plan, all sources, and every evidence artifact."""

    row = _read_json_object(path, "evidence bundle")
    _only_keys(row, {"format_version", "kind", "plan", "entries"}, "evidence bundle")
    if row["kind"] != BUNDLE_KIND:
        raise EvidenceError("evidence bundle kind is unsupported")
    if (
        isinstance(row["format_version"], bool)
        or row["format_version"] not in {FORMAT_VERSION, BUNDLE_V2_FORMAT_VERSION}
    ):
        raise EvidenceError("evidence bundle format_version is unsupported")
    plan_record = verify_source_record(row["plan"], project_root=project_root, context="evidence bundle plan")
    if plan_record.role != "evidence-plan":
        raise EvidenceError("evidence bundle plan record has the wrong role")
    plan_path = project_root.resolve() / Path(plan_record.path)
    plan = load_sampling_plan(plan_path, project_root=project_root)
    samples = {sample["id"]: sample for sample in plan["samples"]}
    expected = {
        (sample_id, role)
        for sample_id, sample in samples.items()
        for role in _plan_sample_roles(plan, sample)
    }
    expected_version = plan["format_version"]
    if row["format_version"] != expected_version:
        raise EvidenceError("evidence bundle format_version does not match its plan")
    seen: set[tuple[str, str]] = set()
    seen_artifact_ids: set[str] = set()
    normalized_entries: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(row["entries"], "entries")):
        entry = _object(raw, f"entries[{index}]")
        _only_keys(entry, {"sample_id", "role", "binding"}, f"entries[{index}]")
        sample_id = _identifier(entry["sample_id"], f"entries[{index}].sample_id")
        role = _string(entry["role"], f"entries[{index}].role")
        key = (sample_id, role)
        if key not in expected:
            raise EvidenceError(f"unexpected evidence entry for {sample_id}/{role}")
        if key in seen:
            raise EvidenceError(f"duplicate evidence entry for {sample_id}/{role}")
        binding = verify_external_binding(
            entry["binding"], project_root=project_root, context=f"entries[{index}].binding"
        )
        artifact = binding["artifact"]
        artifact_id = artifact["id"]
        if artifact_id in seen_artifact_ids:
            raise EvidenceError(
                f"evidence bundle repeats artifact id {artifact_id!r}"
            )
        if artifact["role"] != role:
            raise EvidenceError(f"evidence entry {sample_id}/{role} has a mismatched artifact role")
        if binding["producer"] != _plan_sample_producer(plan, samples[sample_id], role):
            raise EvidenceError(f"evidence entry {sample_id}/{role} has unplanned producer metadata")
        seen.add(key)
        seen_artifact_ids.add(artifact_id)
        normalized_entries.append({"sample_id": sample_id, "role": role, "binding": binding})
    missing = sorted(expected - seen)
    if missing:
        formatted = ", ".join(f"{sample_id}/{role}" for sample_id, role in missing)
        raise EvidenceError(f"required evidence is missing: {formatted}")
    normalized = {
        "format_version": expected_version,
        "kind": BUNDLE_KIND,
        "plan": plan_record.to_dict(),
        "entries": sorted(normalized_entries, key=lambda item: (item["sample_id"], item["role"])),
    }
    if row != normalized:
        raise EvidenceError("evidence bundle is not in canonical deterministic order")
    return normalized, plan


def load_evidence_bundle_v2(
    path: Path, *, project_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load only a format v2 evidence bundle."""

    bundle, plan = load_evidence_bundle(path, project_root=project_root)
    if bundle["format_version"] != BUNDLE_V2_FORMAT_VERSION:
        raise EvidenceError("evidence bundle must declare xar_promo_evidence_bundle format v2")
    return bundle, plan


# Descriptive aliases for integrations that call the document an evidence plan
# rather than a sampling plan.  They intentionally point at the same immutable
# implementations and do not alter the v1 API.
build_evidence_plan_v2 = build_sampling_plan_v2
write_evidence_plan_v2 = write_sampling_plan_v2
validate_evidence_plan_v2 = validate_sampling_plan_v2
load_evidence_plan_v2 = load_sampling_plan_v2
