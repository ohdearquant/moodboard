"""Deterministic insert compilation and source-backed composition for ADR-0016.

The provider output remains an immutable ``generator_raw`` occurrence.  This module creates a
second, disjoint occurrence only after a user-confirmed crop has been compiled into an RGB insert,
the complete provider ancestry has been validated, and protected pixels have been copied from the
canonical source.  Every byte-producing step is deliberately narrow and reproducible.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import struct
import uuid
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeAlias

import jsonschema
from blake3 import blake3
from referencing import Registry, Resource

from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    compute_document_identity,
    compute_projection_identity,
    is_canonical_utc_timestamp,
)
from moodboard.intent_packet import IntentPacket, validate_intent_packet
from moodboard.intent_packet import to_json_dict as intent_packet_to_json
from moodboard.judgment import (
    SCHEMA_VERSION as JUDGMENT_VERSION,
)
from moodboard.judgment import (
    ConstraintVerificationJudgment,
    JudgmentError,
    validate_locality_blocking_pair,
)
from moodboard.judgment import from_json_dict as judgment_from_json
from moodboard.judgment import to_json_dict as judgment_to_json
from moodboard.locality import (
    COMPILER_REVISION as RASTER_COMPILER_REVISION,
)
from moodboard.locality import (
    LocalityError,
    StructuralVerification,
    _measure_outside_mask_rgb_exact,
    build_locality_not_run,
    compile_canonical_raster,
    compile_rectangle_mask,
    verify_output_structure,
    verify_outside_mask_rgb_exact,
)
from moodboard.locality_contracts import (
    EXACT_LOCALITY_VERIFIER_VERSION,
    STRUCTURAL_VERIFIER_VERSION,
    CanonicalMaskArtifact,
    CanonicalRasterArtifact,
    LocalityContractError,
    compute_exact_locality_input_digest,
    compute_structural_input_digest,
    validate_mask_artifact,
    validate_raster_artifact,
)
from moodboard.provider_artifacts import (
    OUTPUT_VERSION as PROVIDER_OUTPUT_VERSION,
)
from moodboard.provider_artifacts import (
    RECEIPT_VERSION as PROVIDER_RECEIPT_VERSION,
)
from moodboard.provider_artifacts import (
    OutputOccurrence,
    ProviderArtifact,
    validate_artifact_bundle,
    validate_provider_artifact,
)
from moodboard.provider_artifacts import to_json_dict as provider_to_json

__all__ = [
    "COMPOSITOR_OCCURRENCE_VERSION",
    "COMPOSITOR_POLICY",
    "COMPOSITOR_REPLAY_KEY_VERSION",
    "COMPOSITOR_REVISION",
    "DEFAULT_PNG_ENCODER_MANIFEST",
    "DEFLATE_REVISION",
    "INSERT_COMPILER_POLICY",
    "INSERT_COMPILER_REVISION",
    "INSERT_CONFIRMATION_KEY_VERSION",
    "INSERT_CONFIRMATION_VERSION",
    "INSERT_PREVIEW_VERSION",
    "INSERT_VERSION",
    "PNG_ENCODER_REVISION",
    "PNG_VERSION",
    "SCHEMA_PATHS",
    "TARGET_REGION_VERSION",
    "CanonicalInsertArtifact",
    "CanonicalPngArtifact",
    "CompositorArtifact",
    "CompositorError",
    "CompositorOutputOccurrence",
    "CompositorResult",
    "InsertCompileConfirmation",
    "PngEncoderManifest",
    "compile_raw_crop_nearest",
    "compose_source_backed_rect_replace",
    "compute_compositor_occurrence_id",
    "compute_compositor_replay_key",
    "compute_confirmation_key",
    "compute_insert_compile_confirmation_id",
    "compute_insert_sha256",
    "compute_target_region_id",
    "encode_canonical_png",
    "from_json_dict",
    "resolve_compositor_replay",
    "resolve_insert_confirmation_replay",
    "resolve_insert_confirmation_replay",
    "seal_compositor_output_occurrence",
    "seal_insert_compile_confirmation",
    "to_json_dict",
    "validate_compositor_artifact",
    "validate_compositor_output_occurrence",
    "validate_insert_artifact",
    "validate_insert_compile_confirmation",
    "validate_png_artifact",
    "verify_compositor_output_structure",
]

INSERT_CONFIRMATION_VERSION = "moodboard.insert-compile-confirmation.v1"
TARGET_REGION_VERSION = "moodboard.target-region.v1"
INSERT_PREVIEW_VERSION = "moodboard.insert-preview-projection.v1"
INSERT_CONFIRMATION_KEY_VERSION = "moodboard.insert-compile-confirmation-key.v1"

INSERT_VERSION = "moodboard.insert.rgb-u8.v1"
INSERT_COMPILER_POLICY = "raw_crop_nearest.v1"
INSERT_COMPILER_REVISION = "moodboard.insert-compiler.raw-crop-nearest.v1"

PNG_VERSION = "moodboard.canonical-png.v1"
PNG_ENCODER_REVISION = "moodboard.png-encoder.rgb8-filter0-deflate-stored.v1"
DEFLATE_REVISION = "moodboard.deflate.rfc1951-stored-blocks.v1"

COMPOSITOR_OCCURRENCE_VERSION = "moodboard.compositor-output-occurrence.v1"
COMPOSITOR_POLICY = "source_backed_rect_replace.v1"
COMPOSITOR_REVISION = "moodboard.compositor.source-backed-rect-replace.v1"
COMPOSITOR_REPLAY_KEY_VERSION = "moodboard.compositor-replay-key.v1"

_SCHEMA_DIR = Path(__file__).parent / "schema"
SCHEMA_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        INSERT_CONFIRMATION_VERSION: _SCHEMA_DIR / "insert_compile_confirmation_v1.schema.json",
        INSERT_VERSION: _SCHEMA_DIR / "insert_occurrence_v1.schema.json",
        PNG_VERSION: _SCHEMA_DIR / "canonical_png_v1.schema.json",
        COMPOSITOR_OCCURRENCE_VERSION: (
            _SCHEMA_DIR / "compositor_output_occurrence_v1.schema.json"
        ),
    }
)
_REGISTRY_PATHS = (*SCHEMA_PATHS.values(), _SCHEMA_DIR / "raster_srgb_u8_v1.schema.json")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_PIXELS = 16_777_216
_MAX_PROVIDER_ARTIFACTS = 64
_INSERT_IDENTITY_FIELDS = (
    "compiler_revision",
    "insert_compile_confirmation_id",
    "intent_packet_id",
    "raw_output_occurrence_id",
    "raw_output_raster_sha256",
    "raw_structural_evidence_id",
    "raw_locality_evidence_id",
    "crop",
    "target_region_id",
    "width",
    "height",
    "mode",
    "byte_count",
)


class CompositorError(ValueError):
    """One compositor input or deterministic result violates its closed contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


FrozenJson: TypeAlias = (
    None | bool | int | float | str | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
)


@dataclass(frozen=True, slots=True)
class PngEncoderManifest:
    schema_version: str
    encoder_revision: str
    deflate_revision: str
    max_dimension: int
    max_pixels: int


DEFAULT_PNG_ENCODER_MANIFEST = PngEncoderManifest(
    schema_version="moodboard.png-encoder-manifest.v1",
    encoder_revision=PNG_ENCODER_REVISION,
    deflate_revision=DEFLATE_REVISION,
    max_dimension=32_768,
    max_pixels=_MAX_PNG_PIXELS,
)


@dataclass(frozen=True, slots=True)
class InsertCompileConfirmation:
    schema_version: str
    insert_compile_confirmation_id: str
    confirmation_key: str
    principal_id: str
    studio_session_id: str
    intent_packet_id: str
    raw_output_occurrence_id: str
    raw_output_raster_sha256: str
    raw_structural_evidence_id: str
    raw_locality_evidence_id: str
    crop: Mapping[str, FrozenJson]
    target_region: Mapping[str, FrozenJson]
    compiler_policy: Mapping[str, FrozenJson]
    preview_projection: Mapping[str, FrozenJson]
    confirmed_at: str


