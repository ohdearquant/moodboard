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

import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_REGISTRY = REPO_ROOT / "eval" / "thresholds.json"
PACKAGED_REGISTRY = "moodboard/eval/thresholds.json"
DEMO_RUNTIME_DATA = {
    REPO_ROOT / "moodboard" / "demo_sources_v1.json": "moodboard/demo_sources_v1.json",
    REPO_ROOT
    / "moodboard"
    / "schema"
    / "demo_manifest_v1.schema.json": "moodboard/schema/demo_manifest_v1.schema.json",
}
INTENT_PACKET_SCHEMAS = {
    REPO_ROOT / "moodboard" / "schema" / name: f"moodboard/schema/{name}"
    for name in (
        "intent_packet_v1.schema.json",
        "operation_localized_edit_v1.schema.json",
        "verification_policy_v1.schema.json",
        "raster_srgb_u8_v1.schema.json",
        "mask_u8_v1.schema.json",
    )
}
PROVIDER_ARTIFACT_SCHEMAS = {
    REPO_ROOT / "moodboard" / "schema" / name: f"moodboard/schema/{name}"
    for name in (
        "generation_run_v1.schema.json",
        "generation_attempt_v1.schema.json",
        "generation_attempt_event_v1.schema.json",
        "provider_capability_snapshot_v1.schema.json",
        "normalized_provider_request_v1.schema.json",
        "provider_receipt_v1.schema.json",
        "output_occurrence_v1.schema.json",
    )
}


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
        judgment_schema_bytes = archive.read("moodboard/schema/judgment_v1.schema.json")

    assert "moodboard/schema/report_v1_0.schema.json" in members, (
        "the report schema is missing from the wheel, so report validation cannot run from an "
        "installed copy"
    )
    assert "moodboard/schema/report_v1_1.schema.json" in members, (
        "the current report writer schema is missing from the wheel, so rank/report validation "
        "cannot run from an installed copy"
    )
    assert (
        judgment_schema_bytes
        == (REPO_ROOT / "moodboard" / "schema" / "judgment_v1.schema.json").read_bytes()
    ), "the installed typed-judgment contract differs from the reviewed schema bytes"


def test_the_intent_packet_schema_registry_ships_byte_for_byte(tmp_path):
    """External schema references must resolve from an installed, offline package."""

    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        for source, packaged_path in INTENT_PACKET_SCHEMAS.items():
            assert packaged_path in archive.namelist(), (
                f"{packaged_path} is missing from the wheel, so the installed intent-packet "
                "validator cannot resolve its closed operation and verifier registry"
            )
            assert archive.read(packaged_path) == source.read_bytes(), (
                f"the installed {packaged_path} differs from the reviewed contract bytes"
            )


def test_the_provider_artifact_schema_registry_ships_byte_for_byte(tmp_path):
    """Every provider-evidence branch must remain available to an offline install."""

    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        for source, packaged_path in PROVIDER_ARTIFACT_SCHEMAS.items():
            assert packaged_path in archive.namelist(), (
                f"{packaged_path} is missing from the wheel, so the installed provider-artifact "
                "validator cannot reconstruct its closed offline registry"
            )
            assert archive.read(packaged_path) == source.read_bytes(), (
                f"the installed {packaged_path} differs from the reviewed contract bytes"
            )


