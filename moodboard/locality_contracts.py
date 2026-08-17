"""Closed raster, mask, and verifier-input identities for ADR-0016.

This module does not decode images, compile masks, compare pixels, or produce
judgments.  It owns the byte-bearing artifact boundary that those later runtime
operations must satisfy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import jsonschema

from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    compute_projection_identity,
)

__all__ = [
    "EXACT_LOCALITY_VERIFIER_VERSION",
    "MASK_SCHEMA_VERSION",
    "RASTER_SCHEMA_VERSION",
    "SCHEMA_PATHS",
    "STRUCTURAL_VERIFIER_VERSION",
    "CanonicalMaskArtifact",
    "CanonicalRasterArtifact",
    "LocalityContractError",
    "compute_exact_locality_input_digest",
    "compute_exact_locality_not_run_input_digest",
    "compute_mask_sha256",
    "compute_raster_sha256",
    "compute_structural_input_digest",
    "validate_mask_artifact",
    "validate_raster_artifact",
]

RASTER_SCHEMA_VERSION = "moodboard.raster.srgb-u8.v1"
MASK_SCHEMA_VERSION = "moodboard.mask.u8.v1"
STRUCTURAL_VERIFIER_VERSION = "moodboard.verifier.raster-structure.v1"
EXACT_LOCALITY_VERIFIER_VERSION = "moodboard.verifier.outside-mask-rgb-exact.v1"

_STRUCTURAL_INPUT_DOMAIN = "moodboard.verifier.raster-structure.inputs.v1"
_EXACT_INPUT_DOMAIN = "moodboard.verifier.outside-mask-rgb-exact.inputs.v1"
_SCHEMA_DIR = Path(__file__).parent / "schema"
SCHEMA_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        RASTER_SCHEMA_VERSION: _SCHEMA_DIR / "raster_srgb_u8_v1.schema.json",
        MASK_SCHEMA_VERSION: _SCHEMA_DIR / "mask_u8_v1.schema.json",
    }
)

_RASTER_IDENTITY_FIELDS: Final = (
    "compiler_revision",
    "width",
    "height",
    "mode",
    "byte_count",
    "source_content_sha256",
)
_MASK_IDENTITY_FIELDS: Final = (
    "compiler_revision",
    "width",
    "height",
    "byte_count",
    "editable_count",
    "protected_count",
    "source_raster_sha256",
)
_DIGEST_CHARS = frozenset("0123456789abcdef")


class LocalityContractError(ValueError):
    """A raster, mask, or verifier input violates its closed contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CanonicalRasterArtifact:
    schema_version: str
    compiler_revision: str
    width: int
    height: int
    mode: str
    byte_count: int
    source_content_sha256: str
    raster_sha256: str
    rgb_bytes: bytes


@dataclass(frozen=True, slots=True)
class CanonicalMaskArtifact:
    schema_version: str
    compiler_revision: str
    width: int
    height: int
    byte_count: int
    editable_count: int
    protected_count: int
    source_raster_sha256: str
    mask_sha256: str
    mask_bytes: bytes


@cache
def _schema_document(version: str) -> dict[str, Any]:
    path = SCHEMA_PATHS[version]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise LocalityContractError(
            "schema_unavailable", f"locality schema is unavailable or invalid: {error}"
        ) from error
    return document


@cache
def _validator(version: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        _schema_document(version), format_checker=jsonschema.FormatChecker()
    )


