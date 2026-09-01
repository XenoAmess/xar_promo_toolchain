from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from xar_promo.evidence import (  # noqa: E402
    BUNDLE_V2_FORMAT_VERSION,
    EvidenceError,
    bind_external_artifact,
    build_sampling_plan_v2,
    load_evidence_bundle,
    load_evidence_bundle_v2,
    load_sampling_plan,
    load_sampling_plan_v2,
    validate_sampling_plan_v2,
    write_evidence_bundle,
    write_evidence_bundle_v2,
    write_sampling_plan_v2,
)


class EvidenceV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.capture = self.root / "raw" / "capture.mp4"
        self.capture.parent.mkdir(parents=True, exist_ok=True)
        self.capture.write_bytes(b"capture bytes")
        self.frame = {
            "adapter_id": "generic-frame",
            "tool": "frame-tool",
            "tool_version": "1",
            "operation": "extract-frame",
            "execution": "external",
        }
        self.state = {
            "adapter_id": "generic-state",
            "tool": "state-tool",
            "tool_version": "2",
            "operation": "sample-state",
            "execution": "external",
        }
        self.source = bind_external_artifact(
            self.capture,
            project_root=self.root,
            artifact_id="capture",
            collection="raw",
            role="capture",
            label="Raw capture",
            media_type="video/mp4",
            producer=self.frame,
        )
        self.chapters = [
            {
                "id": "chapter-001",
                "kind": "video",
                "source": self.source,
                "start_seconds": 0,
                "end_seconds": 2,
            }
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plan(self, **kwargs):
        return build_sampling_plan_v2(
            self.chapters,
            project_root=self.root,
            interval_seconds=1,
            required_roles=["frame", "state"],
            external_producers={"frame": self.frame, "state": self.state},
            **kwargs,
        )

    def _write_submissions(self, plan: dict) -> list[dict]:
        submissions: list[dict] = []
        producer_by_role = {"frame": self.frame, "state": self.state, "ocr": self.frame}
        for sample in plan["samples"]:
            for role in sample["required_roles"]:
                artifact = self.root / "evidence" / f"{sample['id']}-{role}.bin"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(f"{sample['id']}:{role}".encode("ascii"))
                producer = producer_by_role[role]
                submissions.append(
                    {
                        "sample_id": sample["id"],
                        "role": role,
                        "path": artifact,
                        "media_type": "application/octet-stream",
                        "producer": producer,
                    }
                )
        return submissions

    def test_v2_plan_is_per_sample_and_deterministic(self) -> None:
        first = self._plan(
            sample_roles={
                "sample-000001": ["frame"],
                "sample-000002": ["state", "frame"],
                "sample-000003": ["state"],
            }
        )
        second = self._plan(
            sample_required_roles=[
                ["frame"],
                ["frame", "state"],
                ["state"],
            ]
        )
        self.assertEqual(first, second)
        self.assertEqual(2, first["format_version"])
        self.assertEqual([["frame"], ["frame", "state"], ["state"]], [
            sample["required_roles"] for sample in first["samples"]
        ])

    def test_v2_plan_can_use_sample_specific_producer_override(self) -> None:
        override = {
            "adapter_id": "alternate-frame",
            "tool": "frame-tool",
            "tool_version": "3",
            "operation": "extract-frame",
            "execution": "external",
        }
        plan = self._plan(
            sample_roles=[["frame"], ["frame", "state"], ["state"]],
            sample_producers={"sample-000002": {"frame": override}},
        )
        self.assertEqual(
            override,
            plan["samples"][1]["external_producers"]["frame"],
        )
        self.assertNotIn("external_producers", plan["samples"][0])

    def test_v2_sample_producer_none_uses_plan_default(self) -> None:
        plan = self._plan(
            sample_producers=[None, None, None],
        )
        self.assertTrue(
            all("external_producers" not in sample for sample in plan["samples"])
        )

    def test_v2_bundle_round_trip_uses_effective_sample_roles(self) -> None:
        plan_path = self.root / "plan.json"
        plan = write_sampling_plan_v2(
            plan_path,
            self.chapters,
            project_root=self.root,
            interval_seconds=1,
            required_roles=["frame", "state"],
            external_producers={"frame": self.frame, "state": self.state},
            sample_roles=[["frame"], ["frame", "state"], ["state"]],
        )
        bundle_path = self.root / "bundle.json"
        submissions = self._write_submissions(plan)
        bundle = write_evidence_bundle_v2(
            bundle_path,
            project_root=self.root,
            plan_path=plan_path,
            submissions=submissions,
        )
        self.assertEqual(BUNDLE_V2_FORMAT_VERSION, bundle["format_version"])
        loaded_bundle, loaded_plan = load_evidence_bundle_v2(
            bundle_path, project_root=self.root
        )
        self.assertEqual(bundle, loaded_bundle)
        self.assertEqual(plan, loaded_plan)
        # The generic loaders must transparently support v2 as well.
        self.assertEqual((bundle, plan), load_evidence_bundle(bundle_path, project_root=self.root))
        self.assertEqual(plan, load_sampling_plan(plan_path, project_root=self.root))
        self.assertEqual(plan, load_sampling_plan_v2(plan_path, project_root=self.root))
        self.assertEqual(4, len(bundle["entries"]))

    def test_v2_writer_rejects_unplanned_producer(self) -> None:
        plan_path = self.root / "plan.json"
        plan = write_sampling_plan_v2(
            plan_path,
            self.chapters,
            project_root=self.root,
            interval_seconds=1,
            required_roles=["frame", "state"],
            external_producers={"frame": self.frame, "state": self.state},
        )
        submissions = self._write_submissions(plan)
        submissions[0] = dict(submissions[0], producer=self.state)
        with self.assertRaisesRegex(EvidenceError, "producer metadata does not match"):
            write_evidence_bundle(
                self.root / "bundle.json",
                project_root=self.root,
                plan_path=plan_path,
                submissions=submissions,
            )

    def test_v2_validation_rejects_missing_sample_role_producer(self) -> None:
        plan = self._plan(sample_roles=[["state"], ["frame"], ["state"]])
        # Remove the producer from the global map and leave no sample override.
        plan["sampling"]["external_producers"].pop("state")
        with self.assertRaisesRegex(EvidenceError, "no producer"):
            validate_sampling_plan_v2(plan, project_root=self.root)


if __name__ == "__main__":
    unittest.main()
