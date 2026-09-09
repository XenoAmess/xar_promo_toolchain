#!/usr/bin/env python3
"""Install the repository's required Codex Suno prompt-authoring skill."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable, Mapping
import urllib.request
import zipfile


SKILL_NAME = "suno-engineer"
UPSTREAM_REPOSITORY = "bitwize-music-studio/claude-ai-music-skills"
UPSTREAM_COMMIT = "b1b11a67e8ce5dd07691b68cf736cff8e4c55c87"
UPSTREAM_SKILL_PATH = "skills/suno-engineer"
UPSTREAM_SKILL_URL = (
    "https://github.com/bitwize-music-studio/claude-ai-music-skills/"
    "blob/main/skills/suno-engineer/SKILL.md"
)
ARCHIVE_URL = (
    f"https://github.com/{UPSTREAM_REPOSITORY}/archive/{UPSTREAM_COMMIT}.zip"
)
EXPECTED_SHA256 = {
    "SKILL.md": "e31f1ffe9c6fafaa9d4f46eba5290831d8966c0a907adb9a8881d231419806fc",
    "genre-practices.md": "3c6630514a3f71ea5bc688286f8ec7ee5a7d9d2003fa6fd682d2b05a0af9dc0e",
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class SkillInstallError(RuntimeError):
    """Raised when the pinned skill cannot be installed without overwriting data."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_codex_home(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def inspect_installation(
    destination: Path,
    expected_sha256: Mapping[str, str] = EXPECTED_SHA256,
) -> tuple[str, str]:
    if not destination.exists():
        return "missing", f"{destination} does not exist"
    if not destination.is_dir():
        return "conflict", f"{destination} exists but is not a directory"

    actual_files = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    expected_files = set(expected_sha256)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        return (
            "conflict",
            f"file set differs (missing={missing!r}, unexpected={unexpected!r})",
        )

    for relative_name, expected_hash in expected_sha256.items():
        actual_hash = _sha256((destination / relative_name).read_bytes())
        if actual_hash != expected_hash:
            return (
                "conflict",
                f"SHA-256 mismatch for {relative_name}: {actual_hash}",
            )
    return "current", f"pinned {SKILL_NAME} bytes are installed"


def download_archive(url: str = ARCHIVE_URL) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "xar-promo-skill-bootstrap/1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SkillInstallError(
            f"upstream archive exceeds {MAX_ARCHIVE_BYTES} bytes"
        )
    return data


def skill_files_from_archive(
    archive_bytes: bytes,
    expected_sha256: Mapping[str, str] = EXPECTED_SHA256,
) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise SkillInstallError("upstream response is not a valid ZIP archive") from exc

    files: dict[str, bytes] = {}
    with archive:
        names = archive.namelist()
        for relative_name, expected_hash in expected_sha256.items():
            suffix = f"/{UPSTREAM_SKILL_PATH}/{relative_name}"
            matches = [name for name in names if name.endswith(suffix)]
            if len(matches) != 1:
                raise SkillInstallError(
                    f"expected one archive member ending in {suffix!r}; found {matches!r}"
                )
            data = archive.read(matches[0])
            actual_hash = _sha256(data)
            if actual_hash != expected_hash:
                raise SkillInstallError(
                    f"upstream SHA-256 mismatch for {relative_name}: {actual_hash}"
                )
            files[relative_name] = data
    return files


def install_missing_skill(
    codex_home: Path,
    archive_bytes: bytes,
    expected_sha256: Mapping[str, str] = EXPECTED_SHA256,
) -> Path:
    skills_root = codex_home / "skills"
    destination = skills_root / SKILL_NAME
    state, detail = inspect_installation(destination, expected_sha256)
    if state == "current":
        return destination
    if state == "conflict":
        raise SkillInstallError(
            f"refusing to overwrite an existing {SKILL_NAME} installation: {detail}"
        )

    files = skill_files_from_archive(archive_bytes, expected_sha256)
    skills_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{SKILL_NAME}-", dir=skills_root
    ) as temporary_root:
        payload = Path(temporary_root) / SKILL_NAME
        payload.mkdir()
        for relative_name, data in files.items():
            target = payload / relative_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            payload.rename(destination)
        except FileExistsError:
            race_state, race_detail = inspect_installation(
                destination, expected_sha256
            )
            if race_state != "current":
                raise SkillInstallError(
                    "another process created a conflicting installation: "
                    f"{race_detail}"
                ) from None

    state, detail = inspect_installation(destination, expected_sha256)
    if state != "current":
        raise SkillInstallError(f"post-install verification failed: {detail}")
    return destination


def ensure_skill(
    codex_home: Path,
    *,
    check_only: bool = False,
    downloader: Callable[[], bytes] = download_archive,
    expected_sha256: Mapping[str, str] = EXPECTED_SHA256,
) -> tuple[bool, str]:
    destination = codex_home / "skills" / SKILL_NAME
    state, detail = inspect_installation(destination, expected_sha256)
    if state == "current":
        return True, f"READY: {destination} ({UPSTREAM_COMMIT})"
    if check_only:
        return False, f"NOT READY: {destination}: {detail}"
    if state == "conflict":
        raise SkillInstallError(
            f"refusing to overwrite an existing {SKILL_NAME} installation: {detail}"
        )

    installed = install_missing_skill(
        codex_home,
        downloader(),
        expected_sha256,
    )
    return True, f"INSTALLED: {installed} ({UPSTREAM_COMMIT})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently install the pinned suno-engineer Codex skill when missing."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the exact pinned installation without network access or writes",
    )
    parser.add_argument(
        "--codex-home",
        help="override CODEX_HOME (default: $CODEX_HOME, then ~/.codex)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codex_home = resolve_codex_home(args.codex_home)
    try:
        ready, message = ensure_skill(codex_home, check_only=args.check)
    except (OSError, SkillInstallError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(message)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
