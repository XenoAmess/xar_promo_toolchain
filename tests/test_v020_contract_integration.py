from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import tomllib
import unittest
from importlib.resources import files
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
SCHEMA_ROOT = SOURCE_ROOT / "xar_promo" / "schemas"

EXPECTED_RESOURCES = (
    "install-contract-v1.json",
    "promo-project-config-v1.schema.json",
    "promo-run-manifest-v1.schema.json",
    "phase2-capture-contract-v1.schema.json",
    "capture-receipt-v1.schema.json",
    "evidence-plan-v2.schema.json",
    "evidence-bundle-v2.schema.json",
    "claims-review-v1.schema.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class V020ContractIntegrationTests(unittest.TestCase):
    """Cross-module checks for the generic 0.2 contracts.

    This deliberately exercises only portable fixture data. It must not launch
    a recorder, a game, OCR, FFmpeg, or a network provider.
    """

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(SOURCE_ROOT))
        cls.capture = __import__("xar_promo.capture", fromlist=["*"])
        cls.claims = __import__("xar_promo.claims", fromlist=["*"])
        cls.evidence = __import__("xar_promo.evidence", fromlist=["*"])
        sys.path.pop(0)

    def test_install_contract_and_wheel_resources_are_consistent(self) -> None:
        contract = json.loads(
            (SCHEMA_ROOT / "install-contract-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual("0.2.1", contract["package"]["version"])
        self.assertEqual(
            tuple(f"xar_promo/schemas/{name}" for name in EXPECTED_RESOURCES),
            tuple(contract["artifacts"]["installed_resources"]),
        )

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        self.assertEqual("0.2.1", pyproject["project"]["version"])
        self.assertIn("schemas/*.json", pyproject["tool"]["setuptools"]["package-data"]["xar_promo"])

        # Source-tree check plus importlib.resources check catches a package
        # data omission without requiring a wheel build in every unit test run.
        resource_root = files("xar_promo").joinpath("schemas")
        for name in EXPECTED_RESOURCES:
            source_path = SCHEMA_ROOT / name
            self.assertTrue(source_path.is_file(), name)
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict, name)
            self.assertTrue(resource_root.joinpath(name).is_file(), name)

    def test_new_schema_ids_are_generic_and_legacy_ck3_schema_is_unchanged(self) -> None:
        capture_schema = json.loads(
            (SCHEMA_ROOT / "capture-receipt-v1.schema.json").read_text(encoding="utf-8")
        )
        plan_schema = json.loads(
            (SCHEMA_ROOT / "evidence-plan-v2.schema.json").read_text(encoding="utf-8")
        )
        bundle_schema = json.loads(
            (SCHEMA_ROOT / "evidence-bundle-v2.schema.json").read_text(encoding="utf-8")
        )
        claims_schema = json.loads(
            (SCHEMA_ROOT / "claims-review-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual("urn:xar-promo:schema:capture-receipt:v1", capture_schema["$id"])
        self.assertEqual("urn:xar-promo:schema:evidence-plan:v2", plan_schema["$id"])
        self.assertEqual("urn:xar-promo:schema:evidence-bundle:v2", bundle_schema["$id"])
        self.assertEqual("urn:xar-promo:schema:claims-review:v1", claims_schema["$id"])

        # Domain semantics belong to adapters, not these reusable schemas.
        generic_text = json.dumps(
            [capture_schema, plan_schema, bundle_schema, claims_schema],
            ensure_ascii=False,
        ).lower()
        for forbidden in ("vivhite", "sts2", "ironclad", "card_name", "hp_cost"):
            self.assertNotIn(forbidden, generic_text)

        legacy = json.loads(
            (SCHEMA_ROOT / "phase2-capture-contract-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "urn:xar-promo:schema:zhongguo-361-phase2-capture-contract:v1",
            legacy["$id"],
        )
        self.assertEqual("zhongguo-361-phase2", legacy["properties"]["mode"]["const"])

    def test_public_contract_modules_import_without_domain_runtime(self) -> None:
        self.assertEqual(1, self.capture.CAPTURE_RECEIPT_FORMAT_VERSION)
        self.assertEqual("xar-promo-capture-receipt", self.capture.CAPTURE_RECEIPT_KIND)
        self.assertTrue(callable(self.capture.load_capture_receipt))
        self.assertTrue(callable(self.capture.verify_unchanged))
        self.assertEqual(2, self.evidence.PLAN_V2_FORMAT_VERSION)
        self.assertEqual(2, self.evidence.BUNDLE_V2_FORMAT_VERSION)
        self.assertEqual(1, self.evidence.FORMAT_VERSION)
        self.assertTrue(callable(self.evidence.build_sampling_plan_v2))
        self.assertTrue(callable(self.evidence.write_evidence_bundle_v2))
        self.assertTrue(callable(self.claims.validate_claim_bindings))
        self.assertTrue(callable(self.claims.validate_review_response))

    def test_capture_and_claims_preserve_exact_fixture_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw" / "take.bin"
            proof = root / "proof" / "state.json"
            deliverable = root / "derived" / "final.mp4"
            for path, payload in (
                (raw, b"portable capture fixture"),
                (proof, b'{"state":"fixture"}\n'),
                (deliverable, b"portable deliverable fixture"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            def file_row(path: Path) -> dict[str, object]:
                return {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }

            receipt_path = root / "capture-receipt.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "format_version": 1,
                        "kind": "xar-promo-capture-receipt",
                        "producer": {
                            "adapter_id": "fixture",
                            "tool": "fixture-recorder",
                            "tool_version": "1",
                            "operation": "capture",
                            "execution": "external",
                        },
                        "raw_capture": file_row(raw),
                        "duration_seconds": 4,
                        "timebase": "seconds",
                        "marks": [
                            {"label": "shot.begin", "seconds": 1},
                            {"label": "shot.end", "seconds": 3},
                        ],
                        "clean_spans": [
                            {
                                "id": "shot-01",
                                "begin_mark": "shot.begin",
                                "end_mark": "shot.end",
                                "begin_seconds": 1,
                                "end_seconds": 3,
                                "evidence": [file_row(proof)],
                            }
                        ],
                        "overlap_policy": "forbid",
                        "evidence": [file_row(proof)],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            receipt = self.capture.load_capture_receipt(receipt_path)
            self.assertEqual("shot-01", receipt.clean_spans[0].span_id)
            receipt.verify_unchanged()

            deliverable_binding = self.claims.ArtifactBinding.from_file(
                deliverable,
                project_root=root,
                artifact_id="deliverable",
                media_type="video/mp4",
            )
            proof_binding = self.claims.ArtifactBinding.from_file(
                proof,
                project_root=root,
                artifact_id="state-proof",
                media_type="application/json",
            )
            bundle = self.claims.ClaimBundle(
                deliverable=deliverable_binding,
                evidence_artifacts=(proof_binding,),
                claims=(
                    self.claims.ClaimBinding(
                        claim_id="fixture-claim",
                        evidence_artifact_ids=("state-proof",),
                        validator_id="fixture-validator",
                        result="pass",
                    ),
                ),
            )
            normalized = self.claims.validate_claim_bundle(bundle, project_root=root)
            self.assertEqual("xar-promo-claims-bundle", normalized["kind"])
            self.assertEqual(_sha256(deliverable), normalized["deliverable"]["sha256"])


if __name__ == "__main__":
    unittest.main()
