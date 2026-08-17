"""Frozen provider-neutral generation input defined by ADR-0014.

The packet is the complete confirmed dispatch input, not a mutable Studio draft and not a
provider request.  JSON Schema owns the closed wire shape.  This module adds immutable identity
verification and the cross-field relationships that JSON Schema cannot express without copying
authorities into multiple, drifting definitions.
"""

from __future__ import annotations

import copy
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

import jsonschema
from referencing import Registry, Resource

from moodboard.contracts import (
    ContractIdentityError,
    canonical_json_bytes,
    compute_projection_identity,
    verify_document_identity,
)

__all__ = [
    "OPERATION_SCHEMA_PATH",
    "SCHEMA_PATH",
    "SCHEMA_VERSION",
    "VERIFICATION_POLICY_SCHEMA_PATH",
    "IntentPacket",
    "IntentPacketError",
    "from_json_dict",
    "to_json_dict",
    "validate_intent_packet",
]

SCHEMA_VERSION: Literal["moodboard.intent-packet.v1"] = "moodboard.intent-packet.v1"
LOCALIZED_EDIT_SCHEMA = "moodboard.operation.localized-edit.v1"
VERIFICATION_POLICY_SCHEMA = "moodboard.verification-policy.v1"
EXACT_LOCALITY_VERIFIER = "moodboard.verifier.outside-mask-rgb-exact.v1"

_SCHEMA_DIR = Path(__file__).parent / "schema"
SCHEMA_PATH = _SCHEMA_DIR / "intent_packet_v1.schema.json"
OPERATION_SCHEMA_PATH = _SCHEMA_DIR / "operation_localized_edit_v1.schema.json"
VERIFICATION_POLICY_SCHEMA_PATH = _SCHEMA_DIR / "verification_policy_v1.schema.json"
_RASTER_SCHEMA_PATH = _SCHEMA_DIR / "raster_srgb_u8_v1.schema.json"
_MASK_SCHEMA_PATH = _SCHEMA_DIR / "mask_u8_v1.schema.json"
_SCHEMA_PATHS = (
    SCHEMA_PATH,
    OPERATION_SCHEMA_PATH,
    VERIFICATION_POLICY_SCHEMA_PATH,
    _RASTER_SCHEMA_PATH,
    _MASK_SCHEMA_PATH,
)


class IntentPacketError(ValueError):
    """A document cannot be admitted as one exact confirmed intent packet."""


FrozenJson: TypeAlias = (
    None | bool | int | float | str | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
)


@dataclass(frozen=True, slots=True)
class IntentPacket:
    """One immutable, confirmed provider-neutral generation input."""

    schema_version: str
    intent_packet_id: str
    creative_session_id: str
    operation: Mapping[str, FrozenJson]
    board: Mapping[str, FrozenJson]
    source: Mapping[str, FrozenJson]
    instruction: str
    retrieval_route: Mapping[str, FrozenJson]
    references: tuple[FrozenJson, ...]
    generation_request: Mapping[str, FrozenJson]
    verification_policy: Mapping[str, FrozenJson]
    confirmation: Mapping[str, FrozenJson]


@cache
def _schema_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise IntentPacketError(
            f"intent-packet schema is unavailable or invalid: {error}"
        ) from error
    return document


@cache
def _schema_registry() -> Registry:
    registry = Registry()
    for path in _SCHEMA_PATHS:
        document = _schema_document(path)
        schema_id = document.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise IntentPacketError(f"intent-packet schema {path.name} has no absolute $id")
        registry = registry.with_resource(schema_id, Resource.from_contents(document))
    return registry


@cache
def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        _schema_document(SCHEMA_PATH),
        registry=_schema_registry(),
        format_checker=jsonschema.FormatChecker(),
    )


def _error_sort_key(error: jsonschema.ValidationError) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, f"{part:020d}") if isinstance(part, int) else (1, part) for part in error.absolute_path
    )


def _json_path(error: jsonschema.ValidationError) -> str:
    if not error.absolute_path:
        return "$"
    return "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
    )


def _validate_schema(document: dict[str, Any]) -> None:
    try:
        errors = sorted(_validator().iter_errors(document), key=_error_sort_key)
    except (RecursionError, TypeError, ValueError) as error:
        raise IntentPacketError(f"intent packet is not a finite JSON document: {error}") from error
    if errors:
        error = errors[0]
        raise IntentPacketError(f"intent packet {_json_path(error)}: {error.message}")


