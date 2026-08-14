"""Freeze one validated Pixel RAG artifact and its governed media into the offline viewer.

The bridge keeps the engine artifact authoritative, verifies the exact source and ranked-evidence
bytes it names, and embeds bounded display derivatives. TypeScript receives one closed,
identity-bound static import with no runtime fetch.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import stat
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from blake3 import blake3
from jsonschema import Draft202012Validator
from PIL import Image, ImageOps, UnidentifiedImageError

from moodboard.demo_data import MANIFEST_SCHEMA_PATH
from moodboard.pixel_rag import (
    ARTIFACT_SCHEMA,
    PixelRagError,
    read_pixel_rag_artifact,
    validate_pixel_rag_artifact,
)

BRIDGE_FORMAT = "moodboard.viewer-pixel-rag-bridge.v2"
GENERATOR_REVISION = "moodboard.pixel-rag-viewer-bridge.v2"
_MAX_BRIDGE_BYTES = 256 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_MEDIA_BYTES = 24 * 1024 * 1024
_MAX_MEDIA_TOTAL_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_SIDE = 8192
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_PREVIEW_SIDE = 480
_MAX_PREVIEW_PIXELS = _MAX_PREVIEW_SIDE * _MAX_PREVIEW_SIDE
_MAX_PREVIEW_BYTES = 64 * 1024
_MAX_PREVIEW_TOTAL_BYTES = 180 * 1024
_PREVIEW_JPEG_QUALITY = 45
_FORBIDDEN_PREVIEW_INFO = frozenset({"comment", "exif", "icc_profile", "photoshop", "xmp"})
_MIME_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_TOP_LEVEL_KEYS = frozenset(
    {"artifact", "format_version", "generator_revision", "input", "media", "state"}
)
_INPUT_KEYS = frozenset(
    {"artifact_id", "byte_size", "canonical_sha256", "schema_version", "sha256"}
)
_MEDIA_KEYS = frozenset({"assets", "manifest_sha256"})
_MEDIA_ASSET_KEYS = frozenset(
    {
        "asset_id",
        "content_ref",
        "content_sha256",
        "original_byte_size",
        "original_height",
        "original_width",
        "preview",
    }
)
_PREVIEW_KEYS = frozenset({"byte_size", "data_base64", "height", "mime", "sha256", "width"})

__all__ = [
    "BRIDGE_FORMAT",
    "GENERATOR_REVISION",
    "PixelRagViewerBridgeError",
    "check_viewer_pixel_rag_bridge",
    "compile_viewer_pixel_rag_bridge",
    "fallback_viewer_pixel_rag_bridge",
    "read_viewer_pixel_rag_bridge",
    "validate_viewer_pixel_rag_bridge",
    "write_viewer_pixel_rag_bridge",
]


class PixelRagViewerBridgeError(PixelRagError):
    """A viewer bridge cannot preserve the engine artifact's frozen identity."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PixelRagViewerBridgeError(f"{label} must be an object")
    return value


