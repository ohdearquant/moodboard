"""Immutable provider-artifact contracts for ADR-0014.

This module owns closed wire validation, immutable Python values, contract identities, and the
exact joins between one confirmed intent packet and one provider artifact chain.  It deliberately
does not reduce attempt states, dispatch requests, reconcile providers, or provide durable
append-only storage; those are separate single-concern implementation layers.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
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
    verify_document_identity,
)
from moodboard.intent_packet import IntentPacket
from moodboard.intent_packet import to_json_dict as intent_packet_to_json

__all__ = [
    "ATTEMPT_VERSION",
    "CAPABILITY_VERSION",
    "EVENT_VERSION",
    "OUTPUT_VERSION",
    "RECEIPT_VERSION",
    "REQUEST_VERSION",
    "RUN_VERSION",
    "SCHEMA_PATHS",
    "GenerationAttempt",
    "GenerationAttemptEvent",
    "GenerationRun",
    "NormalizedProviderRequest",
    "OutputOccurrence",
    "ProviderArtifactError",
    "ProviderArtifact",
    "ProviderCapabilitySnapshot",
    "ProviderReceipt",
    "build_normalized_request_ref",
    "compute_provider_request_key",
    "from_json_dict",
    "seal_provider_artifact",
    "to_json_dict",
    "validate_artifact_bundle",
    "validate_provider_artifact",
]

RUN_VERSION = "moodboard.generation-run.v1"
ATTEMPT_VERSION = "moodboard.generation-attempt.v1"
EVENT_VERSION = "moodboard.generation-attempt-event.v1"
CAPABILITY_VERSION = "moodboard.provider-capability-snapshot.v1"
REQUEST_VERSION = "moodboard.normalized-provider-request.v1"
RECEIPT_VERSION = "moodboard.provider-receipt.v1"
OUTPUT_VERSION = "moodboard.output-occurrence.v1"
_REQUEST_KEY_DOMAIN = "moodboard.provider-request-key.v1"

_SCHEMA_DIR = Path(__file__).parent / "schema"
SCHEMA_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        RUN_VERSION: _SCHEMA_DIR / "generation_run_v1.schema.json",
        ATTEMPT_VERSION: _SCHEMA_DIR / "generation_attempt_v1.schema.json",
        EVENT_VERSION: _SCHEMA_DIR / "generation_attempt_event_v1.schema.json",
        CAPABILITY_VERSION: _SCHEMA_DIR / "provider_capability_snapshot_v1.schema.json",
        REQUEST_VERSION: _SCHEMA_DIR / "normalized_provider_request_v1.schema.json",
        RECEIPT_VERSION: _SCHEMA_DIR / "provider_receipt_v1.schema.json",
        OUTPUT_VERSION: _SCHEMA_DIR / "output_occurrence_v1.schema.json",
    }
)
_REGISTRY_PATHS = (
    *SCHEMA_PATHS.values(),
    _SCHEMA_DIR / "intent_packet_v1.schema.json",
    _SCHEMA_DIR / "operation_localized_edit_v1.schema.json",
    _SCHEMA_DIR / "verification_policy_v1.schema.json",
    _SCHEMA_DIR / "raster_srgb_u8_v1.schema.json",
    _SCHEMA_DIR / "mask_u8_v1.schema.json",
)
_IDENTITY_FIELDS = {
    EVENT_VERSION: "attempt_event_id",
    CAPABILITY_VERSION: "capability_snapshot_id",
    REQUEST_VERSION: "normalized_request_id",
    RECEIPT_VERSION: "provider_receipt_id",
}
_SINGLETON_VERSIONS = frozenset(
    {RUN_VERSION, ATTEMPT_VERSION, CAPABILITY_VERSION, REQUEST_VERSION, RECEIPT_VERSION}
)
_MAX_BUNDLE_ARTIFACTS = 64
_MAX_BUNDLE_EVENTS = 32
_MAX_BUNDLE_OUTPUTS = 8
_TIMESTAMP_FIELDS = {
    RUN_VERSION: "created_at",
    ATTEMPT_VERSION: "created_at",
    EVENT_VERSION: "recorded_at",
    CAPABILITY_VERSION: "captured_at",
    RECEIPT_VERSION: "received_at",
}


class ProviderArtifactError(ValueError):
    """A provider artifact or artifact bundle violates its immutable contract."""


FrozenJson: TypeAlias = (
    None | bool | int | float | str | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
)


@dataclass(frozen=True, slots=True)
class GenerationRun:
    schema_version: str
    generation_run_id: str
    creative_session_id: str
    intent_packet_id: str
    requested_provider: str
    requested_model: str
    provider_route_policy_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    schema_version: str
    attempt_id: str
    generation_run_id: str
    intent_packet_id: str
    ordinal: int
    retry_of: str | None
    fallback_of: str | None
    requested_provider: str
    requested_model: str
    provider_route_policy_id: str
    selected_route_id: str
    adapter_revision: str
    capability_snapshot_id: str
    normalized_request_id: str
    normalized_request_ref: Mapping[str, FrozenJson]
    request_key_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GenerationAttemptEvent:
    schema_version: str
    attempt_event_id: str
    attempt_id: str
    sequence: int
    state: str
    recorded_at: str
    detail: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class ProviderCapabilitySnapshot:
    schema_version: str
    capability_snapshot_id: str
    captured_at: str
    adapter_revision: str
    provider: str
    requested_model: str
    input_modalities: tuple[FrozenJson, ...]
    image_input_budget: Mapping[str, FrozenJson]
    outputs: Mapping[str, FrozenJson]
    options: Mapping[str, FrozenJson]
    operation_input_capabilities: tuple[FrozenJson, ...]
    actual_model_disclosure: str
    upstream_route_disclosure: str
    idempotency: Mapping[str, FrozenJson]
    reconciliation: Mapping[str, FrozenJson]
    provider_specific: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class NormalizedProviderRequest:
    schema_version: str
    normalized_request_id: str
    intent_packet_id: str
    requested_provider: str
    requested_model: str
    selected_route_id: str
    provider_route_policy_id: str
    adapter_revision: str
    capability_snapshot_id: str
    prompt: Mapping[str, FrozenJson]
    output_count: int
    options: Mapping[str, FrozenJson]
    operation_inputs: tuple[FrozenJson, ...]
    reference_use: tuple[FrozenJson, ...]
    destination: Mapping[str, FrozenJson]
    provider_body: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    schema_version: str
    provider_receipt_id: str
    attempt_id: str
    normalized_request_id: str
    received_at: str
    requested_provider: str
    requested_model: str
    selected_route_id: str
    http_status: int
    provider_handle: FrozenJson
    actual_model: Mapping[str, FrozenJson]
    upstream_route: Mapping[str, FrozenJson]
    raw_response: Mapping[str, FrozenJson]
    outputs: tuple[FrozenJson, ...]
    cost: Mapping[str, FrozenJson]
    latency: Mapping[str, FrozenJson]


@dataclass(frozen=True, slots=True)
class OutputOccurrence:
    schema_version: str
    output_occurrence_id: str
    producer_kind: str
    attempt_id: str
    output_index: int
    role: str
    generation_run_id: str
    intent_packet_id: str
    normalized_request_id: str
    provider_receipt_id: str
    original: Mapping[str, FrozenJson]
    media_validation: Mapping[str, FrozenJson]
    admission: Mapping[str, FrozenJson]
    lineage: Mapping[str, FrozenJson]


ProviderArtifact: TypeAlias = (
    GenerationRun
    | GenerationAttempt
    | GenerationAttemptEvent
    | ProviderCapabilitySnapshot
    | NormalizedProviderRequest
    | ProviderReceipt
    | OutputOccurrence
)


@cache
def _schema_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, jsonschema.SchemaError) as error:
        raise ProviderArtifactError(
            f"provider-artifact schema is unavailable or invalid: {error}"
        ) from error
    return document


@cache
def _schema_registry() -> Registry:
    registry = Registry()
    for path in _REGISTRY_PATHS:
        document = _schema_document(path)
        schema_id = document.get("$id")
        if not isinstance(schema_id, str) or not schema_id:
            raise ProviderArtifactError(f"provider-artifact schema {path.name} has no absolute $id")
        registry = registry.with_resource(schema_id, Resource.from_contents(document))
    return registry


@cache
def _validator(schema_version: str) -> jsonschema.Draft202012Validator:
    path = SCHEMA_PATHS[schema_version]
    return jsonschema.Draft202012Validator(
        _schema_document(path),
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


def _validate_schema(document: dict[str, Any], schema_version: str) -> None:
    try:
        errors = sorted(_validator(schema_version).iter_errors(document), key=_error_sort_key)
    except (RecursionError, TypeError, ValueError) as error:
        raise ProviderArtifactError(
            f"provider artifact is not a finite JSON document: {error}"
        ) from error
    if errors:
        error = errors[0]
        raise ProviderArtifactError(f"provider artifact {_json_path(error)}: {error.message}")


def _validate_uuid(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ProviderArtifactError(f"{field} must be a canonical UUID string")
    try:
        measured = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise ProviderArtifactError(f"{field} must be a canonical UUID string") from error
    if measured != value:
        raise ProviderArtifactError(f"{field} must use canonical lowercase UUID spelling")


def _validate_identity(document: dict[str, Any], schema_version: str) -> None:
    try:
        if schema_version in _IDENTITY_FIELDS:
            verify_document_identity(
                document,
                schema_version=schema_version,
                identity_field=_IDENTITY_FIELDS[schema_version],
            )
        elif schema_version == OUTPUT_VERSION:
            measured = compute_projection_identity(
                {
                    "attempt_id": document["attempt_id"],
                    "output_index": document["output_index"],
                },
                domain_tag=OUTPUT_VERSION,
            )
            if not hmac.compare_digest(document["output_occurrence_id"], measured):
                raise ProviderArtifactError(
                    "output occurrence identity mismatch for attempt_id/output_index"
                )
    except ContractIdentityError as error:
        raise ProviderArtifactError(str(error)) from error


def compute_provider_request_key(
    *,
    generation_run_id: str,
    attempt_id: str,
    intent_packet_id: str,
    adapter_revision: str,
    normalized_request_id: str,
) -> str:
    """Compute the sole ADR-0014 provider request-key projection."""

    _validate_uuid(generation_run_id, "generation_run_id")
    _validate_uuid(attempt_id, "attempt_id")
    for value, field in (
        (intent_packet_id, "intent_packet_id"),
        (normalized_request_id, "normalized_request_id"),
    ):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ProviderArtifactError(f"{field} must be a lowercase SHA-256 digest")
    if (
        not isinstance(adapter_revision, str)
        or not 1 <= len(adapter_revision) <= 256
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", adapter_revision) is None
    ):
        raise ProviderArtifactError("adapter_revision must satisfy the closed version-token syntax")
    try:
        return compute_projection_identity(
            {
                "generation_run_id": generation_run_id,
                "attempt_id": attempt_id,
                "intent_packet_id": intent_packet_id,
                "adapter_revision": adapter_revision,
                "normalized_request_id": normalized_request_id,
            },
            domain_tag=_REQUEST_KEY_DOMAIN,
        )
    except ContractIdentityError as error:
        raise ProviderArtifactError(f"provider request-key identity is invalid: {error}") from error


def _expected_request_key(document: Mapping[str, Any]) -> str:
    return compute_provider_request_key(
        generation_run_id=document["generation_run_id"],
        attempt_id=document["attempt_id"],
        intent_packet_id=document["intent_packet_id"],
        adapter_revision=document["adapter_revision"],
        normalized_request_id=document["normalized_request_id"],
    )


def _validate_attempt(document: dict[str, Any]) -> None:
    _validate_uuid(document["attempt_id"], "attempt_id")
    _validate_uuid(document["generation_run_id"], "generation_run_id")
    retry_of = document["retry_of"]
    fallback_of = document["fallback_of"]
    if retry_of is not None:
        _validate_uuid(retry_of, "retry_of")
    if fallback_of is not None:
        _validate_uuid(fallback_of, "fallback_of")
    if retry_of is not None and fallback_of is not None:
        raise ProviderArtifactError("an attempt cannot be both a retry and a fallback")
    if retry_of == document["attempt_id"] or fallback_of == document["attempt_id"]:
        raise ProviderArtifactError("an attempt cannot name itself as retry or fallback parent")
    has_parent = retry_of is not None or fallback_of is not None
    if (document["ordinal"] == 1) == has_parent:
        raise ProviderArtifactError(
            "attempt ordinal one has no parent; later attempts require one retry/fallback parent"
        )
    expected = _expected_request_key(document)
    if not hmac.compare_digest(document["request_key_sha256"], expected):
        raise ProviderArtifactError("provider request-key identity mismatch")
    reference = document["normalized_request_ref"]
    if (
        reference["schema_version"] != REQUEST_VERSION
        or reference["artifact_id"] != document["normalized_request_id"]
    ):
        raise ProviderArtifactError("attempt normalized-request reference does not bind its id")


def _validate_event(document: dict[str, Any]) -> None:
    _validate_uuid(document["attempt_id"], "attempt_id")
    if document["detail"]["kind"] != document["state"]:
        raise ProviderArtifactError("attempt event detail kind must equal its state")


def _validate_capability(document: dict[str, Any]) -> None:
    capabilities = document["operation_input_capabilities"]
    capability_ids = [item["capability_id"] for item in capabilities]
    if len(capability_ids) != len(set(capability_ids)):
        raise ProviderArtifactError("operation-input capability ids must be unique")
    roles = [item["role"] for item in capabilities]
    if len(roles) != len(set(roles)):
        raise ProviderArtifactError("operation-input capability roles must be unique")
    outputs = document["outputs"]
    if outputs["min_count"] > outputs["max_count"]:
        raise ProviderArtifactError("capability output min/max range is inverted")
    provider_specific = document["provider_specific"]
    if provider_specific["schema_version"] == "moodboard.openrouter-images-capability.v1":
        expected_path = f"/api/v1/images/models/{document['requested_model']}/endpoints"
        if provider_specific["discovery_endpoint_path"] != expected_path:
            raise ProviderArtifactError(
                "capability discovery endpoint does not name the exact requested model"
            )


def _validate_normalized_request(document: dict[str, Any]) -> None:
    reference_ids = [item["reference_occurrence_id"] for item in document["reference_use"]]
    if len(reference_ids) != len(set(reference_ids)):
        raise ProviderArtifactError("normalized reference-use occurrences must be unique")
    positions = [
        item["provider_position"]
        for item in document["reference_use"]
        if item["provider_position"] is not None
    ]
    if len(positions) != len(set(positions)):
        raise ProviderArtifactError("normalized provider attachment positions must be unique")
    body = document["provider_body"]
    if body["schema_version"] == "moodboard.provider-body.none.v1":
        return
    if (
        body["model"] != document["requested_model"]
        or body["prompt"] != document["prompt"]["text"]
        or body["n"] != document["output_count"]
    ):
        raise ProviderArtifactError("provider body does not mirror normalized model/prompt/count")
    options = document["options"]
    for field in ("seed", "resolution", "aspect_ratio"):
        if field in options and body.get(field) != options[field]:
            raise ProviderArtifactError(f"provider body {field} does not mirror normalized options")
    inputs = body["input_references"]
    if [item["position"] for item in inputs] != list(range(len(inputs))):
        raise ProviderArtifactError(
            "provider input-reference positions must be contiguous from zero"
        )
    for item in inputs:
        expected_field = f"input_references[{item['position']}].image_url.url"
        if item["provider_field"] != expected_field:
            raise ProviderArtifactError(
                "provider input-reference field path must match its array position"
            )


def _validate_receipt(document: dict[str, Any]) -> None:
    _validate_uuid(document["attempt_id"], "attempt_id")
    output_indices = [item["output_index"] for item in document["outputs"]]
    if output_indices != list(range(len(output_indices))):
        raise ProviderArtifactError("provider receipt output indices must be contiguous from zero")


def _validate_output(document: dict[str, Any]) -> None:
    _validate_uuid(document["attempt_id"], "attempt_id")
    _validate_uuid(document["generation_run_id"], "generation_run_id")
    original = document["original"]
    measured = document["media_validation"]
    for original_field, measured_field in (
        ("content_sha256", "measured_content_sha256"),
        ("content_ref", "measured_content_ref"),
        ("byte_count", "measured_byte_count"),
        ("mime", "measured_mime"),
        ("width", "measured_width"),
        ("height", "measured_height"),
    ):
        if original[original_field] != measured[measured_field]:
            raise ProviderArtifactError(
                f"output media validation {measured_field} does not match original bytes"
            )
    admission = document["admission"]
    if (admission["state"] == "eligible") != (not admission["rejection_reasons"]):
        raise ProviderArtifactError(
            "eligible output requires no rejection reasons; rejected output requires at least one"
        )


def validate_provider_artifact(document: dict[str, Any]) -> None:
    """Validate one closed provider artifact without coercion or external state."""

    if not isinstance(document, dict):
        raise ProviderArtifactError("provider artifact must be a JSON object")
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str) or schema_version not in SCHEMA_PATHS:
        raise ProviderArtifactError(
            f"unsupported provider-artifact schema_version: {schema_version!r}"
        )
    timestamp_field = _TIMESTAMP_FIELDS.get(schema_version)
    if (
        timestamp_field is not None
        and timestamp_field in document
        and not is_canonical_utc_timestamp(document[timestamp_field])
    ):
        raise ProviderArtifactError(f"{timestamp_field} must be a real canonical UTC timestamp")
    _validate_schema(document, schema_version)
    _validate_identity(document, schema_version)
    if schema_version == RUN_VERSION:
        _validate_uuid(document["generation_run_id"], "generation_run_id")
        _validate_uuid(document["creative_session_id"], "creative_session_id")
    elif schema_version == ATTEMPT_VERSION:
        _validate_attempt(document)
    elif schema_version == EVENT_VERSION:
        _validate_event(document)
    elif schema_version == CAPABILITY_VERSION:
        _validate_capability(document)
    elif schema_version == REQUEST_VERSION:
        _validate_normalized_request(document)
    elif schema_version == RECEIPT_VERSION:
        _validate_receipt(document)
    else:
        _validate_output(document)


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


def from_json_dict(document: dict[str, Any]) -> ProviderArtifact:
    """Validate and freeze one provider artifact as its exact branch type."""

    validate_provider_artifact(document)
    version = document["schema_version"]
    if version == RUN_VERSION:
        return GenerationRun(**document)
    if version == ATTEMPT_VERSION:
        values = dict(document)
        values["normalized_request_ref"] = _freeze_json(values["normalized_request_ref"])
        return GenerationAttempt(**values)
    if version == EVENT_VERSION:
        values = dict(document)
        values["detail"] = _freeze_json(values["detail"])
        return GenerationAttemptEvent(**values)
    if version == CAPABILITY_VERSION:
        values = dict(document)
        for field in (
            "input_modalities",
            "image_input_budget",
            "outputs",
            "options",
            "operation_input_capabilities",
            "idempotency",
            "reconciliation",
            "provider_specific",
        ):
            values[field] = _freeze_json(values[field])
        return ProviderCapabilitySnapshot(**values)
    if version == REQUEST_VERSION:
        values = dict(document)
        for field in (
            "prompt",
            "options",
            "operation_inputs",
            "reference_use",
            "destination",
            "provider_body",
        ):
            values[field] = _freeze_json(values[field])
        return NormalizedProviderRequest(**values)
    if version == RECEIPT_VERSION:
        values = dict(document)
        for field in (
            "provider_handle",
            "actual_model",
            "upstream_route",
            "raw_response",
            "outputs",
            "cost",
            "latency",
        ):
            values[field] = _freeze_json(values[field])
        return ProviderReceipt(**values)
    values = dict(document)
    for field in ("original", "media_validation", "admission", "lineage"):
        values[field] = _freeze_json(values[field])
    return OutputOccurrence(**values)


def to_json_dict(artifact: ProviderArtifact) -> dict[str, Any]:
    """Emit a detached JSON model, revalidating even directly constructed values."""

    if not isinstance(
        artifact,
        (
            GenerationRun,
            GenerationAttempt,
            GenerationAttemptEvent,
            ProviderCapabilitySnapshot,
            NormalizedProviderRequest,
            ProviderReceipt,
            OutputOccurrence,
        ),
    ):
        raise ProviderArtifactError("to_json_dict requires a provider-artifact branch value")
    document = {
        field: _thaw_json(getattr(artifact, field))
        for field in artifact.__dataclass_fields__  # type: ignore[attr-defined]
    }
    validate_provider_artifact(document)
    return document


def seal_provider_artifact(draft: Mapping[str, Any]) -> ProviderArtifact:
    """Copy a draft, derive its registered identity, validate it, and freeze the result."""

    if not isinstance(draft, Mapping):
        raise ProviderArtifactError("provider artifact draft must be a mapping")
    try:
        document = copy.deepcopy(dict(draft))
    except (RecursionError, TypeError, ValueError) as error:
        raise ProviderArtifactError(
            f"provider artifact draft is not finite JSON: {error}"
        ) from error
    version = document.get("schema_version")
    if not isinstance(version, str):
        raise ProviderArtifactError("provider artifact draft schema_version must be a string")
    if version in {RUN_VERSION, ATTEMPT_VERSION}:
        raise ProviderArtifactError(
            "run and attempt occurrence UUID identities are caller supplied, not sealed"
        )
    if version not in {*_IDENTITY_FIELDS, OUTPUT_VERSION}:
        raise ProviderArtifactError(f"unsupported provider-artifact schema_version: {version!r}")
    identity_field = (
        "output_occurrence_id" if version == OUTPUT_VERSION else _IDENTITY_FIELDS[version]
    )
    if identity_field in document:
        raise ProviderArtifactError(f"provider artifact draft must not prefill {identity_field}")
    if version == OUTPUT_VERSION:
        try:
            document[identity_field] = compute_projection_identity(
                {
                    "attempt_id": document["attempt_id"],
                    "output_index": document["output_index"],
                },
                domain_tag=OUTPUT_VERSION,
            )
        except (KeyError, ContractIdentityError) as error:
            raise ProviderArtifactError(
                f"cannot derive output occurrence identity: {error}"
            ) from error
    else:
        document[identity_field] = "0" * 64
        try:
            document[identity_field] = compute_document_identity(
                document,
                schema_version=version,
                identity_field=identity_field,
            )
        except ContractIdentityError as error:
            raise ProviderArtifactError(str(error)) from error
    return from_json_dict(document)


def build_normalized_request_ref(
    normalized_request: NormalizedProviderRequest | Mapping[str, Any],
) -> dict[str, str | int]:
    """Build the exact canonical artifact reference required by a generation attempt."""

    if isinstance(normalized_request, NormalizedProviderRequest):
        document = to_json_dict(normalized_request)
    elif isinstance(normalized_request, Mapping):
        document = copy.deepcopy(dict(normalized_request))
        validate_provider_artifact(document)
    else:
        raise ProviderArtifactError("normalized_request must be a request artifact")
    if document.get("schema_version") != REQUEST_VERSION:
        raise ProviderArtifactError("normalized_request must use the normalized-request schema")
    encoded = canonical_json_bytes(document)
    return {
        "schema_version": REQUEST_VERSION,
        "artifact_id": document["normalized_request_id"],
        "content_ref": blake3(encoded).hexdigest(),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_count": len(encoded),
    }


def _document(value: dict[str, Any] | ProviderArtifact) -> dict[str, Any]:
    if isinstance(value, dict):
        document = copy.deepcopy(value)
        validate_provider_artifact(document)
        return document
    return to_json_dict(value)


def _only(grouped: Mapping[str, list[dict[str, Any]]], version: str) -> dict[str, Any]:
    documents = grouped.get(version, [])
    if len(documents) != 1:
        raise ProviderArtifactError(
            f"artifact bundle requires exactly one {version}; received {len(documents)}"
        )
    return documents[0]


def _packet_document(intent_packet: IntentPacket | dict[str, Any]) -> dict[str, Any]:
    if isinstance(intent_packet, IntentPacket):
        return intent_packet_to_json(intent_packet)
    if isinstance(intent_packet, dict):
        # The public API accepts the frozen value, but retaining this narrow path makes the
        # validator useful to artifact importers that have already validated their packet.
        from moodboard.intent_packet import validate_intent_packet

        validate_intent_packet(intent_packet)
        return copy.deepcopy(intent_packet)
    raise ProviderArtifactError("intent_packet must be an IntentPacket or validated JSON object")


def _assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise ProviderArtifactError(message)


def _validate_run_binding(run: Mapping[str, Any], packet: Mapping[str, Any]) -> None:
    request = packet["generation_request"]
    policy = request["provider_route_policy"]
    for actual, expected, label in (
        (run["creative_session_id"], packet["creative_session_id"], "creative session"),
        (run["intent_packet_id"], packet["intent_packet_id"], "intent packet"),
        (run["requested_provider"], request["requested_provider"], "requested provider"),
        (run["requested_model"], request["requested_model"], "requested model"),
        (
            run["provider_route_policy_id"],
            policy["provider_route_policy_id"],
            "provider-route policy",
        ),
    ):
        _assert_equal(actual, expected, f"generation run does not bind packet {label}")


def _validate_capability_binding(capability: Mapping[str, Any], packet: Mapping[str, Any]) -> None:
    request = packet["generation_request"]
    for field, packet_field in (
        ("provider", "requested_provider"),
        ("requested_model", "requested_model"),
        ("adapter_revision", "adapter_revision"),
        ("idempotency", "idempotency"),
        ("reconciliation", "reconciliation"),
    ):
        _assert_equal(
            capability[field],
            request[packet_field],
            f"capability snapshot {field} does not match confirmed packet",
        )
    _assert_equal(
        capability["capability_snapshot_id"],
        request["capability_snapshot_id"],
        "capability snapshot id does not match confirmed packet",
    )
    actual_model_policy = request["actual_model_policy"]
    if (
        actual_model_policy == "exact_required"
        and capability["actual_model_disclosure"] != "attested"
    ):
        raise ProviderArtifactError("exact model policy requires attested-model capability")
    route_policy = request["provider_route_policy"]
    if (
        capability["upstream_route_disclosure"] == "not_attested"
        and not route_policy["undisclosed_upstream_routing_permitted"]
    ):
        raise ProviderArtifactError("capability cannot attest the confirmed upstream-route policy")
    output_count = request["output_count"]
    outputs = capability["outputs"]
    if not outputs["min_count"] <= output_count <= outputs["max_count"]:
        raise ProviderArtifactError("capability snapshot does not authorize output count")

    options = request["options"]
    option_capability = capability["options"]
    if "seed" in options and not option_capability["seed_supported"]:
        raise ProviderArtifactError("capability snapshot does not authorize seed")
    for field, plural in (("resolution", "resolutions"), ("aspect_ratio", "aspect_ratios")):
        if field in options and options[field] not in option_capability[plural]:
            raise ProviderArtifactError(f"capability snapshot does not authorize {field}")
        if field in options and options[field] not in outputs[plural]:
            raise ProviderArtifactError(f"output capability does not authorize {field}")

    by_id = {item["capability_id"]: item for item in capability["operation_input_capabilities"]}
    for operation_input in request["operation_inputs"]:
        declared = by_id.get(operation_input["capability_id"])
        if declared is None or declared["role"] != operation_input["role"]:
            raise ProviderArtifactError("operation input has no matching capability")
        if operation_input["delivery_mode"] not in declared["delivery_modes"]:
            raise ProviderArtifactError("operation-input delivery mode is not supported")
        provider_field = operation_input["provider_field"]
        provider_role = operation_input["provider_role"]
        if provider_field is not None and provider_field not in declared["provider_fields"]:
            raise ProviderArtifactError("operation-input provider field is not supported")
        if provider_role is not None and provider_role not in declared["provider_roles"]:
            raise ProviderArtifactError("operation-input provider role is not supported")

    attached_references = sum(
        reference["provider_use"] == "attached_image" for reference in packet["references"]
    )
    image_budget = capability["image_input_budget"]
    if not image_budget["source_and_references_share_budget"]:
        raise ProviderArtifactError(
            "provider image budget must explicitly combine source and references"
        )
    if attached_references and not image_budget["supported"]:
        raise ProviderArtifactError("capability snapshot does not support attached references")
    operation_images = sum(
        operation_input["delivered_artifact"] is not None
        for operation_input in request["operation_inputs"]
    )
    delivered_provider_images = operation_images + attached_references
    modalities = set(capability["input_modalities"])
    if "text" not in modalities:
        raise ProviderArtifactError("capability omits the text modality required by the prompt")
    if delivered_provider_images and "image" not in modalities:
        raise ProviderArtifactError(
            "capability omits the image modality required by dispatched provider images"
        )
    if delivered_provider_images > image_budget["max_count"]:
        raise ProviderArtifactError(
            "capability snapshot shared provider-image budget is too small for "
            "source plus references"
        )
    if attached_references and not image_budget["ordered"]:
        raise ProviderArtifactError("capability snapshot cannot preserve attachment order")
    required_image_roles = {
        operation_input["provider_role"]
        for operation_input in request["operation_inputs"]
        if operation_input["provider_field"] is not None
    }
    if attached_references:
        required_image_roles.add("visual_context")
    if not required_image_roles <= set(image_budget["provider_roles"]):
        raise ProviderArtifactError("capability snapshot omits a delivered provider-image role")
    if request["requested_provider"] == "openrouter":
        selected_tags = {
            route["upstream_provider_tag"] for route in route_policy["permitted_routes"]
        }
        if not selected_tags <= set(capability["provider_specific"]["upstream_provider_tags"]):
            raise ProviderArtifactError(
                "capability snapshot omits a confirmed upstream provider tag"
            )


def _expected_reference_use(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    attached_position = 1  # localized-edit source occupies provider position zero in P0.
    for reference in packet["references"]:
        provider_use = reference["provider_use"]
        item: dict[str, Any] = {
            "reference_occurrence_id": reference["reference_occurrence_id"],
            "provider_use": provider_use,
            "provider_position": None,
            "provider_field": None,
            "provider_role": None,
            "content_sha256": None,
            "prompt_context": None,
        }
        if provider_use == "attached_image":
            item.update(
                {
                    "provider_position": attached_position,
                    "provider_field": f"input_references[{attached_position}]",
                    "provider_role": reference["role"],
                    "content_sha256": reference["content_sha256"],
                }
            )
            attached_position += 1
        elif provider_use == "prompt_context_only":
            item["prompt_context"] = copy.deepcopy(reference["prompt_context"])
        expected.append(item)
    return expected


def _expected_body_input_authorities(
    packet: Mapping[str, Any], reference_use: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    source_input = packet["generation_request"]["operation_inputs"][0]
    expected = [
        {
            "position": 0,
            "provider_field": "input_references[0].image_url.url",
            "item_type": "image_url",
            "media_type": packet["source"]["mime"],
            "role": "source_image",
            "source_kind": "operation_input",
            "source_id": source_input["original_artifact"]["asset_id"],
            "content_sha256": source_input["delivered_artifact"]["content_sha256"],
        }
    ]
    expected.extend(
        {
            "position": item["provider_position"],
            "provider_field": f"input_references[{item['provider_position']}].image_url.url",
            "item_type": "image_url",
            "role": item["provider_role"],
            "source_kind": "reference_occurrence",
            "source_id": item["reference_occurrence_id"],
            "content_sha256": item["content_sha256"],
        }
        for item in reference_use
        if item["provider_use"] == "attached_image"
    )
    return expected


def _validate_request_binding(
    normalized: Mapping[str, Any],
    capability: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> None:
    request = packet["generation_request"]
    policy = request["provider_route_policy"]
    expected_fields = {
        "intent_packet_id": packet["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": policy["provider_route_policy_id"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": capability["capability_snapshot_id"],
        "output_count": request["output_count"],
        "options": request["options"],
        "operation_inputs": request["operation_inputs"],
        "destination": request["destination"],
    }
    for field, expected in expected_fields.items():
        _assert_equal(
            normalized[field], expected, f"normalized request {field} drifted from packet"
        )
    permitted_route_ids = [route["route_id"] for route in policy["permitted_routes"]]
    if normalized["selected_route_id"] not in permitted_route_ids:
        raise ProviderArtifactError("normalized request selected an unconfirmed route")
    expected_reference_use = _expected_reference_use(packet)
    _assert_equal(
        normalized["reference_use"],
        expected_reference_use,
        "normalized request reference use/order drifted from packet",
    )
    if normalized["requested_provider"] != "openrouter":
        return
    locality_mask = next(
        item for item in normalized["operation_inputs"] if item["role"] == "locality_mask"
    )
    if locality_mask["delivery_mode"] != "not_sent":
        raise ProviderArtifactError(
            "OpenRouter P0 requires the locality mask to be not_sent until a mask transport "
            "is registered"
        )
    body_inputs = normalized["provider_body"]["input_references"]
    expected_body_inputs = _expected_body_input_authorities(packet, expected_reference_use)
    if len(body_inputs) != len(expected_body_inputs):
        raise ProviderArtifactError(
            "provider body attachment count drifted from normalized request"
        )
    for actual, expected in zip(body_inputs, expected_body_inputs, strict=True):
        for field, expected_value in expected.items():
            _assert_equal(
                actual[field],
                expected_value,
                f"provider body attachment {field} drifted from normalized request",
            )
    selected_route = next(
        route
        for route in policy["permitted_routes"]
        if route["route_id"] == normalized["selected_route_id"]
    )
    provider_body = normalized["provider_body"]
    _assert_equal(
        provider_body["provider"],
        {
            "only": [selected_route["upstream_provider_tag"]],
            "allow_fallbacks": False,
        },
        "provider body route pin does not match the selected confirmed route",
    )
    _assert_equal(
        provider_body["endpoint_path"],
        capability["provider_specific"]["endpoint_path"],
        "provider body endpoint does not match capability snapshot",
    )
    _assert_equal(provider_body["method"], "POST", "provider body must use the image POST surface")


def _validate_attempt_binding(
    attempt: Mapping[str, Any],
    run: Mapping[str, Any],
    capability: Mapping[str, Any],
    normalized: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> None:
    request = packet["generation_request"]
    policy = request["provider_route_policy"]
    expected = {
        "generation_run_id": run["generation_run_id"],
        "intent_packet_id": packet["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": policy["provider_route_policy_id"],
        "selected_route_id": normalized["selected_route_id"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": capability["capability_snapshot_id"],
        "normalized_request_id": normalized["normalized_request_id"],
    }
    for field, value in expected.items():
        _assert_equal(attempt[field], value, f"attempt {field} drifted from its authorities")
    if (
        attempt["ordinal"] != 1
        or attempt["retry_of"] is not None
        or attempt["fallback_of"] is not None
    ):
        raise ProviderArtifactError("single-attempt P0 bundle requires ordinal one with no parent")
    request_bytes = canonical_json_bytes(normalized)
    request_ref = attempt["normalized_request_ref"]
    for field, expected_value in (
        ("artifact_id", normalized["normalized_request_id"]),
        ("content_ref", blake3(request_bytes).hexdigest()),
        ("content_sha256", hashlib.sha256(request_bytes).hexdigest()),
        ("byte_count", len(request_bytes)),
    ):
        _assert_equal(
            request_ref[field],
            expected_value,
            f"attempt normalized-request artifact {field} does not bind canonical request bytes",
        )


def _validate_receipt_binding(
    receipt: Mapping[str, Any],
    attempt: Mapping[str, Any],
    capability: Mapping[str, Any],
    normalized: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> list[str]:
    expected = {
        "attempt_id": attempt["attempt_id"],
        "normalized_request_id": normalized["normalized_request_id"],
        "requested_provider": attempt["requested_provider"],
        "requested_model": attempt["requested_model"],
        "selected_route_id": attempt["selected_route_id"],
    }
    for field, value in expected.items():
        _assert_equal(receipt[field], value, f"provider receipt {field} drifted from attempt")

    actual_model = receipt["actual_model"]
    policy = packet["generation_request"]["actual_model_policy"]
    rejection_reasons: list[str] = []
    if actual_model["state"] == "attested" and capability["actual_model_disclosure"] != "attested":
        raise ProviderArtifactError(
            "receipt claims an attested actual model that the capability cannot attest"
        )
    if actual_model["state"] == "undisclosed":
        if actual_model["model"] is not None:
            raise ProviderArtifactError("undisclosed actual model must not claim a model")
        if (
            policy != "requested_only_permitted"
            or capability["actual_model_disclosure"] == "attested"
        ):
            rejection_reasons.append("provenance_mismatch")
    elif actual_model["model"] != attempt["requested_model"]:
        rejection_reasons.append("actual_model_conflict")

    route = next(
        route
        for route in packet["generation_request"]["provider_route_policy"]["permitted_routes"]
        if route["route_id"] == attempt["selected_route_id"]
    )
    upstream = receipt["upstream_route"]
    if upstream["state"] == "disclosed" and capability["upstream_route_disclosure"] != "attested":
        raise ProviderArtifactError(
            "receipt claims an attested upstream route that the capability cannot attest"
        )
    if upstream["state"] == "disclosed":
        if upstream["provider_tag"] != route["upstream_provider_tag"]:
            rejection_reasons.append("upstream_route_conflict")
    elif (
        not packet["generation_request"]["provider_route_policy"][
            "undisclosed_upstream_routing_permitted"
        ]
        or capability["upstream_route_disclosure"] == "attested"
    ):
        rejection_reasons.append("provenance_mismatch")

    if len(receipt["outputs"]) != packet["generation_request"]["output_count"]:
        raise ProviderArtifactError("provider receipt output count differs from requested count")
    if receipt["http_status"] != 200:
        raise ProviderArtifactError("successful provider receipt must record HTTP status 200")
    return list(dict.fromkeys(rejection_reasons))


def _validate_output_binding(
    output: Mapping[str, Any],
    receipt: Mapping[str, Any],
    attempt: Mapping[str, Any],
    run: Mapping[str, Any],
    normalized: Mapping[str, Any],
    capability: Mapping[str, Any],
    packet: Mapping[str, Any],
    expected_rejection_reasons: list[str],
) -> None:
    expected = {
        "attempt_id": attempt["attempt_id"],
        "generation_run_id": run["generation_run_id"],
        "intent_packet_id": packet["intent_packet_id"],
        "normalized_request_id": normalized["normalized_request_id"],
        "provider_receipt_id": receipt["provider_receipt_id"],
    }
    for field, value in expected.items():
        _assert_equal(output[field], value, f"output occurrence {field} drifted from lineage")
    expected_admission_state = "rejected" if expected_rejection_reasons else "eligible"
    _assert_equal(
        output["admission"]["state"],
        expected_admission_state,
        "output admission does not reflect provider provenance",
    )
    _assert_equal(
        output["admission"]["rejection_reasons"],
        expected_rejection_reasons,
        "output admission reasons do not reflect provider provenance",
    )
    index = output["output_index"]
    if index >= len(receipt["outputs"]):
        raise ProviderArtifactError("output occurrence index is absent from provider receipt")
    receipt_output = receipt["outputs"][index]
    expected_receipt_output = {
        "output_index": index,
        "role": output["role"],
        "content_ref": output["original"]["content_ref"],
        "content_sha256": output["original"]["content_sha256"],
        "byte_count": output["original"]["byte_count"],
    }
    for field, expected_value in expected_receipt_output.items():
        _assert_equal(
            receipt_output[field],
            expected_value,
            f"output original {field} does not match provider receipt payload",
        )
    media_type_claim = receipt_output["media_type_claim"]
    if media_type_claim is not None and media_type_claim != output["original"]["mime"]:
        raise ProviderArtifactError(
            "output detected media type conflicts with the provider receipt claim"
        )
    output_capability = capability["outputs"]
    if output["original"]["mime"] not in output_capability["mime_types"]:
        raise ProviderArtifactError("output MIME is not authorized by the captured capability")
    for dimension in ("width", "height"):
        if output["original"][dimension] > output_capability[f"max_{dimension}"]:
            raise ProviderArtifactError(
                f"output {dimension} exceeds the captured capability dimension bound"
            )
    lineage = output["lineage"]
    _assert_equal(
        lineage["source_asset_id"],
        packet["source"]["asset_id"],
        "output lineage source asset drifted from packet",
    )
    _assert_equal(
        lineage["source_content_sha256"],
        packet["source"]["content_sha256"],
        "output lineage source bytes drifted from packet",
    )
    _assert_equal(
        lineage["reference_occurrence_ids"],
        [reference["reference_occurrence_id"] for reference in packet["references"]],
        "output lineage reference order drifted from packet",
    )


def _validate_event_bindings(
    events: Sequence[Mapping[str, Any]],
    attempt: Mapping[str, Any],
    receipt: Mapping[str, Any],
    outputs: Sequence[Mapping[str, Any]],
    expected_rejection_reasons: list[str],
) -> None:
    if not events:
        raise ProviderArtifactError("artifact bundle has no append-only attempt events")
    ordered = sorted(events, key=lambda item: item["sequence"])
    if [event["sequence"] for event in ordered] != list(range(1, len(ordered) + 1)):
        raise ProviderArtifactError("attempt event sequences must be unique and contiguous")
    states = [event["state"] for event in ordered]
    terminal_state = states[-1] if states else ""
    allowed_traces = (
        ["prepared", "submitted", "response_received", terminal_state],
        [
            "prepared",
            "submitted",
            "outcome_unknown",
            "response_received",
            terminal_state,
        ],
    )
    if terminal_state not in {"succeeded", "failed"} or states not in allowed_traces:
        raise ProviderArtifactError(
            "completed P0 state trace must be prepared, submitted, optional single "
            "outcome_unknown, response_received, succeeded-or-failed"
        )
    for event in ordered:
        _assert_equal(
            event["attempt_id"], attempt["attempt_id"], "attempt event binds another attempt"
        )
        if event["state"] == "response_received":
            _assert_equal(
                event["detail"]["provider_receipt_id"],
                receipt["provider_receipt_id"],
                "response event binds another provider receipt",
            )
        elif event["state"] == "succeeded":
            if expected_rejection_reasons or any(
                output["admission"]["state"] != "eligible" for output in outputs
            ):
                raise ProviderArtifactError(
                    "succeeded event requires every output occurrence to be eligible"
                )
            _assert_equal(
                event["detail"]["output_occurrence_ids"],
                [output["output_occurrence_id"] for output in outputs],
                "success event does not bind every output occurrence in order",
            )
    terminal_evidence = [event for event in ordered if event["state"] in {"succeeded", "failed"}]
    if len(terminal_evidence) != 1:
        raise ProviderArtifactError(
            "artifact bundle requires one success/failure terminal evidence event"
        )
    terminal = terminal_evidence[0]
    if expected_rejection_reasons:
        if (
            terminal["state"] != "failed"
            or terminal["detail"]["failure_stage"] != "provenance"
            or terminal["detail"]["failure_code"] != expected_rejection_reasons[0]
        ):
            raise ProviderArtifactError(
                "provenance-rejected outputs require matching terminal failed evidence"
            )
    elif terminal["state"] != "succeeded":
        raise ProviderArtifactError("eligible provider outputs require succeeded terminal evidence")


def validate_artifact_bundle(
    artifacts: Iterable[dict[str, Any] | ProviderArtifact],
    *,
    intent_packet: IntentPacket | dict[str, Any],
) -> None:
    """Validate one exact P0 packet/run/attempt/receipt/output artifact chain.

    This relational validator enforces the required completed-P0 trace shape; it is not the
    general ADR-0014 transition reducer. Event sequence uniqueness and occurrence cross-links are
    structural bundle facts.
    """

    packet = _packet_document(intent_packet)
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, value in enumerate(artifacts):
        if index >= _MAX_BUNDLE_ARTIFACTS:
            raise ProviderArtifactError(
                f"artifact bundle accepts at most {_MAX_BUNDLE_ARTIFACTS} artifacts"
            )
        document = _document(value)
        grouped[document["schema_version"]].append(document)
    unknown = set(grouped).difference(SCHEMA_PATHS)
    if unknown:
        raise ProviderArtifactError(
            f"artifact bundle contains unsupported schemas: {sorted(unknown)}"
        )
    for version in _SINGLETON_VERSIONS:
        if len(grouped.get(version, [])) != 1:
            raise ProviderArtifactError(
                f"artifact bundle requires exactly one {version}; "
                f"received {len(grouped.get(version, []))}"
            )
    run = _only(grouped, RUN_VERSION)
    attempt = _only(grouped, ATTEMPT_VERSION)
    capability = _only(grouped, CAPABILITY_VERSION)
    normalized = _only(grouped, REQUEST_VERSION)
    receipt = _only(grouped, RECEIPT_VERSION)
    events = grouped.get(EVENT_VERSION, [])
    outputs = sorted(grouped.get(OUTPUT_VERSION, []), key=lambda item: item["output_index"])
    if len(events) > _MAX_BUNDLE_EVENTS:
        raise ProviderArtifactError(
            f"artifact bundle accepts at most {_MAX_BUNDLE_EVENTS} attempt events"
        )
    if len(outputs) > _MAX_BUNDLE_OUTPUTS:
        raise ProviderArtifactError(
            f"artifact bundle accepts at most {_MAX_BUNDLE_OUTPUTS} output occurrences"
        )
    if not outputs:
        raise ProviderArtifactError("artifact bundle requires at least one output occurrence")

    _validate_run_binding(run, packet)
    _validate_capability_binding(capability, packet)
    _validate_request_binding(normalized, capability, packet)
    _validate_attempt_binding(attempt, run, capability, normalized, packet)
    expected_rejection_reasons = _validate_receipt_binding(
        receipt, attempt, capability, normalized, packet
    )

    seen_keys: dict[tuple[str, int], Mapping[str, Any]] = {}
    for output in outputs:
        key = (output["attempt_id"], output["output_index"])
        previous = seen_keys.get(key)
        if previous is not None:
            if previous != output:
                raise ProviderArtifactError(
                    "protocol conflict: one attempt/output index names different payloads"
                )
            raise ProviderArtifactError("duplicate output occurrence key in artifact bundle")
        seen_keys[key] = output
        _validate_output_binding(
            output,
            receipt,
            attempt,
            run,
            normalized,
            capability,
            packet,
            expected_rejection_reasons,
        )
    _assert_equal(
        [output["output_index"] for output in outputs],
        list(range(len(receipt["outputs"]))),
        "output occurrences do not cover every provider receipt payload",
    )
    _validate_event_bindings(
        events,
        attempt,
        receipt,
        outputs,
        expected_rejection_reasons,
    )
