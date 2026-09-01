from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from xar_promo.capture import CaptureError, CaptureFile
from xar_promo.errors import ManifestError
from xar_promo.model import portable_relative_path


SCHEMA_ROOT = Path(__file__).resolve().parents[1] / "src" / "xar_promo" / "schemas"


def _path_patterns(value: object) -> list[str]:
    """Collect the canonical path regexes from the bundled JSON schemas."""

    result: list[str] = []
    if isinstance(value, dict):
        pattern = value.get("pattern")
        if isinstance(pattern, str) and "(?!.*//)" in pattern:
            result.append(pattern)
        for child in value.values():
            result.extend(_path_patterns(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_path_patterns(child))
    return result


class PortablePathContractTests(unittest.TestCase):
    BAD_PATHS = (
        "media\\take.mp4",
        "media//take.mp4",
        "media/take.mp4/",
        "media/./take.mp4",
        "media/../take.mp4",
        "media/take\n.mp4",
        "media/take\r.mp4",
        "media/take.mp4\n",
        "media/take.mp4\r",
        "media/take\x00.mp4",
        "media/take\t.mp4",
        "media/take\x01.mp4",
        "media/take\x7f.mp4",
    )

    def test_manifest_path_runtime_rejects_noncanonical_spellings(self) -> None:
        for value in self.BAD_PATHS:
            with self.subTest(path=repr(value)), self.assertRaises(ManifestError):
                portable_relative_path(value, "path")

        self.assertEqual(
            "media/take.mp4", portable_relative_path("media/take.mp4", "path")
        )

    def test_capture_path_runtime_matches_manifest_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value in self.BAD_PATHS:
                with self.subTest(path=repr(value)), self.assertRaises(CaptureError):
                    CaptureFile(value, root / "placeholder.bin", 0, "0" * 64)

    def test_bundled_schema_path_patterns_reject_the_same_spellings(self) -> None:
        schemas = tuple(SCHEMA_ROOT.glob("*.schema.json"))
        self.assertGreaterEqual(len(schemas), 5)
        patterns: list[tuple[str, str]] = []
        for schema_path in schemas:
            payload = json.loads(schema_path.read_text(encoding="utf-8"))
            patterns.extend((schema_path.name, pattern) for pattern in _path_patterns(payload))
        self.assertGreaterEqual(len(patterns), 5)

        for schema_name, pattern in patterns:
            with self.subTest(schema=schema_name, path="media/take.mp4"):
                self.assertIsNotNone(re.fullmatch(pattern, "media/take.mp4"))
            for value in self.BAD_PATHS:
                with self.subTest(schema=schema_name, path=repr(value)):
                    # All path patterns are anchored, so fullmatch mirrors the
                    # JSON Schema pattern assertion without another dependency.
                    self.assertIsNone(re.fullmatch(pattern, value))


if __name__ == "__main__":
    unittest.main()
