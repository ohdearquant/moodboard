"""Generate Adobe-facing procedural imagery through the real Moodboard engine.

The source recipe is deterministic and owns pixels only. The JSON report is never assembled or
patched here: ``moodboard build`` and ``moodboard rank`` are the only report producers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

SEED = 20_260_808
WIDTH = 480
HEIGHT = 320
WARM = (242, 235, 221)
INK = (26, 30, 34)
COBALT = (37, 78, 216)
CORAL = (226, 91, 69)
MIST = (202, 212, 226)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(image: Image.Image, path: Path) -> None:
    image.save(path, format="PNG", compress_level=9, optimize=False)


def _editorial_frame(index: int, *, accent: tuple[int, int, int] = CORAL) -> Image.Image:
    backgrounds = (
        (247, 239, 221),
        (235, 225, 204),
        (250, 244, 234),
        (222, 216, 202),
        (245, 231, 210),
        (231, 237, 235),
    )
    blues = ((30, 66, 190), (42, 88, 225), (22, 45, 135), (61, 105, 205))
    corals = ((225, 84, 60), (197, 64, 47), (239, 118, 79), (180, 75, 63))
    background = backgrounds[index % len(backgrounds)]
    blue = blues[index % len(blues)]
    resolved_accent = corals[index % len(corals)] if accent == CORAL else accent
    ink = (20 + index * 3, 24 + index * 2, 29 + index)
    image = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, WIDTH - 24, HEIGHT - 24), outline=ink, width=2)
    layout = index % 3
    if layout == 0:
        draw.rectangle((42, 42, WIDTH - 42, 82 + (index % 4) * 8), fill=blue)
        draw.rectangle((42, 100, WIDTH // 2 + (index % 4) * 20, HEIGHT - 42), fill=ink)
        draw.ellipse(
            (280 - (index % 3) * 20, 120, 400, 240 + (index % 2) * 25),
            fill=resolved_accent,
        )
    elif layout == 1:
        draw.rectangle((42, 42, 160 + (index % 4) * 25, HEIGHT - 42), fill=blue)
        draw.rectangle((180, 42, WIDTH - 42, 150 + (index % 3) * 30), fill=resolved_accent)
        draw.rectangle((180, 180, WIDTH - 42, HEIGHT - 42), fill=ink)
    else:
        draw.ellipse((42, 42, 210 + (index % 4) * 20, 220), fill=blue)
        draw.rectangle((235, 42, WIDTH - 42, HEIGHT - 42), fill=ink)
        draw.rectangle((64, 240, WIDTH - 42, HEIGHT - 42), fill=resolved_accent)
    return image


def _diverse_compatible() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), WARM)
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, WIDTH - 24, HEIGHT - 24), outline=INK, width=2)
    draw.rectangle((42, 42, WIDTH - 42, 92), fill=CORAL)
    draw.rectangle((42, 111, WIDTH - 250, HEIGHT - 42), fill=COBALT)
    draw.rectangle((WIDTH - 228, 111, WIDTH - 42, HEIGHT - 42), fill=INK)
    draw.ellipse((74, 151, 162, 239), fill=WARM)
    for index, length in enumerate((110, 138, 92, 126)):
        y = 145 + index * 29
        draw.rectangle((WIDTH - 202, y, WIDTH - 202 + length, y + 7), fill=WARM)
    return image


def _palette_drift() -> Image.Image:
    image = _editorial_frame(3, accent=(170, 52, 196))
    draw = ImageDraw.Draw(image)
    draw.rectangle((42, 42, WIDTH - 190, 66), fill=(13, 151, 137))
    draw.line((44, HEIGHT - 38, WIDTH - 44, HEIGHT - 38), fill=(170, 52, 196), width=3)
    return image


def _composition_drift() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), WARM)
    draw = ImageDraw.Draw(image)
    draw.polygon(((0, 0), (WIDTH, 0), (0, HEIGHT)), fill=COBALT)
    draw.polygon(((WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)), fill=CORAL)
    draw.ellipse((150, 70, 330, 250), fill=INK)
    draw.rectangle((190, 110, 290, 210), fill=WARM)
    return image


def _far_outlier(seed: int) -> Image.Image:
    randomizer = random.Random(seed)
    return Image.frombytes("RGB", (WIDTH, HEIGHT), randomizer.randbytes(WIDTH * HEIGHT * 3))


def _write_sources(root: Path, seed: int) -> tuple[Path, Path]:
    references = root / "references"
    candidates = root / "candidates"
    references.mkdir(parents=True)
    candidates.mkdir(parents=True)
    for index in range(12):
        _save(_editorial_frame(index), references / f"reference_{index + 1:02d}.png")
    _save(_editorial_frame(8), candidates / "01_aligned.png")
    _save(_diverse_compatible(), candidates / "02_diverse_compatible.png")
    _save(_palette_drift(), candidates / "03_palette_drift.png")
    _save(_composition_drift(), candidates / "04_composition_drift.png")
    shutil.copyfile(references / "reference_01.png", candidates / "05_duplicate.png")
    _save(_far_outlier(seed), candidates / "06_far_outlier.png")
    return references, candidates


def _run_moodboard(arguments: list[str], cwd: Path) -> None:
    command = [
        sys.executable,
        "-c",
        ("import sys; from moodboard.cli import main; raise SystemExit(main(sys.argv[1:]))"),
        *arguments,
    ]
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {command!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def _is_score_source(relative: Path) -> bool:
    if relative in {Path("eval/thresholds.json"), Path("uv.lock")}:
        return True
    return (
        len(relative.parts) > 1
        and relative.parts[0] == "moodboard"
        and relative.suffix in {".py", ".json"}
        and relative != Path("moodboard/viewer.py")
        and "viewer_dist" not in relative.parts
        and "__pycache__" not in relative.parts
    )


def _score_source_files(repository: Path) -> tuple[Path, ...]:
    candidates = [
        path
        for root in (repository / "moodboard",)
        for path in root.rglob("*")
        if path.is_file() and _is_score_source(path.relative_to(repository))
    ]
    thresholds = repository / "eval" / "thresholds.json"
    if thresholds.is_file():
        candidates.append(thresholds)
    lockfile = repository / "uv.lock"
    if lockfile.is_file():
        candidates.append(lockfile)
    return tuple(sorted(candidates, key=lambda path: path.relative_to(repository).as_posix()))


def _engine_source_digest(repository: Path) -> str:
    digest = hashlib.sha256()
    for path in _score_source_files(repository):
        digest.update(path.relative_to(repository).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _engine_source_dirty(repository: Path) -> bool:
    current = {path.relative_to(repository).as_posix() for path in _score_source_files(repository)}
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "-z",
            "--",
            "moodboard",
            "eval/thresholds.json",
            "uv.lock",
        ],
        capture_output=True,
        check=False,
    )
    if tracked.returncode != 0:
        return True
    for raw in tracked.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative = Path(raw.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            return True
        if _is_score_source(relative):
            current.add(relative.as_posix())
    if not current:
        return True
    status = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *sorted(current),
        ],
        capture_output=True,
        check=False,
    )
    return status.returncode != 0 or bool(status.stdout)


def _engine_source_identity(repository: Path) -> tuple[str, str, bool]:
    digest = _engine_source_digest(repository)
    # A content-addressed source-set revision survives both its own fixture commit and unrelated
    # repository commits. The plain digest remains separate for machine comparison and backwards
    # readability; the tagged revision states what kind of identity this is.
    return f"source-set-sha256:{digest}", digest, _engine_source_dirty(repository)


def _generate(repository: Path, workspace: Path, seed: int) -> tuple[Path, dict[str, Any]]:
    references, candidates = _write_sources(workspace, seed)
    board = workspace / "showcase.brand.mb"
    report = workspace / "showcase-report.json"
    references_argument = references.relative_to(repository).as_posix()
    candidates_argument = candidates.relative_to(repository).as_posix()
    board_argument = board.relative_to(repository).as_posix()
    report_argument = report.relative_to(repository).as_posix()
    build_argv = [
        "build",
        references_argument,
        "--name",
        "Adobe editorial system",
        "--output",
        board_argument,
    ]
    rank_argv = [
        "rank",
        candidates_argument,
        "--board",
        board_argument,
        "--references",
        references_argument,
        "--output",
        report_argument,
        "--alpha",
        "0.10",
        "--exemplars",
        "3",
        "--tie-pairs",
        "all",
        "--seed",
        str(seed),
    ]
    _run_moodboard(build_argv, repository)
    _run_moodboard(rank_argv, repository)
    document = json.loads(report.read_text(encoding="utf-8"))
    assets = document["assets"]
    scored = [asset for asset in assets if asset["state"] == "scored"]
    abstained = [asset for asset in assets if asset["state"] == "abstained"]
    if document["schema_version"] != "1.1":
        raise RuntimeError("showcase generator requires the real report 1.1 writer")
    if not scored:
        raise RuntimeError("procedural scenario produced no scored candidates")
    if any(len(asset["exemplars"]) != 3 for asset in assets):
        raise RuntimeError("procedural scenario lost its strict three-reference evidence")
    expected_ids = {
        "01_aligned.png",
        "02_diverse_compatible.png",
        "03_palette_drift.png",
        "04_composition_drift.png",
        "05_duplicate.png",
        "06_far_outlier.png",
    }
    if {asset["asset_id"] for asset in assets} != expected_ids:
        raise RuntimeError("procedural scenario lost a named counterexample")

    source_paths = sorted((*references.glob("*.png"), *candidates.glob("*.png")))
    schema_path = repository / "moodboard" / "schema" / "report_v1_1.schema.json"
    generator_path = Path(__file__).resolve()
    engine_revision, engine_source_digest, engine_source_dirty = _engine_source_identity(repository)
    manifest = {
        "format_version": 1,
        "scenario": "adobe-editorial-system",
        "seed": seed,
        "report_sha256": _sha256(report),
        "report_schema_version": document["schema_version"],
        "report_schema_sha256": _sha256(schema_path),
        "generator_sha256": _sha256(generator_path),
        "engine_revision": engine_revision,
        "engine_source_dirty": engine_source_dirty,
        "engine_source_digest": engine_source_digest,
        "uv_lock_sha256": _sha256(repository / "uv.lock"),
        "commands": {
            "build": ["moodboard", *build_argv],
            "rank": ["moodboard", *rank_argv],
        },
        "source_images": {
            path.relative_to(workspace).as_posix(): _sha256(path) for path in source_paths
        },
        "preconditions": {
            "asset_ids": sorted(expected_ids),
            "scored_assets": len(scored),
            "abstained_assets": len(abstained),
            "tie_pairs": len(document["comparisons"]["ties"]),
            "three_exemplars_per_asset": True,
            "candidate_previews_inline": all("image" in asset for asset in assets),
            "counterexamples": [
                "palette-drift",
                "composition-drift",
                "exact-duplicate",
                "diverse-but-system-compatible",
                "far-outlier",
            ],
            "claim_scope": "functionality-and-interface-fixture",
        },
    }
    return report, manifest


def _normalized_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    report["board"]["built_at"] = "<generated-at>"
    report["provenance"]["created_at"] = "<generated-at>"
    # The published report truthfully names the clean engine commit used to create it. Its own
    # fixture commit (and later documentation-only commits) necessarily move HEAD and dirty the
    # checkout used by a verification run without moving any score-bearing byte. The manifest's
    # content-addressed source-set identity is the verification fence for those bytes, so ignore
    # only these self-referential Git checkout fields while comparing regenerated geometry.
    engine = report["provenance"]["engine"]
    if "source_revision" in engine:
        engine["source_revision"] = "<engine-source-revision>"
        engine["source_dirty"] = "<engine-source-dirty>"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", type=Path)
    action.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[2]
    target: Path = args.write or args.check
    workspace = repository / "viewer" / "test-artifacts" / "showcase-generation"
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    try:
        report, manifest = _generate(repository, workspace, args.seed)
        if args.write is not None:
            if manifest["engine_source_dirty"]:
                raise RuntimeError(
                    "refusing to publish a showcase fixture from dirty score-bearing source; "
                    "commit the engine source, then regenerate the fixture"
                )
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(report, target / "report.json")
            (target / "manifest.json").write_bytes(_canonical_json(manifest))
            return 0

        expected_report = target / "report.json"
        expected_manifest = target / "manifest.json"
        if not expected_report.is_file() or not expected_manifest.is_file():
            raise RuntimeError(f"committed fixture is incomplete under {target}")
        if _normalized_report(report) != _normalized_report(expected_report):
            raise RuntimeError("engine-generated showcase report drifted outside timestamps")
        committed_manifest = json.loads(expected_manifest.read_text(encoding="utf-8"))
        if committed_manifest.get("engine_source_dirty") is not False:
            raise RuntimeError(
                "committed showcase fixture claims dirty score-bearing source provenance"
            )
        if committed_manifest["report_sha256"] != _sha256(expected_report):
            raise RuntimeError("committed showcase report no longer matches its manifest digest")
        for key in (
            "format_version",
            "seed",
            "scenario",
            "report_schema_version",
            "report_schema_sha256",
            "generator_sha256",
            "engine_revision",
            "engine_source_dirty",
            "engine_source_digest",
            "uv_lock_sha256",
            "commands",
            "source_images",
            "preconditions",
        ):
            if committed_manifest[key] != manifest[key]:
                raise RuntimeError(f"showcase manifest drifted at {key}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