def _closed(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        detail = f"unknown keys {unknown}" if unknown else f"missing keys {missing}"
        raise PixelRagViewerBridgeError(f"{label} is not closed: {detail}")


def _digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PixelRagViewerBridgeError(f"{label} must be a lowercase 64-hex digest")
    return value


def _positive_integer(value: object, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PixelRagViewerBridgeError(f"{label} is invalid")
    return value


def _strict_json(path: Path, *, label: str, ceiling: int) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise PixelRagViewerBridgeError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PixelRagViewerBridgeError(f"{label} must be a non-symlink regular file")
    if metadata.st_size > ceiling:
        raise PixelRagViewerBridgeError(f"{label} exceeds its byte ceiling")
    raw = source.read_bytes()
    if len(raw) != metadata.st_size:
        raise PixelRagViewerBridgeError(f"{label} changed while it was read")
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PixelRagViewerBridgeError(f"{label} is not strict JSON: {error}") from error
    return dict(_mapping(value, label)), raw


def _required_media_records(artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = [_mapping(artifact["source"], "artifact source")]
    intents = artifact["intents"]
    if not isinstance(intents, list):
        raise PixelRagViewerBridgeError("artifact intents must be an array")
    for intent_index, intent_value in enumerate(intents):
        intent = _mapping(intent_value, f"artifact intents[{intent_index}]")
        retrieval = _mapping(intent["retrieval"], f"artifact intents[{intent_index}].retrieval")
        evidence = retrieval["ranked_evidence"]
        if not isinstance(evidence, list):
            raise PixelRagViewerBridgeError("artifact ranked evidence must be an array")
        records.extend(
            _mapping(row, f"artifact intents[{intent_index}].ranked_evidence[{row_index}]")
            for row_index, row in enumerate(evidence)
        )

    by_id: dict[str, Mapping[str, Any]] = {}
    identities: set[tuple[str, str, str]] = set()
    seen_sha256: set[str] = set()
    seen_content_refs: set[str] = set()
    for record in records:
        asset_id = record["asset_id"]
        sha256 = record["sha256"]
        khive = _mapping(record["khive"], f"artifact media {asset_id!r} khive")
        identity = (asset_id, sha256, khive["content_ref"])
        if not all(isinstance(part, str) for part in identity):
            raise PixelRagViewerBridgeError("artifact media identity is invalid")
        existing = by_id.get(asset_id)
        if existing is not None:
            existing_ref = _mapping(existing["khive"], "duplicate artifact media khive")[
                "content_ref"
            ]
            if (existing["sha256"], existing_ref) != (sha256, khive["content_ref"]):
                raise PixelRagViewerBridgeError("artifact repeats a media id with another identity")
        by_id[asset_id] = record
        identities.add(identity)
        if sha256 in seen_sha256 or khive["content_ref"] in seen_content_refs:
            raise PixelRagViewerBridgeError(
                "artifact media SHA-256 and ContentRef identities must be one-to-one"
            )
        seen_sha256.add(sha256)
        seen_content_refs.add(khive["content_ref"])
    if len(identities) != len(by_id):
        raise PixelRagViewerBridgeError("artifact media identities are not one-to-one")
    if len(by_id) != 7:
        raise PixelRagViewerBridgeError(
            "artifact must bind exactly seven unique source and top-three evidence media identities"
        )
    return by_id


def _manifest_rows(
    manifest_path: Path, artifact: Mapping[str, Any]
) -> tuple[dict[str, Mapping[str, Any]], str, Path]:
    manifest, raw = _strict_json(
        manifest_path, label="Pixel RAG media manifest", ceiling=_MAX_MANIFEST_BYTES
    )
    try:
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_bytes())
        errors = sorted(
            Draft202012Validator(schema).iter_errors(manifest),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PixelRagViewerBridgeError("Pixel RAG media manifest schema is unavailable") from error
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise PixelRagViewerBridgeError(
            f"Pixel RAG media manifest violates its schema at {location}: {errors[0].message}"
        )
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    source_manifest = _mapping(artifact["source_manifest"], "artifact source_manifest")
    if manifest_sha256 != source_manifest["manifest_sha256"]:
        raise PixelRagViewerBridgeError("Pixel RAG media manifest SHA-256 does not match artifact")
    root = Path(manifest_path).resolve().parent
    assets = manifest["assets"]
    if not isinstance(assets, list):
        raise PixelRagViewerBridgeError("Pixel RAG media manifest assets must be an array")
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(assets):
        row = _mapping(value, f"Pixel RAG media manifest assets[{index}]")
        asset_id = row["asset_id"]
        if not isinstance(asset_id, str) or asset_id in by_id:
            raise PixelRagViewerBridgeError("Pixel RAG media manifest asset ids are invalid")
        by_id[asset_id] = row
    return by_id, manifest_sha256, root


def _read_media_file(root: Path, row: Mapping[str, Any], *, asset_id: str) -> bytes:
    relative = row["local_path"]
    if not isinstance(relative, str):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} local path is invalid")
    candidate = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            component = current.lstat()
        except OSError as error:
            raise PixelRagViewerBridgeError(
                f"media {asset_id!r} is unavailable: {error}"
            ) from error
        if stat.S_ISLNK(component.st_mode):
            raise PixelRagViewerBridgeError(f"media {asset_id!r} path contains a symlink")
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} is unavailable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} must be a regular file")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise PixelRagViewerBridgeError(
            f"media {asset_id!r} escapes the manifest directory"
        ) from error
    if metadata.st_size <= 0 or metadata.st_size > _MAX_MEDIA_BYTES:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} violates the byte ceiling")
    raw = resolved.read_bytes()
    if len(raw) != metadata.st_size:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} changed while it was read")
    return raw


