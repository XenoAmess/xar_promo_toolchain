from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from xar_promo.claims import (  # noqa: E402
    ArtifactBinding,
    ClaimBinding,
    ClaimsError,
    ChecklistItem,
    ClaimBundle,
    ReviewChecklist,
    ReviewResponse,
    ReviewResult,
    load_claim_bundle,
    load_review_response,
    validate_claim_bindings,
    validate_claim_bundle,
    validate_review_response,
    write_claim_bundle,
    write_review_checklist,
    write_review_response,
)


class ClaimsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.media = self.root / "media" / "final.mp4"
        self.evidence_one = self.root / "evidence" / "frame.png"
        self.evidence_two = self.root / "evidence" / "state.json"
        self.media.parent.mkdir()
        self.evidence_one.parent.mkdir()
        self.media.write_bytes(b"deliverable bytes")
        self.evidence_one.write_bytes(b"frame evidence")
        self.evidence_two.write_bytes(b'{"state":"ready"}\n')
        self.deliverable = ArtifactBinding.from_file(
            self.media,
            project_root=self.root,
            artifact_id="final-video",
            media_type="video/mp4",
        )
        self.frame = ArtifactBinding.from_file(
            self.evidence_one,
            project_root=self.root,
            artifact_id="frame-001",
            media_type="image/png",
        )
        self.state = ArtifactBinding.from_file(
            self.evidence_two,
            project_root=self.root,
            artifact_id="state-001",
            media_type="application/json",
        )
        self.claims = [
            ClaimBinding(
                "claim-ready",
                ("frame-001", "state-001"),
                "vivhite-semantic-v1",
                "pass",
                text="The recorded state is ready.",
                details={"source": "fixture"},
            )
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_claim_bundle_round_trip_and_exact_hash_closure(self) -> None:
        path = self.root / "claims.json"
        payload = write_claim_bundle(
            path,
            project_root=self.root,
            deliverable=self.deliverable,
            evidence_artifacts=(self.frame, self.state),
            claims=self.claims,
            metadata={"producer": "fixture"},
        )
        self.assertEqual("xar-promo-claims-bundle", payload["kind"])
        loaded = load_claim_bundle(path, project_root=self.root)
        self.assertEqual(payload, loaded)
        self.assertEqual(self.deliverable.sha256, loaded["deliverable"]["sha256"])

    def test_claim_references_are_closed_and_ids_unique(self) -> None:
        with self.assertRaisesRegex(ClaimsError, "array or mapping"):
            validate_claim_bindings(
                [
                    {
                        "id": "scalar-ref",
                        "evidence_artifact_ids": ["f"],
                        "validator_id": "validator",
                        "result": "pass",
                    }
                ],
                evidence_artifact_ids="frame-001",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ClaimsError, "unknown evidence"):
            validate_claim_bindings(
                [
                    {
                        "id": "missing-ref",
                        "evidence_artifact_ids": ["does-not-exist"],
                        "validator_id": "validator",
                        "result": "pass",
                    }
                ],
                evidence_artifact_ids={"frame-001"},
            )
        duplicate = ClaimBinding("same", ("frame-001",), "validator", "pass")
        with self.assertRaisesRegex(ClaimsError, "duplicate id"):
            validate_claim_bindings(
                [duplicate, duplicate], evidence_artifact_ids={"frame-001"}
            )

    def test_mutated_deliverable_or_evidence_fails_closed(self) -> None:
        path = self.root / "claims.json"
        write_claim_bundle(
            path,
            project_root=self.root,
            deliverable=self.deliverable,
            evidence_artifacts=(self.frame, self.state),
            claims=self.claims,
        )
        self.media.write_bytes(b"changed deliverable bytes")
        with self.assertRaisesRegex(ClaimsError, "artifact 'final-video'.*(byte count|SHA-256)"):
            load_claim_bundle(path, project_root=self.root)

        # Restore the deliverable and mutate an evidence file to demonstrate
        # that the second edge of the graph is independently checked.
        self.media.write_bytes(b"deliverable bytes")
        self.evidence_one.write_bytes(b"changed evidence")
        with self.assertRaisesRegex(ClaimsError, "artifact 'frame-001'.*(byte count|SHA-256)"):
            load_claim_bundle(path, project_root=self.root)

    def test_expected_deliverable_digest_is_checked(self) -> None:
        wrong = "0" * 64
        bundle = ClaimBundle(self.deliverable, (self.frame, self.state), tuple(self.claims))
        with self.assertRaisesRegex(ClaimsError, "expected digest"):
            validate_claim_bundle(
                bundle,
                project_root=self.root,
                expected_deliverable_sha256=wrong,
            )

    def test_claim_bundle_refuses_overwrite(self) -> None:
        path = self.root / "claims.json"
        write_claim_bundle(
            path,
            project_root=self.root,
            deliverable=self.deliverable,
            evidence_artifacts=(self.frame, self.state),
            claims=self.claims,
        )
        with self.assertRaisesRegex(ClaimsError, "overwrite existing claims bundle"):
            write_claim_bundle(
                path,
                project_root=self.root,
                deliverable=self.deliverable,
                evidence_artifacts=(self.frame, self.state),
                claims=self.claims,
            )

    def test_artifact_binding_accepts_external_evidence_wrapper(self) -> None:
        wrapped = {
            "artifact": self.frame.to_dict(),
            "producer": {
                "adapter_id": "fixture",
                "tool": "fixture-tool",
                "tool_version": "1",
                "operation": "capture",
                "execution": "external",
            },
        }
        binding = ArtifactBinding.from_mapping(wrapped)
        self.assertEqual(self.frame, binding)
        wrapped["producer"] = {
            "adapter_id": "fixture",
            "tool": "fixture-tool",
            "tool_version": "1",
            "operation": "capture",
            "execution": "local",
        }
        with self.assertRaisesRegex(ClaimsError, "execution"):
            ArtifactBinding.from_mapping(wrapped)


class StructuredReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.media = self.root / "final.mp4"
        self.media.write_bytes(b"exact final bytes")
        self.binding = ArtifactBinding.from_file(
            self.media,
            project_root=self.root,
            artifact_id="final-video",
            media_type="video/mp4",
        )
        self.checklist = ReviewChecklist(
            "review-001",
            (
                ChecklistItem("watch-complete", "Watch the exact deliverable at 1x.", True),
                ChecklistItem("captions-readable", "Confirm captions are readable.", False),
            ),
            deliverable=self.binding,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _response(self, *, decision: str = "approved", optional: str = "pass") -> ReviewResponse:
        return ReviewResponse(
            checklist_id="review-001",
            deliverable=self.binding,
            reviewer="Human Reviewer",
            reviewed_at="2026-09-02T12:00:00+08:00",
            decision=decision,
            results=(
                ReviewResult("watch-complete", "pass"),
                ReviewResult("captions-readable", optional),
            ),
            notes="Watched continuously at normal speed.",
            checklist_sha256=self.checklist.checklist_sha256,
        )

    def test_checklist_can_be_unbound_template(self) -> None:
        template = ReviewChecklist(
            "template-001",
            (ChecklistItem("picture", "Check picture."),),
        )
        path = self.root / "checklist.json"
        payload = write_review_checklist(path, template)
        self.assertIsNone(payload.get("deliverable"))
        loaded = ReviewChecklist.from_mapping(json.loads(path.read_text(encoding="utf-8")))
        self.assertIsNone(loaded.deliverable)

    def test_approved_response_requires_all_required_rows_to_pass(self) -> None:
        response = self._response(optional="not_applicable")
        payload = validate_review_response(response, self.checklist, project_root=self.root)
        self.assertFalse(payload["is_signoff"])
        self.assertFalse(payload["signoff_recorded"])
        self.assertEqual("approved", payload["decision"])

        failed = ReviewResponse(
            response.checklist_id,
            response.deliverable,
            response.reviewer,
            response.reviewed_at,
            "approved",
            (ReviewResult("watch-complete", "fail"), ReviewResult("captions-readable", "pass")),
            None,
            self.checklist.checklist_sha256,
        )
        with self.assertRaisesRegex(ClaimsError, "non-passing required"):
            validate_review_response(failed, self.checklist, project_root=self.root)

    def test_rejected_response_may_record_failed_rows(self) -> None:
        response = self._response(decision="rejected", optional="fail")
        payload = validate_review_response(response, self.checklist, project_root=self.root)
        self.assertEqual("rejected", payload["decision"])
        self.assertFalse(payload["is_signoff"])

    def test_response_requires_exact_checklist_rows_and_binding(self) -> None:
        response = ReviewResponse(
            "review-001",
            self.binding,
            "Reviewer",
            "2026-09-02T12:00:00Z",
            "approved",
            (ReviewResult("watch-complete", "pass"),),
        )
        with self.assertRaisesRegex(ClaimsError, "missing checklist"):
            validate_review_response(response, self.checklist, project_root=self.root)

        other = self.root / "other.mp4"
        other.write_bytes(b"other bytes")
        other_binding = ArtifactBinding.from_file(
            other, project_root=self.root, artifact_id="other-video"
        )
        mismatched = ReviewResponse(
            "review-001",
            other_binding,
            "Reviewer",
            "2026-09-02T12:00:00Z",
            "rejected",
            (
                ReviewResult("watch-complete", "fail"),
                ReviewResult("captions-readable", "fail"),
            ),
        )
        with self.assertRaisesRegex(ClaimsError, "does not match the checklist"):
            validate_review_response(mismatched, self.checklist, project_root=self.root)

    def test_response_rejects_changed_checklist_with_same_id(self) -> None:
        response = self._response()
        changed = ReviewChecklist(
            self.checklist.checklist_id,
            (
                ChecklistItem("watch-complete", "Watch a different deliverable.", True),
                self.checklist.items[1],
            ),
            deliverable=self.binding,
        )
        with self.assertRaisesRegex(ClaimsError, "checklist_sha256"):
            validate_review_response(response, changed, project_root=self.root)

    def test_response_with_complete_rows_but_no_checklist_hash_is_unbound(self) -> None:
        response = replace(self._response(), checklist_sha256=None)
        with self.assertRaisesRegex(ClaimsError, "legacy_unbound"):
            validate_review_response(response, self.checklist, project_root=self.root)

    def test_response_write_load_is_append_only_and_not_signoff(self) -> None:
        checklist_path = self.root / "checklist.json"
        response_path = self.root / "response.json"
        write_review_checklist(checklist_path, self.checklist)
        response = self._response()
        written = write_review_response(
            response_path,
            response,
            self.checklist,
            project_root=self.root,
        )
        loaded = load_review_response(
            response_path,
            checklist_path,
            project_root=self.root,
        )
        self.assertEqual(written, loaded)
        self.assertEqual(self.checklist.checklist_sha256, written["checklist_sha256"])
        self.assertFalse(loaded["is_signoff"])
        self.assertFalse(loaded["signoff_recorded"])
        with self.assertRaisesRegex(ClaimsError, "overwrite existing review response"):
            write_review_response(
                response_path,
                response,
                self.checklist,
                project_root=self.root,
            )

    def test_response_writer_rejects_unbound_legacy_records(self) -> None:
        response = replace(self._response(), checklist_sha256=None)
        with self.assertRaisesRegex(ClaimsError, "legacy_unbound"):
            write_review_response(
                self.root / "unbound-response.json",
                response,
                self.checklist,
                project_root=self.root,
            )

    def test_response_writer_does_not_rebind_old_response_to_changed_checklist(self) -> None:
        old_response = replace(self._response(), checklist_sha256=None)
        changed = ReviewChecklist(
            self.checklist.checklist_id,
            (
                ChecklistItem("watch-complete", "Watch a different deliverable.", True),
                self.checklist.items[1],
            ),
            deliverable=self.binding,
        )
        with self.assertRaisesRegex(ClaimsError, "legacy_unbound"):
            write_review_response(
                self.root / "rebind-attempt.json",
                old_response,
                changed,
                project_root=self.root,
            )


if __name__ == "__main__":
    unittest.main()
