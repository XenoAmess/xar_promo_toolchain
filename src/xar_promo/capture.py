"""Project-neutral, hash-bound capture receipts.

The capture producer is deliberately outside :mod:`xar_promo`.  A producer
(for example OBS, a game-specific runner, or a desktop recorder) writes a
small JSON receipt after it has collected its media and evidence.  This module
only verifies the receipt's portable structure and the bytes it names.  It
does not start a recorder, inspect frames, run OCR, or attach meaning to a
mark/span.

The generic contract is intentionally smaller than a project capture
contract.  Project adapters may keep additional, hash-bound sidecars (game
state, semantic assertions, and so on) and project policy decides which
spans are useful.  ``CaptureReceipt`` merely exposes those spans as safe
timeline projections.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from .errors import ArtifactError


CAPTURE_RECEIPT_FORMAT_VERSION = 1
CAPTURE_RECEIPT_KIND = "xar-promo-capture-receipt"
OVERLAP_POLICIES = frozenset({"forbid", "allow"})
OVERLAP_POLICY_FORBID = "forbid"
OVERLAP_POLICY_ALLOW = "allow"

_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class CaptureError(ArtifactError):
    """A capture receipt is absent, stale, or structurally unsafe."""


# A descriptive alias is useful to callers that want to distinguish this
# contract from an adapter-specific capture error.  Keeping it an alias also
# means a caller catching CaptureError continues to work.
CaptureReceiptError = CaptureError


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaptureError(f"{context} must be an object")
    return value


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise CaptureError(f"{context} must be an array")
    return value


def _string(value: Any, context: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(f"{context} must be a non-empty string")
    result = value.strip()
    if "\x00" in result or "\n" in result or "\r" in result:
        raise CaptureError(f"{context} must be NUL-free single-line text")
    if identifier and _IDENTIFIER.fullmatch(result) is None:
        raise CaptureError(
            f"{context} must start with an ASCII letter/digit and contain only "
            "letters, digits, '.', '_' or '-' (maximum 128 characters)"
        )
    return result


def _only_keys(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise CaptureError(f"{context} has unsupported fields: {', '.join(extras)}")


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CaptureError(f"{context} must be an integer >= {minimum}")
    return value


def _seconds(value: Any, context: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptureError(f"{context} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        qualifier = "greater than zero" if positive else "finite and non-negative"
        raise CaptureError(f"{context} must be {qualifier}")
    # Avoid retaining a signed zero or an unnecessarily surprising value in
    # canonical JSON.  This does not change any meaningful timestamp.
    return 0.0 if result == 0 else result


def _same_seconds(left: float, right: float) -> bool:
    """Compare timestamps without letting ordinary binary-float noise drift bindings."""

    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)


def _digest(value: Any, context: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CaptureError(f"{context} must be a SHA-256 digest")
    return value.upper()


def _portable_relative(value: Any, context: str) -> str:
    """Validate and normalize a project-relative POSIX path.

    Receipt paths are serialized independently of the host OS.  Backslashes,
    drive-qualified paths, UNC paths, ``.`` and ``..`` are therefore rejected
    instead of being silently normalized into a different path.
    """

    # ``_string`` strips surrounding whitespace for ordinary textual fields;
    # paths must additionally reject control characters before that strip so
    # a trailing newline/NUL cannot be smuggled into a different canonical
    # spelling.  The bundled JSON schema applies the same rule.
    if isinstance(value, str) and any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise CaptureError(f"{context} must not contain control characters")
    raw = _string(value, context)
    if "\\" in raw or _WINDOWS_DRIVE.match(raw) is not None:
        raise CaptureError(f"{context} must use a portable relative '/' path")
    path = PurePosixPath(raw)
    normalized = path.as_posix()
    if (
        path.is_absolute()
        or raw.startswith("//")
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or normalized != raw
    ):
        raise CaptureError(f"{context} must be a normalized relative path")
    return normalized


def _project_path(root: Path, relative_path: str, context: str) -> Path:
    """Resolve a portable path and prove it remains beneath ``root``."""

    root_resolved = root.expanduser().resolve()
    candidate = (root_resolved / Path(*PurePosixPath(relative_path).parts)).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise CaptureError(f"{context} escapes the project root") from exc
    return candidate


def _sha256_file(path: Path, context: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CaptureError(f"could not hash {context}: {path}: {exc}") from exc
    return digest.hexdigest().upper()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise CaptureError(f"capture receipt was not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"could not read capture receipt {path}: {exc}") from exc
    return _object(payload, "capture receipt JSON root")


def _copy_metadata(value: Any, context: str) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise CaptureError(f"{context} must be an object")
    row = dict(value)
    if any(not isinstance(key, str) or not key for key in row):
        raise CaptureError(f"{context} keys must be non-empty strings")
    # Metadata is opaque to the generic contract, but it still travels through
    # JSON.  Normalize it through a strict JSON round-trip so NaN/infinity,
    # custom objects, and mutable nested containers cannot make the in-memory
    # receipt differ from the serialized receipt.
    try:
        normalized = json.loads(
            json.dumps(row, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CaptureError(f"{context} must contain only JSON values") from exc
    return MappingProxyType(dict(normalized))


@dataclass(frozen=True, slots=True, init=False)
class ProducerRecord:
    """Identity of the process that produced a capture receipt.

    ``adapter_id`` mirrors the existing external-producer vocabulary.  The
    keyword alias ``producer_id`` is accepted for integrations that use that
    more general name; :attr:`producer_id` is exposed as a read-only alias.
    """

    adapter_id: str
    tool: str
    tool_version: str
    operation: str
    execution: str
    metadata: Mapping[str, Any]

    def __init__(
        self,
        adapter_id: str | None = None,
        tool: str | None = None,
        tool_version: str | None = None,
        operation: str = "capture",
        execution: str = "external",
        metadata: Mapping[str, Any] | None = None,
        *,
        producer_id: str | None = None,
    ) -> None:
        if adapter_id is None:
            adapter_id = producer_id
        elif producer_id is not None and adapter_id != producer_id:
            raise CaptureError("producer adapter_id and producer_id disagree")
        object.__setattr__(self, "adapter_id", _string(adapter_id, "producer.adapter_id", identifier=True))
        object.__setattr__(self, "tool", _string(tool, "producer.tool"))
        object.__setattr__(
            self,
            "tool_version",
            _string(tool_version, "producer.tool_version"),
        )
        object.__setattr__(self, "operation", _string(operation, "producer.operation"))
        object.__setattr__(self, "execution", _string(execution, "producer.execution"))
        object.__setattr__(self, "metadata", _copy_metadata(metadata, "producer.metadata"))

    @property
    def producer_id(self) -> str:
        """General-name alias for :attr:`adapter_id`."""

        return self.adapter_id

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "adapter_id": self.adapter_id,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "operation": self.operation,
            "execution": self.execution,
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result

    @classmethod
    def from_mapping(cls, value: Any, context: str = "producer") -> "ProducerRecord":
        row = _object(value, context)
        allowed = {
            "adapter_id",
            "producer_id",
            "tool",
            "tool_version",
            "operation",
            "execution",
            "metadata",
        }
        _only_keys(row, allowed, context)
        adapter_id = row.get("adapter_id")
        producer_id = row.get("producer_id")
        if adapter_id is None and producer_id is None:
            raise CaptureError(f"{context} requires adapter_id (or producer_id)")
        return cls(
            adapter_id=adapter_id,
            producer_id=producer_id,
            tool=row.get("tool"),
            tool_version=row.get("tool_version"),
            operation=row.get("operation", "capture"),
            execution=row.get("execution", "external"),
            metadata=row.get("metadata"),
        )


@dataclass(frozen=True, slots=True)
class CaptureFile:
    """One file whose path, byte count and SHA-256 are bound by a receipt."""

    relative_path: str
    path: Path
    bytes: int
    sha256: str
    artifact_id: str | None = None
    role: str | None = None
    label: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _portable_relative(self.relative_path, "capture file.path"))
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        object.__setattr__(self, "bytes", _integer(self.bytes, "capture file.bytes"))
        object.__setattr__(self, "sha256", _digest(self.sha256, "capture file.sha256"))
        if self.artifact_id is not None:
            object.__setattr__(self, "artifact_id", _string(self.artifact_id, "capture file.id", identifier=True))
        if self.role is not None:
            object.__setattr__(self, "role", _string(self.role, "capture file.role", identifier=True))
        if self.label is not None:
            object.__setattr__(self, "label", _string(self.label, "capture file.label"))
        if self.media_type is not None:
            object.__setattr__(self, "media_type", _string(self.media_type, "capture file.media_type"))

    @property
    def size(self) -> int:
        """Alias for the wire-level ``bytes`` field."""

        return self.bytes

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": self.relative_path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }
        if self.artifact_id is not None:
            result["id"] = self.artifact_id
        if self.role is not None:
            result["role"] = self.role
        if self.label is not None:
            result["label"] = self.label
        if self.media_type is not None:
            result["media_type"] = self.media_type
        return result

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        *,
        project_root: str | os.PathLike[str],
        artifact_id: str | None = None,
        role: str | None = None,
        label: str | None = None,
        media_type: str | None = None,
    ) -> "CaptureFile":
        root = Path(project_root).expanduser().resolve()
        candidate = Path(path).expanduser().resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise CaptureError(f"capture file must be inside project root: {candidate}") from exc
        if not candidate.is_file():
            raise CaptureError(f"capture file was not found: {candidate}")
        return cls(
            relative,
            candidate,
            candidate.stat().st_size,
            _sha256_file(candidate, "capture file"),
            artifact_id=artifact_id,
            role=role,
            label=label,
            media_type=media_type,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        project_root: str | os.PathLike[str] | Path,
        context: str = "capture file",
    ) -> "CaptureFile":
        row = _object(value, context)
        _only_keys(
            row,
            {"path", "relative_path", "bytes", "sha256", "id", "artifact_id", "role", "label", "media_type"},
            context,
        )
        if "path" in row and "relative_path" in row and row["path"] != row["relative_path"]:
            raise CaptureError(f"{context}.path and relative_path disagree")
        raw_path = row.get("path", row.get("relative_path"))
        relative = _portable_relative(raw_path, f"{context}.path")
        root = Path(project_root).expanduser().resolve()
        path = _project_path(root, relative, f"{context}.path")
        if not path.is_file():
            raise CaptureError(f"required capture file is missing: {path}")
        expected_bytes = _integer(row.get("bytes"), f"{context}.bytes")
        expected_sha = _digest(row.get("sha256"), f"{context}.sha256")
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            raise CaptureError(
                f"{context} byte count mismatch for {relative}: "
                f"declared {expected_bytes}, actual {actual_bytes}"
            )
        actual_sha = _sha256_file(path, context)
        if actual_sha != expected_sha:
            raise CaptureError(f"{context} SHA-256 mismatch for {relative}")
        artifact_id = row.get("id", row.get("artifact_id"))
        if "id" in row and "artifact_id" in row and row["id"] != row["artifact_id"]:
            raise CaptureError(f"{context}.id and artifact_id disagree")
        return cls(
            relative,
            path,
            expected_bytes,
            expected_sha,
            artifact_id=artifact_id,
            role=row.get("role"),
            label=row.get("label"),
            media_type=row.get("media_type"),
        )


@dataclass(frozen=True, slots=True)
class CaptureMark:
    """A named, ordered point on a capture timeline."""

    label: str
    seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", _string(self.label, "capture mark.label"))
        object.__setattr__(self, "seconds", _seconds(self.seconds, "capture mark.seconds"))

    @property
    def mark_id(self) -> str:
        """Alias used by adapters that call timeline points IDs."""

        return self.label

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "seconds": self.seconds}

    @classmethod
    def from_mapping(cls, value: Any, context: str = "capture mark") -> "CaptureMark":
        row = _object(value, context)
        _only_keys(row, {"label", "id", "seconds"}, context)
        label = row.get("label", row.get("id"))
        if "label" in row and "id" in row and row["label"] != row["id"]:
            raise CaptureError(f"{context}.label and id disagree")
        return cls(label, row.get("seconds"))


@dataclass(frozen=True, slots=True)
class CleanSpan:
    """A positive interval projected from the raw capture."""

    span_id: str
    begin_mark: str
    end_mark: str
    begin_seconds: float
    end_seconds: float
    evidence: tuple[CaptureFile, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "span_id", _string(self.span_id, "clean span.id", identifier=True))
        object.__setattr__(self, "begin_mark", _string(self.begin_mark, "clean span.begin_mark"))
        object.__setattr__(self, "end_mark", _string(self.end_mark, "clean span.end_mark"))
        begin = _seconds(self.begin_seconds, "clean span.begin_seconds")
        end = _seconds(self.end_seconds, "clean span.end_seconds", positive=True)
        if end <= begin:
            raise CaptureError("clean span must have 0 <= begin_seconds < end_seconds")
        object.__setattr__(self, "begin_seconds", begin)
        object.__setattr__(self, "end_seconds", end)
        if self.evidence is None or isinstance(self.evidence, (str, bytes, bytearray)):
            raise CaptureError("clean span evidence must be an iterable of CaptureFile values")
        if not isinstance(self.evidence, tuple):
            try:
                object.__setattr__(self, "evidence", tuple(self.evidence))
            except TypeError as exc:
                raise CaptureError(
                    "clean span evidence must be an iterable of CaptureFile values"
                ) from exc
        if any(not isinstance(item, CaptureFile) for item in self.evidence):
            raise CaptureError("clean span evidence must contain CaptureFile values")
        paths = [item.relative_path for item in self.evidence]
        if len(paths) != len(set(paths)):
            raise CaptureError(f"clean span {self.span_id!r} repeats an evidence path")

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.begin_seconds

    @property
    def start_seconds(self) -> float:
        return self.begin_seconds

    @property
    def stop_seconds(self) -> float:
        return self.end_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.span_id,
            "begin_mark": self.begin_mark,
            "end_mark": self.end_mark,
            "begin_seconds": self.begin_seconds,
            "end_seconds": self.end_seconds,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class CaptureReceipt:
    """A verified, project-neutral capture timeline and its source bindings."""

    artifact_root: Path
    producer: ProducerRecord
    raw_capture: CaptureFile
    duration_seconds: float
    timebase: str
    marks: tuple[CaptureMark, ...]
    clean_spans: tuple[CleanSpan, ...]
    overlap_policy: str = "forbid"
    evidence: tuple[CaptureFile, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    receipt_file: CaptureFile | None = None

    def __post_init__(self) -> None:
        root = Path(self.artifact_root).expanduser().resolve()
        object.__setattr__(self, "artifact_root", root)
        if not isinstance(self.producer, ProducerRecord):
            raise CaptureError("capture receipt producer must be a ProducerRecord")
        if not isinstance(self.raw_capture, CaptureFile):
            raise CaptureError("capture receipt raw_capture must be a CaptureFile")
        duration = _seconds(self.duration_seconds, "capture receipt.duration_seconds", positive=True)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "timebase", _string(self.timebase, "capture receipt.timebase"))
        if not isinstance(self.overlap_policy, str) or self.overlap_policy not in OVERLAP_POLICIES:
            allowed = ", ".join(sorted(OVERLAP_POLICIES))
            raise CaptureError(
                f"capture receipt.overlap_policy must be one of {allowed}"
            )

        for value, label in (
            (self.marks, "marks"),
            (self.clean_spans, "clean_spans"),
            (self.evidence, "evidence"),
        ):
            if value is None or isinstance(value, (str, bytes, bytearray)):
                raise CaptureError(
                    f"capture receipt {label} must be an iterable of typed values"
                )
        try:
            marks = tuple(self.marks)
            spans = tuple(self.clean_spans)
            evidence = tuple(self.evidence)
        except TypeError as exc:
            raise CaptureError("capture receipt timeline fields must be iterable") from exc
        if not marks:
            raise CaptureError("capture receipt marks must contain at least one mark")
        if not spans:
            raise CaptureError("capture receipt clean_spans must contain at least one span")
        if any(not isinstance(item, CaptureMark) for item in marks):
            raise CaptureError("capture receipt marks must contain CaptureMark values")
        if any(not isinstance(item, CleanSpan) for item in spans):
            raise CaptureError("capture receipt clean_spans must contain CleanSpan values")
        if any(not isinstance(item, CaptureFile) for item in evidence):
            raise CaptureError("capture receipt evidence must contain CaptureFile values")
        object.__setattr__(self, "marks", marks)
        object.__setattr__(self, "clean_spans", spans)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata, "capture receipt.metadata"))
        if self.receipt_file is not None and not isinstance(self.receipt_file, CaptureFile):
            raise CaptureError("capture receipt receipt_file must be a CaptureFile")

        mark_map: dict[str, float] = {}
        previous = -1.0
        for mark in marks:
            if mark.label in mark_map:
                raise CaptureError(f"capture receipt repeats mark {mark.label!r}")
            if mark.seconds < previous:
                raise CaptureError("capture receipt marks must be non-negative and ordered")
            if mark.seconds > duration:
                raise CaptureError(f"capture mark {mark.label!r} exceeds capture duration")
            mark_map[mark.label] = mark.seconds
            previous = mark.seconds

        span_ids: set[str] = set()
        previous_begin = -1.0
        previous_end = -1.0
        for span in spans:
            if span.span_id in span_ids:
                raise CaptureError(f"capture receipt repeats clean span {span.span_id!r}")
            span_ids.add(span.span_id)
            if span.begin_mark not in mark_map or span.end_mark not in mark_map:
                raise CaptureError(
                    f"clean span {span.span_id!r} references a missing mark"
                )
            if not _same_seconds(span.begin_seconds, mark_map[span.begin_mark]) or not _same_seconds(
                span.end_seconds, mark_map[span.end_mark]
            ):
                raise CaptureError(
                    f"clean span {span.span_id!r} seconds do not bind its marks"
                )
            if span.end_seconds > duration:
                raise CaptureError(f"clean span {span.span_id!r} exceeds capture duration")
            if span.begin_seconds < previous_begin:
                raise CaptureError("clean spans must be ordered by begin_seconds")
            if self.overlap_policy == "forbid" and span.begin_seconds < previous_end:
                raise CaptureError(
                    f"clean span {span.span_id!r} overlaps the preceding span"
                )
            previous_begin = span.begin_seconds
            previous_end = max(previous_end, span.end_seconds)

        all_records = [self.raw_capture, *evidence]
        all_records.extend(item for span in spans for item in span.evidence)
        if self.receipt_file is not None:
            all_records.append(self.receipt_file)
        for item in all_records:
            _assert_file_under_root(item, root, "capture receipt file")
        _assert_consistent_records(all_records)
        artifact_ids: dict[str, CaptureFile] = {}
        for item in all_records:
            if item.artifact_id is None:
                continue
            prior = artifact_ids.get(item.artifact_id)
            if prior is not None:
                raise CaptureError(
                    "capture receipt repeats artifact id "
                    f"{item.artifact_id!r}"
                )
            artifact_ids[item.artifact_id] = item

    @property
    def raw(self) -> CaptureFile:
        """Short alias for the primary raw recording."""

        return self.raw_capture

    @property
    def source(self) -> CaptureFile:
        """Alias used by source-oriented adapters."""

        return self.raw_capture

    def mark(self, label: str) -> CaptureMark:
        for mark in self.marks:
            if mark.label == label:
                return mark
        raise KeyError(label)

    def clean_span(self, span_id: str) -> CleanSpan:
        for span in self.clean_spans:
            if span.span_id == span_id:
                return span
        raise KeyError(span_id)

    @property
    def all_files(self) -> tuple[CaptureFile, ...]:
        """Unique source records whose bytes can affect this receipt."""

        records = [self.raw_capture, *self.evidence]
        records.extend(item for span in self.clean_spans for item in span.evidence)
        if self.receipt_file is not None:
            records.append(self.receipt_file)
        result: list[CaptureFile] = []
        seen: set[str] = set()
        for item in records:
            if item.relative_path not in seen:
                result.append(item)
                seen.add(item.relative_path)
        return tuple(result)

    def verify_unchanged(self) -> None:
        """Rehash every receipt source and reject any post-load mutation."""

        for record in self.all_files:
            path = _project_path(self.artifact_root, record.relative_path, "capture file")
            if not path.is_file():
                raise CaptureError(
                    f"capture source became unavailable after receipt load: "
                    f"{record.relative_path}"
                )
            try:
                current_bytes = path.stat().st_size
            except OSError as exc:
                raise CaptureError(
                    f"could not stat capture source after receipt load: {record.relative_path}"
                ) from exc
            current_sha = _sha256_file(path, "capture source")
            if (current_bytes, current_sha) != (record.bytes, record.sha256):
                raise CaptureError(
                    f"capture source changed after receipt load: {record.relative_path}; "
                    f"expected {record.bytes} bytes/{record.sha256}, got "
                    f"{current_bytes} bytes/{current_sha}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical, portable JSON representation."""

        result: dict[str, Any] = {
            "format_version": CAPTURE_RECEIPT_FORMAT_VERSION,
            "kind": CAPTURE_RECEIPT_KIND,
            "producer": self.producer.to_dict(),
            "raw_capture": self.raw_capture.to_dict(),
            "duration_seconds": self.duration_seconds,
            "timebase": self.timebase,
            "marks": [item.to_dict() for item in self.marks],
            "clean_spans": [item.to_dict() for item in self.clean_spans],
            "overlap_policy": self.overlap_policy,
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        project_root: str | os.PathLike[str] | Path,
        receipt_path: str | os.PathLike[str] | Path | None = None,
    ) -> "CaptureReceipt":
        row = _object(value, "capture receipt")
        _only_keys(
            row,
            {
                "format_version",
                "kind",
                "producer",
                "raw_capture",
                "duration_seconds",
                "timebase",
                "marks",
                "clean_spans",
                "overlap_policy",
                "evidence",
                "metadata",
            },
            "capture receipt",
        )
        if (
            isinstance(row.get("format_version"), bool)
            or row.get("format_version") != CAPTURE_RECEIPT_FORMAT_VERSION
        ):
            raise CaptureError(
                f"capture receipt format_version must be {CAPTURE_RECEIPT_FORMAT_VERSION}"
            )
        if row.get("kind") != CAPTURE_RECEIPT_KIND:
            raise CaptureError(f"capture receipt kind must be {CAPTURE_RECEIPT_KIND!r}")
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise CaptureError(f"capture project root does not exist: {root}")
        producer = ProducerRecord.from_mapping(row.get("producer"))
        raw_capture = CaptureFile.from_mapping(
            row.get("raw_capture"), project_root=root, context="capture receipt.raw_capture"
        )
        duration = _seconds(row.get("duration_seconds"), "capture receipt.duration_seconds", positive=True)
        timebase = _string(row.get("timebase"), "capture receipt.timebase")

        raw_marks = _array(row.get("marks"), "capture receipt.marks")
        marks = tuple(
            CaptureMark.from_mapping(item, f"capture receipt.marks[{index}]")
            for index, item in enumerate(raw_marks)
        )

        raw_evidence = row.get("evidence", [])
        evidence_rows = _array(raw_evidence, "capture receipt.evidence")
        evidence = tuple(
            CaptureFile.from_mapping(
                item,
                project_root=root,
                context=f"capture receipt.evidence[{index}]",
            )
            for index, item in enumerate(evidence_rows)
        )

        raw_spans = _array(row.get("clean_spans"), "capture receipt.clean_spans")
        mark_map = {mark.label: mark.seconds for mark in marks}
        spans: list[CleanSpan] = []
        for index, raw_span in enumerate(raw_spans):
            context = f"capture receipt.clean_spans[{index}]"
            span_row = _object(raw_span, context)
            _only_keys(
                span_row,
                {
                    "id",
                    "span_id",
                    "begin_mark",
                    "end_mark",
                    "begin_seconds",
                    "end_seconds",
                    "start_seconds",
                    "stop_seconds",
                    "evidence",
                },
                context,
            )
            span_id = span_row.get("id", span_row.get("span_id"))
            if "id" in span_row and "span_id" in span_row and span_row["id"] != span_row["span_id"]:
                raise CaptureError(f"{context}.id and span_id disagree")
            begin_mark = span_row.get("begin_mark")
            end_mark = span_row.get("end_mark")
            begin_mark_string = _string(begin_mark, f"{context}.begin_mark")
            end_mark_string = _string(end_mark, f"{context}.end_mark")
            if begin_mark_string not in mark_map or end_mark_string not in mark_map:
                raise CaptureError(f"{context} references a missing mark")
            begin = mark_map[begin_mark_string]
            end = mark_map[end_mark_string]
            if "begin_seconds" in span_row:
                declared_begin = _seconds(span_row["begin_seconds"], f"{context}.begin_seconds")
                if not _same_seconds(declared_begin, begin):
                    raise CaptureError(f"{context}.begin_seconds does not bind begin_mark")
            elif "start_seconds" in span_row:
                declared_begin = _seconds(span_row["start_seconds"], f"{context}.start_seconds")
                if not _same_seconds(declared_begin, begin):
                    raise CaptureError(f"{context}.start_seconds does not bind begin_mark")
            if "end_seconds" in span_row:
                declared_end = _seconds(span_row["end_seconds"], f"{context}.end_seconds", positive=True)
                if not _same_seconds(declared_end, end):
                    raise CaptureError(f"{context}.end_seconds does not bind end_mark")
            elif "stop_seconds" in span_row:
                declared_end = _seconds(span_row["stop_seconds"], f"{context}.stop_seconds", positive=True)
                if not _same_seconds(declared_end, end):
                    raise CaptureError(f"{context}.stop_seconds does not bind end_mark")
            span_evidence_rows = _array(span_row.get("evidence", []), f"{context}.evidence")
            span_evidence = tuple(
                CaptureFile.from_mapping(
                    item,
                    project_root=root,
                    context=f"{context}.evidence[{evidence_index}]",
                )
                for evidence_index, item in enumerate(span_evidence_rows)
            )
            spans.append(
                CleanSpan(
                    span_id,
                    begin_mark_string,
                    end_mark_string,
                    begin,
                    end,
                    span_evidence,
                )
            )

        receipt_file: CaptureFile | None = None
        if receipt_path is not None:
            receipt_file = CaptureFile.from_path(receipt_path, project_root=root, role="capture-receipt")
        return cls(
            artifact_root=root,
            producer=producer,
            raw_capture=raw_capture,
            duration_seconds=duration,
            timebase=timebase,
            marks=marks,
            clean_spans=tuple(spans),
            overlap_policy=row.get("overlap_policy"),
            evidence=evidence,
            metadata=row.get("metadata", {}),
            receipt_file=receipt_file,
        )


