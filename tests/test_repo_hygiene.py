"""Tracked files must not cite documents that are not tracked.

This repository is public. The engine was built against a specification document that is not
committed here, and thirteen tracked files cited it by name, several of them by numbered item
("deliverable 8"). Every one of those citations was unresolvable for any reader who does not have
the uncommitted file: a footnote pointing at nothing, in prose that reads as if the reader could
go and check.

The constraints those citations carried were real and are kept. What is removed is the pointer,
because a reference is a promise that the referent can be reached, and this one could not be.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Assembled rather than written out, so this file does not match its own search. A guard whose
# own text trips it either fails forever or gets an exemption, and an exemption is a hole in the
# only place guaranteed to be inside the search.
UNTRACKED_SPEC = "IMPLEMENTATION" + "_CONTRACT" + ".md"


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / name for name in completed.stdout.split("\0") if name]


def test_no_tracked_file_cites_the_untracked_specification():
    """Named-file citations. The positive control runs in the same test, on the same predicate,
    because a search that silently matches nothing reports a clean repository in exactly the same
    words as a search that works."""
    files = _tracked_files()
    assert len(files) > 20, f"git ls-files returned {len(files)} paths, which is a failed read"

    assert UNTRACKED_SPEC in f"see {UNTRACKED_SPEC} for details", (
        "the predicate does not match a string built to contain the needle, so a clean result "
        "from it would mean nothing"
    )

    offenders = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if UNTRACKED_SPEC in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")

    assert not offenders, (
        f"tracked files cite {UNTRACKED_SPEC}, which is not committed here, so the reference "
        f"cannot be followed by any reader of this repository: {offenders}"
    )


def test_no_tracked_file_cites_the_specification_by_numbered_item():
    """The same defect without the filename, which a search for the filename alone does not see.

    This is the arm that matters: "deliverable 3" names an item in a document this repository
    does not contain, and it survives any grep aimed at the document's name. Removing the
    filename citations while leaving these would have looked complete and left half the problem.
    """
    import re

    pattern = re.compile(r"\bdeliverables?\s+\d+", re.IGNORECASE)

    assert pattern.search("per deliverable 8 above"), (
        "the pattern does not match a known-positive string, so its silence is not evidence"
    )

    offenders = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}  {line.strip()[:70]}")

    assert not offenders, (
        "tracked files cite numbered items of a specification this repository does not carry; "
        f"state the constraint instead of pointing at it: {offenders}"
    )