def test_installed_provider_artifact_validator_resolves_its_registry_offline(tmp_path):
    """Import and execute the validator from wheel bytes, not from this source checkout."""

    wheel = _build_wheel(tmp_path / "dist")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)

    document = {
        "schema_version": "moodboard.generation-run.v1",
        "generation_run_id": "20000000-0000-4000-8000-000000000001",
        "creative_session_id": "20000000-0000-4000-8000-000000000002",
        "intent_packet_id": "1" * 64,
        "requested_provider": "openrouter",
        "requested_model": "qwen/qwen-image-3",
        "provider_route_policy_id": "2" * 64,
        "created_at": "2026-08-16T20:30:02Z",
    }
    script = (
        "import json, pathlib, sys\n"
        f"sys.path.insert(0, {str(installed)!r})\n"
        "import moodboard.attempt_state\n"
        "import moodboard.attempt_journal\n"
        "import moodboard.locality_contracts\n"
        "from moodboard.provider_artifacts import validate_provider_artifact\n"
        f"document = json.loads({json.dumps(json.dumps(document))})\n"
        "validate_provider_artifact(document)\n"
        "raster_bytes = bytes([1, 2, 3])\n"
        "raster_projection = {\n"
        "    'compiler_revision': 'packaging-probe.v1',\n"
        "    'width': 1, 'height': 1, 'mode': 'RGB', 'byte_count': 3,\n"
        "    'source_content_sha256': '3' * 64,\n"
        "}\n"
        "raster_document = {\n"
        "    'schema_version': moodboard.locality_contracts.RASTER_SCHEMA_VERSION,\n"
        "    **raster_projection,\n"
        "    'raster_sha256': moodboard.locality_contracts.compute_raster_sha256(\n"
        "        raster_projection, raster_bytes\n"
        "    ),\n"
        "}\n"
        "moodboard.locality_contracts.validate_raster_artifact(\n"
        "    raster_document, raster_bytes\n"
        ")\n"
        "mask_bytes = bytes([0, 1])\n"
        "mask_projection = {\n"
        "    'compiler_revision': 'packaging-mask-probe.v1',\n"
        "    'width': 2, 'height': 1, 'byte_count': 2,\n"
        "    'editable_count': 1, 'protected_count': 1,\n"
        "    'source_raster_sha256': raster_document['raster_sha256'],\n"
        "}\n"
        "mask_document = {\n"
        "    'schema_version': moodboard.locality_contracts.MASK_SCHEMA_VERSION,\n"
        "    **mask_projection,\n"
        "    'mask_sha256': moodboard.locality_contracts.compute_mask_sha256(\n"
        "        mask_projection, mask_bytes\n"
        "    ),\n"
        "}\n"
        "moodboard.locality_contracts.validate_mask_artifact(mask_document, mask_bytes)\n"
        "module_path = pathlib.Path(sys.modules['moodboard.provider_artifacts'].__file__)\n"
        f"assert module_path.is_relative_to(pathlib.Path({str(installed)!r}))\n"
        "state_path = pathlib.Path(sys.modules['moodboard.attempt_state'].__file__)\n"
        f"assert state_path.is_relative_to(pathlib.Path({str(installed)!r}))\n"
        "journal_path = pathlib.Path(sys.modules['moodboard.attempt_journal'].__file__)\n"
        f"assert journal_path.is_relative_to(pathlib.Path({str(installed)!r}))\n"
        "locality_path = pathlib.Path(sys.modules['moodboard.locality_contracts'].__file__)\n"
        f"assert locality_path.is_relative_to(pathlib.Path({str(installed)!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
    )
    assert completed.returncode == 0, (
        "installed provider-artifact validator could not resolve its packaged schemas\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_the_demo_acquisition_catalog_and_manifest_contract_ship_byte_for_byte(tmp_path):
    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        for source, packaged_path in DEMO_RUNTIME_DATA.items():
            assert packaged_path in archive.namelist(), (
                f"{packaged_path} is missing from the wheel, so installed demo acquisition "
                "cannot validate its governed inputs or output"
            )
            assert archive.read(packaged_path) == source.read_bytes(), (
                f"the wheel's {packaged_path} differs from the reviewed repository bytes"
            )


def test_the_verified_viewer_package_ships_when_the_build_has_staged_it(tmp_path):
    staged = REPO_ROOT / "moodboard" / "viewer_dist"
    if not staged.is_dir():
        pytest.skip("run `npm --prefix viewer run build` to stage the verified viewer package")
    wheel = _build_wheel(tmp_path / "dist")

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())

    required = {
        "moodboard/viewer_dist/artifact-manifest.json",
        "moodboard/viewer_dist/standalone-template.html",
        "moodboard/viewer_dist/schemas/report_v1_0.schema.json",
        "moodboard/viewer_dist/schemas/report_v1_1.schema.json",
    }
    assert required <= members, (
        "the ignored build output was staged but omitted from the wheel; report --html would "
        f"fail after installation. Missing {sorted(required - members)}"
    )


def test_the_staged_viewer_package_also_survives_the_sdist_boundary(tmp_path):
    staged = REPO_ROOT / "moodboard" / "viewer_dist"
    if not staged.is_dir():
        pytest.skip("run `npm --prefix viewer run build` to stage the verified viewer package")
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH, so the source distribution cannot be measured")
    destination = tmp_path / "dist"
    completed = subprocess.run(
        [uv, "build", "--sdist", "-o", str(destination)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    archives = sorted(destination.glob("*.tar.gz"))
    assert len(archives) == 1

    with tarfile.open(archives[0], "r:gz") as archive:
        members = {Path(name).as_posix() for name in archive.getnames()}
    suffixes = {
        "moodboard/viewer_dist/artifact-manifest.json",
        "moodboard/viewer_dist/standalone-template.html",
    }
    for suffix in suffixes:
        assert any(name.endswith(suffix) for name in members), f"sdist omitted {suffix}"