def _validate_schema(document: dict[str, Any], version: str) -> None:
    try:
        errors = sorted(
            _validator(version).iter_errors(document),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (RecursionError, TypeError, ValueError) as error:
        raise LocalityContractError(
            "schema_invalid", f"locality artifact is not finite JSON: {error}"
        ) from error
    if errors:
        raise LocalityContractError("schema_invalid", errors[0].message)


def _require_bytes(value: object, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise LocalityContractError("payload_invalid", f"{field} must be immutable bytes")
    return value


def _binary_identity(*, domain: str, projection: Mapping[str, Any], payload: bytes) -> str:
    try:
        encoded = canonical_json_bytes(dict(projection))
    except ContractIdentityError as error:
        raise LocalityContractError("identity_invalid", str(error)) from error
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    digest.update(encoded)
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def _exact_projection(
    projection: Mapping[str, Any], fields: tuple[str, ...], label: str
) -> dict[str, Any]:
    if not isinstance(projection, Mapping):
        raise LocalityContractError("identity_invalid", f"{label} projection must be a mapping")
    if set(projection) != set(fields):
        raise LocalityContractError(
            "identity_invalid", f"{label} projection fields do not match the registered contract"
        )
    return {field: projection[field] for field in fields}


def compute_raster_sha256(projection: Mapping[str, Any], rgb_bytes: bytes) -> str:
    """Compute the ADR-0016 raster identity over metadata and RGB bytes."""

    payload = _require_bytes(rgb_bytes, "rgb_bytes")
    exact = _exact_projection(projection, _RASTER_IDENTITY_FIELDS, "raster")
    return _binary_identity(domain=RASTER_SCHEMA_VERSION, projection=exact, payload=payload)


def compute_mask_sha256(projection: Mapping[str, Any], mask_bytes: bytes) -> str:
    """Compute the ADR-0016 mask identity over metadata and binary mask bytes."""

    payload = _require_bytes(mask_bytes, "mask_bytes")
    exact = _exact_projection(projection, _MASK_IDENTITY_FIELDS, "mask")
    return _binary_identity(domain=MASK_SCHEMA_VERSION, projection=exact, payload=payload)


def _document_copy(document: Mapping[str, Any], version: str) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise LocalityContractError("schema_invalid", "locality artifact must be a mapping")
    copied = dict(document)
    if copied.get("schema_version") != version:
        raise LocalityContractError("schema_invalid", f"expected schema_version {version}")
    return copied


def validate_raster_artifact(
    document: Mapping[str, Any], rgb_bytes: bytes
) -> CanonicalRasterArtifact:
    """Validate and freeze one canonical RGB raster plus its out-of-band bytes."""

    copied = _document_copy(document, RASTER_SCHEMA_VERSION)
    payload = _require_bytes(rgb_bytes, "rgb_bytes")
    _validate_schema(copied, RASTER_SCHEMA_VERSION)
    if copied["byte_count"] != len(payload):
        raise LocalityContractError(
            "byte_count_mismatch", "raster byte_count does not equal the supplied RGB bytes"
        )
    if copied["byte_count"] != copied["width"] * copied["height"] * 3:
        raise LocalityContractError(
            "shape_mismatch", "raster byte_count does not equal width * height * 3"
        )
    projection = {field: copied[field] for field in _RASTER_IDENTITY_FIELDS}
    measured = compute_raster_sha256(projection, payload)
    if not hmac.compare_digest(copied["raster_sha256"], measured):
        raise LocalityContractError("identity_mismatch", "raster_sha256 does not match the bytes")
    return CanonicalRasterArtifact(**copied, rgb_bytes=payload)


def validate_mask_artifact(document: Mapping[str, Any], mask_bytes: bytes) -> CanonicalMaskArtifact:
    """Validate and freeze one canonical binary mask plus its out-of-band bytes."""

    copied = _document_copy(document, MASK_SCHEMA_VERSION)
    payload = _require_bytes(mask_bytes, "mask_bytes")
    if type(copied.get("editable_count")) is int and copied["editable_count"] == 0:
        raise LocalityContractError("empty_editable_set", "mask has no editable pixels")
    if type(copied.get("protected_count")) is int and copied["protected_count"] == 0:
        raise LocalityContractError("empty_protected_set", "mask has no protected pixels")
    _validate_schema(copied, MASK_SCHEMA_VERSION)
    if copied["byte_count"] != len(payload):
        raise LocalityContractError(
            "byte_count_mismatch", "mask byte_count does not equal the supplied mask bytes"
        )
    if copied["byte_count"] != copied["width"] * copied["height"]:
        raise LocalityContractError(
            "shape_mismatch", "mask byte_count does not equal width * height"
        )
    if any(value not in (0, 1) for value in payload):
        raise LocalityContractError("mask_not_binary", "mask bytes must be exactly zero or one")
    editable = payload.count(1)
    protected = payload.count(0)
    if copied["editable_count"] != editable or copied["protected_count"] != protected:
        raise LocalityContractError(
            "count_mismatch", "mask editable/protected counts do not match its bytes"
        )
    projection = {field: copied[field] for field in _MASK_IDENTITY_FIELDS}
    measured = compute_mask_sha256(projection, payload)
    if not hmac.compare_digest(copied["mask_sha256"], measured):
        raise LocalityContractError("identity_mismatch", "mask_sha256 does not match the bytes")
    return CanonicalMaskArtifact(**copied, mask_bytes=payload)


def _digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _DIGEST_CHARS for character in value)
    ):
        raise LocalityContractError(
            "identity_invalid", f"{field} must be a lowercase 64-character digest"
        )
    return value


def _input_digest(projection: Mapping[str, Any], domain: str) -> str:
    try:
        return compute_projection_identity(projection, domain_tag=domain)
    except ContractIdentityError as error:
        raise LocalityContractError("identity_invalid", str(error)) from error


def compute_structural_input_digest(
    *, source_raster_sha256: str, output_content_sha256: str
) -> str:
    """Bind the canonical source and exact original provider-output bytes."""

    return _input_digest(
        {
            "source_raster_sha256": _digest(source_raster_sha256, "source_raster_sha256"),
            "output_content_sha256": _digest(output_content_sha256, "output_content_sha256"),
        },
        _STRUCTURAL_INPUT_DOMAIN,
    )


def compute_exact_locality_input_digest(
    *, source_raster_sha256: str, output_raster_sha256: str, mask_sha256: str
) -> str:
    """Bind every byte identity consumed by a measured exact-locality result."""

    return _input_digest(
        {
            "source_raster_sha256": _digest(source_raster_sha256, "source_raster_sha256"),
            "output_raster_sha256": _digest(output_raster_sha256, "output_raster_sha256"),
            "mask_sha256": _digest(mask_sha256, "mask_sha256"),
        },
        _EXACT_INPUT_DOMAIN,
    )


def compute_exact_locality_not_run_input_digest(
    *,
    source_raster_sha256: str,
    mask_sha256: str,
    blocking_structural_evidence_id: str,
) -> str:
    """Bind the inputs and blocker carried by a locality ``not_run`` result."""

    return _input_digest(
        {
            "source_raster_sha256": _digest(source_raster_sha256, "source_raster_sha256"),
            "mask_sha256": _digest(mask_sha256, "mask_sha256"),
            "blocking_structural_evidence_id": _digest(
                blocking_structural_evidence_id,
                "blocking_structural_evidence_id",
            ),
        },
        _EXACT_INPUT_DOMAIN,
    )
