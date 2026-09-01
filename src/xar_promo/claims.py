"""Hash-bound promotional claims and human-review records.

This module provides deliberately domain-neutral *structure* for project
claims.  A project adapter supplies the meaning of a claim and the validator
that produced its result; the toolchain only checks that the referenced bytes
exist, have not changed, and that all identifiers are closed.  In particular,
an approved :class:`ReviewResponse` is not a run-manifest sign-off and this
module never calls the sign-off operation.

The JSON contracts in this module are intentionally independent from the
native project/run manifests.  A project may keep a claims document beside a
run and bind it to a preserved deliverable by its exact path, byte count, and
SHA-256 digest.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ManifestError, PromoToolchainError
from .model import SourceRecord, portable_relative_path


CLAIMS_FORMAT_VERSION = 1
CLAIMS_KIND = "xar-promo-claims-bundle"
REVIEW_CHECKLIST_KIND = "xar-promo-review-checklist"
REVIEW_RESPONSE_KIND = "xar-promo-review-response"
REVIEW_FORMAT_VERSION = 1

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_REVIEW_RESULTS = frozenset({"pass", "fail", "not_applicable"})
_DECISIONS = frozenset({"approved", "rejected"})
_EXTERNAL_PRODUCER_FIELDS = frozenset(
    {"adapter_id", "tool", "tool_version", "operation", "execution"}
)


class ClaimsError(PromoToolchainError):
    """A claims or structured-review document is invalid or stale."""


# These aliases make the narrower failure classes discoverable without making
# callers depend on an implementation-specific exception hierarchy.
ClaimValidationError = ClaimsError
ReviewChecklistError = ClaimsError


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ClaimsError(f"{context} must be an object")
    return dict(value)


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ClaimsError(f"{context} must be an array")
    return value


def _string(value: Any, context: str, *, single_line: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaimsError(f"{context} must be a non-empty string")
    result = value.strip()
    if single_line and ("\x00" in result or "\n" in result or "\r" in result):
        raise ClaimsError(f"{context} must be NUL-free single-line text")
    return result


def _identifier(value: Any, context: str) -> str:
    result = _string(value, context, single_line=True)
    if _IDENTIFIER.fullmatch(result) is None:
        raise ClaimsError(
            f"{context} must start with an ASCII letter/digit and contain only "
            "letters, digits, '.', '_' or '-' (maximum 128 characters)"
        )
    return result


def _sha(value: Any, context: str) -> str:
    result = _string(value, context, single_line=True).upper()
    if _SHA256.fullmatch(result) is None:
        raise ClaimsError(f"{context} must be a SHA-256 digest")
    return result


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ClaimsError(f"{context} must be an integer >= {minimum}")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise ClaimsError(f"{context} has unsupported fields: {', '.join(extras)}")


def _portable_path(value: Any, context: str) -> str:
    try:
        return portable_relative_path(value, context)
    except ManifestError as exc:
        raise ClaimsError(str(exc)) from exc


def _json_value(value: Any, context: str) -> Any:
    """Return a JSON-safe copy and reject NaN/infinite/non-JSON values."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ClaimsError(f"{context} must contain only JSON values") from exc


def _external_producer(value: Any, context: str) -> dict[str, str]:
    """Validate the producer sibling accepted by an evidence wrapper.

    ``ArtifactBinding`` intentionally does not retain producer metadata, but
    accepting and then silently discarding malformed metadata would make a
    caller believe an external evidence binding had been checked.  Keep this
    small validation local to avoid making the claims contract depend on the
    evidence module's exception hierarchy.
    """

    row = _object(value, context)
    _only_keys(row, set(_EXTERNAL_PRODUCER_FIELDS), context)
    result = {
        key: _string(row.get(key), f"{context}.{key}", single_line=True)
        for key in sorted(_EXTERNAL_PRODUCER_FIELDS)
    }
    if result["execution"] != "external":
        raise ClaimsError(f"{context}.execution must be 'external'")
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ClaimsError(f"could not hash artifact {path}: {exc}") from exc
    return digest.hexdigest().upper()


def _canonical_json_sha256(value: Any) -> str:
    """Hash a JSON value independently of object key order/whitespace."""

    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ClaimsError("value cannot be canonically encoded as JSON") from exc
    return hashlib.sha256(payload).hexdigest().upper()