def _validate_media_bytes(
    raw: bytes,
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    asset_id: str,
) -> tuple[int, int]:
    byte_size = row["byte_size"]
    if isinstance(byte_size, bool) or not isinstance(byte_size, int) or len(raw) != byte_size:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} byte size drifted")
    if hashlib.sha256(raw).hexdigest() != record["sha256"] or record["sha256"] != row["sha256"]:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} SHA-256 drifted")
    khive = _mapping(record["khive"], f"artifact media {asset_id!r} khive")
    if (
        blake3(raw).hexdigest() != khive["content_ref"]
        or khive["content_ref"] != row["khive_content_ref"]
    ):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} ContentRef drifted")
    image_row = _mapping(row["image"], f"manifest media {asset_id!r} image")
    record_image = _mapping(record["image"], f"artifact media {asset_id!r} image")
    http = _mapping(row["http"], f"manifest media {asset_id!r} http")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                image_format = image.format
                width, height = image.size
                image.verify()
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} is not a bounded image") from error
    if (
        image_format not in _MIME_BY_FORMAT
        or width <= 0
        or height <= 0
        or width > _MAX_IMAGE_SIDE
        or height > _MAX_IMAGE_SIDE
        or width * height > _MAX_IMAGE_PIXELS
    ):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} violates the image contract")
    expected = (record_image["format"], record_image["width"], record_image["height"])
    manifest_expected = (image_row["format"], image_row["width"], image_row["height"])
    if (image_format, width, height) != expected or expected != manifest_expected:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} dimensions drifted")
    mime = http["content_type"]
    if mime != _MIME_BY_FORMAT[image_format]:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} MIME drifted")
    return width, height


def _jpeg_preview(raw: bytes, *, asset_id: str) -> dict[str, Any]:
    """Render one deterministic, metadata-free display derivative from verified bytes."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                if oriented.mode in {"RGBA", "LA"} or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    converted = Image.new("RGB", rgba.size, (255, 255, 255))
                    converted.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    converted = oriented.convert("RGB")
                clean = Image.new("RGB", converted.size)
                clean.paste(converted)
                clean.thumbnail(
                    (_MAX_PREVIEW_SIDE, _MAX_PREVIEW_SIDE),
                    Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )
                output = io.BytesIO()
                clean.save(
                    output,
                    format="JPEG",
                    quality=_PREVIEW_JPEG_QUALITY,
                    subsampling=2,
                    optimize=True,
                    progressive=True,
                    exif=b"",
                )
                width, height = clean.size
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview generation failed") from error
    preview = output.getvalue()
    if not preview or len(preview) > _MAX_PREVIEW_BYTES:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview violates the byte ceiling")
    return {
        "byte_size": len(preview),
        "data_base64": base64.b64encode(preview).decode("ascii"),
        "height": height,
        "mime": "image/jpeg",
        "sha256": hashlib.sha256(preview).hexdigest(),
        "width": width,
    }


def _compile_media(artifact: Mapping[str, Any], manifest_path: Path) -> dict[str, Any]:
    required = _required_media_records(artifact)
    manifest, manifest_sha256, root = _manifest_rows(manifest_path, artifact)
    if not set(required) <= set(manifest):
        raise PixelRagViewerBridgeError("manifest is missing required Pixel RAG media")
    assets: list[dict[str, Any]] = []
    total_bytes = 0
    for asset_id in sorted(required):
        row = manifest[asset_id]
        record = required[asset_id]
        raw = _read_media_file(root, row, asset_id=asset_id)
        total_bytes += len(raw)
        if total_bytes > _MAX_MEDIA_TOTAL_BYTES:
            raise PixelRagViewerBridgeError("Pixel RAG media exceeds the aggregate byte ceiling")
        width, height = _validate_media_bytes(raw, row, record, asset_id=asset_id)
        preview = _jpeg_preview(raw, asset_id=asset_id)
        assets.append(
            {
                "asset_id": asset_id,
                "content_ref": record["khive"]["content_ref"],
                "content_sha256": record["sha256"],
                "original_byte_size": len(raw),
                "original_height": height,
                "original_width": width,
                "preview": preview,
            }
        )
    return {"assets": assets, "manifest_sha256": manifest_sha256}


def fallback_viewer_pixel_rag_bridge() -> dict[str, Any]:
    """Return the only accepted sentinel for the presentation-owned fixture fallback."""

    return {
        "artifact": None,
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": None,
        "media": None,
        "state": "fallback",
    }


def compile_viewer_pixel_rag_bridge(source: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate and pin one canonical engine artifact for static viewer import."""

    path = Path(source)
    artifact = read_pixel_rag_artifact(path)
    raw = path.read_bytes()
    canonical = _canonical_bytes(artifact)
    if raw != canonical:
        raise PixelRagViewerBridgeError(
            "Pixel RAG viewer input must use the engine's canonical JSON encoding"
        )
    input_sha256 = hashlib.sha256(raw).hexdigest()
    bridge: dict[str, Any] = {
        "artifact": artifact,
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": {
            "artifact_id": artifact["artifact_id"],
            "byte_size": len(raw),
            "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
            "schema_version": artifact["schema_version"],
            "sha256": input_sha256,
        },
        "media": _compile_media(artifact, manifest_path),
        "state": "projected",
    }
    validate_viewer_pixel_rag_bridge(bridge)
    return bridge