def _assert_file_under_root(record: CaptureFile, root: Path, context: str) -> None:
    expected = _project_path(root, record.relative_path, context)
    if record.path != expected:
        raise CaptureError(
            f"{context} path does not match its project-relative path: "
            f"{record.relative_path}"
        )


def _assert_consistent_records(records: Iterable[CaptureFile]) -> None:
    """Reject two references to one path that disagree about its bytes."""

    seen: dict[str, CaptureFile] = {}
    for record in records:
        prior = seen.get(record.relative_path)
        if prior is None:
            seen[record.relative_path] = record
            continue
        if (prior.bytes, prior.sha256, prior.path) != (
            record.bytes,
            record.sha256,
            record.path,
        ):
            raise CaptureError(
                "capture receipt contains conflicting bindings for "
                f"{record.relative_path}"
            )


def load_capture_receipt(
    path: str | os.PathLike[str] | Path | Mapping[str, Any],
    *,
    project_root: str | os.PathLike[str] | Path | None = None,
) -> CaptureReceipt:
    """Read and verify one generic capture receipt without mutating it.

    ``path`` normally names the receipt JSON.  Passing a directory is also
    supported for convenience; the loader looks for ``capture-receipt.json``
    and then ``capture.json``.  All media/evidence paths in the JSON remain
    relative to ``project_root`` (the receipt's parent by default).
    """

    if isinstance(path, Mapping):
        if project_root is None:
            raise CaptureError(
                "project_root is required when loading an in-memory capture receipt"
            )
        return CaptureReceipt.from_mapping(path, project_root=project_root)
    requested = Path(path).expanduser()
    if requested.is_dir():
        candidates = (requested / "capture-receipt.json", requested / "capture.json")
        requested = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    receipt_path = requested.resolve()
    root = Path(project_root).expanduser().resolve() if project_root is not None else receipt_path.parent
    payload = _read_json(receipt_path)
    return CaptureReceipt.from_mapping(payload, project_root=root, receipt_path=receipt_path)


def verify_unchanged(
    receipt: CaptureReceipt | str | os.PathLike[str] | Path | Mapping[str, Any],
    *,
    project_root: str | os.PathLike[str] | Path | None = None,
) -> None:
    """Run :meth:`CaptureReceipt.verify_unchanged` on an object or JSON path."""

    if isinstance(receipt, CaptureReceipt):
        receipt.verify_unchanged()
        return
    load_capture_receipt(receipt, project_root=project_root).verify_unchanged()


__all__ = [
    "CAPTURE_RECEIPT_FORMAT_VERSION",
    "CAPTURE_RECEIPT_KIND",
    "OVERLAP_POLICIES",
    "OVERLAP_POLICY_FORBID",
    "OVERLAP_POLICY_ALLOW",
    "CaptureError",
    "CaptureReceiptError",
    "ProducerRecord",
    "CaptureFile",
    "CaptureMark",
    "CleanSpan",
    "CaptureReceipt",
    "load_capture_receipt",
    "verify_unchanged",
]
