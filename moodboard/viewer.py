"""Verify the built viewer and atomically inline one validated report.

The browser owns presentation. This module owns only the file boundary: exact packaged bytes,
the supported report contracts, an inert base64 payload, and an atomic destination replacement.
It deliberately contains no score, rank, interval, exemplar, or display computation.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import shlex
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Final

import jsonschema
from PIL import Image, UnidentifiedImageError

from .report import (
    THUMBNAIL_MAX_COMPRESSED_BYTES,
    THUMBNAIL_MAX_COUNT,
    THUMBNAIL_MAX_DECODED_BYTES,
    THUMBNAIL_MAX_PIXELS,
    THUMBNAIL_MAX_SIDE,
    THUMBNAIL_TOTAL_DECODED_BYTES,
    from_json_dict,
    read_report_bytes,
)

__all__ = [
    "ViewerPackage",
    "ViewerPackagingError",
    "decode_report_document",
    "inline_report",
    "validate_viewer_package",
]

_SUPPORTED_REPORTS: Final = frozenset({"1.0", "1.1"})
_SAFE_INTEGER: Final = 9_007_199_254_740_991
_SAFE_MIMES: Final = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
}
_PAYLOAD_TOKEN: Final = "__MOODBOARD_REPORT_BASE64__"
_MAX_THUMBNAIL_COMPRESSED_BYTES: Final = THUMBNAIL_MAX_COMPRESSED_BYTES
_MAX_THUMBNAIL_SIDE: Final = THUMBNAIL_MAX_SIDE
_MAX_THUMBNAIL_PIXELS: Final = THUMBNAIL_MAX_PIXELS
_MAX_THUMBNAIL_DECODED_BYTES: Final = THUMBNAIL_MAX_DECODED_BYTES
_MAX_TOTAL_THUMBNAILS: Final = THUMBNAIL_MAX_COUNT
_MAX_TOTAL_THUMBNAIL_DECODED_BYTES: Final = THUMBNAIL_TOTAL_DECODED_BYTES
_STANDALONE_CSP: Final = (
    "default-src 'none'; script-src data:; style-src data:; img-src data:; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"
)
_CSP_META: Final = (
    f'<meta http-equiv="Content-Security-Policy" content="{_STANDALONE_CSP}" />'.encode()
)
_CSP_META_PATTERN: Final = re.compile(
    rb'<meta(?=[^>]*\bhttp-equiv=["\']Content-Security-Policy["\'])[^>]*>',
    flags=re.IGNORECASE,
)
_SCRIPT_PATTERN: Final = re.compile(
    rb'<script type="module" src="data:text/javascript;base64,([A-Za-z0-9+/]+={0,2})"></script>'
)
_STYLE_PATTERN: Final = re.compile(
    rb'<link rel="stylesheet" href="data:text/css;base64,([A-Za-z0-9+/]+={0,2})">'
)


class ViewerPackagingError(ValueError):
    """The report or packaged viewer cannot safely produce a standalone artifact."""


@dataclass(frozen=True, slots=True)
class ViewerPackage:
    """One fully verified, closed viewer distribution."""

    root: Path
    version: str
    manifest: Mapping[str, Any]
    template: bytes


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json_object(value: bytes, label: str) -> dict[str, Any]:
    try:
        text = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ViewerPackagingError(f"{label} is not strict UTF-8") from exc

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ViewerPackagingError(f"{label} repeats JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(token: str) -> None:
        raise ViewerPackagingError(f"{label} contains non-finite JSON constant {token}")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ViewerPackagingError(f"{label} is not one unambiguous JSON document") from exc
    if not isinstance(parsed, dict):
        raise ViewerPackagingError(f"{label} must contain one JSON object")
    return parsed


def _safe_relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ViewerPackagingError(f"{label} must be a non-empty relative POSIX path")
    if "\\" in value or "%" in value:
        raise ViewerPackagingError(f"{label} contains a forbidden escape or path separator")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ViewerPackagingError(f"{label} escapes the viewer package")
    return path


def _package_version() -> str:
    try:
        return importlib.metadata.version("moodboard")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _read_owned_file(root: Path, relative: PurePosixPath) -> bytes:
    candidate = root.joinpath(*relative.parts)
    if not candidate.is_file() or candidate.is_symlink():
        raise ViewerPackagingError(f"viewer artifact is missing or not a regular file: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ViewerPackagingError(f"viewer artifact escapes its package root: {relative}") from exc
    return candidate.read_bytes()


def _manifest_files(manifest: Mapping[str, Any]) -> dict[PurePosixPath, str]:
    values: list[tuple[PurePosixPath, str]] = []
    for name in ("manifest_schema", "verification_toolchain", "consumer_contract", "static_entry"):
        entry = manifest[name]
        if not isinstance(entry, Mapping):
            raise ViewerPackagingError(f"manifest {name} entry is not an object")
        values.append(
            (
                _safe_relative_path(entry.get("path"), f"manifest.{name}.path"),
                str(entry.get("sha256")),
            )
        )
    template = manifest["template"]
    if not isinstance(template, Mapping):
        raise ViewerPackagingError("manifest template entry is not an object")
    values.append(
        (
            _safe_relative_path(template.get("path"), "manifest.template.path"),
            str(template.get("sha256")),
        )
    )
    for collection_name in ("writer_schemas", "assets"):
        collection = manifest[collection_name]
        if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes)):
            raise ViewerPackagingError(f"manifest {collection_name} is not an array")
        for index, entry in enumerate(collection):
            if not isinstance(entry, Mapping):
                raise ViewerPackagingError(f"manifest {collection_name}[{index}] is not an object")
            values.append(
                (
                    _safe_relative_path(
                        entry.get("path"), f"manifest.{collection_name}[{index}].path"
                    ),
                    str(entry.get("sha256")),
                )
            )
    result: dict[PurePosixPath, str] = {}
    for relative, expected_hash in values:
        if relative in result:
            raise ViewerPackagingError(f"manifest repeats artifact path {relative}")
        if re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
            raise ViewerPackagingError(f"manifest carries an invalid SHA-256 for {relative}")
        result[relative] = expected_hash
    return result


def _decode_template_asset(template: bytes, pattern: re.Pattern[bytes], label: str) -> bytes:
    matches = pattern.findall(template)
    if len(matches) != 1:
        raise ViewerPackagingError(f"standalone template contains {len(matches)} {label} assets")
    try:
        decoded = base64.b64decode(matches[0], validate=True)
    except binascii.Error as exc:
        raise ViewerPackagingError(f"standalone template {label} is not canonical base64") from exc
    if base64.b64encode(decoded) != matches[0]:
        raise ViewerPackagingError(f"standalone template {label} base64 is not canonical")
    return decoded


def validate_viewer_package(package_root: Path | None = None) -> ViewerPackage:
    """Verify the exact manifest-owned bytes before report data is considered."""

    root = package_root or Path(__file__).with_name("viewer_dist")
    if not root.is_dir() or root.is_symlink():
        raise ViewerPackagingError(
            "verified viewer package data is absent; run the pinned viewer build before packaging"
        )

    manifest_path = root / "artifact-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ViewerPackagingError("artifact-manifest.json is missing from the viewer package")
    manifest = _load_json_object(manifest_path.read_bytes(), "artifact-manifest.json")

    manifest_schema_path = _safe_relative_path(
        manifest.get("manifest_schema", {}).get("path")
        if isinstance(manifest.get("manifest_schema"), Mapping)
        else None,
        "manifest.manifest_schema.path",
    )
    schema_bytes = _read_owned_file(root, manifest_schema_path)
    schema = _load_json_object(schema_bytes, "artifact manifest schema")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(manifest),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    except jsonschema.SchemaError as exc:
        raise ViewerPackagingError("packaged artifact manifest schema is itself invalid") from exc
    if errors:
        path = "/" + "/".join(str(part) for part in errors[0].absolute_path)
        raise ViewerPackagingError(f"artifact manifest fails its schema at {path}")

    version = manifest.get("viewer_version")
    if version != _package_version():
        raise ViewerPackagingError(
            f"viewer version {version!r} does not match moodboard package "
            f"version {_package_version()!r}"
        )

    owned = _manifest_files(manifest)
    for relative, expected in owned.items():
        actual = _sha256(_read_owned_file(root, relative))
        if actual != expected:
            raise ViewerPackagingError(f"viewer artifact hash mismatch: {relative}")

    expected_inventory = {PurePosixPath("artifact-manifest.json"), *owned.keys()}
    actual_inventory = {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_inventory != expected_inventory:
        missing = sorted(str(item) for item in expected_inventory - actual_inventory)
        extra = sorted(str(item) for item in actual_inventory - expected_inventory)
        raise ViewerPackagingError(
            f"viewer package inventory is not closed; missing={missing}, extra={extra}"
        )

    template_entry = manifest["template"]
    assert isinstance(template_entry, Mapping)
    template_path = _safe_relative_path(template_entry["path"], "manifest.template.path")
    template = _read_owned_file(root, template_path)
    token = template_entry.get("payload_token")
    if token != _PAYLOAD_TOKEN or template.count(_PAYLOAD_TOKEN.encode("ascii")) != 1:
        raise ViewerPackagingError("standalone template payload token is missing or duplicated")
    if template_entry.get("payload_token_count") != 1:
        raise ViewerPackagingError("manifest does not pin one standalone payload token")
    if template_entry.get("content_security_policy") != _STANDALONE_CSP:
        raise ViewerPackagingError("manifest does not pin the standalone Content Security Policy")
    csp_tags = _CSP_META_PATTERN.findall(template)
    if csp_tags != [_CSP_META]:
        raise ViewerPackagingError(
            "standalone template does not contain exactly the pinned Content Security Policy"
        )
    if re.search(rb"\b(?:src|href)=[\"'](?!data:)", template, flags=re.IGNORECASE):
        raise ViewerPackagingError(
            "standalone template contains a non-data runtime asset reference"
        )

    script_bytes = _decode_template_asset(template, _SCRIPT_PATTERN, "application-js")
    style_bytes = _decode_template_asset(template, _STYLE_PATTERN, "application-css")
    assets = manifest["assets"]
    assert isinstance(assets, Sequence)
    assets_by_role = {
        str(entry["role"]): entry
        for entry in assets
        if isinstance(entry, Mapping) and "role" in entry
    }
    if set(assets_by_role) != {"application-js", "application-css"}:
        raise ViewerPackagingError("manifest must contain exactly one JavaScript and one CSS role")
    for role, embedded in (("application-js", script_bytes), ("application-css", style_bytes)):
        entry = assets_by_role[role]
        assert isinstance(entry, Mapping)
        relative = _safe_relative_path(entry["path"], f"manifest.assets[{role}].path")
        staged = _read_owned_file(root, relative)
        if embedded != staged or _sha256(embedded) != entry["sha256"]:
            raise ViewerPackagingError(f"template-embedded {role} differs from its manifest bytes")

    return ViewerPackage(root=root, version=str(version), manifest=manifest, template=template)


def decode_report_document(report_bytes: bytes) -> tuple[str, dict[str, Any]]:
    """Decode one strict UTF-8 report without duplicate keys or lossy/non-finite numbers."""

    try:
        report_text = report_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ViewerPackagingError("report bytes are not strict UTF-8") from exc

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ViewerPackagingError(f"report repeats JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise ViewerPackagingError(f"report contains non-finite JSON constant {token}")

    try:
        parsed = json.loads(
            report_text,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=reject_constant,
            object_pairs_hook=object_pairs,
        )
    except (json.JSONDecodeError, InvalidOperation, RecursionError) as exc:
        raise ViewerPackagingError("report is not one unambiguous JSON document") from exc
    if not isinstance(parsed, dict):
        raise ViewerPackagingError("report root must be an object")
    version = parsed.get("schema_version")
    if version not in _SUPPORTED_REPORTS:
        raise ViewerPackagingError(
            f"unsupported_schema_version: expected exactly 1.0 or 1.1, received {version!r}"
        )

    def convert(value: Any, path: str) -> Any:
        if isinstance(value, Decimal):
            try:
                number = float(value)
            except (OverflowError, ValueError) as exc:
                raise ViewerPackagingError(f"numeric-range at {path}") from exc
            if not math.isfinite(number) or Decimal(str(number)) != value:
                raise ViewerPackagingError(f"numeric-range at {path}")
            if value == value.to_integral_value() and abs(value) > _SAFE_INTEGER:
                raise ViewerPackagingError(f"numeric-range at {path}")
            return int(value) if value == value.to_integral_value() else number
        if isinstance(value, list):
            return [convert(item, f"{path}/{index}") for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {
                key: convert(item, f"{path}/{key.replace('~', '~0').replace('/', '~1')}")
                for key, item in value.items()
            }
        return value

    return str(version), convert(parsed, "")


def _schema_entries(viewer: ViewerPackage) -> dict[str, tuple[bytes, dict[str, Any]]]:
    entries = viewer.manifest["writer_schemas"]
    assert isinstance(entries, Sequence)
    result: dict[str, tuple[bytes, dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ViewerPackagingError("writer schema manifest entry is malformed")
        version = entry.get("schema_version")
        relative = _safe_relative_path(entry.get("path"), "writer schema path")
        schema_bytes = _read_owned_file(viewer.root, relative)
        result[str(version)] = (
            schema_bytes,
            _load_json_object(schema_bytes, f"report schema {version}"),
        )
    if set(result) != _SUPPORTED_REPORTS:
        raise ViewerPackagingError("viewer package does not carry exactly schemas 1.0 and 1.1")
    return result


def _report_schema_validate(report: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(report),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    except jsonschema.SchemaError as exc:
        raise ViewerPackagingError("packaged report schema is itself invalid") from exc
    if errors:
        path = "/" + "/".join(str(part) for part in errors[0].absolute_path)
        raise ViewerPackagingError(f"report fails schema at {path}")


def _thumbnail_limits(thumbnail: Mapping[str, Any], path: str) -> None:
    width = thumbnail.get("width")
    height = thumbnail.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ViewerPackagingError(f"thumbnail dimensions are not bounded integers at {path}")
    if width > _MAX_THUMBNAIL_SIDE or height > _MAX_THUMBNAIL_SIDE:
        raise ViewerPackagingError(
            f"thumbnail dimensions exceed the {_MAX_THUMBNAIL_SIDE}-pixel side limit at {path}"
        )
    pixels = width * height
    if pixels > _MAX_THUMBNAIL_PIXELS:
        raise ViewerPackagingError(
            f"thumbnail dimensions exceed the {_MAX_THUMBNAIL_PIXELS}-pixel decode limit at {path}"
        )
    if pixels * 4 > _MAX_THUMBNAIL_DECODED_BYTES:
        raise ViewerPackagingError(
            "thumbnail raster exceeds the "
            f"{_MAX_THUMBNAIL_DECODED_BYTES}-byte decode limit at {path}"
        )
    payload = thumbnail.get("data_base64")
    if not isinstance(payload, str):
        raise ViewerPackagingError(f"thumbnail base64 is not a string at {path}/data_base64")
    estimated_bytes = (len(payload) * 3 + 3) // 4
    if estimated_bytes > _MAX_THUMBNAIL_COMPRESSED_BYTES:
        raise ViewerPackagingError(
            "thumbnail payload exceeds the "
            f"{_MAX_THUMBNAIL_COMPRESSED_BYTES}-byte compressed limit at {path}"
        )


def _thumbnail_bytes(thumbnail: Mapping[str, Any], path: str) -> bytes:
    _thumbnail_limits(thumbnail, path)
    mime = thumbnail.get("mime")
    if mime not in _SAFE_MIMES:
        raise ViewerPackagingError(f"unsupported thumbnail MIME at {path}/mime")
    payload = thumbnail.get("data_base64")
    if not isinstance(payload, str):
        raise ViewerPackagingError(f"thumbnail base64 is not a string at {path}/data_base64")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ViewerPackagingError(f"thumbnail base64 is invalid at {path}/data_base64") from exc
    if base64.b64encode(decoded).decode("ascii") != payload:
        raise ViewerPackagingError(f"thumbnail base64 is not canonical at {path}/data_base64")
    if len(decoded) > _MAX_THUMBNAIL_COMPRESSED_BYTES:
        raise ViewerPackagingError(
            "thumbnail payload exceeds the "
            f"{_MAX_THUMBNAIL_COMPRESSED_BYTES}-byte compressed limit at {path}"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(decoded)) as image:
                actual_format = image.format
                actual_size = image.size
                _thumbnail_limits(
                    {
                        "width": actual_size[0],
                        "height": actual_size[1],
                        "data_base64": payload,
                    },
                    path,
                )
                expected_size = (thumbnail.get("width"), thumbnail.get("height"))
                if actual_size != expected_size:
                    raise ViewerPackagingError(
                        f"thumbnail dimensions do not match its bytes at {path}"
                    )
                image.load()
    except ViewerPackagingError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as exc:
        raise ViewerPackagingError(f"thumbnail does not decode at {path}") from exc
    if actual_format != _SAFE_MIMES[mime]:
        raise ViewerPackagingError(f"thumbnail MIME does not match its bytes at {path}")
    return decoded


def _validate_cross_fields(
    version: str,
    report: Mapping[str, Any],
    schema_bytes: bytes,
) -> None:
    references = report["references"]
    assets = report["assets"]
    board = report["board"]
    comparisons = report["comparisons"]
    provenance = report["provenance"]
    assert isinstance(references, list)
    assert isinstance(assets, list)
    assert isinstance(board, Mapping)
    assert isinstance(comparisons, Mapping)
    assert isinstance(provenance, Mapping)

    thumbnails: list[tuple[Mapping[str, Any], str]] = []
    for index, reference in enumerate(references):
        assert isinstance(reference, Mapping)
        thumbnail = reference["thumbnail"]
        assert isinstance(thumbnail, Mapping)
        thumbnails.append((thumbnail, f"/references/{index}/thumbnail"))
    for index, asset in enumerate(assets):
        assert isinstance(asset, Mapping)
        image = asset.get("image")
        if isinstance(image, Mapping):
            thumbnail = image["thumbnail"]
            assert isinstance(thumbnail, Mapping)
            thumbnails.append((thumbnail, f"/assets/{index}/image/thumbnail"))
    if len(thumbnails) > _MAX_TOTAL_THUMBNAILS:
        raise ViewerPackagingError(
            f"report carries {len(thumbnails)} thumbnails and exceeds the "
            f"{_MAX_TOTAL_THUMBNAILS}-thumbnail decode limit"
        )
    declared_raster_bytes = 0
    for thumbnail, path in thumbnails:
        _thumbnail_limits(thumbnail, path)
        width = thumbnail["width"]
        height = thumbnail["height"]
        assert isinstance(width, int) and not isinstance(width, bool)
        assert isinstance(height, int) and not isinstance(height, bool)
        declared_raster_bytes += width * height * 4
    if declared_raster_bytes > _MAX_TOTAL_THUMBNAIL_DECODED_BYTES:
        raise ViewerPackagingError(
            "report thumbnails exceed the aggregate "
            f"{_MAX_TOTAL_THUMBNAIL_DECODED_BYTES}-byte raster limit"
        )

    reference_positions: dict[str, int] = {}
    for index, reference in enumerate(references):
        assert isinstance(reference, Mapping)
        reference_id = str(reference["reference_id"])
        if reference_id in reference_positions:
            raise ViewerPackagingError(
                f"duplicate reference id at /references/{index}/reference_id"
            )
        reference_positions[reference_id] = index
    if board["n_references"] != len(references):
        raise ViewerPackagingError("board.n_references does not match the reference catalogue")

    representation = board["representation"]
    assert isinstance(representation, Mapping)
    axis_ids = ["style", *representation["axes"]]
    assets_by_id: dict[str, Mapping[str, Any]] = {}
    for asset_index, asset in enumerate(assets):
        assert isinstance(asset, Mapping)
        asset_id = str(asset["asset_id"])
        if asset_id in assets_by_id:
            raise ViewerPackagingError(f"duplicate asset id at /assets/{asset_index}/asset_id")
        assets_by_id[asset_id] = asset
        if set(asset["axes"]) != set(axis_ids) or len(asset["axes"]) != len(axis_ids):
            raise ViewerPackagingError(f"axis vocabulary mismatch at /assets/{asset_index}/axes")
        if asset["state"] == "scored":
            interval = asset["interval"]
            assert isinstance(interval, Mapping)
            if interval["low"] > interval["high"]:
                raise ViewerPackagingError(f"reversed interval at /assets/{asset_index}/interval")
            if asset["score"] != asset["axes"]["style"]:
                raise ViewerPackagingError(
                    f"score/style mismatch at /assets/{asset_index}/axes/style"
                )
        elif asset["axes"]["style"] is not None:
            raise ViewerPackagingError(
                f"abstained style axis is not null at /assets/{asset_index}/axes/style"
            )

        seen: set[str] = set()
        exemplars = asset["exemplars"]
        assert isinstance(exemplars, list)
        for exemplar_index, exemplar in enumerate(exemplars):
            assert isinstance(exemplar, Mapping)
            reference_id = str(exemplar["reference_id"])
            if reference_id in seen:
                raise ViewerPackagingError(
                    f"duplicate exemplar at /assets/{asset_index}/exemplars/{exemplar_index}"
                )
            seen.add(reference_id)
            if version == "1.1" and reference_id not in reference_positions:
                raise ViewerPackagingError(
                    f"unresolved exemplar at /assets/{asset_index}/exemplars/{exemplar_index}"
                )
            if version == "1.1" and exemplar_index > 0:
                previous = exemplars[exemplar_index - 1]
                assert isinstance(previous, Mapping)
                previous_position = reference_positions.get(
                    str(previous["reference_id"]), _SAFE_INTEGER
                )
                current_position = reference_positions.get(reference_id, _SAFE_INTEGER)
                if exemplar["similarity"] > previous["similarity"] or (
                    exemplar["similarity"] == previous["similarity"]
                    and current_position < previous_position
                ):
                    raise ViewerPackagingError(
                        "exemplar order mismatch at "
                        f"/assets/{asset_index}/exemplars/{exemplar_index}"
                    )
        if version == "1.1" and len(exemplars) != min(3, len(references)):
            raise ViewerPackagingError(
                f"strict triptych mismatch at /assets/{asset_index}/exemplars"
            )

    tie_pairs: set[tuple[str, str]] = set()
    for index, pair in enumerate(comparisons["ties"]):
        left, right = pair
        if (
            left == right
            or assets_by_id.get(left, {}).get("state") != "scored"
            or assets_by_id.get(right, {}).get("state") != "scored"
        ):
            raise ViewerPackagingError(f"invalid tie endpoints at /comparisons/ties/{index}")
        key = tuple(sorted((left, right)))
        if key in tie_pairs:
            raise ViewerPackagingError(f"duplicate unordered tie at /comparisons/ties/{index}")
        tie_pairs.add(key)

    if version != "1.1":
        return

    schema_provenance = provenance["schema"]
    assert isinstance(schema_provenance, Mapping)
    if schema_provenance["sha256"] != _sha256(schema_bytes):
        raise ViewerPackagingError("report provenance schema hash differs from packaged bytes")
    if provenance["command"] != shlex.join(provenance["argv"]):
        raise ViewerPackagingError("report command does not equal shlex.join(argv)")

    definitions = representation["axis_definitions"]
    assert isinstance(definitions, list)
    if [item["axis_id"] for item in definitions] != axis_ids:
        raise ViewerPackagingError("axis definition ids do not follow the declared vocabulary")

    for thumbnail, path in thumbnails:
        _thumbnail_bytes(thumbnail, path)


def _validate_report_bytes(
    report_bytes: bytes,
    viewer: ViewerPackage,
) -> None:
    version, report = decode_report_document(report_bytes)
    schema_bytes, schema = _schema_entries(viewer)[version]
    _report_schema_validate(report, schema)
    _validate_cross_fields(version, report, schema_bytes)
    try:
        from_json_dict(report)
    except (ValueError, TypeError, KeyError, jsonschema.ValidationError) as exc:
        raise ViewerPackagingError(f"report fails the shared Python contract: {exc}") from exc


def _atomic_publish(destination: Path, content: bytes) -> None:
    parent = destination.parent
    if not parent.is_dir():
        raise ViewerPackagingError(f"output directory does not exist: {parent}")
    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise
        os.replace(temporary_path, destination)
        temporary_path = None
        try:
            directory_descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            # Directory fsync is unavailable on some supported filesystems; the atomic replace
            # has already completed and remains the required publication boundary.
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def inline_report(
    report: Path,
    output: Path,
    *,
    package_root: Path | None = None,
) -> Path:
    """Publish one offline HTML artifact, preserving an existing output on every failure."""

    viewer = validate_viewer_package(package_root)
    try:
        report_bytes = read_report_bytes(report)
    except OSError as exc:
        raise ViewerPackagingError(f"could not read report: {report}") from exc
    except ValueError as exc:
        raise ViewerPackagingError(str(exc)) from exc
    _validate_report_bytes(report_bytes, viewer)

    try:
        same_input_and_output = report.resolve(strict=True) == output.resolve(strict=False)
    except OSError as exc:
        raise ViewerPackagingError(f"could not resolve report path: {report}") from exc
    if same_input_and_output:
        raise ViewerPackagingError("output cannot replace the input report")

    try:
        output.resolve(strict=False).relative_to(viewer.root.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ViewerPackagingError("output cannot overwrite viewer package data")

    encoded = base64.b64encode(report_bytes)
    token = _PAYLOAD_TOKEN.encode("ascii")
    if viewer.template.count(token) != 1:
        raise ViewerPackagingError("verified template lost its unique payload token")
    html = viewer.template.replace(token, encoded, 1)
    _atomic_publish(output, html)
    return output