def _validate_preview(
    value: object,
    *,
    asset_id: str,
    original_width: int,
    original_height: int,
) -> int:
    preview = _mapping(value, f"Pixel RAG media {asset_id!r} preview")
    _closed(preview, _PREVIEW_KEYS, f"Pixel RAG media {asset_id!r} preview")
    if preview["mime"] != "image/jpeg":
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview MIME drifted")
    byte_size = _positive_integer(
        preview["byte_size"],
        f"media {asset_id!r} preview byte size",
        maximum=_MAX_PREVIEW_BYTES,
    )
    width = _positive_integer(
        preview["width"], f"media {asset_id!r} preview width", maximum=_MAX_PREVIEW_SIDE
    )
    height = _positive_integer(
        preview["height"], f"media {asset_id!r} preview height", maximum=_MAX_PREVIEW_SIDE
    )
    if width * height > _MAX_PREVIEW_PIXELS:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview dimensions are invalid")
    expected_geometry = Image.new("RGB", (original_width, original_height))
    expected_geometry.thumbnail(
        (_MAX_PREVIEW_SIDE, _MAX_PREVIEW_SIDE),
        Image.Resampling.LANCZOS,
        reducing_gap=3.0,
    )
    if (width, height) != expected_geometry.size:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview dimensions drifted")
    payload = preview["data_base64"]
    if not isinstance(payload, str):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview base64 is invalid")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as error:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview base64 is invalid") from error
    if base64.b64encode(raw).decode("ascii") != payload:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview base64 is not canonical")
    if len(raw) != byte_size:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview byte size drifted")
    if hashlib.sha256(raw).hexdigest() != _digest(
        preview["sha256"], f"media {asset_id!r} preview sha256"
    ):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview SHA-256 drifted")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as decoded:
                image_format = decoded.format
                actual_dimensions = decoded.size
                metadata = set(decoded.info)
                exif = decoded.getexif()
                decoded.verify()
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview does not decode") from error
    if image_format != "JPEG" or actual_dimensions != (width, height):
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview dimensions drifted")
    if exif or metadata & _FORBIDDEN_PREVIEW_INFO:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} preview carries metadata")
    return byte_size


