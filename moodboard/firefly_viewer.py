"""Freeze the measured Adobe Firefly web iteration into a closed offline-viewer bridge.

This contract is deliberately independent of the Pixel RAG engine artifact.  It binds three
already-frozen evidence documents plus the exact image bytes and Khive transport records. It
never relabels a deterministic compositor pass as intrinsic generator
locality, and it keeps the restyle acceptance decision as ``not_computed``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import stat
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

BRIDGE_FORMAT = "moodboard.viewer-firefly-measured-loop-bridge.v2"
GENERATOR_REVISION = "moodboard.firefly-viewer-bridge.v2"
_BRIDGE_DOMAIN = b"moodboard-viewer-firefly-bridge-v2\0"
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_PREVIEW_MAX_SIDE = 480
_HEX = frozenset("0123456789abcdef")

_EXPECTED_INPUTS = {
    "replace_evidence": {
        "byte_size": 18528,
        "schema_version": "moodboard.firefly-iteration-evidence.v1",
        "sha256": "a55e262a0d2752f7946028248359842864ec26932d66f4e6b76eb2233bf3fce5",
    },
    "restyle_evidence": {
        "byte_size": 2799,
        "schema_version": "moodboard.firefly-restyle-evidence.v1",
        "sha256": "9d7f75fdc63de6147a97d326dc01b85602e45474fb40c662f8352891bf0129c7",
    },
    "khive_evidence": {
        "byte_size": 4761,
        "schema_version": "moodboard.firefly-khive-evidence.v1",
        "sha256": "86c31855c9c2107d2688a54173434ad17292eb24a86484a613031602d9453abb",
    },
}
_EXPECTED_TRANSPORT = {
    "binary_sha256": "5ea7a96415225b19c2bcbde85c4f68cde1b43e833563026d59f9f7a015db39ec",
    "ingest_command_sha256": "89d0c41f5130781857d9c0f4be1a84bc611370bd240cdc25ac104c64c0003da5",
    "ingest_results_sha256": "305f86bc4e50dae905488d8edd50aae19216ab310627c20c113b70731e879da7",
    "search_command_sha256": "1ea4cf377e19ca1a9a3729671e79f87ef2e8987bc8244c63d47eabae9ca6514c",
    "search_results_sha256": "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
    "restart_command_sha256": "69d047a6125cba85794623baaad19d52b719d1904456fd7f9ba81602d84c4b29",
    "restart_results_sha256": "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
}
_OUTPUTS = {
    "raw": {
        "sha256": "8e33e4e6485ab776d5794cfa8ebba2f20687dfc391de1cd97d587bb4e3632f27",
        "content_ref": "4c052e6cff3913a4949fe2bef13331c56ac32f34d14b31e27e4e295f7884c052",
        "record_id": "d49dd28a-fdb7-47f6-9e7a-2730c4ac3892",
    },
    "selected": {
        "sha256": "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d",
        "content_ref": "7ad97f3c2cf9e7f7b5236369d57f922f2897dd0bad55122d26bc4eb8f6e7cc47",
        "record_id": "5b8ce0e1-d2ff-4164-be8c-fa0eff456521",
    },
    "restyle": {
        "sha256": "930dd8ddfb4fafcf724027fbeee652f19fe56233994926dc0f7a44186510b45a",
        "content_ref": "00a768f43e854cafc0af2df67ebf70f5e439531951cc591f21432860ba1f07cb",
        "record_id": "532ed912-0811-4c3e-b6bf-aa6cc3979348",
    },
}
_STRUCTURAL_OUTPUT = {
    "revision": "firefly-gemini25-replace-v1",
    "sha256": "76abe16ec31fdfa4448094fc27e9b62debf933c566973264719722efc7f9acef",
}
_SOURCE = {
    "asset_id": "fruit_apple_garden",
    "byte_size": 645201,
    "content_ref": "d9c1a0e3e6a5a72a9da252a0ea9fb4616c9099dd20cdc65ea00ffc29d14f23a8",
    "height": 960,
    "mime": "image/jpeg",
    "sha256": "3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23",
    "width": 1280,
}
_MASK_EVIDENCE = {
    "bounds_half_open_source_pixels": {
        "x0_inclusive": 230,
        "x1_exclusive": 1152,
        "y0_inclusive": 48,
        "y1_exclusive": 912,
    },
    "inside_pixel_count": 796608,
    "inside_pixel_fraction": 0.64828125,
    "normalized": {"height": "0.90", "width": "0.72", "x": "0.18", "y": "0.05"},
    "outside_pixel_count": 432192,
    "rasterization": "floor(start * extent), ceil((start + span) * extent)",
}
_MASK_IDENTITY = {
    "bounds_half_open_source_pixels": dict(_MASK_EVIDENCE["bounds_half_open_source_pixels"]),
    "encoding": "u8_row_major_1_inside_0_outside",
    "height": 960,
    "inside_pixel_count": 796608,
    "outside_pixel_count": 432192,
    "sha256": "09f9072f646ef8d99af30736210a57f2de448e8ca90fbff07a07edd7bd5eef4b",
    "width": 1280,
}
_EXACT_OUTSIDE_MASK = {
    "changed_pixel_count": 0,
    "comparison": "decoded_rgb_u8_outside_mask",
    "mask": _MASK_IDENTITY,
    "max_abs_channel_error": 0,
    "result": "pass",
    "selected_output_sha256": _OUTPUTS["selected"]["sha256"],
    "semantics": "deterministic_compositor_invariant_not_generator_locality",
    "source_sha256": _SOURCE["sha256"],
}
_REPLACE_REFERENCE = {
    "asset_id": "fruit_lemon_santa_clara",
    "content_ref": "cf72f06b425eb52039d6926e057f7f5720f16435341625ce2fc9b92f5b52069d",
    "direct_generator_reference": False,
    "license_id": "CC0-1.0",
    "raw_cosine": 0.843299582601,
    "sha256": "d53ca28eb2d59727fc577118d2d23dd0a16af8f0b8670d54fec6993428d71429",
}
_RESTYLE_DIAGNOSTICS = {
    "aligned_pixel_rgb_cosine": 0.899155132364,
    "horizontal_luma_gradient_cosine": 0.003698292898,
    "vertical_luma_gradient_cosine": 0.003281997953,
}
_DESCRIPTOR = {
    "checkpoint_sha256": "a2b07345b0196bc20273bba8486097fd94dcab1c284d96bdc0306f03ae8f567e",
    "dimensions": 1024,
    "fingerprint": "094eb84e3779fd64babac765d2b540bcf851de3adec1d0e17f06493b7fa50c74",
    "inference": {"provider": "lattice-embed", "version": "0.9.0"},
}
_PROJECTION_REVISION = "moodboard.showcase-firefly-frozen-projection.v1"
_PREVIEW_SHA256 = {
    "raw": "631d06e11c8e71a710164faf25643e81817de6f34f6fcec1ba3bed25b172737f",
    "selected": "dd416b042b8cf8d4df5bd99a5d759b11391d0ff82211bfbec405619cdd76af91",
    "restyle": "f6036b07909b43a4d07dd00ad222f169c5fdb1ec798ce8da3e660f9a610c2032",
}
_NONCLAIMS = [
    "The locality pass is enforced by deterministic compositing, not intrinsic Firefly locality.",
    "The Firefly background-removal alpha is a model output, not a ground-truth segmentation mask.",
    (
        "The routed references guided prompt wording and were not attached as direct generator "
        "image references."
    ),
    (
        "The restyle has no computed acceptance decision for style, semantic preservation, or "
        "preference."
    ),
    "Qwen cosine is experimental geometry, not probability or a validated aesthetic/style score.",
    "The premium Gemini 3.1 model was not used in this measured capture.",
]

__all__ = [
    "BRIDGE_FORMAT",
    "GENERATOR_REVISION",
    "FireflyViewerBridgeError",
    "compile_viewer_firefly_bridge",
    "read_viewer_firefly_bridge",
    "validate_viewer_firefly_bridge",
    "write_viewer_firefly_bridge",
]


class FireflyViewerBridgeError(ValueError):
    """The measured Firefly loop is incomplete, drifted, or overclaimed."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _bridge_id(value: Mapping[str, Any]) -> str:
    core = {key: item for key, item in value.items() if key != "bridge_id"}
    return hashlib.sha256(_BRIDGE_DOMAIN + _canonical_bytes(core)).hexdigest()


