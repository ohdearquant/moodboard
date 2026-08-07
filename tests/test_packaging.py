"""The built wheel must carry the runtime registry it refuses to run without.

`moodboard.abstain` reads `eval/thresholds.json` at runtime and raises rather than falling back
to numbers compiled into its own source. That registry lives outside the package directory, so a
wheel built from `packages = ["moodboard"]` alone did not contain it, and every non-editable
install raised FileNotFoundError on the first abstention call. The failure was in the safe
direction and the tool was still unusable when installed.

Nothing in the test suite could see this, because the suite runs from a source checkout where the
upward walk always finds the repository's own copy. That is the shape worth naming: a defect
reachable only through an artifact the tests never build is invisible to every test that does not
build one. So this module builds the wheel.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY = REPO_ROOT / "eval" / "thresholds.json"
PACKAGED_REGISTRY = "moodboard/eval/thresholds.json"


def _build_wheel(destination: Path) -> Path:
    """Build a wheel into `destination` and return its path.

    A missing build tool means this check did not run. Locally that is a skip, because a
    contributor without `uv` can still work on the engine. Under CI it is a failure, because a
    required check that silently skips emits a green standing in for a measurement nobody made,
    which is the same defect this repository audits its own workflows for.
    """
    uv = shutil.which("uv")
    if uv is None:
        message = (
            "uv is not on PATH, so the wheel could not be built and its contents are unmeasured"
        )
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    completed = subprocess.run(
        [uv, "build", "--wheel", "-o", str(destination)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"uv build failed with rc={completed.returncode}\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    wheels = sorted(destination.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel in {destination}, found {wheels}"
    return wheels[0]


def test_the_wheel_carries_the_runtime_registry_byte_for_byte(tmp_path):
    """The packaged registry must exist and must be the repository's file, not a copy of it.

    Byte identity rather than mere presence, because a stale packaged registry is worse than an
    absent one: absent fails loudly at import, stale answers every query with numbers nobody
    registered. The build is the only writer of the packaged copy, so any difference here means
    the mirror contract in pyproject.toml has been broken.
    """
    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        assert PACKAGED_REGISTRY in members, (
            f"{PACKAGED_REGISTRY} is missing from the built wheel, so an installed copy cannot "
            f"find its registry and raises on the first abstention call; wheel carries "
            f"{sorted(n for n in members if not n.endswith('.py'))}"
        )
        packaged_bytes = archive.read(PACKAGED_REGISTRY)

    assert packaged_bytes == SOURCE_REGISTRY.read_bytes(), (
        "the wheel's registry differs from eval/thresholds.json; the build is supposed to be the "
        "only thing that writes the packaged copy"
    )


def test_the_source_tree_has_no_package_local_registry(tmp_path):
    """A `moodboard/eval/` directory in the source tree would silently shadow the real registry.

    `_default_thresholds_path` walks up from `moodboard/abstain.py` and stops at the first
    `eval/thresholds.json` it finds. The first parent it checks is `moodboard/` itself, which is
    exactly what makes the wheel layout work, and it is also what would make a stray file there
    take precedence over the repository's registry in a source checkout. The two cases are
    indistinguishable to the loader, so the source tree must not contain one.
    """
    stray = REPO_ROOT / "moodboard" / "eval"
    assert not stray.exists(), (
        f"{stray} exists in the source tree and would shadow {SOURCE_REGISTRY} for every source "
        "checkout and editable install, silently, since the loader stops at the first match"
    )


def test_the_schema_the_report_validates_against_also_ships(tmp_path):
    """The other runtime data file, included because a fix aimed at one gap should say what the
    population was. `report.py` resolves its JSON Schema package-locally, so it ships already; the
    assertion exists so that stops being an accident of layout and becomes a checked property."""
    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())

    assert "moodboard/schema/report_v1_0.schema.json" in members, (
        "the report schema is missing from the wheel, so report validation cannot run from an "
        "installed copy"
    )