def _validate_media_asset(
    value: object, record: Mapping[str, Any], *, asset_id: str
) -> tuple[int, int]:
    row = _mapping(value, f"Pixel RAG media {asset_id!r}")
    _closed(row, _MEDIA_ASSET_KEYS, f"Pixel RAG media {asset_id!r}")
    if row["asset_id"] != asset_id:
        raise PixelRagViewerBridgeError("Pixel RAG media asset order or identity drifted")
    content_ref = _digest(row["content_ref"], f"media {asset_id!r} content_ref")
    sha256 = _digest(row["content_sha256"], f"media {asset_id!r} content_sha256")
    khive = _mapping(record["khive"], f"artifact media {asset_id!r} khive")
    image = _mapping(record["image"], f"artifact media {asset_id!r} image")
    if content_ref != khive["content_ref"] or sha256 != record["sha256"]:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} identity drifted from artifact")
    byte_size = _positive_integer(
        row["original_byte_size"],
        f"media {asset_id!r} original byte size",
        maximum=_MAX_MEDIA_BYTES,
    )
    width = _positive_integer(
        row["original_width"],
        f"media {asset_id!r} original width",
        maximum=_MAX_IMAGE_SIDE,
    )
    height = _positive_integer(
        row["original_height"],
        f"media {asset_id!r} original height",
        maximum=_MAX_IMAGE_SIDE,
    )
    if width * height > _MAX_IMAGE_PIXELS:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} original dimensions are invalid")
    expected_dimensions = (image["width"], image["height"])
    if (width, height) != expected_dimensions:
        raise PixelRagViewerBridgeError(f"media {asset_id!r} original dimensions drifted")
    preview_bytes = _validate_preview(
        row["preview"],
        asset_id=asset_id,
        original_width=width,
        original_height=height,
    )
    return byte_size, preview_bytes


def _validate_media_inventory(value: object, artifact: Mapping[str, Any]) -> None:
    media = _mapping(value, "Pixel RAG viewer media")
    _closed(media, _MEDIA_KEYS, "Pixel RAG viewer media")
    source_manifest = _mapping(artifact["source_manifest"], "artifact source_manifest")
    if (
        _digest(media["manifest_sha256"], "media manifest_sha256")
        != source_manifest["manifest_sha256"]
    ):
        raise PixelRagViewerBridgeError("Pixel RAG media manifest SHA-256 drifted")
    expected = _required_media_records(artifact)
    rows = media["assets"]
    if not isinstance(rows, list):
        raise PixelRagViewerBridgeError("Pixel RAG media assets must be an array")
    expected_ids = sorted(expected)
    actual_ids = [
        _mapping(row, f"Pixel RAG media assets[{index}]").get("asset_id")
        for index, row in enumerate(rows)
    ]
    if actual_ids != expected_ids:
        raise PixelRagViewerBridgeError("Pixel RAG bridge requires the exact media asset set")
    sizes = [
        _validate_media_asset(row, expected[asset_id], asset_id=asset_id)
        for asset_id, row in zip(expected_ids, rows, strict=True)
    ]
    if sum(original for original, _preview in sizes) > _MAX_MEDIA_TOTAL_BYTES:
        raise PixelRagViewerBridgeError("Pixel RAG media exceeds the aggregate byte ceiling")
    if sum(preview for _original, preview in sizes) > _MAX_PREVIEW_TOTAL_BYTES:
        raise PixelRagViewerBridgeError("Pixel RAG previews exceed the aggregate byte ceiling")


