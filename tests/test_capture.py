from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from xar_promo.capture import (
    CAPTURE_RECEIPT_KIND,
    CaptureError,
    CaptureFile,
    CaptureMark,
    CaptureReceipt,
    CleanSpan,
    ProducerRecord,
    load_capture_receipt,
    verify_unchanged,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _record(root: Path, path: Path, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    row.update(extra)
    return row


def _build_payload(root: Path) -> tuple[Path, dict[str, object]]:
    raw = root / "raw" / "take-01.mp4"
    proof = root / "proof" / "shot-a.json"
    raw.parent.mkdir(parents=True)
    proof.parent.mkdir(parents=True)
    raw.write_bytes(b"synthetic video bytes")
    proof.write_text('{"frame": "a"}\n', encoding="utf-8")
    payload: dict[str, object] = {
        "format_version": 1,
        "kind": CAPTURE_RECEIPT_KIND,
        "producer": {
            "adapter_id": "fixture",
            "tool": "recorder",
            "tool_version": "1.2.3",
            "operation": "capture",
            "execution": "external",
        },
        "raw_capture": _record(root, raw, role="raw-video", media_type="video/mp4"),
        "duration_seconds": 12.0,
        "timebase": "seconds",
        "marks": [
            {"label": "take-begin", "seconds": 1.0},
            {"label": "shot-a-begin", "seconds": 2.0},
            {"label": "shot-a-end", "seconds": 5.0},
            {"label": "take-end", "seconds": 11.0},
        ],
        "clean_spans": [
            {
                "id": "shot-a",
                "begin_mark": "shot-a-begin",
                "end_mark": "shot-a-end",
                "begin_seconds": 2.0,
                "end_seconds": 5.0,
                "evidence": [_record(root, proof, role="frame-gate")],
            }
        ],
        "overlap_policy": "forbid",
        "evidence": [],
    }
    return root / "capture-receipt.json", payload


class CaptureReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.receipt_path, self.payload = _build_payload(self.root)
        self.receipt_path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_loads_portable_hash_bound_receipt(self) -> None:
        receipt = load_capture_receipt(self.receipt_path)
        self.assertEqual(self.root, receipt.artifact_root)
        self.assertEqual("fixture", receipt.producer.producer_id)
        self.assertEqual(receipt.producer.adapter_id, receipt.producer.producer_id)
        self.assertEqual(3.0, receipt.clean_span("shot-a").duration_seconds)
        self.assertEqual(4, len(receipt.marks))
        self.assertEqual(3, len(receipt.all_files))  # raw, proof, receipt JSON
        self.assertEqual("raw/take-01.mp4", receipt.raw_capture.relative_path)

    def test_verify_unchanged_accepts_untouched_sources(self) -> None:
        receipt = load_capture_receipt(self.receipt_path)
        receipt.verify_unchanged()
        verify_unchanged(receipt)
        verify_unchanged(self.receipt_path)

    def test_verify_unchanged_rejects_mutated_raw_bytes(self) -> None:
        receipt = load_capture_receipt(self.receipt_path)
        raw = self.root / "raw" / "take-01.mp4"
        raw.write_bytes(raw.read_bytes() + b"tampered")
        with self.assertRaisesRegex(CaptureError, "capture source changed.*raw/take-01.mp4"):
            receipt.verify_unchanged()

    def test_rejects_absolute_or_parent_escape_paths(self) -> None:
        for bad in (str((self.root / "raw" / "take-01.mp4").resolve()), "../outside.mp4", "raw\\take.mp4"):
            payload = json.loads(json.dumps(self.payload))
            payload["raw_capture"]["path"] = bad  # type: ignore[index]
            with self.subTest(path=bad), self.assertRaisesRegex(CaptureError, "relative|portable|normalized"):
                CaptureReceipt.from_mapping(payload, project_root=self.root)

    def test_rejects_hash_and_size_mismatch(self) -> None:
        payload = json.loads(json.dumps(self.payload))
        payload["raw_capture"]["bytes"] += 1  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "byte count mismatch"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

        payload = json.loads(json.dumps(self.payload))
        payload["raw_capture"]["sha256"] = "0" * 64  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "SHA-256 mismatch"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

    def test_rejects_unordered_marks_and_unbound_span_seconds(self) -> None:
        payload = json.loads(json.dumps(self.payload))
        payload["marks"][3]["seconds"] = 1.5  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "marks.*ordered"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

        payload = json.loads(json.dumps(self.payload))
        payload["clean_spans"][0]["end_seconds"] = 5.5  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "does not bind"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

    def test_overlap_policy_is_explicit_and_enforced(self) -> None:
        payload = json.loads(json.dumps(self.payload))
        payload.pop("overlap_policy")
        with self.assertRaisesRegex(CaptureError, "overlap_policy"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

        payload = json.loads(json.dumps(self.payload))
        payload["clean_spans"].append(  # type: ignore[union-attr]
            {
                "id": "shot-b",
                "begin_mark": "shot-a-end",
                "end_mark": "take-end",
                "begin_seconds": 5.0,
                "end_seconds": 11.0,
                "evidence": [],
            }
        )
        # Touching spans are safe under the forbid policy.
        self.assertEqual(2, len(CaptureReceipt.from_mapping(payload, project_root=self.root).clean_spans))

        payload["clean_spans"][1]["begin_seconds"] = 2.0  # type: ignore[index]
        payload["clean_spans"][1]["begin_mark"] = "shot-a-begin"  # type: ignore[index]
        payload["clean_spans"][1]["end_seconds"] = 11.0  # type: ignore[index]
        payload["clean_spans"][1]["end_mark"] = "take-end"  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "overlaps"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)

        payload["overlap_policy"] = "allow"
        receipt = CaptureReceipt.from_mapping(payload, project_root=self.root)
        self.assertEqual("allow", receipt.overlap_policy)

    def test_constructor_helpers_bind_files_and_emit_portable_json(self) -> None:
        raw = self.root / "raw" / "take-01.mp4"
        proof = self.root / "proof" / "shot-a.json"
        raw_file = CaptureFile.from_path(raw, project_root=self.root, role="raw-video")
        proof_file = CaptureFile.from_path(proof, project_root=self.root, role="proof")
        producer = ProducerRecord(producer_id="fixture", tool="recorder", tool_version="1")
        receipt = CaptureReceipt(
            self.root,
            producer,
            raw_file,
            12,
            "seconds",
            (CaptureMark("start", 0), CaptureMark("begin", 1), CaptureMark("end", 2)),
            (CleanSpan("shot", "begin", "end", 1, 2, (proof_file,)),),
        )
        encoded = receipt.to_dict()
        self.assertEqual("raw/take-01.mp4", encoded["raw_capture"]["path"])  # type: ignore[index]
        self.assertNotIn(str(self.root), json.dumps(encoded))

    def test_rejects_duplicate_evidence_within_span(self) -> None:
        raw = self.root / "raw" / "take-01.mp4"
        proof = self.root / "proof" / "shot-a.json"
        raw_file = CaptureFile.from_path(raw, project_root=self.root)
        proof_file = CaptureFile.from_path(proof, project_root=self.root)
        with self.assertRaisesRegex(CaptureError, "repeats an evidence path"):
            CleanSpan("shot", "begin", "end", 1, 2, (proof_file, proof_file))

    def test_rejects_duplicate_artifact_ids_across_receipt_records(self) -> None:
        payload = json.loads(json.dumps(self.payload))
        payload["raw_capture"]["id"] = "shared-artifact"  # type: ignore[index]
        payload["clean_spans"][0]["evidence"][0]["id"] = "shared-artifact"  # type: ignore[index]
        with self.assertRaisesRegex(CaptureError, "repeats artifact id"):
            CaptureReceipt.from_mapping(payload, project_root=self.root)


if __name__ == "__main__":
    unittest.main()
