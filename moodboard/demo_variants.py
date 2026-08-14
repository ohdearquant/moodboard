"""Build a deterministic, lineage-bound preference pool from the demo paintings.

The variants are deliberately boring: original bytes, an exact 90% centre crop, and a
horizontal mirror.  They create enough distinct unordered pairs to exercise Khive's real support
gates without pretending that synthetic random vectors are graphic assets.  Derived pixels are
serialized with Moodboard's byte-frozen PNG writer, so each Khive ContentRef is reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from blake3 import blake3
from PIL import Image, UnidentifiedImageError

from .demo_data import AcquisitionError, _validate_manifest
from .encoders import _png_bytes

POOL_SCHEMA_VERSION = "moodboard.preference-demo-pool.v1"
POOL_PRODUCER_REVISION = "moodboard.preference-demo-variants.v1"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _source_bytes(manifest_path: Path, row: dict[str, Any]) -> tuple[Path, bytes, np.ndarray]:
    relative = row["local_path"]
    if not isinstance(relative, str) or not relative.startswith("assets/"):
        raise ValueError("demo source local_path must remain inside assets/")
    source = manifest_path.parent / relative
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"demo source {relative!r} must be a regular non-symlink file")
    resolved_parent = source.resolve().parent
    if resolved_parent != (manifest_path.parent / "assets").resolve():
        raise ValueError(f"demo source {relative!r} escapes the governed assets directory")
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != row["sha256"]:
        raise ValueError(f"source sha256 mismatch for {row['asset_id']}")
    if blake3(payload).hexdigest() != row["khive_content_ref"]:
        raise ValueError(f"source Khive ContentRef mismatch for {row['asset_id']}")
    try:
        with Image.open(source) as handle:
            handle.load()
            pixels = np.asarray(handle.convert("RGB"), dtype=np.uint8)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as error:
        raise ValueError(f"source image {row['asset_id']} cannot be decoded: {error}") from error
    if pixels.shape[:2] != (row["image"]["height"], row["image"]["width"]):
        raise ValueError(f"source dimensions mismatch for {row['asset_id']}")
    return source, payload, pixels


def _variant_payloads(payload: bytes, pixels: np.ndarray) -> tuple[tuple[str, bytes], ...]:
    height, width = pixels.shape[:2]
    margin_x = max(1, width // 20)
    margin_y = max(1, height // 20)
    if width <= 2 * margin_x or height <= 2 * margin_y:
        raise ValueError("demo source is too small for the frozen 90% centre crop")
    cropped = pixels[margin_y : height - margin_y, margin_x : width - margin_x]
    mirrored = np.ascontiguousarray(pixels[:, ::-1])
    return (
        ("original", payload),
        ("center_crop_90pct", _png_bytes(np.ascontiguousarray(cropped), 0)),
        ("horizontal_mirror", _png_bytes(mirrored, 0)),
    )


def build_preference_demo_pool(manifest_path: Path, destination: Path) -> Path:
    """Publish one immutable 3x style-asset pool and return its manifest path."""

    source_manifest = Path(manifest_path)
    raw_manifest = source_manifest.read_bytes()
    try:
        document = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"demo manifest is not strict UTF-8 JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("demo manifest must be a JSON object")
    try:
        _validate_manifest(document)
    except AcquisitionError as error:
        raise ValueError(str(error)) from error

    style_rows = [
        row
        for row in document["assets"]
        if isinstance(row, dict) and str(row.get("collection", "")).startswith("style-")
    ]
    if len(style_rows) < 2:
        raise ValueError("preference demo pool requires at least two governed style assets")
    # Validate and materialize every source before creating the destination.  A failed source
    # therefore cannot leave a directory that looks like a complete pool.
    prepared = [(row, *_source_bytes(source_manifest, row)) for row in style_rows]

    output = Path(destination)
    if output.exists():
        raise FileExistsError(f"preference demo pool destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        assets_dir = staging / "assets"
        assets_dir.mkdir()
        result_rows: list[dict[str, Any]] = []
        for row, source, payload, pixels in prepared:
            del source
            for transform, variant in _variant_payloads(payload, pixels):
                extension = Path(row["local_path"]).suffix if transform == "original" else ".png"
                variant_id = f"{row['asset_id']}--{transform.replace('_', '-')}"
                local_path = f"assets/{variant_id}{extension}"
                target = staging / local_path
                target.write_bytes(variant)
                with Image.open(target) as handle:
                    width, height = handle.size
                result_rows.append(
                    {
                        "artist": row["artist"],
                        "asset_id": variant_id,
                        "collection": row["collection"],
                        "height": height,
                        "khive_content_ref": blake3(variant).hexdigest(),
                        "license": row["license"],
                        "local_path": local_path,
                        "sha256": hashlib.sha256(variant).hexdigest(),
                        "source_asset_id": row["asset_id"],
                        "source_page_url": row["source_page_url"],
                        "title": row["title"],
                        "transform": transform,
                        "width": width,
                    }
                )

        sha_values = [row["sha256"] for row in result_rows]
        refs = [row["khive_content_ref"] for row in result_rows]
        if len(set(sha_values)) != len(result_rows) or len(set(refs)) != len(result_rows):
            raise ValueError("preference demo transforms produced duplicate content")
        pool = {
            "asset_count": len(result_rows),
            "assets": result_rows,
            "producer_revision": POOL_PRODUCER_REVISION,
            "schema_version": POOL_SCHEMA_VERSION,
            "source_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        }
        (staging / "manifest.json").write_bytes(_canonical_json(pool))
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output / "manifest.json"