@dataclass(frozen=True, slots=True)
class CanonicalInsertArtifact:
    schema_version: str
    insert_sha256: str
    compiler_revision: str
    insert_compile_confirmation_id: str
    intent_packet_id: str
    raw_output_occurrence_id: str
    raw_output_raster_sha256: str
    raw_structural_evidence_id: str
    raw_locality_evidence_id: str
    crop: Mapping[str, FrozenJson]
    target_region_id: str
    width: int
    height: int
    mode: str
    byte_count: int
    rgb_bytes: bytes


@dataclass(frozen=True, slots=True)
class CanonicalPngArtifact:
    schema_version: str
    encoder_revision: str
    deflate_revision: str
    width: int
    height: int
    mode: str
    byte_count: int
    content_sha256: str
    content_ref: str
    png_bytes: bytes


@dataclass(frozen=True, slots=True)
class CompositorOutputOccurrence:
    schema_version: str
    output_occurrence_id: str
    compositor_replay_key: str
    producer_kind: str
    role: str
    intent_packet_id: str
    original: Mapping[str, FrozenJson]
    output_raster: Mapping[str, FrozenJson]
    admission: Mapping[str, FrozenJson]
    producer: Mapping[str, FrozenJson]
    lineage: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class CompositorResult:
    insert: CanonicalInsertArtifact
    png: CanonicalPngArtifact
    output_raster: CanonicalRasterArtifact
    occurrence: CompositorOutputOccurrence
    structural: StructuralVerification
    locality: ConstraintVerificationJudgment


CompositorArtifact: TypeAlias = (
    InsertCompileConfirmation
    | CanonicalInsertArtifact
    | CanonicalPngArtifact
    | CompositorOutputOccurrence
)


@cache
def _schema_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise CompositorError(
            "schema_unavailable", f"compositor schema is unavailable or invalid: {error}"
        ) from error
    return document


@cache
def _schema_registry() -> Registry:
    registry = Registry()
    for path in _REGISTRY_PATHS:
        document = _schema_document(path)
        schema_id = document.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise CompositorError("schema_unavailable", f"schema {path.name} has no absolute id")
        registry = registry.with_resource(schema_id, Resource.from_contents(document))
    return registry


@cache
def _validator(version: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        _schema_document(SCHEMA_PATHS[version]),
        registry=_schema_registry(),
        format_checker=jsonschema.FormatChecker(),
    )


def _error_sort_key(error: jsonschema.ValidationError) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, f"{part:020d}") if isinstance(part, int) else (1, str(part))
        for part in error.absolute_path
    )


def _json_path(error: jsonschema.ValidationError) -> str:
    if not error.absolute_path:
        return "$"
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )


def _validate_schema(document: dict[str, Any], version: str) -> None:
    try:
        errors = sorted(_validator(version).iter_errors(document), key=_error_sort_key)
    except (RecursionError, TypeError, ValueError):
        raise CompositorError("schema_invalid", "artifact is not finite JSON") from None
    if errors:
        first = errors[0]
        raise CompositorError(
            "schema_invalid",
            f"compositor artifact {_json_path(first)} violates {first.validator}",
        )


def _freeze_json(value: Any) -> FrozenJson:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: FrozenJson) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _copy_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise CompositorError("schema_invalid", "compositor artifact must be a mapping")
    try:
        return copy.deepcopy(dict(document))
    except Exception:
        raise CompositorError(
            "schema_invalid", "artifact cannot be copied as finite JSON"
        ) from None


