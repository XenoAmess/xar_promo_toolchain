"""Offline validation smoke for the ZhongGuo 361 iteration-1 demo."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


DEMO_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = DEMO_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from xar_promo.project import load_document  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def main() -> int:
    legacy_path = DEMO_ROOT / "promo-manifest.json"
    native_path = DEMO_ROOT / "promo-project.json"
    provenance_path = DEMO_ROOT / "provenance.json"

    legacy = load_document(legacy_path)
    native = load_document(native_path)
    if legacy.source_format != "legacy-showcase-v1":
        raise RuntimeError(f"unexpected legacy format: {legacy.source_format}")
    if native.source_format != "project-config-v1":
        raise RuntimeError(f"unexpected native format: {native.source_format}")

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assets = provenance.get("assets", [])
    if not isinstance(assets, list) or len(assets) != 3:
        raise RuntimeError("provenance must contain exactly three assets")
    for record in assets:
        path = DEMO_ROOT / record["path"]
        if path.stat().st_size != record["bytes"]:
            raise RuntimeError(f"byte count mismatch: {path}")
        if _sha256(path) != record["sha256"].upper():
            raise RuntimeError(f"SHA-256 mismatch: {path}")

    print(
        "GREEN: zhongguo-361-iteration1 "
        f"legacy chapters={legacy.chapter_count} bound_sources={legacy.artifact_count} "
        f"native chapters={native.chapter_count} assets={len(assets)}"
    )
    print("STATUS: fixture-live demo only; no CK3/FFmpeg/TTS/network invoked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