def _resolve_under_root(path: str, project_root: Path, context: str) -> Path:
    root = Path(project_root).expanduser().resolve()
    candidate = (root / Path(path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ClaimsError(f"{context} resolves outside project root {root}") from exc
    return candidate


def _same_artifact(left: "ArtifactBinding", right: "ArtifactBinding") -> bool:
    """Compare the immutable identity, ignoring descriptive optional fields."""

    return (
        left.artifact_id,
        left.path,
        left.bytes,
        left.sha256,
    ) == (
        right.artifact_id,
        right.path,
        right.bytes,
        right.sha256,
    )


@dataclass(frozen=True)
class ArtifactBinding:
    """A portable path and exact byte binding for a project artifact."""

    artifact_id: str
    path: str
    bytes: int
    sha256: str
    media_type: str | None = None
    source_name: str | None = None

    @classmethod
    def from_mapping(cls, value: Any, context: str = "artifact") -> "ArtifactBinding":
        if isinstance(value, cls):
            # Re-parse a manually constructed dataclass as well; callers must
            # not be able to bypass the portable-path and digest checks by
            # constructing fields directly.
            return cls.from_mapping(value.to_dict(), context)
        if isinstance(value, SourceRecord):
            # ``SourceRecord`` is a mutable-shape compatibility input rather
            # than a guarantee that the caller used ``from_mapping``.  Route
            # it through the same parser so a manually constructed record
            # cannot bypass identifier/path/digest validation.
            return cls.from_mapping(value.to_dict(), context)
        row = _object(value, context)
        # Accept a nested evidence/source-record wrapper when callers pass an
        # evidence bundle entry directly, while emitting one canonical shape.
        # ``bind_external_artifact`` adds a producer sibling; it belongs to
        # the evidence contract rather than the immutable artifact identity,
        # but accepting the wrapper here keeps the claims boundary composable.
        if "artifact" in row:
            _only_keys(row, {"artifact", "producer"}, context)
            if "producer" in row:
                _external_producer(row["producer"], f"{context}.producer")
            row = _object(row["artifact"], f"{context}.artifact")
        allowed = {
            "id",
            "artifact_id",
            "path",
            "bytes",
            "sha256",
            "media_type",
            "source_name",
            # These descriptive SourceRecord fields are accepted when a
            # project passes a native run/evidence artifact directly.  They
            # are intentionally not emitted: the claims contract only binds
            # portable identity and exact bytes.
            "collection",
            "role",
            "label",
        }
        _only_keys(row, allowed, context)
        id_value = row.get("id")
        if id_value is None:
            id_value = row.get("artifact_id")
        elif "artifact_id" in row:
            raise ClaimsError(f"{context} must not provide both id and artifact_id")
        return cls(
            _identifier(id_value, f"{context}.id"),
            _portable_path(row.get("path"), f"{context}.path"),
            _integer(row.get("bytes"), f"{context}.bytes"),
            _sha(row.get("sha256"), f"{context}.sha256"),
            None
            if row.get("media_type") is None
            else _string(row.get("media_type"), f"{context}.media_type", single_line=True),
            None
            if row.get("source_name") is None
            else _string(row.get("source_name"), f"{context}.source_name", single_line=True),
        )

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        project_root: Path,
        artifact_id: str,
        media_type: str | None = None,
        source_name: str | None = None,
    ) -> "ArtifactBinding":
        """Create a binding from existing bytes without copying or mutating them."""

        root = Path(project_root).expanduser().resolve()
        absolute = Path(path).expanduser().resolve()
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise ClaimsError(
                f"artifact must be inside project root {root}: {absolute}"
            ) from exc
        if not absolute.is_file():
            raise ClaimsError(f"artifact was not found: {absolute}")
        return cls(
            _identifier(artifact_id, "artifact_id"),
            _portable_path(relative, "artifact.path"),
            absolute.stat().st_size,
            _sha256_file(absolute),
            None if media_type is None else _string(media_type, "media_type", single_line=True),
            None
            if source_name is None
            else _string(source_name, "source_name", single_line=True),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.artifact_id,
            "path": self.path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }
        if self.media_type is not None:
            result["media_type"] = self.media_type
        if self.source_name is not None:
            result["source_name"] = self.source_name
        return result

    def verify(self, project_root: Path) -> "ArtifactBinding":
        """Recompute the current file binding and return this immutable record."""

        # Reparse direct dataclass construction as well; otherwise callers
        # could bypass the wire-level path and digest invariants by assigning
        # fields without going through ``from_mapping``.
        canonical = ArtifactBinding.from_mapping(self.to_dict(), "artifact")
        path = _resolve_under_root(
            canonical.path, project_root, f"artifact {canonical.artifact_id}.path"
        )
        if not path.is_file():
            raise ClaimsError(f"artifact {canonical.artifact_id!r} was not found: {path}")
        actual_bytes = path.stat().st_size
        if actual_bytes != canonical.bytes:
            raise ClaimsError(
                f"artifact {canonical.artifact_id!r} byte count does not match its binding"
            )
        actual_sha = _sha256_file(path)
        if actual_sha != canonical.sha256:
            raise ClaimsError(
                f"artifact {canonical.artifact_id!r} SHA-256 does not match its binding"
            )
        return canonical


@dataclass(frozen=True)
class ClaimBinding:
    """One project-supplied claim and its hash-bound evidence references."""

    claim_id: str
    evidence_artifact_ids: tuple[str, ...]
    validator_id: str
    result: str
    text: str | None = None
    details: Any = None

    @classmethod
    def from_mapping(cls, value: Any, context: str = "claim") -> "ClaimBinding":
        row = _object(value, context)
        allowed = {
            "id",
            "claim_id",
            "evidence_artifact_ids",
            "validator_id",
            "validator",
            "result",
            "text",
            "details",
        }
        _only_keys(row, allowed, context)
        claim_id_value = row.get("id")
        if claim_id_value is None:
            claim_id_value = row.get("claim_id")
        elif "claim_id" in row:
            raise ClaimsError(f"{context} must not provide both id and claim_id")

        validator_id = row.get("validator_id")
        result = row.get("result")
        nested_validator = row.get("validator")
        if nested_validator is not None:
            if validator_id is not None or result is not None:
                raise ClaimsError(
                    f"{context} must use either validator or validator_id/result, not both"
                )
            validator = _object(nested_validator, f"{context}.validator")
            _only_keys(validator, {"id", "validator_id", "result"}, f"{context}.validator")
            if "id" in validator and "validator_id" in validator:
                raise ClaimsError(
                    f"{context}.validator must not provide both id and validator_id"
                )
            validator_id = validator.get("id", validator.get("validator_id"))
            result = validator.get("result")
        evidence = _array(
            row.get("evidence_artifact_ids"),
            f"{context}.evidence_artifact_ids",
        )
        evidence_ids = tuple(
            _identifier(item, f"{context}.evidence_artifact_ids[{index}]")
            for index, item in enumerate(evidence)
        )
        if not evidence_ids:
            raise ClaimsError(f"{context}.evidence_artifact_ids must not be empty")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ClaimsError(f"{context}.evidence_artifact_ids contains duplicates")
        text = row.get("text")
        if text is not None:
            text = _string(text, f"{context}.text", single_line=True)
        details = None if row.get("details") is None else _json_value(row["details"], f"{context}.details")
        return cls(
            _identifier(claim_id_value, f"{context}.id"),
            evidence_ids,
            _identifier(validator_id, f"{context}.validator_id"),
            _string(result, f"{context}.result", single_line=True),
            text,
            details,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.claim_id,
            "evidence_artifact_ids": list(self.evidence_artifact_ids),
            "validator_id": self.validator_id,
            "result": self.result,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.details is not None:
            result["details"] = self.details
        return result


def _artifact_ids(value: Iterable[Any] | Mapping[str, Any], context: str) -> set[str]:
    if value is None:
        raise ClaimsError(f"{context} ids must be provided")
    if isinstance(value, Mapping):
        raw_ids = value.keys()
    else:
        if isinstance(value, (str, bytes, bytearray)):
            raise ClaimsError(f"{context} ids must be an array or mapping, not text")
        raw_ids = value
    result = {_identifier(item, f"{context} id") for item in raw_ids}
    return result


def validate_claim_bindings(
    claims: Iterable[ClaimBinding | Mapping[str, Any]],
    *,
    evidence_artifact_ids: Iterable[Any] | Mapping[str, Any],
    deliverable_sha256: str | None = None,
) -> tuple[ClaimBinding, ...]:
    """Validate claim IDs and evidence references without judging claim truth.

    ``deliverable_sha256`` is optional because a caller may validate a claim
    list before assembling its bundle.  When supplied it is only format
    checked; the bundle-level validator binds it to the actual deliverable.
    """

    if deliverable_sha256 is not None:
        _sha(deliverable_sha256, "deliverable_sha256")
    available = _artifact_ids(evidence_artifact_ids, "evidence_artifact")
    if not available:
        raise ClaimsError("evidence_artifact_ids must contain at least one artifact")
    normalized: list[ClaimBinding] = []
    seen: set[str] = set()
    for index, raw in enumerate(claims):
        claim = (
            ClaimBinding.from_mapping(raw.to_dict(), f"claims[{index}]")
            if isinstance(raw, ClaimBinding)
            else ClaimBinding.from_mapping(raw, f"claims[{index}]")
        )
        if claim.claim_id in seen:
            raise ClaimsError(f"claims contains duplicate id {claim.claim_id!r}")
        seen.add(claim.claim_id)
        missing = sorted(set(claim.evidence_artifact_ids) - available)
        if missing:
            raise ClaimsError(
                f"claim {claim.claim_id!r} references unknown evidence artifact(s): "
                + ", ".join(missing)
            )
        normalized.append(claim)
    if not normalized:
        raise ClaimsError("claims must contain at least one claim")
    return tuple(normalized)


@dataclass(frozen=True)
class ClaimBundle:
    """A complete, independently hash-bound claims document."""

    deliverable: ArtifactBinding
    evidence_artifacts: tuple[ArtifactBinding, ...]
    claims: tuple[ClaimBinding, ...]
    metadata: Any = None

    @classmethod
    def from_mapping(cls, value: Any) -> "ClaimBundle":
        row = _object(value, "claims bundle")
        _only_keys(
            row,
            {"format_version", "kind", "deliverable", "evidence_artifacts", "claims", "metadata"},
            "claims bundle",
        )
        if (
            isinstance(row.get("format_version"), bool)
            or row.get("format_version") != CLAIMS_FORMAT_VERSION
            or row.get("kind") != CLAIMS_KIND
        ):
            raise ClaimsError(f"claims bundle must declare {CLAIMS_KIND} format v1")
        deliverable = ArtifactBinding.from_mapping(row.get("deliverable"), "deliverable")
        evidence_rows = _array(row.get("evidence_artifacts"), "evidence_artifacts")
        evidence: list[ArtifactBinding] = []
        seen: set[str] = set()
        for index, raw in enumerate(evidence_rows):
            artifact = ArtifactBinding.from_mapping(raw, f"evidence_artifacts[{index}]")
            if artifact.artifact_id in seen:
                raise ClaimsError(f"evidence_artifacts contains duplicate id {artifact.artifact_id!r}")
            if artifact.artifact_id == deliverable.artifact_id:
                raise ClaimsError("deliverable id must be distinct from evidence artifact ids")
            seen.add(artifact.artifact_id)
            evidence.append(artifact)
        claims = validate_claim_bindings(
            _array(row.get("claims"), "claims"),
            evidence_artifact_ids=seen,
            deliverable_sha256=deliverable.sha256,
        )
        metadata = None if row.get("metadata") is None else _json_value(row["metadata"], "claims bundle.metadata")
        return cls(deliverable, tuple(evidence), claims, metadata)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format_version": CLAIMS_FORMAT_VERSION,
            "kind": CLAIMS_KIND,
            "deliverable": self.deliverable.to_dict(),
            "evidence_artifacts": [item.to_dict() for item in self.evidence_artifacts],
            "claims": [item.to_dict() for item in self.claims],
        }
        if self.metadata is not None:
            result["metadata"] = self.metadata
        return result

    def verify(
        self,
        project_root: Path,
        *,
        expected_deliverable_sha256: str | None = None,
    ) -> "ClaimBundle":
        # Reparse dataclass instances too, so direct construction cannot
        # bypass identifier/path/digest validation.
        canonical = ClaimBundle.from_mapping(self.to_dict())
        canonical.deliverable.verify(project_root)
        if expected_deliverable_sha256 is not None:
            expected = _sha(expected_deliverable_sha256, "expected_deliverable_sha256")
            if expected != canonical.deliverable.sha256:
                raise ClaimsError("deliverable SHA-256 does not match the expected digest")
        evidence_ids: set[str] = set()
        for artifact in canonical.evidence_artifacts:
            if artifact.artifact_id in evidence_ids:
                raise ClaimsError(
                    f"evidence_artifacts contains duplicate id {artifact.artifact_id!r}"
                )
            if artifact.artifact_id == canonical.deliverable.artifact_id:
                raise ClaimsError("deliverable id must be distinct from evidence artifact ids")
            evidence_ids.add(artifact.artifact_id)
            artifact.verify(project_root)
        # Re-run reference validation after parsing so callers using a manually
        # constructed dataclass receive the same closed-graph guarantees.
        validate_claim_bindings(
            canonical.claims,
            evidence_artifact_ids=evidence_ids,
            deliverable_sha256=canonical.deliverable.sha256,
        )
        return canonical


def _read_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ClaimsError(f"{context} was not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimsError(f"could not read {context} {path}: {exc}") from exc
    return _object(value, context)


def _write_new_json(path: Path, value: Mapping[str, Any], context: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ClaimsError(f"refusing to overwrite existing {context}: {target}") from exc
    except OSError as exc:
        raise ClaimsError(f"could not write {context} {target}: {exc}") from exc


def validate_claim_bundle(
    value: Mapping[str, Any] | ClaimBundle,
    *,
    project_root: Path,
    expected_deliverable_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate structure and all on-disk hash bindings, returning canonical JSON."""

    bundle = value if isinstance(value, ClaimBundle) else ClaimBundle.from_mapping(value)
    bundle = bundle.verify(
        project_root, expected_deliverable_sha256=expected_deliverable_sha256
    )
    return bundle.to_dict()


def load_claim_bundle(
    path: Path,
    *,
    project_root: Path,
    expected_deliverable_sha256: str | None = None,
) -> dict[str, Any]:
    """Read and reverify a claims bundle against current project bytes."""

    return validate_claim_bundle(
        _read_json_object(path, "claims bundle"),
        project_root=project_root,
        expected_deliverable_sha256=expected_deliverable_sha256,
    )


def write_claim_bundle(
    path: Path,
    *,
    project_root: Path,
    deliverable: ArtifactBinding | SourceRecord | Mapping[str, Any],
    evidence_artifacts: Iterable[ArtifactBinding | SourceRecord | Mapping[str, Any]],
    claims: Iterable[ClaimBinding | Mapping[str, Any]],
    metadata: Any = None,
) -> dict[str, Any]:
    """Write one new claims bundle after validating every byte binding."""

    normalized_evidence = tuple(
        ArtifactBinding.from_mapping(item, f"evidence_artifacts[{index}]")
        for index, item in enumerate(evidence_artifacts)
    )
    bundle = ClaimBundle(
        ArtifactBinding.from_mapping(deliverable, "deliverable"),
        normalized_evidence,
        validate_claim_bindings(
            claims,
            evidence_artifact_ids={item.artifact_id for item in normalized_evidence},
        ),
        None if metadata is None else _json_value(metadata, "claims bundle.metadata"),
    )
    bundle = bundle.verify(project_root)
    payload = bundle.to_dict()
    _write_new_json(path, payload, "claims bundle")
    return payload


@dataclass(frozen=True)
class ChecklistItem:
    """One human-attested review row."""

    item_id: str
    instruction: str
    required: bool = True

    @classmethod
    def from_mapping(cls, value: Any, context: str = "checklist item") -> "ChecklistItem":
        row = _object(value, context)
        _only_keys(row, {"id", "item_id", "instruction", "required"}, context)
        item_id = row.get("id")
        if item_id is None:
            item_id = row.get("item_id")
        elif "item_id" in row:
            raise ClaimsError(f"{context} must not provide both id and item_id")
        required = row.get("required", True)
        if not isinstance(required, bool):
            raise ClaimsError(f"{context}.required must be a boolean")
        return cls(
            _identifier(item_id, f"{context}.id"),
            _string(row.get("instruction"), f"{context}.instruction", single_line=True),
            required,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.item_id, "instruction": self.instruction, "required": self.required}


@dataclass(frozen=True)
class ReviewChecklist:
    """A reusable checklist, optionally bound to a deliverable's bytes."""

    checklist_id: str
    items: tuple[ChecklistItem, ...]
    deliverable: ArtifactBinding | None = None
    metadata: Any = None

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewChecklist":
        row = _object(value, "review checklist")
        _only_keys(
            row,
            {
                "format_version",
                "kind",
                "checklist_id",
                "items",
                "deliverable",
                "metadata",
                "template_only",
                "is_signoff",
            },
            "review checklist",
        )
        if (
            isinstance(row.get("format_version"), bool)
            or row.get("format_version") != REVIEW_FORMAT_VERSION
            or row.get("kind") != REVIEW_CHECKLIST_KIND
        ):
            raise ClaimsError(f"review checklist must declare {REVIEW_CHECKLIST_KIND} format v1")
        if "template_only" in row and row["template_only"] is not True:
            raise ClaimsError("review checklist template_only must be true")
        if "is_signoff" in row and row["is_signoff"] is not False:
            raise ClaimsError("review checklist is_signoff must be false")
        raw_items = _array(row.get("items"), "review checklist.items")
        items = tuple(
            ChecklistItem.from_mapping(item, f"review checklist.items[{index}]")
            for index, item in enumerate(raw_items)
        )
        if not items:
            raise ClaimsError("review checklist.items must contain at least one item")
        ids = [item.item_id for item in items]
        if len(ids) != len(set(ids)):
            raise ClaimsError("review checklist.items contains duplicate ids")
        deliverable = (
            None
            if row.get("deliverable") is None
            else ArtifactBinding.from_mapping(row["deliverable"], "review checklist.deliverable")
        )
        metadata = None if row.get("metadata") is None else _json_value(row["metadata"], "review checklist.metadata")
        return cls(
            _identifier(row.get("checklist_id"), "review checklist.checklist_id"),
            items,
            deliverable,
            metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format_version": REVIEW_FORMAT_VERSION,
            "kind": REVIEW_CHECKLIST_KIND,
            "checklist_id": self.checklist_id,
            "items": [item.to_dict() for item in self.items],
            "template_only": True,
            "is_signoff": False,
        }
        if self.deliverable is not None:
            result["deliverable"] = self.deliverable.to_dict()
        if self.metadata is not None:
            result["metadata"] = self.metadata
        return result

    @property
    def checklist_sha256(self) -> str:
        """Return the canonical digest that a response must carry.

        The digest covers the normalized checklist document, including its
        stable format/kind markers, row order, deliverable binding, and
        metadata.  It intentionally does not include a response field, so a
        response can bind to the checklist without a recursive structure.
        """

        canonical = ReviewChecklist.from_mapping(self.to_dict())
        return _canonical_json_sha256(canonical.to_dict())

    # ``sha256`` is a short compatibility spelling for callers that treat a
    # checklist like the other hash-bound records in this package.
    @property
    def sha256(self) -> str:
        return self.checklist_sha256

    def verify(self, project_root: Path) -> "ReviewChecklist":
        if self.deliverable is not None:
            self.deliverable.verify(project_root)
        return self


@dataclass(frozen=True)
class ReviewResult:
    """A human response for one checklist row."""

    item_id: str
    result: str
    note: str | None = None

    @classmethod
    def from_mapping(cls, value: Any, context: str = "review result") -> "ReviewResult":
        row = _object(value, context)
        _only_keys(row, {"item_id", "id", "result", "note"}, context)
        item_id = row.get("item_id")
        if item_id is None:
            item_id = row.get("id")
        elif "id" in row:
            raise ClaimsError(f"{context} must not provide both item_id and id")
        result = _string(row.get("result"), f"{context}.result", single_line=True)
        if result not in _REVIEW_RESULTS:
            raise ClaimsError(
                f"{context}.result must be one of: {', '.join(sorted(_REVIEW_RESULTS))}"
            )
        note = None if row.get("note") is None else _string(row["note"], f"{context}.note", single_line=True)
        return cls(_identifier(item_id, f"{context}.item_id"), result, note)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"item_id": self.item_id, "result": self.result}
        if self.note is not None:
            result["note"] = self.note
        return result


def _timestamp(value: Any, context: str) -> str:
    result = _string(value, context, single_line=True)
    try:
        parsed = dt.datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaimsError(f"{context} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ClaimsError(f"{context} must include a timezone")
    return result


@dataclass(frozen=True)
class ReviewResponse:
    """A completed human response, explicitly distinct from run sign-off."""

    checklist_id: str
    deliverable: ArtifactBinding
    reviewer: str
    reviewed_at: str
    decision: str
    results: tuple[ReviewResult, ...]
    notes: str | None = None
    checklist_sha256: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "ReviewResponse":
        row = _object(value, "review response")
        _only_keys(
            row,
            {
                "format_version",
                "kind",
                "checklist_id",
                "deliverable",
                "reviewer",
                "reviewed_at",
                "decision",
                "results",
                "notes",
                "checklist_sha256",
                "is_signoff",
                "signoff_recorded",
            },
            "review response",
        )
        if (
            isinstance(row.get("format_version"), bool)
            or row.get("format_version") != REVIEW_FORMAT_VERSION
            or row.get("kind") != REVIEW_RESPONSE_KIND
        ):
            raise ClaimsError(f"review response must declare {REVIEW_RESPONSE_KIND} format v1")
        if row.get("is_signoff", False) is not False:
            raise ClaimsError("review response is_signoff must be false")
        if row.get("signoff_recorded", False) is not False:
            raise ClaimsError("review response signoff_recorded must be false")
        decision = _string(row.get("decision"), "review response.decision", single_line=True)
        if decision not in _DECISIONS:
            raise ClaimsError("review response.decision must be approved or rejected")
        raw_results = _array(row.get("results"), "review response.results")
        results = tuple(
            ReviewResult.from_mapping(item, f"review response.results[{index}]")
            for index, item in enumerate(raw_results)
        )
        if not results:
            raise ClaimsError("review response.results must contain at least one item")
        ids = [item.item_id for item in results]
        if len(ids) != len(set(ids)):
            raise ClaimsError("review response.results contains duplicate item ids")
        notes = None if row.get("notes") is None else _string(row["notes"], "review response.notes", single_line=True)
        checklist_sha256 = (
            None
            if row.get("checklist_sha256") is None
            else _sha(row["checklist_sha256"], "review response.checklist_sha256")
        )
        return cls(
            _identifier(row.get("checklist_id"), "review response.checklist_id"),
            ArtifactBinding.from_mapping(row.get("deliverable"), "review response.deliverable"),
            _string(row.get("reviewer"), "review response.reviewer", single_line=True),
            _timestamp(row.get("reviewed_at"), "review response.reviewed_at"),
            decision,
            results,
            notes,
            checklist_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "format_version": REVIEW_FORMAT_VERSION,
            "kind": REVIEW_RESPONSE_KIND,
            "checklist_id": self.checklist_id,
            "deliverable": self.deliverable.to_dict(),
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "decision": self.decision,
            "results": [item.to_dict() for item in self.results],
            "is_signoff": False,
            "signoff_recorded": False,
        }
        if self.notes is not None:
            result["notes"] = self.notes
        if self.checklist_sha256 is not None:
            result["checklist_sha256"] = self.checklist_sha256
        return result


def validate_review_response(
    response: ReviewResponse | Mapping[str, Any],
    checklist: ReviewChecklist | Mapping[str, Any],
    *,
    project_root: Path,
    expected_deliverable: ArtifactBinding | SourceRecord | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a response against a checklist and exact deliverable bytes.

    This function performs no mutation and never writes a ``RunManifest`` or
    sign-off row.  An ``approved`` response is only an attestation record;
    callers must still invoke the existing explicit ``signoff`` operation if
    release policy requires one.
    """

    checklist_obj = (
        ReviewChecklist.from_mapping(checklist.to_dict())
        if isinstance(checklist, ReviewChecklist)
        else ReviewChecklist.from_mapping(checklist)
    )
    response_obj = (
        ReviewResponse.from_mapping(response.to_dict())
        if isinstance(response, ReviewResponse)
        else ReviewResponse.from_mapping(response)
    )
    checklist_obj.verify(project_root)
    response_obj.deliverable.verify(project_root)
    if response_obj.checklist_id != checklist_obj.checklist_id:
        raise ClaimsError("review response checklist_id does not match the checklist")
    if checklist_obj.deliverable is not None and not _same_artifact(
        checklist_obj.deliverable, response_obj.deliverable
    ):
        raise ClaimsError("review response deliverable does not match the checklist binding")
    if expected_deliverable is not None:
        expected = ArtifactBinding.from_mapping(expected_deliverable, "expected_deliverable")
        if not _same_artifact(expected, response_obj.deliverable):
            raise ClaimsError("review response deliverable does not match expected bytes")
        expected.verify(project_root)

    expected_ids = {item.item_id for item in checklist_obj.items}
    supplied_ids = {item.item_id for item in response_obj.results}
    missing = sorted(expected_ids - supplied_ids)
    extra = sorted(supplied_ids - expected_ids)
    if missing:
        raise ClaimsError("review response is missing checklist result(s): " + ", ".join(missing))
    if extra:
        raise ClaimsError("review response contains unknown checklist result(s): " + ", ".join(extra))
    expected_checklist_sha256 = checklist_obj.checklist_sha256
    if response_obj.checklist_sha256 is None:
        raise ClaimsError(
            "review response is legacy_unbound: checklist_sha256 is required to bind "
            "the checklist hash"
        )
    if response_obj.checklist_sha256 != expected_checklist_sha256:
        raise ClaimsError(
            "review response checklist_sha256 does not match the checklist hash"
        )
    result_map = {item.item_id: item.result for item in response_obj.results}
    if response_obj.decision == "approved":
        failed_required = [
            item.item_id
            for item in checklist_obj.items
            if item.required and result_map[item.item_id] != "pass"
        ]
        if failed_required:
            raise ClaimsError(
                "approved review response has non-passing required item(s): "
                + ", ".join(failed_required)
            )
    return response_obj.to_dict()


def write_review_checklist(
    path: Path,
    checklist: ReviewChecklist | Mapping[str, Any],
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Write a checklist template exclusively; this is never a sign-off."""

    value = (
        ReviewChecklist.from_mapping(checklist.to_dict())
        if isinstance(checklist, ReviewChecklist)
        else ReviewChecklist.from_mapping(checklist)
    )
    if project_root is not None:
        value.verify(project_root)
    payload = value.to_dict()
    _write_new_json(path, payload, "review checklist")
    return payload


def load_review_checklist(path: Path, *, project_root: Path | None = None) -> dict[str, Any]:
    value = ReviewChecklist.from_mapping(_read_json_object(path, "review checklist"))
    if project_root is not None:
        value.verify(project_root)
    return value.to_dict()


def write_review_response(
    path: Path,
    response: ReviewResponse | Mapping[str, Any],
    checklist: ReviewChecklist | Mapping[str, Any],
    *,
    project_root: Path,
    expected_deliverable: ArtifactBinding | SourceRecord | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one response after validation; never records run-manifest sign-off."""

    checklist_obj = (
        ReviewChecklist.from_mapping(checklist.to_dict())
        if isinstance(checklist, ReviewChecklist)
        else ReviewChecklist.from_mapping(checklist)
    )
    response_obj = (
        ReviewResponse.from_mapping(response.to_dict())
        if isinstance(response, ReviewResponse)
        else ReviewResponse.from_mapping(response)
    )
    payload = validate_review_response(
        response_obj,
        checklist_obj,
        project_root=project_root,
        expected_deliverable=expected_deliverable,
    )
    _write_new_json(path, payload, "review response")
    return payload


def load_review_response(
    path: Path,
    checklist: ReviewChecklist | Mapping[str, Any] | Path,
    *,
    project_root: Path,
    expected_deliverable: ArtifactBinding | SourceRecord | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(checklist, Path):
        checklist = ReviewChecklist.from_mapping(_read_json_object(checklist, "review checklist"))
    return validate_review_response(
        _read_json_object(path, "review response"),
        checklist,
        project_root=project_root,
        expected_deliverable=expected_deliverable,
    )


__all__ = [
    "ArtifactBinding",
    "ClaimBinding",
    "ClaimBundle",
    "ClaimValidationError",
    "ClaimsError",
    "ChecklistItem",
    "REVIEW_CHECKLIST_KIND",
    "REVIEW_FORMAT_VERSION",
    "REVIEW_RESPONSE_KIND",
    "ReviewChecklist",
    "ReviewChecklistError",
    "ReviewResponse",
    "ReviewResult",
    "CLAIMS_FORMAT_VERSION",
    "CLAIMS_KIND",
    "load_claim_bundle",
    "load_review_checklist",
    "load_review_response",
    "validate_claim_bindings",
    "validate_claim_bundle",
    "validate_review_response",
    "write_claim_bundle",
    "write_review_checklist",
    "write_review_response",
]
