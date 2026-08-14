"""Public source prose stays about product contracts, never delivery context."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_TEXT_SUFFIXES = frozenset({".html", ".md", ".mjs", ".py", ".rs", ".ts", ".tsx"})
_SOURCE_ROOTS = (
    REPO_ROOT / "datasets",
    REPO_ROOT / "docs",
    REPO_ROOT / "eval",
    REPO_ROOT / "moodboard",
    REPO_ROOT / "tests",
    REPO_ROOT / "viewer" / "scripts",
    REPO_ROOT / "viewer" / "src",
    REPO_ROOT / "viewer" / "tests",
)
_TOP_LEVEL_TEXT = (
    REPO_ROOT / "DATASETS.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "INTERFACES.md",
    REPO_ROOT / "viewer" / "index.html",
)
_IDENTITY_TOKEN = "showcase-public-domain-v1"
_DEMO_IDENTIFIER = re.compile(
    r"(?:[\"'`][^\s\"'`]*showcase[^\s\"'`]*[\"'`])"
    r"|(?:(?:[A-Za-z0-9_.:/-]*[/.:])?showcase(?:[-:/.][A-Za-z0-9_./:${}-]+)+)",
    re.IGNORECASE,
)
_FORBIDDEN = re.compile(
    "|".join(
        (
            r"\binterview\b",
            r"\badobe[ -]+demo\b",
            r"\badobe[ -]+facing\b",
            r"\b(?:shown|presented|demoed|sent)[^\n]{0,48}\bto adobe\b",
            r"\bdemo[ -]+deadline\b",
            r"\breviewer\b",
            r"\bprincipal[ -]+scientists?\b",
            r"\bscreen[ -]+share\b",
        )
    ),
    re.IGNORECASE,
)


def _contains_private_delivery_context(line: str) -> bool:
    searchable = _DEMO_IDENTIFIER.sub("", line.replace(_IDENTITY_TOKEN, ""))
    return _FORBIDDEN.search(searchable) is not None


def test_guard_separates_provider_and_identity_facts_from_audience_context() -> None:
    allowed = (
        "Adobe Firefly provider metadata is retained.",
        'namespace = "showcase-firefly-v1"',
        'dataset_id = "showcase-public-domain-v1"',
    )
    denied = (
        "Generate Adobe-facing procedural imagery.",
        "This will be shown to Adobe tomorrow.",
        "The interview demo uses this corpus.",
    )

    assert not any(_contains_private_delivery_context(value) for value in allowed)
    assert all(_contains_private_delivery_context(value) for value in denied)


def _publication_text_files() -> tuple[Path, ...]:
    files = list(_TOP_LEVEL_TEXT)
    for root in _SOURCE_ROOTS:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in _TEXT_SUFFIXES
            and "generated" not in path.parts
            and "dist-static" not in path.parts
            and "viewer_dist" not in path.parts
            and path.name != "test_publication_hygiene.py"
        )
    return tuple(sorted(set(files)))


def test_public_source_prose_excludes_private_delivery_context() -> None:
    violations: list[str] = []
    for path in _publication_text_files():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _contains_private_delivery_context(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

    assert not violations, "private delivery context leaked into public source:\n" + "\n".join(
        violations
    )
