"""Shared canonical primitives for immutable Moodboard contracts.

ADR-0012 through ADR-0016 identify documents with RFC 8785 JSON Canonicalization
Scheme bytes and a schema-version domain separator.  This module is deliberately
small: artifact-specific shape validation belongs to each owning contract.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import rfc8785

__all__ = [
    "ContractIdentityError",
    "canonical_json_bytes",
    "compute_document_identity",
    "compute_projection_identity",
    "is_canonical_utc_timestamp",
    "verify_document_identity",
]

_LOWER_HEX = frozenset("0123456789abcdef")
_CANONICAL_UTC_TIMESTAMP_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?Z$"
)


class ContractIdentityError(ValueError):
    """A value cannot participate in the declared immutable identity contract."""


def is_canonical_utc_timestamp(value: object) -> bool:
    """Return whether ``value`` is one real canonical UTC timestamp.

    The v1 spelling is deliberately narrower than general RFC 3339: uppercase
    ``T``/``Z``, no offset, and at most nine fractional-second digits. Leap
    seconds and ``24:00:00`` are not part of this contract.
    """

    if not isinstance(value, str):
        return False
    matched = _CANONICAL_UTC_TIMESTAMP_RE.fullmatch(value)
    if matched is None:
        return False
    try:
        datetime(
            int(matched["year"]),
            int(matched["month"]),
            int(matched["day"]),
            int(matched["hour"]),
            int(matched["minute"]),
            int(matched["second"]),
            tzinfo=UTC,
        )
    except ValueError:
        return False
    return True


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize one I-JSON value using RFC 8785/JCS, or fail closed."""

    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, RecursionError, TypeError, ValueError) as error:
        raise ContractIdentityError(f"value is not RFC 8785 canonicalizable: {error}") from error


def _domain_tag_bytes(domain_tag: str) -> bytes:
    if not isinstance(domain_tag, str) or not domain_tag or "\0" in domain_tag:
        raise ContractIdentityError("domain_tag must be a non-empty string without NUL")
    try:
        return domain_tag.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContractIdentityError("domain_tag must be valid UTF-8") from error


def compute_projection_identity(projection: Mapping[str, Any], *, domain_tag: str) -> str:
    """Hash one contract-declared identity projection under an exact domain tag.

    Callers, not this helper, own the closed projection.  This distinction matters:
    some contracts hash a complete document minus its id, while others intentionally
    bind only a small key such as ``{attempt_id, output_index}``.
    """

    encoded_domain_tag = _domain_tag_bytes(domain_tag)
    if not isinstance(projection, Mapping):
        raise ContractIdentityError("identity projection must be a mapping")
    digest = hashlib.sha256()
    digest.update(encoded_domain_tag)
    digest.update(b"\0")
    digest.update(canonical_json_bytes(dict(projection)))
    return digest.hexdigest()


def _identity_projection(
    document: Mapping[str, Any], *, schema_version: str, identity_field: str
) -> dict[str, Any]:
    _domain_tag_bytes(schema_version)
    if not isinstance(identity_field, str) or not identity_field:
        raise ContractIdentityError("identity_field must be a non-empty string")
    if identity_field not in document:
        raise ContractIdentityError(f"document is missing identity field {identity_field!r}")
    if document.get("schema_version") != schema_version:
        raise ContractIdentityError(
            "identity mismatch: document schema_version does not equal the domain tag"
        )

    projection = dict(document)
    del projection[identity_field]
    return projection


def compute_document_identity(
    document: Mapping[str, Any], *, schema_version: str, identity_field: str
) -> str:
    """Compute ``sha256(UTF8(schema_version + NUL) || JCS(projection))``.

    The projection is the complete top-level document with exactly ``identity_field``
    omitted.  The input mapping is never mutated.
    """

    projection = _identity_projection(
        document,
        schema_version=schema_version,
        identity_field=identity_field,
    )
    return compute_projection_identity(projection, domain_tag=schema_version)


def verify_document_identity(
    document: Mapping[str, Any], *, schema_version: str, identity_field: str
) -> None:
    """Fail unless a document carries its exact domain-separated computed identity."""

    claimed = document.get(identity_field)
    if not isinstance(claimed, str) or len(claimed) != 64 or not set(claimed) <= _LOWER_HEX:
        raise ContractIdentityError(
            f"document {identity_field} must be 64 lowercase hexadecimal characters"
        )
    measured = compute_document_identity(
        document,
        schema_version=schema_version,
        identity_field=identity_field,
    )
    if not hmac.compare_digest(claimed, measured):
        raise ContractIdentityError(
            f"identity mismatch for {identity_field}: claimed {claimed}, measured {measured}"
        )