def _require_bytes(value: object, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise CompositorError("payload_invalid", f"{field} must be immutable bytes")
    return value


def _validate_uuid(value: object, field: str) -> None:
    if not isinstance(value, str):
        raise CompositorError("schema_invalid", f"{field} must be a canonical UUID")
    try:
        measured = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise CompositorError("schema_invalid", f"{field} must be a canonical UUID") from error
    if measured != value:
        raise CompositorError("schema_invalid", f"{field} must use lowercase canonical spelling")


def _validate_bounds(bounds: Mapping[str, Any], *, width: int, height: int, label: str) -> None:
    if set(bounds) != {"left", "top", "right", "bottom"}:
        raise CompositorError("bounds_invalid", f"{label} must contain exactly four bounds")
    left, top, right, bottom = (bounds[key] for key in ("left", "top", "right", "bottom"))
    if any(type(value) is not int for value in (left, top, right, bottom)):
        raise CompositorError("bounds_invalid", f"{label} bounds must be plain integers")
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise CompositorError("bounds_invalid", f"{label} lies outside its raster")


def _raster_document(raster: CanonicalRasterArtifact) -> dict[str, Any]:
    if not isinstance(raster, CanonicalRasterArtifact):
        raise CompositorError("contract_mismatch", "raster must be a canonical raster artifact")
    document = {
        "schema_version": raster.schema_version,
        "compiler_revision": raster.compiler_revision,
        "width": raster.width,
        "height": raster.height,
        "mode": raster.mode,
        "byte_count": raster.byte_count,
        "source_content_sha256": raster.source_content_sha256,
        "raster_sha256": raster.raster_sha256,
    }
    try:
        validate_raster_artifact(document, raster.rgb_bytes)
    except LocalityContractError as error:
        raise CompositorError("contract_mismatch", str(error)) from error
    return document


def _mask_document(mask: CanonicalMaskArtifact) -> dict[str, Any]:
    if not isinstance(mask, CanonicalMaskArtifact):
        raise CompositorError("contract_mismatch", "mask must be a canonical mask artifact")
    document = {
        "schema_version": mask.schema_version,
        "compiler_revision": mask.compiler_revision,
        "width": mask.width,
        "height": mask.height,
        "byte_count": mask.byte_count,
        "editable_count": mask.editable_count,
        "protected_count": mask.protected_count,
        "source_raster_sha256": mask.source_raster_sha256,
        "mask_sha256": mask.mask_sha256,
    }
    try:
        validate_mask_artifact(document, mask.mask_bytes)
    except LocalityContractError as error:
        raise CompositorError("contract_mismatch", str(error)) from error
    return document


def _binary_identity(*, domain: str, projection: Mapping[str, Any], payload: bytes) -> str:
    try:
        encoded = canonical_json_bytes(dict(projection))
    except ContractIdentityError as error:
        raise CompositorError("identity_invalid", str(error)) from error
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    digest.update(encoded)
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def compute_target_region_id(target_region: Mapping[str, Any]) -> str:
    document = _copy_document(target_region)
    if document.get("schema_version") != TARGET_REGION_VERSION:
        raise CompositorError("identity_invalid", "target region uses an unsupported version")
    if "target_region_id" not in document:
        document["target_region_id"] = "0" * 64
    try:
        return compute_document_identity(
            document,
            schema_version=TARGET_REGION_VERSION,
            identity_field="target_region_id",
        )
    except ContractIdentityError as error:
        raise CompositorError("identity_invalid", str(error)) from error


def _expected_preview(document: Mapping[str, Any]) -> dict[str, Any]:
    target = document["target_region"]
    policy = document["compiler_policy"]
    return {
        "schema_version": INSERT_PREVIEW_VERSION,
        "intent_packet_id": document["intent_packet_id"],
        "raw_output_occurrence_id": document["raw_output_occurrence_id"],
        "raw_output_raster_sha256": document["raw_output_raster_sha256"],
        "raw_structural_evidence_id": document["raw_structural_evidence_id"],
        "raw_locality_evidence_id": document["raw_locality_evidence_id"],
        "crop": copy.deepcopy(document["crop"]),
        "target_region_id": target["target_region_id"],
        "target_width": target["width"],
        "target_height": target["height"],
        "policy_id": policy["policy_id"],
        "compiler_revision": policy["compiler_revision"],
    }


def compute_confirmation_key(document: Mapping[str, Any]) -> str:
    try:
        projection = {
            "principal_id": document["principal_id"],
            "studio_session_id": document["studio_session_id"],
            "intent_packet_id": document["intent_packet_id"],
            "preview_projection": copy.deepcopy(document["preview_projection"]),
        }
        return compute_projection_identity(projection, domain_tag=INSERT_CONFIRMATION_KEY_VERSION)
    except (KeyError, ContractIdentityError) as error:
        raise CompositorError(
            "identity_invalid", f"cannot derive confirmation key: {error}"
        ) from error


def compute_insert_compile_confirmation_id(document: Mapping[str, Any]) -> str:
    copied = _copy_document(document)
    if copied.get("schema_version") != INSERT_CONFIRMATION_VERSION:
        raise CompositorError("identity_invalid", "confirmation uses an unsupported version")
    if "insert_compile_confirmation_id" not in copied:
        copied["insert_compile_confirmation_id"] = "0" * 64
    try:
        return compute_document_identity(
            copied,
            schema_version=INSERT_CONFIRMATION_VERSION,
            identity_field="insert_compile_confirmation_id",
        )
    except ContractIdentityError as error:
        raise CompositorError("identity_invalid", str(error)) from error


def validate_insert_compile_confirmation(document: Mapping[str, Any]) -> None:
    copied = _copy_document(document)
    _validate_schema(copied, INSERT_CONFIRMATION_VERSION)
    _validate_uuid(copied["principal_id"], "principal_id")
    _validate_uuid(copied["studio_session_id"], "studio_session_id")
    if not is_canonical_utc_timestamp(copied["confirmed_at"]):
        raise CompositorError("timestamp_invalid", "confirmed_at must be canonical UTC")
    target = copied["target_region"]
    target_bounds = {key: target[key] for key in ("left", "top", "right", "bottom")}
    _validate_bounds(target_bounds, width=32_768, height=32_768, label="target region")
    if target["width"] != target["right"] - target["left"] or target["height"] != (
        target["bottom"] - target["top"]
    ):
        raise CompositorError("target_region_mismatch", "target dimensions do not match bounds")
    if target["width"] * target["height"] > _MAX_PNG_PIXELS:
        raise CompositorError(
            "target_region_mismatch", "target region exceeds the compositor pixel bound"
        )
    measured_target = compute_target_region_id(target)
    if not hmac.compare_digest(target["target_region_id"], measured_target):
        raise CompositorError("identity_mismatch", "target_region_id does not match its document")
    if copied["preview_projection"] != _expected_preview(copied):
        raise CompositorError(
            "preview_mismatch", "preview projection does not mirror the confirmed crop and target"
        )
    measured_key = compute_confirmation_key(copied)
    if not hmac.compare_digest(copied["confirmation_key"], measured_key):
        raise CompositorError("identity_mismatch", "confirmation_key does not match its projection")
    measured_confirmation = compute_insert_compile_confirmation_id(copied)
    if not hmac.compare_digest(copied["insert_compile_confirmation_id"], measured_confirmation):
        raise CompositorError(
            "identity_mismatch", "insert confirmation id does not match its document"
        )


def seal_insert_compile_confirmation(draft: Mapping[str, Any]) -> InsertCompileConfirmation:
    document = _copy_document(draft)
    if document.get("schema_version") != INSERT_CONFIRMATION_VERSION:
        raise CompositorError(
            "schema_invalid", f"confirmation must use {INSERT_CONFIRMATION_VERSION}"
        )
    for derived in ("confirmation_key", "insert_compile_confirmation_id"):
        if derived in document:
            raise CompositorError("identity_prefilled", f"confirmation draft must omit {derived}")
    target = document.get("target_region")
    if not isinstance(target, dict):
        raise CompositorError("schema_invalid", "confirmation target_region must be an object")
    if "target_region_id" not in target:
        target["target_region_id"] = compute_target_region_id(target)
    if "preview_projection" not in document:
        document["preview_projection"] = _expected_preview(document)
    document["confirmation_key"] = compute_confirmation_key(document)
    document["insert_compile_confirmation_id"] = compute_insert_compile_confirmation_id(document)
    artifact = from_json_dict(document)
    if not isinstance(artifact, InsertCompileConfirmation):
        raise CompositorError("contract_mismatch", "sealed another compositor artifact branch")
    return artifact


def resolve_insert_confirmation_replay(
    existing: InsertCompileConfirmation,
    candidate: InsertCompileConfirmation,
) -> InsertCompileConfirmation:
    """Return the first artifact for one exact user/session/preview confirmation key.

    ``confirmed_at`` is intentionally outside the idempotency key.  A later retry of the same
    displayed projection therefore recovers the first immutable confirmation rather than minting
    a second insert lineage.  A cryptographic-key collision over different projections fails.
    """

    existing_document = to_json_dict(existing)
    candidate_document = to_json_dict(candidate)
    if existing.confirmation_key != candidate.confirmation_key:
        return candidate
    key_fields = (
        "principal_id",
        "studio_session_id",
        "intent_packet_id",
        "preview_projection",
    )
    if all(existing_document[field] == candidate_document[field] for field in key_fields):
        return existing
    raise CompositorError(
        "replay_conflict", "one confirmation key names different preview projections"
    )


def compute_insert_sha256(projection: Mapping[str, Any], rgb_bytes: bytes) -> str:
    copied = _copy_document(projection)
    if set(copied) != set(_INSERT_IDENTITY_FIELDS):
        raise CompositorError(
            "identity_invalid", "insert identity projection does not match the registered fields"
        )
    return _binary_identity(
        domain=INSERT_VERSION,
        projection={field: copied[field] for field in _INSERT_IDENTITY_FIELDS},
        payload=_require_bytes(rgb_bytes, "rgb_bytes"),
    )


def validate_insert_artifact(document: Mapping[str, Any], rgb_bytes: bytes) -> None:
    copied = _copy_document(document)
    payload = _require_bytes(rgb_bytes, "rgb_bytes")
    _validate_schema(copied, INSERT_VERSION)
    if copied["byte_count"] != len(payload) or copied["byte_count"] != (
        copied["width"] * copied["height"] * 3
    ):
        raise CompositorError("shape_mismatch", "insert byte count does not match RGB dimensions")
    if copied["width"] * copied["height"] > _MAX_PNG_PIXELS:
        raise CompositorError("shape_mismatch", "insert exceeds the compositor pixel bound")
    _validate_bounds(copied["crop"], width=32_768, height=32_768, label="insert crop")
    measured = compute_insert_sha256(
        {field: copied[field] for field in _INSERT_IDENTITY_FIELDS}, payload
    )
    if not hmac.compare_digest(copied["insert_sha256"], measured):
        raise CompositorError("identity_mismatch", "insert_sha256 does not match its RGB bytes")


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _canonical_png_bytes(width: int, height: int, rgb_bytes: bytes) -> bytes:
    row_bytes = width * 3
    filtered = bytearray(height * (row_bytes + 1))
    source_offset = target_offset = 0
    for _ in range(height):
        filtered[target_offset] = 0
        target_offset += 1
        filtered[target_offset : target_offset + row_bytes] = rgb_bytes[
            source_offset : source_offset + row_bytes
        ]
        source_offset += row_bytes
        target_offset += row_bytes
    compressed = bytearray(b"\x78\x01")
    for offset in range(0, len(filtered), 65_535):
        block = filtered[offset : offset + 65_535]
        compressed.append(1 if offset + len(block) == len(filtered) else 0)
        compressed.extend(struct.pack("<H", len(block)))
        compressed.extend(struct.pack("<H", len(block) ^ 0xFFFF))
        compressed.extend(block)
    compressed.extend(struct.pack(">I", zlib.adler32(filtered) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", bytes(compressed))
        + _png_chunk(b"IEND", b"")
    )


def _canonical_png_byte_count(width: int, height: int) -> int:
    filtered_bytes = height * (width * 3 + 1)
    stored_blocks = (filtered_bytes + 65_534) // 65_535
    return filtered_bytes + 5 * stored_blocks + 63


def encode_canonical_png(
    *,
    width: int,
    height: int,
    rgb_bytes: bytes,
    encoder_manifest: PngEncoderManifest = DEFAULT_PNG_ENCODER_MANIFEST,
) -> CanonicalPngArtifact:
    if not isinstance(encoder_manifest, PngEncoderManifest) or (
        encoder_manifest != DEFAULT_PNG_ENCODER_MANIFEST
    ):
        raise CompositorError(
            "encoder_manifest_mismatch", "PNG encoder manifest is not the registered manifest"
        )
    if type(width) is not int or type(height) is not int:
        raise CompositorError("shape_mismatch", "PNG dimensions must be plain integers")
    if not (1 <= width <= encoder_manifest.max_dimension) or not (
        1 <= height <= encoder_manifest.max_dimension
    ):
        raise CompositorError("shape_mismatch", "PNG dimensions are outside the encoder bounds")
    if width * height > encoder_manifest.max_pixels:
        raise CompositorError("shape_mismatch", "PNG pixel count exceeds the encoder bound")
    payload = _require_bytes(rgb_bytes, "rgb_bytes")
    if len(payload) != width * height * 3:
        raise CompositorError("shape_mismatch", "RGB byte count does not match PNG dimensions")
    encoded = _canonical_png_bytes(width, height, payload)
    document = {
        "schema_version": PNG_VERSION,
        "encoder_revision": PNG_ENCODER_REVISION,
        "deflate_revision": DEFLATE_REVISION,
        "width": width,
        "height": height,
        "mode": "RGB",
        "byte_count": len(encoded),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "content_ref": blake3(encoded).hexdigest(),
    }
    artifact = from_json_dict(document, payload=encoded)
    if not isinstance(artifact, CanonicalPngArtifact):
        raise CompositorError("contract_mismatch", "encoded another compositor artifact branch")
    return artifact


def _read_png_chunk(
    payload: bytes,
    position: int,
    expected_kind: bytes,
    *,
    expected_length: int | None = None,
) -> tuple[bytes, int]:
    if position + 12 > len(payload):
        raise CompositorError("png_profile_mismatch", "PNG chunk framing is truncated")
    length = int.from_bytes(payload[position : position + 4], "big")
    kind = payload[position + 4 : position + 8]
    if kind != expected_kind or (expected_length is not None and length != expected_length):
        raise CompositorError("png_profile_mismatch", "canonical PNG chunk profile drifted")
    data_start = position + 8
    data_end = data_start + length
    crc_end = data_end + 4
    if crc_end > len(payload):
        raise CompositorError("png_profile_mismatch", "PNG chunk payload is truncated")
    data = payload[data_start:data_end]
    measured_crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    if int.from_bytes(payload[data_end:crc_end], "big") != measured_crc:
        raise CompositorError("png_profile_mismatch", "PNG chunk CRC does not match")
    return data, crc_end


def _decode_canonical_png(document: Mapping[str, Any], png_bytes: bytes) -> bytes:
    payload = _require_bytes(png_bytes, "png_bytes")
    if not payload.startswith(_PNG_SIGNATURE):
        raise CompositorError("png_profile_mismatch", "canonical PNG signature is absent")
    ihdr, position = _read_png_chunk(payload, len(_PNG_SIGNATURE), b"IHDR", expected_length=13)
    compressed, position = _read_png_chunk(payload, position, b"IDAT")
    iend, position = _read_png_chunk(payload, position, b"IEND", expected_length=0)
    if position != len(payload):
        raise CompositorError("png_profile_mismatch", "canonical PNG has trailing bytes")
    if iend or len(ihdr) != 13:
        raise CompositorError("png_profile_mismatch", "canonical PNG terminal/header drifted")
    expected_ihdr = struct.pack(">IIBBBBB", document["width"], document["height"], 8, 2, 0, 0, 0)
    if ihdr != expected_ihdr or not compressed.startswith(b"\x78\x01"):
        raise CompositorError("png_profile_mismatch", "canonical PNG coding profile drifted")
    row_bytes = document["width"] * 3
    expected_filtered_bytes = document["height"] * (row_bytes + 1)
    try:
        decoder = zlib.decompressobj()
        filtered = decoder.decompress(compressed, expected_filtered_bytes + 1)
    except zlib.error as error:
        raise CompositorError("png_profile_mismatch", "canonical PNG Deflate is invalid") from error
    if (
        len(filtered) != expected_filtered_bytes
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise CompositorError("png_profile_mismatch", "canonical PNG scanline size drifted")
    rgb = bytearray(document["width"] * document["height"] * 3)
    for row in range(document["height"]):
        start = row * (row_bytes + 1)
        if filtered[start] != 0:
            raise CompositorError("png_profile_mismatch", "canonical PNG filter is not zero")
        rgb[row * row_bytes : (row + 1) * row_bytes] = filtered[start + 1 : start + 1 + row_bytes]
    result = bytes(rgb)
    if _canonical_png_bytes(document["width"], document["height"], result) != payload:
        raise CompositorError("png_profile_mismatch", "PNG is not the byte-frozen encoding")
    return result


def validate_png_artifact(document: Mapping[str, Any], png_bytes: bytes) -> None:
    copied = _copy_document(document)
    payload = _require_bytes(png_bytes, "png_bytes")
    _validate_schema(copied, PNG_VERSION)
    if copied["width"] * copied["height"] > _MAX_PNG_PIXELS:
        raise CompositorError("shape_mismatch", "PNG exceeds the encoder pixel bound")
    if copied["byte_count"] != len(payload):
        raise CompositorError("byte_count_mismatch", "PNG byte_count does not match its bytes")
    if len(payload) != _canonical_png_byte_count(copied["width"], copied["height"]):
        raise CompositorError(
            "byte_count_mismatch", "PNG byte count differs from the canonical stored profile"
        )
    if not hmac.compare_digest(copied["content_sha256"], hashlib.sha256(payload).hexdigest()):
        raise CompositorError("identity_mismatch", "PNG content SHA-256 does not match")
    if not hmac.compare_digest(copied["content_ref"], blake3(payload).hexdigest()):
        raise CompositorError("identity_mismatch", "PNG ContentRef does not match")
    _decode_canonical_png(copied, payload)


def _compositor_replay_projection(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "producer_kind": document["producer_kind"],
        "role": document["role"],
        "intent_packet_id": document["intent_packet_id"],
        "producer": copy.deepcopy(document["producer"]),
        "lineage": copy.deepcopy(document["lineage"]),
    }


def compute_compositor_replay_key(document: Mapping[str, Any]) -> str:
    try:
        return compute_projection_identity(
            _compositor_replay_projection(document),
            domain_tag=COMPOSITOR_REPLAY_KEY_VERSION,
        )
    except (KeyError, ContractIdentityError) as error:
        raise CompositorError(
            "identity_invalid", f"cannot derive compositor replay key: {error}"
        ) from error


def compute_compositor_occurrence_id(document: Mapping[str, Any]) -> str:
    copied = _copy_document(document)
    if copied.get("schema_version") != COMPOSITOR_OCCURRENCE_VERSION:
        raise CompositorError("identity_invalid", "occurrence uses an unsupported version")
    if "output_occurrence_id" not in copied:
        copied["output_occurrence_id"] = "0" * 64
    try:
        return compute_document_identity(
            copied,
            schema_version=COMPOSITOR_OCCURRENCE_VERSION,
            identity_field="output_occurrence_id",
        )
    except ContractIdentityError as error:
        raise CompositorError("identity_invalid", str(error)) from error


def validate_compositor_output_occurrence(
    document: Mapping[str, Any],
    *,
    png_bytes: bytes | None = None,
    output_raster: CanonicalRasterArtifact | None = None,
) -> None:
    copied = _copy_document(document)
    _validate_schema(copied, COMPOSITOR_OCCURRENCE_VERSION)
    measured_key = compute_compositor_replay_key(copied)
    if not hmac.compare_digest(copied["compositor_replay_key"], measured_key):
        raise CompositorError("identity_mismatch", "compositor replay key does not match inputs")
    measured_occurrence = compute_compositor_occurrence_id(copied)
    if not hmac.compare_digest(copied["output_occurrence_id"], measured_occurrence):
        raise CompositorError(
            "identity_mismatch", "compositor occurrence id does not match its document"
        )
    original = copied["original"]
    raster = copied["output_raster"]
    if (
        raster["source_content_sha256"] != original["content_sha256"]
        or raster["width"] != original["width"]
        or raster["height"] != original["height"]
        or raster["mode"] != "RGB"
        or raster["compiler_revision"] != RASTER_COMPILER_REVISION
        or raster["byte_count"] != raster["width"] * raster["height"] * 3
        or original["width"] * original["height"] > _MAX_PNG_PIXELS
        or original["byte_count"]
        != _canonical_png_byte_count(original["width"], original["height"])
    ):
        raise CompositorError(
            "output_binding_mismatch", "compositor PNG and output raster descriptors diverge"
        )
    if png_bytes is not None:
        validate_png_artifact(original, png_bytes)
        try:
            replay = compile_canonical_raster(
                png_bytes,
                source_content_sha256=original["content_sha256"],
            )
        except LocalityError as error:
            raise CompositorError("output_binding_mismatch", str(error)) from error
        if _raster_document(replay) != raster:
            raise CompositorError(
                "output_binding_mismatch", "canonical PNG does not replay to the output raster"
            )
    if output_raster is not None:
        raster_document = _raster_document(output_raster)
        if raster_document != raster:
            raise CompositorError(
                "output_binding_mismatch", "output raster does not match occurrence descriptor"
            )


def seal_compositor_output_occurrence(
    draft: Mapping[str, Any],
    *,
    png_bytes: bytes,
    output_raster: CanonicalRasterArtifact,
) -> CompositorOutputOccurrence:
    document = _copy_document(draft)
    for derived in ("compositor_replay_key", "output_occurrence_id"):
        if derived in document:
            raise CompositorError("identity_prefilled", f"occurrence draft must omit {derived}")
    document["compositor_replay_key"] = compute_compositor_replay_key(document)
    document["output_occurrence_id"] = compute_compositor_occurrence_id(document)
    validate_compositor_output_occurrence(
        document, png_bytes=png_bytes, output_raster=output_raster
    )
    artifact = from_json_dict(document)
    if not isinstance(artifact, CompositorOutputOccurrence):
        raise CompositorError("contract_mismatch", "sealed another compositor artifact branch")
    return artifact


def validate_compositor_artifact(document: Mapping[str, Any], payload: bytes | None = None) -> None:
    copied = _copy_document(document)
    version = copied.get("schema_version")
    if version == INSERT_CONFIRMATION_VERSION:
        if payload is not None:
            raise CompositorError("payload_invalid", "confirmation has no binary payload")
        validate_insert_compile_confirmation(copied)
    elif version == INSERT_VERSION:
        if payload is None:
            raise CompositorError("payload_required", "insert RGB bytes are required")
        validate_insert_artifact(copied, payload)
    elif version == PNG_VERSION:
        if payload is None:
            raise CompositorError("payload_required", "canonical PNG bytes are required")
        validate_png_artifact(copied, payload)
    elif version == COMPOSITOR_OCCURRENCE_VERSION:
        if payload is not None:
            validate_compositor_output_occurrence(copied, png_bytes=payload)
        else:
            validate_compositor_output_occurrence(copied)
    else:
        raise CompositorError(
            "unsupported_schema_version", f"unsupported compositor artifact version: {version!r}"
        )


def from_json_dict(
    document: Mapping[str, Any], *, payload: bytes | None = None
) -> CompositorArtifact:
    copied = _copy_document(document)
    validate_compositor_artifact(copied, payload)
    version = copied["schema_version"]
    if version == INSERT_CONFIRMATION_VERSION:
        values = dict(copied)
        for field in ("crop", "target_region", "compiler_policy", "preview_projection"):
            values[field] = _freeze_json(values[field])
        return InsertCompileConfirmation(**values)
    if version == INSERT_VERSION:
        values = dict(copied)
        values["crop"] = _freeze_json(values["crop"])
        return CanonicalInsertArtifact(**values, rgb_bytes=_require_bytes(payload, "rgb_bytes"))
    if version == PNG_VERSION:
        return CanonicalPngArtifact(**copied, png_bytes=_require_bytes(payload, "png_bytes"))
    values = dict(copied)
    for field in ("original", "output_raster", "admission", "producer", "lineage"):
        values[field] = _freeze_json(values[field])
    return CompositorOutputOccurrence(**values)


def to_json_dict(artifact: CompositorArtifact) -> dict[str, Any]:
    if not isinstance(
        artifact,
        (
            InsertCompileConfirmation,
            CanonicalInsertArtifact,
            CanonicalPngArtifact,
            CompositorOutputOccurrence,
        ),
    ):
        raise CompositorError("contract_mismatch", "value is not a compositor artifact")
    excluded = {"rgb_bytes", "png_bytes"}
    try:
        document = {
            field: _thaw_json(getattr(artifact, field))
            for field in artifact.__dataclass_fields__  # type: ignore[attr-defined]
            if field not in excluded
        }
        payload: bytes | None = None
        if isinstance(artifact, CanonicalInsertArtifact):
            payload = artifact.rgb_bytes
        elif isinstance(artifact, CanonicalPngArtifact):
            payload = artifact.png_bytes
    except Exception:
        raise CompositorError(
            "contract_mismatch", "compositor artifact fields cannot be projected"
        ) from None
    validate_compositor_artifact(document, payload)
    return document


def _provider_occurrence_document(
    occurrence: OutputOccurrence | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(occurrence, OutputOccurrence):
        try:
            return provider_to_json(occurrence)
        except Exception:
            raise CompositorError(
                "provider_contract_mismatch", "raw provider occurrence is invalid"
            ) from None
    copied = _copy_document(occurrence)
    try:
        validate_provider_artifact(copied)
    except Exception:
        raise CompositorError(
            "provider_contract_mismatch", "raw provider occurrence is invalid"
        ) from None
    if copied.get("schema_version") != PROVIDER_OUTPUT_VERSION:
        raise CompositorError("provider_contract_mismatch", "raw occurrence uses another schema")
    return copied


def _judgment_document(
    judgment: ConstraintVerificationJudgment,
) -> dict[str, Any]:
    if not isinstance(judgment, ConstraintVerificationJudgment):
        raise CompositorError("judgment_mismatch", "expected a constraint-verification judgment")
    try:
        return judgment_to_json(judgment)
    except Exception:
        raise CompositorError("judgment_mismatch", "constraint judgment is invalid") from None


def _validate_raw_compiler_inputs(
    raw_output_raster: CanonicalRasterArtifact,
    *,
    raw_output_occurrence: OutputOccurrence | Mapping[str, Any],
    raw_structural_judgment: ConstraintVerificationJudgment,
    raw_locality_judgment: ConstraintVerificationJudgment,
    confirmation: InsertCompileConfirmation,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    raster = _raster_document(raw_output_raster)
    occurrence = _provider_occurrence_document(raw_output_occurrence)
    confirmation_document = to_json_dict(confirmation)
    structural = _judgment_document(raw_structural_judgment)
    locality = _judgment_document(raw_locality_judgment)
    expected_subject = {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": occurrence["output_occurrence_id"],
    }
    if occurrence["producer_kind"] != "generator_raw" or occurrence["admission"] != {
        "state": "eligible",
        "rejection_reasons": [],
    }:
        raise CompositorError(
            "raw_output_ineligible", "insert compiler requires one eligible raw generator output"
        )
    if (
        raster["source_content_sha256"] != occurrence["original"]["content_sha256"]
        or raster["width"] != occurrence["original"]["width"]
        or raster["height"] != occurrence["original"]["height"]
    ):
        raise CompositorError(
            "raw_output_mismatch", "raw raster does not bind the provider output occurrence"
        )
    if (
        structural["authority"].get("schema_version") != STRUCTURAL_VERIFIER_VERSION
        or structural["subject"] != expected_subject
        or structural["evidence_ref"]
        != {"kind": "artifact", "artifact_id": occurrence["output_occurrence_id"]}
        or structural["authority"]["output_content_sha256"]
        != occurrence["original"]["content_sha256"]
        or structural["authority"]["output_raster_sha256"] != raster["raster_sha256"]
        or structural["authority"]["decoder_revision"] != RASTER_COMPILER_REVISION
        or occurrence["media_validation"]["decoder_revision"] != RASTER_COMPILER_REVISION
        or raster["compiler_revision"] != RASTER_COMPILER_REVISION
    ):
        raise CompositorError("judgment_mismatch", "structural judgment does not bind raw output")
    measurements = structural["result"].get("measurements", {})
    if (
        measurements.get("container_decoded") is not True
        or measurements.get("canonical_raster_compiled") is not True
        or measurements.get("frame_count") != 1
        or measurements.get("output_width") != raster["width"]
        or measurements.get("output_height") != raster["height"]
        or measurements.get("output_mode") != occurrence["media_validation"]["measured_mode"]
        or measurements.get("opaque") is not True
    ):
        raise CompositorError(
            "judgment_mismatch", "structural measurements do not describe the raw raster"
        )
    state = structural["result"]["state"]
    reason = structural["result"].get("reason")
    locality_state = locality["result"]["state"]
    if state == "pass":
        if (
            locality["authority"].get("schema_version") != EXACT_LOCALITY_VERIFIER_VERSION
            or locality["subject"] != expected_subject
            or locality["evidence_ref"]
            != {"kind": "artifact", "artifact_id": occurrence["output_occurrence_id"]}
            or locality_state not in {"pass", "fail"}
            or locality["authority"]["output_raster_sha256"] != raster["raster_sha256"]
        ):
            raise CompositorError(
                "judgment_mismatch", "structural pass requires a measured exact-locality result"
            )
    elif state == "fail" and reason == "dimension_mismatch":
        if locality["evidence_ref"] != {
            "kind": "artifact",
            "artifact_id": structural["evidence_id"],
        }:
            raise CompositorError(
                "judgment_mismatch", "locality not-run does not reference its structural blocker"
            )
        try:
            validate_locality_blocking_pair(structural, locality)
        except JudgmentError as error:
            raise CompositorError("judgment_mismatch", str(error)) from error
    else:
        raise CompositorError(
            "raw_structure_ineligible",
            "only structural pass or repairable dimension mismatch can feed the insert compiler",
        )
    target = confirmation_document["target_region"]
    expected = {
        "intent_packet_id": occurrence["intent_packet_id"],
        "raw_output_occurrence_id": occurrence["output_occurrence_id"],
        "raw_output_raster_sha256": raster["raster_sha256"],
        "raw_structural_evidence_id": structural["evidence_id"],
        "raw_locality_evidence_id": locality["evidence_id"],
    }
    for field, value in expected.items():
        if confirmation_document[field] != value:
            raise CompositorError("confirmation_mismatch", f"confirmation {field} drifted")
    if (
        structural["authority"]["source_raster_sha256"] != target["source_raster_sha256"]
        or locality["authority"]["source_raster_sha256"] != target["source_raster_sha256"]
        or locality["authority"]["mask_sha256"] != target["mask_sha256"]
    ):
        raise CompositorError(
            "confirmation_mismatch", "raw judgments do not bind the confirmed target region"
        )
    return occurrence, structural, locality, confirmation_document


def _nearest_source_offset(target_index: int, source_span: int, target_span: int) -> int:
    """Map one target coordinate to the registered center-sampled nearest source offset."""

    return min(source_span - 1, ((2 * target_index + 1) * source_span) // (2 * target_span))


def compile_raw_crop_nearest(
    raw_output_raster: CanonicalRasterArtifact,
    *,
    raw_output_occurrence: OutputOccurrence | Mapping[str, Any],
    raw_structural_judgment: ConstraintVerificationJudgment,
    raw_locality_judgment: ConstraintVerificationJudgment,
    confirmation: InsertCompileConfirmation,
) -> CanonicalInsertArtifact:
    occurrence, structural, locality, confirmed = _validate_raw_compiler_inputs(
        raw_output_raster,
        raw_output_occurrence=raw_output_occurrence,
        raw_structural_judgment=raw_structural_judgment,
        raw_locality_judgment=raw_locality_judgment,
        confirmation=confirmation,
    )
    crop = confirmed["crop"]
    _validate_bounds(
        crop, width=raw_output_raster.width, height=raw_output_raster.height, label="raw crop"
    )
    crop_width = crop["right"] - crop["left"]
    crop_height = crop["bottom"] - crop["top"]
    target = confirmed["target_region"]
    width = target["width"]
    height = target["height"]
    result = bytearray(width * height * 3)
    raw = raw_output_raster.rgb_bytes
    for target_y in range(height):
        source_y = crop["top"] + _nearest_source_offset(target_y, crop_height, height)
        for target_x in range(width):
            source_x = crop["left"] + _nearest_source_offset(target_x, crop_width, width)
            source_offset = (source_y * raw_output_raster.width + source_x) * 3
            target_offset = (target_y * width + target_x) * 3
            result[target_offset : target_offset + 3] = raw[source_offset : source_offset + 3]
    rgb = bytes(result)
    document: dict[str, Any] = {
        "schema_version": INSERT_VERSION,
        "insert_sha256": "0" * 64,
        "compiler_revision": INSERT_COMPILER_REVISION,
        "insert_compile_confirmation_id": confirmed["insert_compile_confirmation_id"],
        "intent_packet_id": confirmed["intent_packet_id"],
        "raw_output_occurrence_id": occurrence["output_occurrence_id"],
        "raw_output_raster_sha256": raw_output_raster.raster_sha256,
        "raw_structural_evidence_id": structural["evidence_id"],
        "raw_locality_evidence_id": locality["evidence_id"],
        "crop": copy.deepcopy(crop),
        "target_region_id": target["target_region_id"],
        "width": width,
        "height": height,
        "mode": "RGB",
        "byte_count": len(rgb),
    }
    document["insert_sha256"] = compute_insert_sha256(
        {field: document[field] for field in _INSERT_IDENTITY_FIELDS}, rgb
    )
    artifact = from_json_dict(document, payload=rgb)
    if not isinstance(artifact, CanonicalInsertArtifact):
        raise CompositorError("contract_mismatch", "compiled another compositor artifact branch")
    return artifact


def _packet_document(packet: IntentPacket | Mapping[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(packet, IntentPacket):
            return intent_packet_to_json(packet)
        copied = _copy_document(packet)
        validate_intent_packet(copied)
        return copied
    except CompositorError:
        raise
    except Exception:
        raise CompositorError("intent_packet_mismatch", "intent packet is invalid") from None


def _provider_bundle_documents(
    artifacts: Iterable[dict[str, Any] | ProviderArtifact],
    *,
    packet: IntentPacket | dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    materialized_list: list[dict[str, Any] | ProviderArtifact] = []
    try:
        for index, artifact in enumerate(artifacts):
            if index >= _MAX_PROVIDER_ARTIFACTS:
                raise CompositorError(
                    "provider_bundle_invalid",
                    f"provider bundle accepts at most {_MAX_PROVIDER_ARTIFACTS} artifacts",
                )
            materialized_list.append(artifact)
    except CompositorError:
        raise
    except Exception:
        raise CompositorError(
            "provider_bundle_invalid", "provider bundle iteration failed"
        ) from None
    materialized = tuple(materialized_list)
    try:
        validate_artifact_bundle(materialized, intent_packet=packet)
    except Exception:
        raise CompositorError("provider_bundle_invalid", "provider bundle is invalid") from None
    documents: list[dict[str, Any]] = []
    for artifact in materialized:
        if isinstance(artifact, dict):
            copied = _copy_document(artifact)
            try:
                validate_provider_artifact(copied)
            except Exception:
                raise CompositorError(
                    "provider_bundle_invalid", "provider bundle artifact is invalid"
                ) from None
            documents.append(copied)
        else:
            try:
                documents.append(provider_to_json(artifact))
            except Exception:
                raise CompositorError(
                    "provider_bundle_invalid", "provider bundle artifact is invalid"
                ) from None
    return tuple(documents)


def _seal_judgment(draft: dict[str, Any]) -> ConstraintVerificationJudgment:
    document = copy.deepcopy(draft)
    document["evidence_id"] = "0" * 64
    try:
        document["evidence_id"] = compute_document_identity(
            document,
            schema_version=JUDGMENT_VERSION,
            identity_field="evidence_id",
        )
        judgment = judgment_from_json(document)
    except (ContractIdentityError, JudgmentError) as error:
        raise CompositorError("judgment_mismatch", str(error)) from error
    if not isinstance(judgment, ConstraintVerificationJudgment):
        raise CompositorError("judgment_mismatch", "compositor minted another judgment kind")
    return judgment


def verify_compositor_output_structure(
    source_raster: CanonicalRasterArtifact,
    *,
    compositor_occurrence: CompositorOutputOccurrence | Mapping[str, Any],
    output_raster: CanonicalRasterArtifact,
    output_bytes: bytes,
) -> StructuralVerification:
    source = _raster_document(source_raster)
    output = _raster_document(output_raster)
    if isinstance(compositor_occurrence, CompositorOutputOccurrence):
        occurrence = to_json_dict(compositor_occurrence)
    else:
        occurrence = _copy_document(compositor_occurrence)
    validate_compositor_output_occurrence(
        occurrence, png_bytes=output_bytes, output_raster=output_raster
    )
    if occurrence["lineage"]["source_raster_sha256"] != source["raster_sha256"]:
        raise CompositorError(
            "output_binding_mismatch", "compositor occurrence does not bind the source raster"
        )
    if source["width"] != output["width"] or source["height"] != output["height"]:
        raise CompositorError(
            "output_binding_mismatch", "compositor output dimensions differ from source"
        )
    subject = {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": occurrence["output_occurrence_id"],
    }
    authority = {
        "schema_version": STRUCTURAL_VERIFIER_VERSION,
        "input_digest": compute_structural_input_digest(
            source_raster_sha256=source["raster_sha256"],
            output_content_sha256=occurrence["original"]["content_sha256"],
        ),
        "source_raster_sha256": source["raster_sha256"],
        "output_content_sha256": occurrence["original"]["content_sha256"],
        "output_raster_sha256": output["raster_sha256"],
        "decoder_revision": RASTER_COMPILER_REVISION,
    }
    judgment = _seal_judgment(
        {
            "schema_version": JUDGMENT_VERSION,
            "kind": "constraint_verification",
            "subject": subject,
            "result": {
                "state": "pass",
                "measurements": {
                    "source_width": source["width"],
                    "source_height": source["height"],
                    "container_decoded": True,
                    "canonical_raster_compiled": True,
                    "frame_count": 1,
                    "output_width": output["width"],
                    "output_height": output["height"],
                    "output_mode": "RGB",
                    "opaque": True,
                },
            },
            "authority": authority,
            "evidence_ref": {
                "kind": "artifact",
                "artifact_id": occurrence["output_occurrence_id"],
            },
        }
    )
    return StructuralVerification(judgment=judgment, output_raster=output_raster)


def _verify_compositor_locality(
    source_raster: CanonicalRasterArtifact,
    output_raster: CanonicalRasterArtifact,
    mask: CanonicalMaskArtifact,
    *,
    occurrence: CompositorOutputOccurrence,
    structural: StructuralVerification,
) -> ConstraintVerificationJudgment:
    source = _raster_document(source_raster)
    output = _raster_document(output_raster)
    mask_document = _mask_document(mask)
    occurrence_document = to_json_dict(occurrence)
    structural_document = _judgment_document(structural.judgment)
    expected_subject = {
        "kind": "selectable_output_occurrence",
        "output_occurrence_id": occurrence_document["output_occurrence_id"],
    }
    if (
        structural_document["result"]["state"] != "pass"
        or structural_document["subject"] != expected_subject
        or structural_document["authority"]["source_raster_sha256"] != source["raster_sha256"]
        or structural_document["authority"]["output_raster_sha256"] != output["raster_sha256"]
    ):
        raise CompositorError(
            "judgment_mismatch", "compositor structural pass does not bind output"
        )
    if (
        source["width"] != output["width"]
        or source["height"] != output["height"]
        or mask_document["width"] != source["width"]
        or mask_document["height"] != source["height"]
        or mask_document["source_raster_sha256"] != source["raster_sha256"]
    ):
        raise CompositorError("contract_mismatch", "source, output, and mask dimensions diverge")
    try:
        changed, maximum = _measure_outside_mask_rgb_exact(
            source_raster.rgb_bytes,
            output_raster.rgb_bytes,
            mask.mask_bytes,
        )
    except LocalityError as error:
        raise CompositorError("contract_mismatch", str(error)) from error
    state = "pass" if changed == 0 and maximum == 0 else "fail"
    return _seal_judgment(
        {
            "schema_version": JUDGMENT_VERSION,
            "kind": "constraint_verification",
            "subject": expected_subject,
            "result": {
                "state": state,
                "measurements": {
                    "protected_pixel_count": mask.protected_count,
                    "changed_pixel_count": changed,
                    "max_abs_channel_error": maximum,
                },
            },
            "authority": {
                "schema_version": EXACT_LOCALITY_VERIFIER_VERSION,
                "input_digest": compute_exact_locality_input_digest(
                    source_raster_sha256=source["raster_sha256"],
                    output_raster_sha256=output["raster_sha256"],
                    mask_sha256=mask_document["mask_sha256"],
                ),
                "source_raster_sha256": source["raster_sha256"],
                "output_raster_sha256": output["raster_sha256"],
                "mask_sha256": mask_document["mask_sha256"],
            },
            "evidence_ref": {
                "kind": "artifact",
                "artifact_id": occurrence_document["output_occurrence_id"],
            },
        }
    )


def compose_source_backed_rect_replace(
    source_raster: CanonicalRasterArtifact,
    insert: CanonicalInsertArtifact,
    mask: CanonicalMaskArtifact,
    *,
    intent_packet: IntentPacket | dict[str, Any],
    provider_artifacts: Iterable[dict[str, Any] | ProviderArtifact],
    raw_output_occurrence: OutputOccurrence | Mapping[str, Any],
    raw_output_bytes: bytes,
    raw_output_raster: CanonicalRasterArtifact,
    raw_structural_judgment: ConstraintVerificationJudgment,
    raw_locality_judgment: ConstraintVerificationJudgment,
    confirmation: InsertCompileConfirmation,
) -> CompositorResult:
    source = _raster_document(source_raster)
    mask_document = _mask_document(mask)
    insert_document = to_json_dict(insert)
    confirmation_document = to_json_dict(confirmation)
    packet_document = _packet_document(intent_packet)
    bundle = _provider_bundle_documents(provider_artifacts, packet=packet_document)
    raw_occurrence = _provider_occurrence_document(raw_output_occurrence)
    bundled_raw = [
        item
        for item in bundle
        if item.get("schema_version") == PROVIDER_OUTPUT_VERSION
        and item.get("output_occurrence_id") == raw_occurrence["output_occurrence_id"]
    ]
    if len(bundled_raw) != 1 or bundled_raw[0] != raw_occurrence:
        raise CompositorError(
            "provider_bundle_invalid", "raw occurrence is not the exact eligible bundled ancestor"
        )
    if raw_occurrence["admission"] != {"state": "eligible", "rejection_reasons": []}:
        raise CompositorError(
            "raw_output_ineligible", "rejected provider ancestry cannot feed the compositor"
        )
    receipts = [
        item
        for item in bundle
        if item.get("schema_version") == PROVIDER_RECEIPT_VERSION
        and item.get("provider_receipt_id") == raw_occurrence["provider_receipt_id"]
    ]
    if len(receipts) != 1:
        raise CompositorError(
            "provider_bundle_invalid", "raw occurrence has no exact bundled provider receipt"
        )
    payload = _require_bytes(raw_output_bytes, "raw_output_bytes")
    try:
        replayed_structure = verify_output_structure(
            source_raster,
            provider_receipt=receipts[0],
            output_index=raw_occurrence["output_index"],
            output_bytes=payload,
            output_occurrence=raw_occurrence,
        )
    except LocalityError as error:
        raise CompositorError("judgment_mismatch", "raw provider payload replay failed") from error
    if replayed_structure.output_raster != raw_output_raster or _judgment_document(
        replayed_structure.judgment
    ) != _judgment_document(raw_structural_judgment):
        raise CompositorError(
            "judgment_mismatch",
            "raw raster and structural judgment do not replay from provider bytes",
        )
    raw_structural_document = _judgment_document(raw_structural_judgment)
    raw_measurements = raw_structural_document["result"]["measurements"]
    if (
        raw_measurements["source_width"] != source_raster.width
        or raw_measurements["source_height"] != source_raster.height
    ):
        raise CompositorError(
            "judgment_mismatch", "raw structural judgment does not describe the source raster"
        )
    raw_dimensions_match = (
        source_raster.width == raw_output_raster.width
        and source_raster.height == raw_output_raster.height
    )
    raw_state = raw_structural_document["result"]["state"]
    raw_reason = raw_structural_document["result"].get("reason")
    if raw_dimensions_match != (raw_state == "pass") or (
        not raw_dimensions_match and raw_reason != "dimension_mismatch"
    ):
        raise CompositorError(
            "judgment_mismatch", "raw structural verdict conflicts with measured dimensions"
        )
    if raw_state == "pass":
        try:
            measured_raw_locality = verify_outside_mask_rgb_exact(
                source_raster,
                raw_output_raster,
                mask,
                output_occurrence=raw_output_occurrence,
                structural_pass=raw_structural_judgment,
            )
        except LocalityError as error:
            raise CompositorError("judgment_mismatch", str(error)) from error
    else:
        try:
            measured_raw_locality = build_locality_not_run(
                replayed_structure.judgment,
                mask,
            )
        except LocalityError as error:
            raise CompositorError("judgment_mismatch", str(error)) from error
    if _judgment_document(measured_raw_locality) != _judgment_document(raw_locality_judgment):
        raise CompositorError(
            "judgment_mismatch",
            "raw exact-locality judgment does not replay from source, output, and mask",
        )
    replay_insert = compile_raw_crop_nearest(
        raw_output_raster,
        raw_output_occurrence=raw_output_occurrence,
        raw_structural_judgment=raw_structural_judgment,
        raw_locality_judgment=raw_locality_judgment,
        confirmation=confirmation,
    )
    if replay_insert != insert:
        raise CompositorError("insert_mismatch", "insert does not replay from confirmed raw inputs")
    operation = packet_document["operation"]["payload"]
    region = operation["region"]
    bounds = {key: region[key] for key in ("left", "top", "right", "bottom")}
    target = confirmation_document["target_region"]
    if operation["source_raster"] != source or operation["mask"] != mask_document:
        raise CompositorError("intent_packet_mismatch", "packet source or mask descriptor drifted")
    if (
        operation["insert_compiler_policy"] != INSERT_COMPILER_POLICY
        or operation["compositor_policy"] != COMPOSITOR_POLICY
        or bounds != {key: target[key] for key in bounds}
        or target["source_raster_sha256"] != source["raster_sha256"]
        or target["mask_sha256"] != mask_document["mask_sha256"]
    ):
        raise CompositorError("intent_packet_mismatch", "packet region or producer policy drifted")
    packet_confirmation = packet_document["confirmation"]
    if (
        confirmation_document["principal_id"] != packet_confirmation["principal_id"]
        or confirmation_document["studio_session_id"] != packet_confirmation["studio_session_id"]
        or confirmation_document["intent_packet_id"] != packet_document["intent_packet_id"]
        or insert_document["insert_compile_confirmation_id"]
        != confirmation_document["insert_compile_confirmation_id"]
    ):
        raise CompositorError(
            "confirmation_mismatch",
            "insert confirmation does not bind the packet principal/session",
        )
    try:
        compiled_mask = compile_rectangle_mask(source_raster, **bounds)
    except LocalityError as error:
        raise CompositorError("mask_mismatch", str(error)) from error
    if (
        _mask_document(compiled_mask) != mask_document
        or compiled_mask.mask_bytes != mask.mask_bytes
    ):
        raise CompositorError("mask_mismatch", "mask is not the exact confirmed rectangle")
    width = bounds["right"] - bounds["left"]
    height = bounds["bottom"] - bounds["top"]
    if insert.width != width or insert.height != height:
        raise CompositorError("insert_mismatch", "insert dimensions differ from edit bounds")
    composed = bytearray(source_raster.rgb_bytes)
    insert_row_bytes = width * 3
    for target_y, source_y in enumerate(range(bounds["top"], bounds["bottom"])):
        destination = (source_y * source_raster.width + bounds["left"]) * 3
        insert_offset = target_y * insert_row_bytes
        composed[destination : destination + insert_row_bytes] = insert.rgb_bytes[
            insert_offset : insert_offset + insert_row_bytes
        ]
    composed_bytes = bytes(composed)
    png = encode_canonical_png(
        width=source_raster.width,
        height=source_raster.height,
        rgb_bytes=composed_bytes,
    )
    try:
        output_raster = compile_canonical_raster(
            png.png_bytes,
            source_content_sha256=png.content_sha256,
        )
    except LocalityError as error:
        raise CompositorError("encoder_replay_failed", str(error)) from error
    if output_raster.rgb_bytes != composed_bytes:
        raise CompositorError("encoder_replay_failed", "canonical PNG changed compositor RGB bytes")
    occurrence = seal_compositor_output_occurrence(
        {
            "schema_version": COMPOSITOR_OCCURRENCE_VERSION,
            "producer_kind": "deterministic_compositor",
            "role": "generated_image",
            "intent_packet_id": packet_document["intent_packet_id"],
            "original": to_json_dict(png),
            "output_raster": _raster_document(output_raster),
            "admission": {"state": "eligible", "rejection_reasons": []},
            "producer": {
                "compositor_policy": COMPOSITOR_POLICY,
                "compositor_revision": COMPOSITOR_REVISION,
                "encoder_revision": PNG_ENCODER_REVISION,
                "deflate_revision": DEFLATE_REVISION,
            },
            "lineage": {
                "source_raster_sha256": source["raster_sha256"],
                "mask_sha256": mask_document["mask_sha256"],
                "raw_output_occurrence_id": raw_occurrence["output_occurrence_id"],
                "raw_output_raster_sha256": raw_output_raster.raster_sha256,
                "raw_structural_evidence_id": raw_structural_judgment.evidence_id,
                "raw_locality_evidence_id": raw_locality_judgment.evidence_id,
                "insert_sha256": insert.insert_sha256,
                "insert_compile_confirmation_id": insert.insert_compile_confirmation_id,
            },
        },
        png_bytes=png.png_bytes,
        output_raster=output_raster,
    )
    structural = verify_compositor_output_structure(
        source_raster,
        compositor_occurrence=occurrence,
        output_raster=output_raster,
        output_bytes=png.png_bytes,
    )
    locality = _verify_compositor_locality(
        source_raster,
        output_raster,
        mask,
        occurrence=occurrence,
        structural=structural,
    )
    locality_document = _judgment_document(locality)
    if locality_document["result"] != {
        "state": "pass",
        "measurements": {
            "protected_pixel_count": mask.protected_count,
            "changed_pixel_count": 0,
            "max_abs_channel_error": 0,
        },
    }:
        raise CompositorError(
            "producer_failure", "source-backed compositor changed a protected source pixel"
        )
    return CompositorResult(
        insert=insert,
        png=png,
        output_raster=output_raster,
        occurrence=occurrence,
        structural=structural,
        locality=locality,
    )


def resolve_compositor_replay(
    existing: CompositorOutputOccurrence,
    candidate: CompositorOutputOccurrence,
    *,
    existing_png_bytes: bytes,
    candidate_png_bytes: bytes,
) -> CompositorOutputOccurrence:
    """Return the prior exact occurrence, or reject one same-key protocol conflict.

    Different replay keys are different operations and therefore return ``candidate``.  Durable
    storage is responsible for applying this helper under a unique-key transaction.
    """

    existing_document = to_json_dict(existing)
    candidate_document = to_json_dict(candidate)
    existing_payload = _require_bytes(existing_png_bytes, "existing_png_bytes")
    candidate_payload = _require_bytes(candidate_png_bytes, "candidate_png_bytes")
    validate_compositor_output_occurrence(existing_document, png_bytes=existing_payload)
    validate_compositor_output_occurrence(candidate_document, png_bytes=candidate_payload)
    if existing.compositor_replay_key != candidate.compositor_replay_key:
        return candidate
    if existing_document == candidate_document and hmac.compare_digest(
        existing_payload, candidate_payload
    ):
        return existing
    raise CompositorError(
        "replay_conflict", "one compositor replay key names different occurrence bytes"
    )