def _validate_uuid(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise IntentPacketError(f"{field} must be a canonical UUID string")
    try:
        measured = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise IntentPacketError(f"{field} must be a canonical UUID string") from error
    if measured != value:
        raise IntentPacketError(f"{field} must use canonical lowercase UUID spelling")


def _validate_identities(document: dict[str, Any]) -> None:
    try:
        verify_document_identity(
            document,
            schema_version=SCHEMA_VERSION,
            identity_field="intent_packet_id",
        )
        operation = document["operation"]
        measured_operation = compute_projection_identity(
            operation["payload"], domain_tag=LOCALIZED_EDIT_SCHEMA
        )
        if not hmac.compare_digest(operation["payload_sha256"], measured_operation):
            raise IntentPacketError(
                "operation payload identity mismatch: payload_sha256 does not bind the payload"
            )
        verify_document_identity(
            document["verification_policy"],
            schema_version=VERIFICATION_POLICY_SCHEMA,
            identity_field="policy_id",
        )
    except ContractIdentityError as error:
        raise IntentPacketError(str(error)) from error


def _validate_localized_payload(document: dict[str, Any]) -> None:
    payload = document["operation"]["payload"]
    source_raster = payload["source_raster"]
    region = payload["region"]
    mask = payload["mask"]

    if source_raster["source_content_sha256"] != document["source"]["content_sha256"]:
        raise IntentPacketError("localized source raster does not bind packet source bytes")
    if source_raster["byte_count"] != source_raster["width"] * source_raster["height"] * 3:
        raise IntentPacketError("localized source raster byte_count must equal width * height * 3")
    if not (
        0 <= region["left"] < region["right"] <= source_raster["width"]
        and 0 <= region["top"] < region["bottom"] <= source_raster["height"]
    ):
        raise IntentPacketError("localized edit region is outside its canonical source raster")
    if (mask["width"], mask["height"]) != (
        source_raster["width"],
        source_raster["height"],
    ):
        raise IntentPacketError("localized mask dimensions must equal canonical source dimensions")
    if mask["source_raster_sha256"] != source_raster["raster_sha256"]:
        raise IntentPacketError("localized mask does not bind the exact source raster")
    if mask["byte_count"] != mask["width"] * mask["height"]:
        raise IntentPacketError("localized mask byte_count must equal width * height")
    if mask["editable_count"] + mask["protected_count"] != mask["byte_count"]:
        raise IntentPacketError("localized mask editable/protected counts must cover every byte")
    region_area = (region["right"] - region["left"]) * (region["bottom"] - region["top"])
    if mask["editable_count"] != region_area:
        raise IntentPacketError("localized rectangular region area must equal mask editable_count")
    if EXACT_LOCALITY_VERIFIER not in document["verification_policy"]["required_verifiers"]:
        raise IntentPacketError("localized edit requires the exact outside-mask locality verifier")


def _validate_references(document: dict[str, Any]) -> None:
    references = document["references"]
    if not references:
        raise IntentPacketError("a dispatch-ready intent packet cannot have an empty route")
    routed = [reference["routed_rank"] for reference in references]
    if routed != list(range(1, len(references) + 1)):
        raise IntentPacketError("reference routed_rank must equal one-based array order")
    source_ranks = [reference["source_search_rank"] for reference in references]
    if source_ranks != sorted(source_ranks) or len(source_ranks) != len(set(source_ranks)):
        raise IntentPacketError("reference source_search_rank must be strictly increasing")
    similarities = [reference["source_similarity"] for reference in references]
    if any(left < right for left, right in zip(similarities, similarities[1:], strict=False)):
        raise IntentPacketError("reference source_similarity must preserve source-cosine order")
    for field in ("reference_occurrence_id", "asset_id", "content_ref"):
        values = [reference[field] for reference in references]
        if len(values) != len(set(values)):
            raise IntentPacketError(f"reference {field} values must be unique")
    for index, reference in enumerate(references):
        _validate_uuid(reference["reference_occurrence_id"], f"references[{index}].occurrence")
        _validate_uuid(reference["asset_id"], f"references[{index}].asset_id")


def _validate_derivative(
    derivative: Mapping[str, Any], delivered: Mapping[str, Any], *, source_identity: str
) -> None:
    if derivative["source_identity_sha256"] != source_identity:
        raise IntentPacketError("operation-input derivative does not bind its original artifact")
    for field in ("byte_count", "width", "height", "content_ref", "content_sha256"):
        if derivative[field] != delivered[field]:
            raise IntentPacketError(
                f"operation-input derivative {field} does not match delivered artifact"
            )


def _validate_operation_inputs(document: dict[str, Any]) -> None:
    request = document["generation_request"]
    inputs = request["operation_inputs"]
    roles = [item["role"] for item in inputs]
    if roles != ["source_image", "locality_mask"]:
        raise IntentPacketError(
            "localized operation_inputs must contain source_image then locality_mask exactly once"
        )
    source_input, mask_input = inputs
    source = document["source"]
    source_original = source_input["original_artifact"]
    for field in ("asset_id", "content_ref", "content_sha256"):
        if source_original[field] != source[field]:
            raise IntentPacketError(f"source operation input does not bind packet source {field}")
    source_delivered = source_input["delivered_artifact"]
    if source_input["delivery_mode"] == "native_input":
        if source_input["derivative"] is not None:
            raise IntentPacketError("native source input cannot claim a derivative")
        for field in ("content_ref", "content_sha256", "width", "height"):
            # Width/height live on the packet source rather than original_artifact.
            expected = source[field] if field in {"width", "height"} else source_original[field]
            if source_delivered[field] != expected:
                raise IntentPacketError(f"native source delivery changes original {field}")
    else:
        _validate_derivative(
            source_input["derivative"],
            source_delivered,
            source_identity=source_original["content_sha256"],
        )

    mask = document["operation"]["payload"]["mask"]
    mask_original = mask_input["original_artifact"]
    if mask_original["mask_sha256"] != mask["mask_sha256"]:
        raise IntentPacketError("mask operation input does not bind the packet mask")
    if mask_input["delivery_mode"] in {"native_mask", "attached_overlay"}:
        _validate_derivative(
            mask_input["derivative"],
            mask_input["delivered_artifact"],
            source_identity=mask_original["mask_sha256"],
        )


def _validate_request_policy(document: dict[str, Any]) -> None:
    request = document["generation_request"]
    policy = request["provider_route_policy"]
    destination = request["destination"]
    routes = policy["permitted_routes"]
    if len({route["route_id"] for route in routes}) != len(routes):
        raise IntentPacketError("provider route ids must be unique")
    if not policy["moodboard_fallback_permitted"] and len(routes) != 1:
        raise IntentPacketError("fallback-disabled provider policy must contain exactly one route")
    for route in routes:
        if route["provider"] != request["requested_provider"]:
            raise IntentPacketError("provider route escapes the requested provider")
        if route["model"] != request["requested_model"]:
            raise IntentPacketError("provider fallback cannot substitute a different model")
        if route["privacy_class"] != destination["privacy_class"]:
            raise IntentPacketError("provider route escapes the confirmed privacy boundary")
        if route["retention_class"] != destination["retention_class"]:
            raise IntentPacketError("provider route escapes the confirmed retention boundary")
    if policy["provider_route_policy_id"] == document["retrieval_route"]["route_policy_id"]:
        raise IntentPacketError("retrieval and provider-route policy ids must be distinct")

    idempotency = request["idempotency"]
    if idempotency["provider_accepts_key"]:
        if idempotency["deduplication_scope"] is None or idempotency["retention_seconds"] is None:
            raise IntentPacketError("provider idempotency guarantee requires scope and retention")
    elif (
        idempotency["deduplication_scope"] is not None
        or idempotency["retention_seconds"] is not None
        or idempotency["ambiguous_transport_retransmit_safe"]
    ):
        raise IntentPacketError("unsupported provider idempotency cannot authorize retransmission")
    reconciliation = request["reconciliation"]
    if reconciliation["supported"] != (reconciliation["provider_handle_kind"] is not None):
        raise IntentPacketError("reconciliation support must agree with provider handle kind")
    if (
        request["options"]["schema_version"] == "moodboard.openrouter-images-options.v1"
        and request["requested_provider"] != "openrouter"
    ):
        raise IntentPacketError("OpenRouter image options require the OpenRouter provider")


def _expected_confirmation(document: dict[str, Any]) -> dict[str, Any]:
    references = document["references"]
    request = document["generation_request"]
    policy = document["verification_policy"]
    return {
        "references_shown": copy.deepcopy(references),
        "reference_use": [
            {
                "reference_occurrence_id": reference["reference_occurrence_id"],
                "provider_use": reference["provider_use"],
            }
            for reference in references
        ],
        "operation_inputs_shown": copy.deepcopy(request["operation_inputs"]),
        "dispatch_shown": {
            "requested_provider": request["requested_provider"],
            "requested_model": request["requested_model"],
            "output_count": request["output_count"],
            "destination": copy.deepcopy(request["destination"]),
            "adapter_revision": request["adapter_revision"],
            "capability_snapshot_id": request["capability_snapshot_id"],
            "options": copy.deepcopy(request["options"]),
            "provider_route_policy": copy.deepcopy(request["provider_route_policy"]),
            "actual_model_policy": request["actual_model_policy"],
            "idempotency": copy.deepcopy(request["idempotency"]),
            "reconciliation": copy.deepcopy(request["reconciliation"]),
            "verification_policy_id": policy["policy_id"],
            "required_verifiers": copy.deepcopy(policy["required_verifiers"]),
        },
    }


def _validate_confirmation(document: dict[str, Any]) -> None:
    confirmation = document["confirmation"]
    for field, expected in _expected_confirmation(document).items():
        if confirmation[field] != expected:
            raise IntentPacketError(f"confirmation {field} does not match the dispatch boundary")


def validate_intent_packet(document: dict[str, Any]) -> None:
    """Validate one unmodified JSON packet without coercion or provider inference."""

    if not isinstance(document, dict):
        raise IntentPacketError("intent packet must be a JSON object")
    _validate_schema(document)
    _validate_identities(document)
    _validate_uuid(document["creative_session_id"], "creative_session_id")
    _validate_uuid(document["source"]["asset_id"], "source.asset_id")
    _validate_uuid(document["confirmation"]["studio_session_id"], "confirmation.studio_session_id")
    _validate_uuid(document["confirmation"]["principal_id"], "confirmation.principal_id")
    _validate_localized_payload(document)
    _validate_references(document)
    _validate_operation_inputs(document)
    _validate_request_policy(document)
    _validate_confirmation(document)


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


def from_json_dict(document: dict[str, Any]) -> IntentPacket:
    """Validate and freeze one packet."""

    validate_intent_packet(document)
    return IntentPacket(
        schema_version=document["schema_version"],
        intent_packet_id=document["intent_packet_id"],
        creative_session_id=document["creative_session_id"],
        operation=_freeze_json(document["operation"]),  # type: ignore[arg-type]
        board=_freeze_json(document["board"]),  # type: ignore[arg-type]
        source=_freeze_json(document["source"]),  # type: ignore[arg-type]
        instruction=document["instruction"],
        retrieval_route=_freeze_json(document["retrieval_route"]),  # type: ignore[arg-type]
        references=_freeze_json(document["references"]),  # type: ignore[arg-type]
        generation_request=_freeze_json(document["generation_request"]),  # type: ignore[arg-type]
        verification_policy=_freeze_json(document["verification_policy"]),  # type: ignore[arg-type]
        confirmation=_freeze_json(document["confirmation"]),  # type: ignore[arg-type]
    )


def to_json_dict(packet: IntentPacket) -> dict[str, Any]:
    """Emit one detached JSON model, refusing forged typed values."""

    if not isinstance(packet, IntentPacket):
        raise IntentPacketError("to_json_dict requires an IntentPacket")
    document = {
        "schema_version": packet.schema_version,
        "intent_packet_id": packet.intent_packet_id,
        "creative_session_id": packet.creative_session_id,
        "operation": _thaw_json(packet.operation),
        "board": _thaw_json(packet.board),
        "source": _thaw_json(packet.source),
        "instruction": packet.instruction,
        "retrieval_route": _thaw_json(packet.retrieval_route),
        "references": _thaw_json(packet.references),
        "generation_request": _thaw_json(packet.generation_request),
        "verification_policy": _thaw_json(packet.verification_policy),
        "confirmation": _thaw_json(packet.confirmation),
    }
    validate_intent_packet(document)
    return document


def canonical_packet_bytes(packet: IntentPacket) -> bytes:
    """Return canonical RFC 8785 bytes for callers that need an immutable payload blob."""

    try:
        return canonical_json_bytes(to_json_dict(packet))
    except ContractIdentityError as error:
        raise IntentPacketError(str(error)) from error
