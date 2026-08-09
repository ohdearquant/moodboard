"""The showcase fixture identifies score-bearing bytes, not a self-invalidating repository HEAD."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

GENERATOR = (
    Path(__file__).resolve().parents[1] / "viewer" / "scripts" / "generate_showcase_fixture.py"
)


def _generator_module():
    spec = importlib.util.spec_from_file_location("showcase_fixture_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True)


def test_fixture_and_document_commits_do_not_move_score_source_identity(tmp_path):
    generator = _generator_module()
    repository = tmp_path / "repository"
    (repository / "moodboard").mkdir(parents=True)
    (repository / "eval").mkdir()
    (repository / "viewer" / "tests" / "fixtures" / "showcase").mkdir(parents=True)
    (repository / "docs").mkdir()
    (repository / "moodboard" / "axes.py").write_text("SCALE = 1\n", encoding="utf-8")
    (repository / "moodboard" / "viewer.py").write_text("UI_ONLY = True\n", encoding="utf-8")
    (repository / "eval" / "thresholds.json").write_text('{"alpha":0.1}\n', encoding="utf-8")
    (repository / "docs" / "note.md").write_text("first\n", encoding="utf-8")
    fixture = repository / "viewer" / "tests" / "fixtures" / "showcase" / "manifest.json"
    fixture.write_text("{}\n", encoding="utf-8")

    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "fixture@example.test")
    _git(repository, "config", "user.name", "Fixture Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    revision, digest, dirty = generator._engine_source_identity(repository)
    assert revision == f"source-set-sha256:{digest}"
    assert dirty is False

    (repository / "docs" / "note.md").write_text("unrelated docs\n", encoding="utf-8")
    fixture.write_text('{"fixture":1}\n', encoding="utf-8")
    assert generator._engine_source_identity(repository) == (revision, digest, False)
    _git(repository, "add", "docs", "viewer/tests/fixtures")
    _git(repository, "commit", "-qm", "fixture and docs only")
    assert generator._engine_source_identity(repository) == (revision, digest, False)

    (repository / "moodboard" / "axes.py").write_text("SCALE = 2\n", encoding="utf-8")
    changed_revision, changed_digest, changed_dirty = generator._engine_source_identity(repository)
    assert changed_revision == f"source-set-sha256:{changed_digest}"
    assert changed_digest != digest
    assert changed_revision != revision
    assert changed_dirty is True