def _closed(value: Mapping[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        unknown = sorted(set(value) - set(expected))
        missing = sorted(set(expected) - set(value))
        detail = f"unknown keys {unknown}" if unknown else f"missing keys {missing}"
        raise FireflyViewerBridgeError(f"{label} is not closed: {detail}")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FireflyViewerBridgeError(f"{label} must be an object")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise FireflyViewerBridgeError(f"{label} must be a lowercase 64-hex digest")
    return value


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise FireflyViewerBridgeError(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise FireflyViewerBridgeError(f"{label} must be a canonical UUID") from error
    if str(parsed) != value:
        raise FireflyViewerBridgeError(f"{label} must be a canonical UUID")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FireflyViewerBridgeError(f"{label} must be a finite number")
    measured = float(value)
    if not math.isfinite(measured):
        raise FireflyViewerBridgeError(f"{label} must be a finite number")
    return measured


def _read_regular(path: Path, label: str, ceiling: int) -> bytes:
    source = Path(path)
    try:
        metadata = source.lstat()
    except OSError as error:
        raise FireflyViewerBridgeError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FireflyViewerBridgeError(f"{label} must be a non-symlink regular file")
    if metadata.st_size > ceiling:
        raise FireflyViewerBridgeError(f"{label} exceeds its byte ceiling")
    try:
        raw = source.read_bytes()
    except OSError as error:
        raise FireflyViewerBridgeError(f"{label} could not be read: {error}") from error
    if len(raw) != metadata.st_size:
        raise FireflyViewerBridgeError(f"{label} changed while it was read")
    return raw


def _read_json(path: Path, label: str, expected: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = _read_regular(path, label, _MAX_JSON_BYTES)
    if len(raw) != expected["byte_size"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise FireflyViewerBridgeError(f"{label} does not match its frozen byte identity")
    try:
        parsed = json.loads(
            raw, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token))
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise FireflyViewerBridgeError(f"{label} is not strict JSON: {error}") from error
    document = _mapping(parsed, label)
    if document.get("schema_version") != expected["schema_version"]:
        raise FireflyViewerBridgeError(f"{label} schema_version drifted")
    return document


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise FireflyViewerBridgeError(message)


def _preview(path: Path, *, expected_sha256: str) -> dict[str, Any]:
    raw = _read_regular(path, "Firefly output", _MAX_IMAGE_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise FireflyViewerBridgeError("Firefly output SHA-256 drifted")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            picture = image.convert("RGB")
            picture.thumbnail((_PREVIEW_MAX_SIDE, _PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            picture.save(
                output,
                format="JPEG",
                quality=78,
                optimize=False,
                progressive=False,
                subsampling=2,
            )
    except (OSError, ValueError) as error:
        raise FireflyViewerBridgeError("Firefly output is not a valid bounded image") from error
    preview = output.getvalue()
    return {
        "data_base64": base64.b64encode(preview).decode("ascii"),
        "height": picture.height,
        "mime": "image/jpeg",
        "sha256": hashlib.sha256(preview).hexdigest(),
        "width": picture.width,
    }


def _exact_rgb_bytes(
    path: Path,
    *,
    label: str,
    expected_format: str,
    expected_sha256: str,
) -> bytes:
    raw = _read_regular(path, label, _MAX_IMAGE_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise FireflyViewerBridgeError(f"{label} SHA-256 drifted")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if (
                image.format != expected_format
                or image.mode != "RGB"
                or image.size != (_SOURCE["width"], _SOURCE["height"])
            ):
                raise FireflyViewerBridgeError(f"{label} decoded identity drifted")
            return image.tobytes()
    except FireflyViewerBridgeError:
        raise
    except (OSError, ValueError) as error:
        raise FireflyViewerBridgeError(f"{label} is not a valid bounded RGB image") from error


def _measure_exact_outside_mask(source_image: Path, selected_output: Path) -> dict[str, Any]:
    """Compare frozen decoded RGB values outside the frozen mask, failing on any change."""

    source_rgb = _exact_rgb_bytes(
        source_image,
        label="exact locality source",
        expected_format="JPEG",
        expected_sha256=_SOURCE["sha256"],
    )
    selected_rgb = _exact_rgb_bytes(
        selected_output,
        label="exact locality selected output",
        expected_format="PNG",
        expected_sha256=_OUTPUTS["selected"]["sha256"],
    )
    width = _SOURCE["width"]
    height = _SOURCE["height"]
    bounds = _MASK_EVIDENCE["bounds_half_open_source_pixels"]
    x0 = bounds["x0_inclusive"]
    x1 = bounds["x1_exclusive"]
    y0 = bounds["y0_inclusive"]
    y1 = bounds["y1_exclusive"]

    mask_sha256 = hashlib.sha256()
    outside_row = b"\x00" * width
    inside_row = b"\x00" * x0 + b"\x01" * (x1 - x0) + b"\x00" * (width - x1)
    for y in range(height):
        mask_sha256.update(inside_row if y0 <= y < y1 else outside_row)
    if mask_sha256.hexdigest() != _MASK_IDENTITY["sha256"]:
        raise FireflyViewerBridgeError("exact outside-mask raster identity drifted")

    changed_pixel_count = 0
    max_abs_channel_error = 0
    for y in range(height):
        spans = ((0, x0), (x1, width)) if y0 <= y < y1 else ((0, width),)
        for start_x, end_x in spans:
            first_channel = (y * width + start_x) * 3
            final_channel = (y * width + end_x) * 3
            for channel in range(first_channel, final_channel, 3):
                red = abs(source_rgb[channel] - selected_rgb[channel])
                green = abs(source_rgb[channel + 1] - selected_rgb[channel + 1])
                blue = abs(source_rgb[channel + 2] - selected_rgb[channel + 2])
                pixel_error = max(red, green, blue)
                if pixel_error:
                    changed_pixel_count += 1
                    max_abs_channel_error = max(max_abs_channel_error, pixel_error)
    if changed_pixel_count != 0 or max_abs_channel_error != 0:
        raise FireflyViewerBridgeError(
            "selected compositor exact outside-mask equality failed: "
            f"changed_pixel_count={changed_pixel_count}, "
            f"max_abs_channel_error={max_abs_channel_error}"
        )
    return json.loads(json.dumps(_EXACT_OUTSIDE_MASK))


def _validate_preview(value: object, label: str) -> None:
    preview = _mapping(value, label)
    _closed(preview, {"data_base64", "height", "mime", "sha256", "width"}, label)
    if preview["mime"] != "image/jpeg":
        raise FireflyViewerBridgeError(f"{label} MIME drifted")
    width, height = preview["width"], preview["height"]
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
        or width > _PREVIEW_MAX_SIDE
        or height > _PREVIEW_MAX_SIDE
    ):
        raise FireflyViewerBridgeError(f"{label} dimensions are invalid")
    payload = preview["data_base64"]
    if not isinstance(payload, str):
        raise FireflyViewerBridgeError(f"{label} base64 is invalid")
    try:
        raw = base64.b64decode(payload, validate=True)
    except ValueError as error:
        raise FireflyViewerBridgeError(f"{label} base64 is invalid") from error
    if base64.b64encode(raw).decode("ascii") != payload:
        raise FireflyViewerBridgeError(f"{label} base64 is not canonical")
    if hashlib.sha256(raw).hexdigest() != _digest(preview["sha256"], f"{label}.sha256"):
        raise FireflyViewerBridgeError(f"{label} preview SHA-256 drifted")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            actual = (image.format, image.size)
    except OSError as error:
        raise FireflyViewerBridgeError(f"{label} preview does not decode") from error
    if actual != ("JPEG", (width, height)):
        raise FireflyViewerBridgeError(f"{label} preview metadata drifted")


def _transport_document(path: Path, label: str, expected_sha256: str) -> Mapping[str, Any]:
    raw = _read_regular(path, label, _MAX_JSON_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise FireflyViewerBridgeError(f"{label} SHA-256 drifted")
    try:
        parsed = json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise FireflyViewerBridgeError(f"{label} is not strict JSON: {error}") from error
    return _mapping(parsed, label)


def _flag_value(command: Sequence[object], flag: str) -> object | None:
    try:
        index = command.index(flag)
    except ValueError:
        return None
    return command[index + 1] if index + 1 < len(command) else None


def _validate_transport(
    *,
    ingest_command: Path,
    ingest_results: Path,
    search_command: Path,
    search_results: Path,
    restart_command: Path,
    restart_results: Path,
) -> dict[str, Any]:
    result_paths = {
        "ingest": (ingest_results, _EXPECTED_TRANSPORT["ingest_results_sha256"]),
        "search": (search_results, _EXPECTED_TRANSPORT["search_results_sha256"]),
        "restart": (restart_results, _EXPECTED_TRANSPORT["restart_results_sha256"]),
    }
    for label, (path, expected) in result_paths.items():
        raw = _read_regular(path, f"{label} results", _MAX_JSON_BYTES)
        _expect(hashlib.sha256(raw).hexdigest() == expected, f"{label} results SHA-256 drifted")
    command_paths = {
        "ingest": (ingest_command, _EXPECTED_TRANSPORT["ingest_command_sha256"]),
        "search": (search_command, _EXPECTED_TRANSPORT["search_command_sha256"]),
        "restart": (restart_command, _EXPECTED_TRANSPORT["restart_command_sha256"]),
    }
    for label, (path, expected) in command_paths.items():
        document = _transport_document(path, f"{label} command", expected)
        command = document.get("command")
        manifest = _mapping(document.get("manifest"), f"{label} manifest")
        _expect(document.get("returncode") == 0, f"{label} command did not succeed")
        _expect(
            _mapping(document.get("binary"), f"{label} binary").get("sha256")
            == _EXPECTED_TRANSPORT["binary_sha256"],
            f"{label} binary identity drifted",
        )
        _expect(
            isinstance(command, list)
            and all(flag in command for flag in ("--serial", "--strict"))
            and _flag_value(command, "--presentation") == "verbose"
            and _flag_value(command, "--output-format") == "json",
            f"{label} command lost the serial strict verbose JSON contract",
        )
        _expect(
            manifest.get("checksum") == result_paths[label][1]
            and _mapping(manifest.get("summary"), f"{label} summary").get("failed") == 0
            and _mapping(manifest.get("summary"), f"{label} summary").get("aborted") == 0,
            f"{label} manifest does not bind a complete successful result",
        )
    return dict(_EXPECTED_TRANSPORT)


def compile_viewer_firefly_bridge(
    *,
    replace_evidence: Path,
    restyle_evidence: Path,
    khive_evidence: Path,
    raw_output: Path,
    selected_output: Path,
    restyle_output: Path,
    ingest_command: Path,
    ingest_results: Path,
    search_command: Path,
    search_results: Path,
    restart_command: Path,
    restart_results: Path,
    projection_revision: str,
    projection_sha256: str,
    source_image: Path | None = None,
) -> dict[str, Any]:
    """Project only the exact frozen Firefly web and Khive evidence used by the demo."""

    replace = _read_json(replace_evidence, "replace evidence", _EXPECTED_INPUTS["replace_evidence"])
    restyle = _read_json(restyle_evidence, "restyle evidence", _EXPECTED_INPUTS["restyle_evidence"])
    khive = _read_json(khive_evidence, "Khive evidence", _EXPECTED_INPUTS["khive_evidence"])
    capture = _mapping(replace.get("capture"), "replace capture")
    generator = _mapping(replace.get("generator"), "replace generator")
    source = _mapping(
        _mapping(replace.get("inputs"), "replace inputs").get("source"),
        "replace source",
    )
    _expect(
        Path(str(source.get("path"))).stem == _SOURCE["asset_id"]
        and source.get("byte_size") == _SOURCE["byte_size"]
        and source.get("blake3") == _SOURCE["content_ref"]
        and source.get("height") == _SOURCE["height"]
        and source.get("format") == "JPEG"
        and source.get("mode") == "RGB"
        and source.get("sha256") == _SOURCE["sha256"]
        and source.get("width") == _SOURCE["width"],
        "replace source identity drifted",
    )
    declared_source_path = source.get("path")
    _expect(
        isinstance(declared_source_path, str)
        and not Path(declared_source_path).is_absolute()
        and ".." not in Path(declared_source_path).parts,
        "replace source path is not a safe repository-relative path",
    )
    exact_source_image = (
        Path(source_image)
        if source_image is not None
        else Path(__file__).parents[1] / str(declared_source_path)
    )
    restyle_source = _mapping(restyle.get("source"), "restyle source")
    _expect(
        Path(str(restyle_source.get("path"))).stem == _SOURCE["asset_id"]
        and restyle_source.get("sha256") == _SOURCE["sha256"]
        and restyle_source.get("dimensions") == [_SOURCE["width"], _SOURCE["height"]],
        "restyle source identity drifted",
    )
    _expect(
        capture.get("native_firefly_api") is False, "capture must not claim a native Firefly API"
    )
    _expect(
        capture.get("browser_surface")
        == "Codex in-app browser with the user's authenticated Adobe session",
        "capture does not bind the authenticated Adobe web session",
    )
    _expect(
        generator.get("display_name") == "Gemini 2.5 (Nano Banana)"
        and generator.get("cost_display") == "Uses 0 credits"
        and generator.get("execution") == "Adobe Firefly web Edit > Prompt",
        "Firefly generator surface, model, or displayed cost drifted",
    )
    _expect(
        generator.get("provider_boundary") == "Google partner model served through Adobe Firefly",
        "Firefly provider boundary drifted",
    )
    iterations = replace.get("iterations")
    _expect(isinstance(iterations, list) and len(iterations) == 5, "replace iteration set drifted")
    by_revision = {item["revision"]: item for item in iterations}
    first = by_revision["firefly-gemini25-replace-v1"]
    raw = by_revision["firefly-gemini25-replace-v2"]
    selected = by_revision["pixel-rag-firefly-cutout-compositor-v1"]
    raw_decision = raw["metrics"]["acceptance_decision"]
    selected_decision = selected["metrics"]["acceptance_decision"]
    raw_mask = _mapping(raw["metrics"].get("mask"), "raw replacement mask")
    selected_mask = _mapping(selected["metrics"].get("mask"), "selected replacement mask")
    _expect(
        raw_mask == _MASK_EVIDENCE and selected_mask == _MASK_EVIDENCE,
        "replacement mask evidence drifted",
    )
    _expect(
        first.get("revision") == _STRUCTURAL_OUTPUT["revision"]
        and first.get("output", {}).get("sha256") == _STRUCTURAL_OUTPUT["sha256"]
        and first.get("acceptance_decision")
        == {
            "reason": (
                "output is square while the source is 4:3; locality gate is not comparable"
            ),
            "state": "fail_structural_aspect_ratio",
        },
        "structural replacement evidence drifted",
    )
    _expect(
        raw["output"]["sha256"] == _OUTPUTS["raw"]["sha256"]
        and raw_decision
        == {
            "gate": "outside_mask_ssim >= 0.95",
            "reason": (
                "fixed preregistered mask and threshold; deterministic alignment declared before "
                "measurement"
            ),
            "state": "fail",
            "threshold": 0.95,
            "value": 0.174819482254,
        },
        "raw replacement evidence drifted",
    )
    _expect(
        selected["output"]["sha256"] == _OUTPUTS["selected"]["sha256"]
        and selected_decision["state"] == "pass"
        and selected_decision["threshold"] == 0.95
        and selected_decision["value"] == 1.0
        and selected["derivation"]["generator_output_sha256"] == _OUTPUTS["raw"]["sha256"]
        and selected["derivation"]["operation"]
        == (
            "composite the Firefly foreground alpha only inside the fixed allowed mask; copy "
            "source pixels exactly outside"
        ),
        "selected deterministic preservation evidence drifted",
    )
    alpha_source = _mapping(selected["derivation"].get("alpha_source"), "Firefly alpha source")
    _expect(
        alpha_source.get("operation") == "Adobe Firefly web Remove background"
        and _mapping(alpha_source.get("output"), "Firefly alpha output").get("sha256")
        == "eb31d58eaaeaa40827cdfc89ec2baf5a11b2ad1e8c98cd9a2ed203d2cfda7c43"
        and selected["derivation"].get(
            "outside_source_pixels_preserved_byte_for_byte_before PNG encoding"
        )
        is True,
        "Firefly alpha or source-preservation derivation drifted",
    )
    _expect(
        replace.get("selection", {}).get("selected_revision")
        == "pixel-rag-firefly-cutout-compositor-v1",
        "selected Firefly iteration drifted",
    )
    exact_outside_mask = _measure_exact_outside_mask(exact_source_image, selected_output)
    _expect(
        restyle.get("generator", {}).get("display_name") == "Gemini 2.5 (Nano Banana)"
        and restyle.get("generator", {}).get("cost_display") == "Uses 0 credits"
        and restyle.get("descriptive_diagnostics", {}).get("acceptance_decision") == "not_computed"
        and restyle.get("output", {}).get("sha256") == _OUTPUTS["restyle"]["sha256"],
        "restyle evidence or not_computed decision drifted",
    )
    _expect(
        restyle.get("generator", {}).get("surface") == "Adobe Firefly web Edit > Prompt"
        and restyle.get("pixel_rag_binding", {})
        .get("retrieved_reference", {})
        .get("direct_generator_reference")
        is False,
        "restyle surface or direct-reference boundary drifted",
    )
    projected_restyle_diagnostics = {
        key: restyle.get("descriptive_diagnostics", {}).get(key) for key in _RESTYLE_DIAGNOSTICS
    }
    _expect(
        projected_restyle_diagnostics == _RESTYLE_DIAGNOSTICS,
        "restyle descriptive diagnostics drifted",
    )
    replace_reference = {
        key: replace.get("pixel_rag_binding", {}).get("retrieved_evidence", {}).get(key)
        for key in _REPLACE_REFERENCE
    }
    _expect(
        replace_reference == _REPLACE_REFERENCE,
        "replacement retrieved reference identity drifted",
    )
    descriptor = _mapping(khive.get("descriptor"), "Khive descriptor")
    for key, expected in _DESCRIPTOR.items():
        _expect(descriptor.get(key) == expected, f"Khive/Lattice descriptor {key} drifted")
    assets = khive.get("assets")
    _expect(isinstance(assets, list) and len(assets) == 3, "Khive asset set drifted")
    projected_assets = []
    for role, source in zip(("raw", "selected", "restyle"), assets, strict=True):
        expected = _OUTPUTS[role]
        asset = _mapping(source.get("asset"), f"Khive {role} asset")
        embedding = _mapping(source.get("embedding"), f"Khive {role} embedding")
        _expect(
            source.get("source_sha256") == expected["sha256"]
            and asset.get("content_ref") == expected["content_ref"]
            and asset.get("id") == expected["record_id"]
            and asset.get("created") is True
            and asset.get("indexed") is True,
            f"Khive {role} asset identity drifted",
        )
        _expect(
            embedding.get("dimensions") == 1024
            and math.isclose(float(embedding.get("l2_norm")), 1.0, abs_tol=1.0e-6),
            f"Khive {role} embedding contract drifted",
        )
        projected_assets.append(
            {
                "content_ref": expected["content_ref"],
                "embedding_dimensions": 1024,
                "output_sha256": expected["sha256"],
                "record_id": expected["record_id"],
                "role": role,
            }
        )
    restart = _mapping(khive.get("restart"), "Khive restart")
    _expect(
        restart.get("canonical_search_byte_exact") is True
        and restart.get("descriptor_fingerprint") == _DESCRIPTOR["fingerprint"],
        "Khive restart evidence drifted",
    )
    search = _mapping(khive.get("raw_output_search"), "Khive search")
    _expect(
        search.get("query_asset_id") == _OUTPUTS["raw"]["record_id"]
        and [hit["content_ref"] for hit in search.get("hits", [])]
        == [_OUTPUTS["selected"]["content_ref"], _OUTPUTS["restyle"]["content_ref"]],
        "Khive search result identity drifted",
    )
    transport = _validate_transport(
        ingest_command=ingest_command,
        ingest_results=ingest_results,
        search_command=search_command,
        search_results=search_results,
        restart_command=restart_command,
        restart_results=restart_results,
    )
    bridge: dict[str, Any] = {
        "bridge_id": "0" * 64,
        "evidence": {
            "capture": {
                "authenticated_session": True,
                "cost_display": "Uses 0 credits",
                "model": "Gemini 2.5 (Nano Banana)",
                "native_firefly_api": False,
                "provider_boundary": "Google partner model served through Adobe Firefly",
                "surface": "Adobe Firefly web Edit > Prompt",
            },
            "khive": {
                "assets": projected_assets,
                "descriptor": {key: descriptor[key] for key in _DESCRIPTOR},
                "namespace": "showcase-firefly-v1",
                "restart": {
                    "canonical_search_byte_exact": True,
                    "first_search_sha256": transport["search_results_sha256"],
                    "restart_search_sha256": transport["restart_results_sha256"],
                },
                "transport": transport,
            },
            "nonclaims": list(_NONCLAIMS),
            "projection": {
                "revision": projection_revision,
                "sha256": _digest(projection_sha256, "projection_sha256"),
            },
            "source": dict(_SOURCE),
            "replacement": {
                "compositor_exact_outside_mask": exact_outside_mask,
                "intent": replace["pixel_rag_binding"]["intent"],
                "retrieved_reference": dict(_REPLACE_REFERENCE),
                "threshold": 0.95,
                "timeline": [
                    {
                        "content_ref": None,
                        "decision": "fail_structural_aspect_ratio",
                        "id": "iteration_01_square",
                        "label": "Iteration 1 · square structural rejection",
                        "outside_mask_ssim": None,
                        "output_sha256": first["output"]["sha256"],
                        "pass_semantics": None,
                        "preview": None,
                        "record_id": None,
                        "revision": first["revision"],
                    },
                    {
                        "content_ref": _OUTPUTS["raw"]["content_ref"],
                        "decision": "fail",
                        "id": "iteration_02_raw",
                        "label": "Raw generator output",
                        "outside_mask_ssim": 0.174819482254,
                        "output_sha256": _OUTPUTS["raw"]["sha256"],
                        "pass_semantics": None,
                        "preview": _preview(raw_output, expected_sha256=_OUTPUTS["raw"]["sha256"]),
                        "record_id": _OUTPUTS["raw"]["record_id"],
                        "revision": raw["revision"],
                    },
                    {
                        "content_ref": _OUTPUTS["selected"]["content_ref"],
                        "decision": "pass",
                        "id": "iteration_02_cutout_composite",
                        "label": "Selected cutout + compositor",
                        "outside_mask_ssim": 1.0,
                        "output_sha256": _OUTPUTS["selected"]["sha256"],
                        "pass_semantics": (
                            "deterministic_preservation_constraint_not_intrinsic_generator_locality"
                        ),
                        "preview": _preview(
                            selected_output,
                            expected_sha256=_OUTPUTS["selected"]["sha256"],
                        ),
                        "record_id": _OUTPUTS["selected"]["record_id"],
                        "revision": selected["revision"],
                    },
                ],
                "verifier_revision": replace["verification"]["method_revision"],
            },
            "restyle": {
                "acceptance_decision": "not_computed",
                "content_ref": _OUTPUTS["restyle"]["content_ref"],
                "direct_generator_reference": False,
                "diagnostics": dict(_RESTYLE_DIAGNOSTICS),
                "output_sha256": _OUTPUTS["restyle"]["sha256"],
                "preview": _preview(
                    restyle_output,
                    expected_sha256=_OUTPUTS["restyle"]["sha256"],
                ),
                "record_id": _OUTPUTS["restyle"]["record_id"],
            },
        },
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "inputs": json.loads(json.dumps(_EXPECTED_INPUTS)),
        "state": "projected",
    }
    bridge["bridge_id"] = _bridge_id(bridge)
    validate_viewer_firefly_bridge(bridge)
    return bridge


def _validate_timeline(value: object, khive_assets: Mapping[str, Mapping[str, Any]]) -> None:
    if not isinstance(value, list) or len(value) != 3:
        raise FireflyViewerBridgeError("replacement timeline must contain exactly three steps")
    expected_ids = ["iteration_01_square", "iteration_02_raw", "iteration_02_cutout_composite"]
    if [item.get("id") for item in value if isinstance(item, Mapping)] != expected_ids:
        raise FireflyViewerBridgeError("replacement timeline order drifted")
    for index, item_value in enumerate(value):
        item = _mapping(item_value, f"timeline[{index}]")
        _closed(
            item,
            {
                "content_ref",
                "decision",
                "id",
                "label",
                "outside_mask_ssim",
                "output_sha256",
                "pass_semantics",
                "preview",
                "record_id",
                "revision",
            },
            f"timeline[{index}]",
        )
        _digest(item["output_sha256"], f"timeline[{index}].output_sha256")
    first, raw, selected = value
    if (
        first["decision"] != "fail_structural_aspect_ratio"
        or first["revision"] != _STRUCTURAL_OUTPUT["revision"]
        or first["label"] != "Iteration 1 · square structural rejection"
        or first["output_sha256"] != _STRUCTURAL_OUTPUT["sha256"]
        or first["outside_mask_ssim"] is not None
        or first["content_ref"] is not None
        or first["record_id"] is not None
        or first["preview"] is not None
        or first["pass_semantics"] is not None
    ):
        raise FireflyViewerBridgeError("structural replacement iteration drifted")
    raw_value = _finite(raw["outside_mask_ssim"], "raw replacement outside_mask_ssim")
    if (
        raw["decision"] != "fail"
        or raw["revision"] != "firefly-gemini25-replace-v2"
        or raw["label"] != "Raw generator output"
        or raw_value != 0.174819482254
        or raw["pass_semantics"] is not None
    ):
        raise FireflyViewerBridgeError("raw replacement must retain its measured locality failure")
    if raw["output_sha256"] != _OUTPUTS["raw"]["sha256"]:
        raise FireflyViewerBridgeError("raw replacement output identity drifted")
    selected_value = _finite(selected["outside_mask_ssim"], "selected outside_mask_ssim")
    if (
        selected["decision"] != "pass"
        or selected["revision"] != "pixel-rag-firefly-cutout-compositor-v1"
        or selected["label"] != "Selected cutout + compositor"
        or selected_value != 1.0
        or selected["pass_semantics"]
        != "deterministic_preservation_constraint_not_intrinsic_generator_locality"
    ):
        raise FireflyViewerBridgeError(
            "selected output must retain deterministic preservation semantics"
        )
    for role, item in (("raw", raw), ("selected", selected)):
        expected = _OUTPUTS[role]
        asset = khive_assets[role]
        if (
            item["output_sha256"] != expected["sha256"]
            or item["content_ref"] != expected["content_ref"]
            or item["record_id"] != expected["record_id"]
            or item["content_ref"] != asset["content_ref"]
            or item["record_id"] != asset["record_id"]
            or item["output_sha256"] != asset["output_sha256"]
        ):
            raise FireflyViewerBridgeError(f"{role} timeline output does not match its Khive asset")
        _validate_preview(item["preview"], f"timeline {role} preview")
        if item["preview"]["sha256"] != _PREVIEW_SHA256[role]:
            raise FireflyViewerBridgeError(f"{role} preview derivative identity drifted")


def validate_viewer_firefly_bridge(value: Mapping[str, Any]) -> None:
    bridge = _mapping(value, "Firefly viewer bridge")
    _closed(
        bridge,
        {"bridge_id", "evidence", "format_version", "generator_revision", "inputs", "state"},
        "Firefly viewer bridge",
    )
    if (
        bridge["format_version"] != BRIDGE_FORMAT
        or bridge["generator_revision"] != GENERATOR_REVISION
        or bridge["state"] != "projected"
    ):
        raise FireflyViewerBridgeError("Firefly bridge contract identity drifted")
    inputs = _mapping(bridge["inputs"], "Firefly bridge inputs")
    _closed(inputs, set(_EXPECTED_INPUTS), "Firefly bridge inputs")
    if inputs != _EXPECTED_INPUTS:
        raise FireflyViewerBridgeError("Firefly bridge frozen input identities drifted")
    evidence = _mapping(bridge["evidence"], "Firefly bridge evidence")
    _closed(
        evidence,
        {"capture", "khive", "nonclaims", "projection", "replacement", "restyle", "source"},
        "Firefly bridge evidence",
    )
    capture = _mapping(evidence["capture"], "capture")
    _closed(
        capture,
        {
            "authenticated_session",
            "cost_display",
            "model",
            "native_firefly_api",
            "provider_boundary",
            "surface",
        },
        "capture",
    )
    if capture != {
        "authenticated_session": True,
        "cost_display": "Uses 0 credits",
        "model": "Gemini 2.5 (Nano Banana)",
        "native_firefly_api": False,
        "provider_boundary": "Google partner model served through Adobe Firefly",
        "surface": "Adobe Firefly web Edit > Prompt",
    }:
        if capture.get("native_firefly_api") is not False:
            raise FireflyViewerBridgeError("capture must not claim a native Firefly API")
        raise FireflyViewerBridgeError("Firefly web capture identity drifted")
    projection = _mapping(evidence["projection"], "projection")
    _closed(projection, {"revision", "sha256"}, "projection")
    if not isinstance(projection["revision"], str) or not projection["revision"]:
        raise FireflyViewerBridgeError("projection revision is invalid")
    if projection["revision"] != _PROJECTION_REVISION:
        raise FireflyViewerBridgeError("projection revision drifted")
    expected_projection_sha256 = hashlib.sha256(
        (Path(__file__).parents[1] / "eval" / "showcase_firefly_projection.py").read_bytes()
    ).hexdigest()
    if _digest(projection["sha256"], "projection.sha256") != expected_projection_sha256:
        raise FireflyViewerBridgeError("projection SHA-256 drifted")
    source = _mapping(evidence["source"], "source identity")
    _closed(source, set(_SOURCE), "source identity")
    if source != _SOURCE or any(
        type(source[key]) is not type(expected) for key, expected in _SOURCE.items()
    ):
        raise FireflyViewerBridgeError("source identity drifted")
    khive = _mapping(evidence["khive"], "Khive evidence")
    _closed(khive, {"assets", "descriptor", "namespace", "restart", "transport"}, "Khive evidence")
    if khive["namespace"] != "showcase-firefly-v1":
        raise FireflyViewerBridgeError("Khive namespace drifted")
    descriptor = _mapping(khive["descriptor"], "Khive descriptor")
    _closed(descriptor, set(_DESCRIPTOR), "Khive descriptor")
    if descriptor != _DESCRIPTOR:
        raise FireflyViewerBridgeError("Khive/Lattice 0.9 descriptor drifted")
    assets = khive["assets"]
    if not isinstance(assets, list) or len(assets) != 3:
        raise FireflyViewerBridgeError("Khive asset set drifted")
    asset_by_role: dict[str, Mapping[str, Any]] = {}
    for index, asset_value in enumerate(assets):
        asset = _mapping(asset_value, f"Khive asset {index}")
        _closed(
            asset,
            {"content_ref", "embedding_dimensions", "output_sha256", "record_id", "role"},
            f"Khive asset {index}",
        )
        role = asset["role"]
        if role not in _OUTPUTS or role in asset_by_role:
            raise FireflyViewerBridgeError("Khive asset roles are invalid")
        expected = _OUTPUTS[role]
        if (
            asset["embedding_dimensions"] != 1024
            or asset["content_ref"] != expected["content_ref"]
            or asset["output_sha256"] != expected["sha256"]
            or asset["record_id"] != expected["record_id"]
        ):
            raise FireflyViewerBridgeError(f"Khive {role} asset identity drifted")
        _uuid(asset["record_id"], f"Khive {role} record_id")
        asset_by_role[role] = asset
    transport = _mapping(khive["transport"], "Khive transport")
    _closed(transport, set(_EXPECTED_TRANSPORT), "Khive transport")
    if transport != _EXPECTED_TRANSPORT:
        raise FireflyViewerBridgeError("Khive transport identities drifted")
    restart = _mapping(khive["restart"], "Khive restart")
    _closed(
        restart,
        {"canonical_search_byte_exact", "first_search_sha256", "restart_search_sha256"},
        "Khive restart",
    )
    if (
        restart["canonical_search_byte_exact"] is not True
        or restart["first_search_sha256"] != _EXPECTED_TRANSPORT["search_results_sha256"]
        or restart["restart_search_sha256"] != _EXPECTED_TRANSPORT["restart_results_sha256"]
        or restart["first_search_sha256"] != restart["restart_search_sha256"]
    ):
        raise FireflyViewerBridgeError("Khive restart byte-exact evidence drifted")
    replacement = _mapping(evidence["replacement"], "replacement")
    _closed(
        replacement,
        {
            "compositor_exact_outside_mask",
            "intent",
            "retrieved_reference",
            "threshold",
            "timeline",
            "verifier_revision",
        },
        "replacement",
    )
    exact_outside_mask = _mapping(
        replacement["compositor_exact_outside_mask"],
        "exact outside-mask evidence",
    )
    _closed(exact_outside_mask, set(_EXACT_OUTSIDE_MASK), "exact outside-mask evidence")
    exact_mask = _mapping(exact_outside_mask["mask"], "exact outside-mask mask identity")
    _closed(exact_mask, set(_MASK_IDENTITY), "exact outside-mask mask identity")
    if (
        exact_outside_mask != _EXACT_OUTSIDE_MASK
        or exact_mask != _MASK_IDENTITY
        or type(exact_outside_mask["changed_pixel_count"]) is not int
        or type(exact_outside_mask["max_abs_channel_error"]) is not int
        or any(
            type(exact_mask[key]) is not int
            for key in ("height", "inside_pixel_count", "outside_pixel_count", "width")
        )
    ):
        raise FireflyViewerBridgeError("exact outside-mask evidence drifted")
    if replacement["threshold"] != 0.95:
        raise FireflyViewerBridgeError("replacement preregistered threshold drifted")
    reference = _mapping(replacement["retrieved_reference"], "retrieved reference")
    _closed(
        reference,
        {
            "asset_id",
            "content_ref",
            "direct_generator_reference",
            "license_id",
            "raw_cosine",
            "sha256",
        },
        "retrieved reference",
    )
    if reference != _REPLACE_REFERENCE:
        if reference.get("direct_generator_reference") is not False:
            raise FireflyViewerBridgeError(
                "retrieved reference must not be claimed as a direct generator input"
            )
        raise FireflyViewerBridgeError("retrieved reference identity drifted")
    if replacement["intent"] != "replace the apple tree with a lemon tree":
        raise FireflyViewerBridgeError("replacement intent drifted")
    if replacement["verifier_revision"] != "moodboard.image-edit-pixel-verification.v1":
        raise FireflyViewerBridgeError("replacement verifier revision drifted")
    _validate_timeline(replacement["timeline"], asset_by_role)
    restyle = _mapping(evidence["restyle"], "restyle")
    _closed(
        restyle,
        {
            "acceptance_decision",
            "content_ref",
            "diagnostics",
            "direct_generator_reference",
            "output_sha256",
            "preview",
            "record_id",
        },
        "restyle",
    )
    if restyle["acceptance_decision"] != "not_computed":
        raise FireflyViewerBridgeError("restyle acceptance must remain not_computed")
    if restyle["direct_generator_reference"] is not False:
        raise FireflyViewerBridgeError("restyle reference must not be claimed as a direct input")
    expected_restyle = _OUTPUTS["restyle"]
    if (
        restyle["content_ref"] != expected_restyle["content_ref"]
        or restyle["output_sha256"] != expected_restyle["sha256"]
        or restyle["record_id"] != expected_restyle["record_id"]
        or any(
            restyle[field] != asset_by_role["restyle"][field]
            for field in ("content_ref", "output_sha256", "record_id")
        )
    ):
        raise FireflyViewerBridgeError("restyle output does not match its Khive asset")
    diagnostics = _mapping(restyle["diagnostics"], "restyle diagnostics")
    _closed(
        diagnostics,
        {
            "aligned_pixel_rgb_cosine",
            "horizontal_luma_gradient_cosine",
            "vertical_luma_gradient_cosine",
        },
        "restyle diagnostics",
    )
    for key, number in diagnostics.items():
        _finite(number, f"restyle diagnostics {key}")
    if diagnostics != _RESTYLE_DIAGNOSTICS:
        raise FireflyViewerBridgeError("restyle diagnostics drifted")
    _validate_preview(restyle["preview"], "restyle preview")
    if restyle["preview"]["sha256"] != _PREVIEW_SHA256["restyle"]:
        raise FireflyViewerBridgeError("restyle preview derivative identity drifted")
    nonclaims = evidence["nonclaims"]
    if nonclaims != _NONCLAIMS:
        raise FireflyViewerBridgeError("Firefly nonclaims are incomplete")
    if _digest(bridge["bridge_id"], "bridge_id") != _bridge_id(bridge):
        raise FireflyViewerBridgeError("Firefly bridge_id does not match its canonical contents")


def write_viewer_firefly_bridge(value: Mapping[str, Any], destination: Path) -> None:
    validate_viewer_firefly_bridge(value)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def read_viewer_firefly_bridge(path: Path) -> dict[str, Any]:
    raw = _read_regular(path, "Firefly viewer bridge", 8 * 1024 * 1024)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FireflyViewerBridgeError(f"Firefly viewer bridge is invalid JSON: {error}") from error
    value = dict(_mapping(parsed, "Firefly viewer bridge"))
    if raw != _canonical_bytes(value):
        raise FireflyViewerBridgeError("Firefly viewer bridge is not canonical JSON")
    validate_viewer_firefly_bridge(value)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.check is None:
        raise SystemExit("--check is required; use the tracked evidence projector to write")
    try:
        value = read_viewer_firefly_bridge(args.check)
    except (FireflyViewerBridgeError, OSError) as error:
        raise SystemExit(f"firefly-viewer: {error}") from error
    print(json.dumps({"bridge_id": value["bridge_id"], "state": value["state"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