def validate_viewer_pixel_rag_bridge(value: Mapping[str, Any]) -> None:
    """Fail closed on bridge shape, engine schema, or any pinned identity drift."""

    bridge = _mapping(value, "Pixel RAG viewer bridge")
    _closed(bridge, _TOP_LEVEL_KEYS, "Pixel RAG viewer bridge")
    if bridge["format_version"] != BRIDGE_FORMAT:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge format_version drifted")
    if bridge["generator_revision"] != GENERATOR_REVISION:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge generator_revision drifted")

    state = bridge["state"]
    if state == "fallback":
        if (
            bridge["input"] is not None
            or bridge["artifact"] is not None
            or bridge["media"] is not None
        ):
            raise PixelRagViewerBridgeError(
                "fallback bridge cannot carry input, artifact, or media data"
            )
        return
    if state != "projected":
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge state is unsupported")

    input_identity = _mapping(bridge["input"], "Pixel RAG viewer bridge input")
    _closed(input_identity, _INPUT_KEYS, "Pixel RAG viewer bridge input")
    artifact = _mapping(bridge["artifact"], "Pixel RAG viewer bridge artifact")

    # This is the engine's complete schema + semantic validator, not a viewer-side substitute.
    validate_pixel_rag_artifact(artifact)
    canonical = _canonical_bytes(artifact)
    canonical_sha256 = hashlib.sha256(canonical).hexdigest()
    if input_identity["schema_version"] != ARTIFACT_SCHEMA:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input schema_version drifted")
    if input_identity["artifact_id"] != artifact["artifact_id"]:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input artifact_id drifted")
    if _digest(input_identity["canonical_sha256"], "input canonical_sha256") != canonical_sha256:
        raise PixelRagViewerBridgeError("Pixel RAG viewer input canonical_sha256 drifted")
    if _digest(input_identity["sha256"], "input sha256") != canonical_sha256:
        raise PixelRagViewerBridgeError(
            "Pixel RAG viewer input sha256 drifted from canonical bytes"
        )
    byte_size = input_identity["byte_size"]
    if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size != len(canonical):
        raise PixelRagViewerBridgeError("Pixel RAG viewer input byte_size drifted")
    _validate_media_inventory(bridge["media"], artifact)
    if len(_canonical_bytes(bridge)) >= _MAX_BRIDGE_BYTES:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge exceeds the byte ceiling")


def write_viewer_pixel_rag_bridge(value: Mapping[str, Any], destination: Path) -> None:
    """Atomically replace the generated viewer asset with canonical bridge JSON."""

    validate_viewer_pixel_rag_bridge(value)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def read_viewer_pixel_rag_bridge(path: Path) -> dict[str, Any]:
    """Read a canonical generated asset and repeat every build-time drift check."""

    source = Path(path)
    if not source.is_file():
        raise PixelRagViewerBridgeError(f"Pixel RAG viewer bridge is missing: {source}")
    if source.stat().st_size >= _MAX_BRIDGE_BYTES:
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge exceeds the byte ceiling")
    raw = source.read_bytes()
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PixelRagViewerBridgeError(
            f"Pixel RAG viewer bridge is invalid JSON: {error}"
        ) from error
    bridge = dict(_mapping(value, "Pixel RAG viewer bridge"))
    validate_viewer_pixel_rag_bridge(bridge)
    if raw != _canonical_bytes(bridge):
        raise PixelRagViewerBridgeError("Pixel RAG viewer bridge is not canonical JSON")
    return bridge


def check_viewer_pixel_rag_bridge(
    path: Path,
    artifact_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Recompile from governed originals and require exact generated bridge bytes."""

    bridge = read_viewer_pixel_rag_bridge(path)
    expected = compile_viewer_pixel_rag_bridge(artifact_path, manifest_path)
    if _canonical_bytes(bridge) != _canonical_bytes(expected):
        raise PixelRagViewerBridgeError(
            "Pixel RAG viewer bridge does not match deterministic recompilation "
            "from the supplied artifact and manifest"
        )
    return bridge


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moodboard.pixel_rag_viewer",
        description="Embed one validated Pixel RAG artifact into the offline viewer build.",
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--manifest", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", type=Path)
    action.add_argument("--write", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.check is not None:
        if arguments.input is None or arguments.manifest is None:
            raise SystemExit("BLOCKED: --check requires --input and --manifest")
    else:
        if arguments.input is None or arguments.manifest is None:
            raise SystemExit("BLOCKED: --input requires --manifest and --write")
    try:
        if arguments.check is not None:
            bridge = check_viewer_pixel_rag_bridge(
                arguments.check,
                arguments.input,
                arguments.manifest,
            )
        else:
            bridge = compile_viewer_pixel_rag_bridge(arguments.input, arguments.manifest)
            write_viewer_pixel_rag_bridge(bridge, arguments.write)
    except (OSError, PixelRagError, ValueError) as error:
        raise SystemExit(f"BLOCKED: {error}") from error
    print(
        json.dumps(
            {
                "artifact_id": bridge["input"]["artifact_id"] if bridge["input"] else None,
                "bridge": str((arguments.check or arguments.write).resolve()),
                "state": bridge["state"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
