from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "ensure_suno_engineer_skill.py"
SPEC = importlib.util.spec_from_file_location("ensure_suno_engineer_skill", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_archive(files: dict[str, bytes]) -> bytes:
    payload = io.BytesIO()
    root = f"claude-ai-music-skills-{bootstrap.UPSTREAM_COMMIT}"
    with zipfile.ZipFile(payload, "w") as archive:
        for relative_name, data in files.items():
            archive.writestr(
                f"{root}/{bootstrap.UPSTREAM_SKILL_PATH}/{relative_name}", data
            )
    return payload.getvalue()


class SunoSkillBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = {
            "SKILL.md": b"---\nname: suno-engineer\n---\n",
            "genre-practices.md": b"# Genre practices\n",
        }
        self.hashes = {name: sha256(data) for name, data in self.files.items()}
        self.archive = make_archive(self.files)

    def test_missing_skill_is_installed_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            codex_home = Path(temporary_root) / ".codex"
            ready, message = bootstrap.ensure_skill(
                codex_home,
                downloader=lambda: self.archive,
                expected_sha256=self.hashes,
            )
            destination = codex_home / "skills" / bootstrap.SKILL_NAME
            self.assertTrue(ready)
            self.assertIn("INSTALLED:", message)
            self.assertEqual("current", bootstrap.inspect_installation(destination, self.hashes)[0])
            self.assertEqual(self.files["SKILL.md"], (destination / "SKILL.md").read_bytes())

    def test_current_skill_is_offline_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            codex_home = Path(temporary_root) / ".codex"
            bootstrap.install_missing_skill(codex_home, self.archive, self.hashes)

            def network_must_not_run() -> bytes:
                raise AssertionError("network downloader was called")

            ready, message = bootstrap.ensure_skill(
                codex_home,
                downloader=network_must_not_run,
                expected_sha256=self.hashes,
            )
            self.assertTrue(ready)
            self.assertIn("READY:", message)

    def test_check_only_reports_missing_without_network_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            codex_home = Path(temporary_root) / ".codex"

            def network_must_not_run() -> bytes:
                raise AssertionError("network downloader was called")

            ready, message = bootstrap.ensure_skill(
                codex_home,
                check_only=True,
                downloader=network_must_not_run,
                expected_sha256=self.hashes,
            )
            self.assertFalse(ready)
            self.assertIn("NOT READY:", message)
            self.assertFalse(codex_home.exists())

    def test_conflicting_installation_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            codex_home = Path(temporary_root) / ".codex"
            destination = codex_home / "skills" / bootstrap.SKILL_NAME
            destination.mkdir(parents=True)
            original = b"local customization\n"
            (destination / "SKILL.md").write_bytes(original)

            with self.assertRaises(bootstrap.SkillInstallError):
                bootstrap.ensure_skill(
                    codex_home,
                    downloader=lambda: self.archive,
                    expected_sha256=self.hashes,
                )
            self.assertEqual(original, (destination / "SKILL.md").read_bytes())

    def test_repository_contract_names_pinned_source(self) -> None:
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        installation = (PROJECT_ROOT / "docs" / "installation.md").read_text(
            encoding="utf-8"
        )
        music = (PROJECT_ROOT / "docs" / "music-authoring.md").read_text(
            encoding="utf-8"
        )
        for document in (agents, installation, music):
            self.assertIn("ensure_suno_engineer_skill.py", document)
            self.assertIn(bootstrap.UPSTREAM_COMMIT, document)
        self.assertIn(bootstrap.UPSTREAM_SKILL_URL, music)


if __name__ == "__main__":
    unittest.main()
