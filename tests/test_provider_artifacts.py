"""RED contracts for ADR-0014's immutable provider artifact chain.

This module deliberately stops at closed wire artifacts, identity, and exact cross-artifact
binding.  Dispatch, retry policy, transition reduction, reconciliation, network I/O, and durable
storage behavior belong to later single-concern changes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError, is_dataclass
from typing import Any

import jsonschema
import pytest
from blake3 import blake3

import moodboard.provider_artifacts as provider_artifacts_module
from moodboard.contracts import (
    canonical_json_bytes,
    compute_document_identity,
    compute_projection_identity,
)
from moodboard.intent_packet import from_json_dict as intent_packet_from_json
from moodboard.provider_artifacts import (
    SCHEMA_PATHS,
    GenerationAttempt,
    GenerationAttemptEvent,
    GenerationRun,
    NormalizedProviderRequest,
    OutputOccurrence,
    ProviderArtifactError,
    ProviderCapabilitySnapshot,
    ProviderReceipt,
    from_json_dict,
    to_json_dict,
    validate_artifact_bundle,
    validate_provider_artifact,
)
from tests.test_intent_packet import (
    _native_mask_operation_input,
    _sync_confirmation,
    _valid_packet,
)

JsonObject = dict[str, Any]
JsonPath = tuple[str | int, ...]

RUN_VERSION = "moodboard.generation-run.v1"
ATTEMPT_VERSION = "moodboard.generation-attempt.v1"
EVENT_VERSION = "moodboard.generation-attempt-event.v1"
CAPABILITY_VERSION = "moodboard.provider-capability-snapshot.v1"
REQUEST_VERSION = "moodboard.normalized-provider-request.v1"
RECEIPT_VERSION = "moodboard.provider-receipt.v1"
OUTPUT_VERSION = "moodboard.output-occurrence.v1"

_SCHEMA_VERSIONS = (
    RUN_VERSION,
    ATTEMPT_VERSION,
    EVENT_VERSION,
    CAPABILITY_VERSION,
    REQUEST_VERSION,
    RECEIPT_VERSION,
    OUTPUT_VERSION,
)
_FULL_DOCUMENT_IDENTITIES = {
    EVENT_VERSION: "attempt_event_id",
    CAPABILITY_VERSION: "capability_snapshot_id",
    REQUEST_VERSION: "normalized_request_id",
    RECEIPT_VERSION: "provider_receipt_id",
}
_GOLDEN_IDENTITIES = {
    EVENT_VERSION: "a7f29a65b126c65f3a3363646a0d4d49bf519e35262717c177b0220f72aaecaf",
    CAPABILITY_VERSION: "85c9636bce925c80ad8daacd5cf3dcf862bb1fccfa322914594ccf20a293cb62",
    REQUEST_VERSION: "0a948303d3ce1c75879db5ae160d0c4e974793a75253177dd92c5530ec3b33f2",
    RECEIPT_VERSION: "f7e2f090eec674c6b64efbc7b6919586bcc2abcc44f0b2b6af5c23b39d794316",
    OUTPUT_VERSION: "186039478aeaf68fd5adeb3abfc7bf12ab557dca7aaaaaac783150f658b0483d",
}
_GOLDEN_REQUEST_KEY = "8373114217d049791f6c02759a8e174326f45c0a5c1216eaddd924c122cae125"

_RUN_ID = "20000000-0000-4000-8000-000000000001"
_ATTEMPT_ID = "20000000-0000-4000-8000-000000000002"
_CLAIM_ID = "20000000-0000-4000-8000-000000000003"

_TIMESTAMP_FIELDS = (
    (RUN_VERSION, "created_at"),
    (ATTEMPT_VERSION, "created_at"),
    (EVENT_VERSION, "recorded_at"),
    (CAPABILITY_VERSION, "captured_at"),
    (RECEIPT_VERSION, "received_at"),
)


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _refresh_document_id(document: JsonObject) -> JsonObject:
    version = document["schema_version"]
    field = _FULL_DOCUMENT_IDENTITIES[version]
    document[field] = "0" * 64
    document[field] = compute_document_identity(
        document,
        schema_version=version,
        identity_field=field,
    )
    return document


def _output_id(attempt_id: str, output_index: int) -> str:
    return compute_projection_identity(
        {"attempt_id": attempt_id, "output_index": output_index},
        domain_tag=OUTPUT_VERSION,
    )


def _request_key(
    *,
    generation_run_id: str,
    attempt_id: str,
    intent_packet_id: str,
    adapter_revision: str,
    normalized_request_id: str,
) -> str:
    return compute_projection_identity(
        {
            "generation_run_id": generation_run_id,
            "attempt_id": attempt_id,
            "intent_packet_id": intent_packet_id,
            "adapter_revision": adapter_revision,
            "normalized_request_id": normalized_request_id,
        },
        domain_tag="moodboard.provider-request-key.v1",
    )


def _valid_capability(packet: JsonObject) -> JsonObject:
    request = packet["generation_request"]
    document: JsonObject = {
        "schema_version": CAPABILITY_VERSION,
        "capability_snapshot_id": "0" * 64,
        "captured_at": "2026-08-16T20:30:01Z",
        "adapter_revision": request["adapter_revision"],
        "provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "input_modalities": ["text", "image"],
        "image_input_budget": {
            "supported": True,
            "max_count": 4,
            "ordered": True,
            "source_and_references_share_budget": True,
            "provider_roles": ["source_image", "visual_context"],
        },
        "outputs": {
            "min_count": 1,
            "max_count": 6,
            "mime_types": ["image/png"],
            "resolutions": ["1K", "2K"],
            "aspect_ratios": ["1:1", "4:3", "3:4"],
            "max_width": 4096,
            "max_height": 4096,
        },
        "options": {
            "schema_version": "moodboard.openrouter-images-options-capability.v1",
            "seed_supported": True,
            "resolutions": ["1K", "2K"],
            "aspect_ratios": ["1:1", "4:3", "3:4"],
        },
        "operation_input_capabilities": [
            {
                "capability_id": request["operation_inputs"][0]["capability_id"],
                "role": "source_image",
                "delivery_modes": ["native_input"],
                "provider_roles": ["source_image"],
                "provider_fields": ["input_references[0]"],
            },
            {
                "capability_id": request["operation_inputs"][1]["capability_id"],
                "role": "locality_mask",
                "delivery_modes": ["not_sent"],
                "provider_roles": [],
                "provider_fields": [],
            },
        ],
        "actual_model_disclosure": "not_attested",
        "upstream_route_disclosure": "not_attested",
        "idempotency": copy.deepcopy(request["idempotency"]),
        "reconciliation": copy.deepcopy(request["reconciliation"]),
        "provider_specific": {
            "schema_version": "moodboard.openrouter-images-capability.v1",
            "endpoint_path": "/api/v1/images",
            "discovery_endpoint_path": ("/api/v1/images/models/qwen/qwen-image-3/endpoints"),
            "upstream_provider_tags": ["alibaba"],
            "input_reference_parameter": "input_references",
            "supports_streaming": False,
            "allowed_passthrough_parameters": [],
            "discovery_response": {
                "content_ref": _digest("c"),
                "content_sha256": _digest("d"),
                "byte_count": 4096,
            },
        },
    }
    return _refresh_document_id(document)


def _reference_use(packet: JsonObject) -> list[JsonObject]:
    attached, prompt_only, not_sent = packet["references"]
    return [
        {
            "reference_occurrence_id": attached["reference_occurrence_id"],
            "provider_use": "attached_image",
            "provider_position": 1,
            "provider_field": "input_references[1]",
            "provider_role": "visual_context",
            "content_sha256": attached["content_sha256"],
            "prompt_context": None,
        },
        {
            "reference_occurrence_id": prompt_only["reference_occurrence_id"],
            "provider_use": "prompt_context_only",
            "provider_position": None,
            "provider_field": None,
            "provider_role": None,
            "content_sha256": None,
            "prompt_context": copy.deepcopy(prompt_only["prompt_context"]),
        },
        {
            "reference_occurrence_id": not_sent["reference_occurrence_id"],
            "provider_use": "not_sent",
            "provider_position": None,
            "provider_field": None,
            "provider_role": None,
            "content_sha256": None,
            "prompt_context": None,
        },
    ]


def _valid_normalized_request(packet: JsonObject, capability: JsonObject) -> JsonObject:
    request = packet["generation_request"]
    final_prompt = (
        "Replace only the selected tree with a mature lemon tree. "
        "Reference context: Mature lemon canopy; Natural branching structure."
    )
    reference_use = _reference_use(packet)
    document: JsonObject = {
        "schema_version": REQUEST_VERSION,
        "normalized_request_id": "0" * 64,
        "intent_packet_id": packet["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "selected_route_id": request["provider_route_policy"]["permitted_routes"][0]["route_id"],
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": capability["capability_snapshot_id"],
        "prompt": {
            "text": final_prompt,
            "compiler_revision": "moodboard.openrouter-prompt.v1",
        },
        "output_count": request["output_count"],
        "options": copy.deepcopy(request["options"]),
        "operation_inputs": copy.deepcopy(request["operation_inputs"]),
        "reference_use": reference_use,
        "destination": copy.deepcopy(request["destination"]),
        "provider_body": {
            "schema_version": "moodboard.openrouter-images-body-projection.v1",
            "method": "POST",
            "endpoint_path": "/api/v1/images",
            "model": request["requested_model"],
            "prompt": final_prompt,
            "n": request["output_count"],
            "seed": request["options"]["seed"],
            "resolution": request["options"]["resolution"],
            "aspect_ratio": request["options"]["aspect_ratio"],
            "input_references": [
                {
                    "position": 0,
                    "provider_field": "input_references[0].image_url.url",
                    "item_type": "image_url",
                    "transport": "data_url",
                    "media_type": packet["source"]["mime"],
                    "role": "source_image",
                    "source_kind": "operation_input",
                    "source_id": request["operation_inputs"][0]["original_artifact"]["asset_id"],
                    "content_sha256": request["operation_inputs"][0]["delivered_artifact"][
                        "content_sha256"
                    ],
                    "transport_value_sha256": _digest("e"),
                },
                {
                    "position": 1,
                    "provider_field": "input_references[1].image_url.url",
                    "item_type": "image_url",
                    "transport": "data_url",
                    "media_type": "image/png",
                    "role": "visual_context",
                    "source_kind": "reference_occurrence",
                    "source_id": packet["references"][0]["reference_occurrence_id"],
                    "content_sha256": packet["references"][0]["content_sha256"],
                    "transport_value_sha256": _digest("f"),
                },
            ],
            "provider": {
                "only": ["alibaba"],
                "allow_fallbacks": False,
            },
        },
    }
    return _refresh_document_id(document)


def _valid_run(packet: JsonObject) -> JsonObject:
    request = packet["generation_request"]
    return {
        "schema_version": RUN_VERSION,
        "generation_run_id": _RUN_ID,
        "creative_session_id": packet["creative_session_id"],
        "intent_packet_id": packet["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "created_at": "2026-08-16T20:30:02Z",
    }


def _valid_attempt(
    packet: JsonObject,
    run: JsonObject,
    capability: JsonObject,
    normalized_request: JsonObject,
) -> JsonObject:
    request = packet["generation_request"]
    request_key = _request_key(
        generation_run_id=run["generation_run_id"],
        attempt_id=_ATTEMPT_ID,
        intent_packet_id=packet["intent_packet_id"],
        adapter_revision=request["adapter_revision"],
        normalized_request_id=normalized_request["normalized_request_id"],
    )
    normalized_request_bytes = canonical_json_bytes(normalized_request)
    return {
        "schema_version": ATTEMPT_VERSION,
        "attempt_id": _ATTEMPT_ID,
        "generation_run_id": run["generation_run_id"],
        "intent_packet_id": packet["intent_packet_id"],
        "ordinal": 1,
        "retry_of": None,
        "fallback_of": None,
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "selected_route_id": request["provider_route_policy"]["permitted_routes"][0]["route_id"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": capability["capability_snapshot_id"],
        "normalized_request_id": normalized_request["normalized_request_id"],
        "normalized_request_ref": {
            "schema_version": REQUEST_VERSION,
            "artifact_id": normalized_request["normalized_request_id"],
            "content_ref": blake3(normalized_request_bytes).hexdigest(),
            "content_sha256": hashlib.sha256(normalized_request_bytes).hexdigest(),
            "byte_count": len(normalized_request_bytes),
        },
        "request_key_sha256": request_key,
        "created_at": "2026-08-16T20:30:03Z",
    }


def _valid_receipt(
    packet: JsonObject,
    attempt: JsonObject,
    normalized_request: JsonObject,
) -> JsonObject:
    document: JsonObject = {
        "schema_version": RECEIPT_VERSION,
        "provider_receipt_id": "0" * 64,
        "attempt_id": attempt["attempt_id"],
        "normalized_request_id": normalized_request["normalized_request_id"],
        "received_at": "2026-08-16T20:30:05Z",
        "requested_provider": attempt["requested_provider"],
        "requested_model": attempt["requested_model"],
        "selected_route_id": attempt["selected_route_id"],
        "http_status": 200,
        "provider_handle": None,
        "actual_model": {"state": "undisclosed", "model": None, "source_field": None},
        "upstream_route": {
            "state": "unknown",
            "provider_tag": None,
            "source_field": None,
        },
        "raw_response": {
            "state": "retained",
            "content_ref": _digest("7"),
            "content_sha256": _digest("8"),
            "byte_count": 804_211,
            "privacy": "private_provider_payload",
        },
        "outputs": [
            {
                "output_index": 0,
                "role": "generated_image",
                "content_ref": _digest("9"),
                "content_sha256": _digest("a"),
                "byte_count": 803_992,
                "media_type_claim": "image/png",
            }
        ],
        "cost": {
            "state": "reported",
            "amount": "0.030000",
            "currency": "USD",
            "provenance": "provider_receipt",
        },
        "latency": {
            "milliseconds": 2450,
            "boundary": "submit_to_response_received",
        },
    }
    return _refresh_document_id(document)


def _valid_output(
    packet: JsonObject,
    run: JsonObject,
    attempt: JsonObject,
    normalized_request: JsonObject,
    receipt: JsonObject,
) -> JsonObject:
    provider_output = receipt["outputs"][0]
    return {
        "schema_version": OUTPUT_VERSION,
        "output_occurrence_id": _output_id(attempt["attempt_id"], 0),
        "producer_kind": "generator_raw",
        "attempt_id": attempt["attempt_id"],
        "output_index": 0,
        "role": "generated_image",
        "generation_run_id": run["generation_run_id"],
        "intent_packet_id": packet["intent_packet_id"],
        "normalized_request_id": normalized_request["normalized_request_id"],
        "provider_receipt_id": receipt["provider_receipt_id"],
        "original": {
            "content_ref": provider_output["content_ref"],
            "content_sha256": provider_output["content_sha256"],
            "mime": "image/png",
            "byte_count": 803_992,
            "width": 1280,
            "height": 960,
        },
        "media_validation": {
            "schema_version": "moodboard.media-validation.v1",
            "state": "pass",
            "decoder_revision": "pillow-12.3-image.v1",
            "measured_content_sha256": provider_output["content_sha256"],
            "measured_content_ref": provider_output["content_ref"],
            "measured_byte_count": provider_output["byte_count"],
            "measured_mime": "image/png",
            "measured_width": 1280,
            "measured_height": 960,
            "measured_mode": "RGB",
            "frame_count": 1,
            "active_content": False,
            "bounded": True,
        },
        "admission": {"state": "eligible", "rejection_reasons": []},
        "lineage": {
            "source_asset_id": packet["source"]["asset_id"],
            "source_content_sha256": packet["source"]["content_sha256"],
            "reference_occurrence_ids": [
                reference["reference_occurrence_id"] for reference in packet["references"]
            ],
        },
    }


def _event(
    *,
    attempt_id: str,
    sequence: int,
    state: str,
    recorded_at: str,
    detail: JsonObject,
) -> JsonObject:
    return _refresh_document_id(
        {
            "schema_version": EVENT_VERSION,
            "attempt_event_id": "0" * 64,
            "attempt_id": attempt_id,
            "sequence": sequence,
            "state": state,
            "recorded_at": recorded_at,
            "detail": detail,
        }
    )


def _valid_events(
    attempt: JsonObject,
    capability: JsonObject,
    normalized_request: JsonObject,
    receipt: JsonObject,
    output: JsonObject,
) -> list[JsonObject]:
    return [
        _event(
            attempt_id=attempt["attempt_id"],
            sequence=1,
            state="prepared",
            recorded_at="2026-08-16T20:30:03Z",
            detail={
                "kind": "prepared",
            },
        ),
        _event(
            attempt_id=attempt["attempt_id"],
            sequence=2,
            state="submitted",
            recorded_at="2026-08-16T20:30:04Z",
            detail={
                "kind": "submitted",
                "provider_handle": None,
            },
        ),
        _event(
            attempt_id=attempt["attempt_id"],
            sequence=3,
            state="response_received",
            recorded_at="2026-08-16T20:30:05Z",
            detail={
                "kind": "response_received",
                "provider_receipt_id": receipt["provider_receipt_id"],
            },
        ),
        _event(
            attempt_id=attempt["attempt_id"],
            sequence=4,
            state="succeeded",
            recorded_at="2026-08-16T20:30:06Z",
            detail={
                "kind": "succeeded",
                "output_occurrence_ids": [output["output_occurrence_id"]],
            },
        ),
    ]


def _valid_artifact_chain(
    *,
    actual_model_disclosure: str = "not_attested",
    upstream_route_disclosure: str = "not_attested",
    native_mask: bool = False,
    input_modalities: list[str] | None = None,
) -> tuple[JsonObject, list[JsonObject]]:
    packet = _valid_packet()
    if native_mask:
        packet["generation_request"]["operation_inputs"][1] = _native_mask_operation_input()
        _sync_confirmation(packet)
    capability = _valid_capability(packet)
    if input_modalities is not None:
        capability["input_modalities"] = input_modalities
    if native_mask:
        mask_capability = capability["operation_input_capabilities"][1]
        mask_capability["delivery_modes"] = ["native_mask"]
        mask_capability["provider_roles"] = ["locality_mask"]
        mask_capability["provider_fields"] = ["mask"]
        capability["image_input_budget"]["provider_roles"].append("locality_mask")
    capability["actual_model_disclosure"] = actual_model_disclosure
    capability["upstream_route_disclosure"] = upstream_route_disclosure
    _refresh_document_id(capability)
    packet["generation_request"]["capability_snapshot_id"] = capability["capability_snapshot_id"]
    _sync_confirmation(packet)
    packet["intent_packet_id"] = "0" * 64
    packet["intent_packet_id"] = compute_document_identity(
        packet,
        schema_version="moodboard.intent-packet.v1",
        identity_field="intent_packet_id",
    )
    normalized_request = _valid_normalized_request(packet, capability)
    run = _valid_run(packet)
    attempt = _valid_attempt(packet, run, capability, normalized_request)
    receipt = _valid_receipt(packet, attempt, normalized_request)
    output = _valid_output(packet, run, attempt, normalized_request, receipt)
    events = _valid_events(attempt, capability, normalized_request, receipt, output)
    artifacts = [run, capability, normalized_request, attempt, *events, receipt, output]
    return packet, artifacts


def _object_paths(value: Any, path: JsonPath = ()) -> list[JsonPath]:
    paths: list[JsonPath] = []
    if isinstance(value, dict):
        paths.append(path)
        for key, nested in value.items():
            paths.extend(_object_paths(nested, (*path, key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_object_paths(nested, (*path, index)))
    return paths


def _at_path(document: Any, path: JsonPath) -> Any:
    value = document
    for segment in path:
        value = value[segment]
    return value


def _refresh_if_content_addressed(document: JsonObject) -> None:
    if document["schema_version"] in _FULL_DOCUMENT_IDENTITIES:
        _refresh_document_id(document)


def _artifact(artifacts: list[JsonObject], schema_version: str) -> JsonObject:
    matches = [item for item in artifacts if item["schema_version"] == schema_version]
    assert len(matches) == 1
    return matches[0]


def _first_artifact(artifacts: list[JsonObject], schema_version: str) -> JsonObject:
    matches = [item for item in artifacts if item["schema_version"] == schema_version]
    assert matches
    return matches[0]


def _refresh_receipt_dependents(artifacts: list[JsonObject]) -> None:
    """Refresh the immutable links downstream from an intentionally changed receipt."""

    receipt = _artifact(artifacts, RECEIPT_VERSION)
    _refresh_document_id(receipt)
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["provider_receipt_id"] = receipt["provider_receipt_id"]
    response_event = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "response_received"
    )
    response_event["detail"]["provider_receipt_id"] = receipt["provider_receipt_id"]
    _refresh_document_id(response_event)


def _refresh_normalized_request_dependents(artifacts: list[JsonObject]) -> None:
    """Refresh exact downstream references without changing packet authority."""

    normalized = _artifact(artifacts, REQUEST_VERSION)
    _refresh_document_id(normalized)
    normalized_bytes = canonical_json_bytes(normalized)
    attempt = _artifact(artifacts, ATTEMPT_VERSION)
    attempt["normalized_request_id"] = normalized["normalized_request_id"]
    attempt["normalized_request_ref"] = {
        "schema_version": REQUEST_VERSION,
        "artifact_id": normalized["normalized_request_id"],
        "content_ref": blake3(normalized_bytes).hexdigest(),
        "content_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
        "byte_count": len(normalized_bytes),
    }
    attempt["request_key_sha256"] = _request_key(
        generation_run_id=attempt["generation_run_id"],
        attempt_id=attempt["attempt_id"],
        intent_packet_id=attempt["intent_packet_id"],
        adapter_revision=attempt["adapter_revision"],
        normalized_request_id=normalized["normalized_request_id"],
    )
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt["normalized_request_id"] = normalized["normalized_request_id"]
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["normalized_request_id"] = normalized["normalized_request_id"]
    _refresh_receipt_dependents(artifacts)


def _drop_event_and_resequence(
    artifacts: list[JsonObject],
    *,
    state: str,
) -> list[JsonObject]:
    retained = [
        item
        for item in artifacts
        if item["schema_version"] != EVENT_VERSION or item["state"] != state
    ]
    events = [item for item in retained if item["schema_version"] == EVENT_VERSION]
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
        _refresh_document_id(event)
    return retained


def test_all_provider_artifact_schemas_are_closed_draft_2020_12_contracts() -> None:
    packet, artifacts = _valid_artifact_chain()
    assert packet["schema_version"] == "moodboard.intent-packet.v1"
    assert set(SCHEMA_PATHS) == set(_SCHEMA_VERSIONS)

    for version in _SCHEMA_VERSIONS:
        path = SCHEMA_PATHS[version]
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        jsonschema.Draft202012Validator.check_schema(schema)

    for document in artifacts:
        validate_provider_artifact(document)


@pytest.mark.parametrize(("schema_version", "field"), _TIMESTAMP_FIELDS)
def test_every_provider_artifact_timestamp_field_rejects_an_impossible_date(
    schema_version: str,
    field: str,
) -> None:
    _, artifacts = _valid_artifact_chain()
    document = copy.deepcopy(_first_artifact(artifacts, schema_version))
    document[field] = "2026-02-30T20:30:02Z"
    _refresh_if_content_addressed(document)

    with pytest.raises(
        ProviderArtifactError,
        match=rf"^{field} must be a real canonical UTC timestamp$",
    ):
        validate_provider_artifact(document)


@pytest.mark.parametrize(
    "timestamp",
    (
        "0000-01-01T00:00:00Z",
        "2025-02-29T20:30:02Z",
        "2026-04-31T20:30:02Z",
        "2026-13-01T20:30:02Z",
        "2026-08-16T24:00:00Z",
        "2026-08-16T20:60:00Z",
        "2026-08-16T20:30:60Z",
        "2026-08-16T20:30:02+00:00",
        "2026-08-16T16:30:02-04:00",
        "2026-08-16t20:30:02z",
        "2026-08-16T20:30:02.1234567890Z",
    ),
)
def test_provider_artifact_timestamps_reject_impossible_or_noncanonical_values(
    timestamp: str,
) -> None:
    document = _valid_run(_valid_packet())
    document["created_at"] = timestamp

    with pytest.raises(
        ProviderArtifactError,
        match=r"^created_at must be a real canonical UTC timestamp$",
    ):
        validate_provider_artifact(document)


@pytest.mark.parametrize(
    "timestamp",
    (
        "2024-02-29T00:00:00Z",
        "2026-08-16T20:30:02Z",
        "2026-08-16T20:30:02.1Z",
        "2026-08-16T20:30:02.123456789Z",
    ),
)
def test_provider_artifact_timestamps_accept_real_canonical_utc_forms(timestamp: str) -> None:
    document = _valid_run(_valid_packet())
    document["created_at"] = timestamp

    validate_provider_artifact(document)


def test_every_provider_artifact_object_is_recursively_closed() -> None:
    _, artifacts = _valid_artifact_chain()

    for document in artifacts:
        for path in _object_paths(document):
            tampered = copy.deepcopy(document)
            _at_path(tampered, path)["unexpected"] = "closed means closed"
            _refresh_if_content_addressed(tampered)
            with pytest.raises(ProviderArtifactError):
                validate_provider_artifact(tampered)


def test_all_seven_artifact_branches_round_trip_as_immutable_values() -> None:
    _, artifacts = _valid_artifact_chain()
    expected_types = {
        RUN_VERSION: GenerationRun,
        ATTEMPT_VERSION: GenerationAttempt,
        EVENT_VERSION: GenerationAttemptEvent,
        CAPABILITY_VERSION: ProviderCapabilitySnapshot,
        REQUEST_VERSION: NormalizedProviderRequest,
        RECEIPT_VERSION: ProviderReceipt,
        OUTPUT_VERSION: OutputOccurrence,
    }

    seen: set[str] = set()
    for document in artifacts:
        version = document["schema_version"]
        if version in seen:
            continue
        seen.add(version)
        frozen = from_json_dict(document)
        assert is_dataclass(frozen)
        assert isinstance(frozen, expected_types[version])
        assert to_json_dict(frozen) == document
        with pytest.raises(FrozenInstanceError):
            frozen.schema_version = "tampered"  # type: ignore[misc]
        detached = to_json_dict(frozen)
        detached["schema_version"] = "tampered"
        assert to_json_dict(frozen) == document


def test_content_addressed_artifacts_use_full_document_domain_separated_identities() -> None:
    _, artifacts = _valid_artifact_chain()

    for version, field in _FULL_DOCUMENT_IDENTITIES.items():
        document = next(item for item in artifacts if item["schema_version"] == version)
        assert document[field] == compute_document_identity(
            document,
            schema_version=version,
            identity_field=field,
        )
        tampered = copy.deepcopy(document)
        if version == EVENT_VERSION:
            tampered["recorded_at"] = "2026-08-16T20:31:00Z"
        elif version == CAPABILITY_VERSION:
            tampered["outputs"]["max_count"] = 5
        elif version == REQUEST_VERSION:
            tampered["prompt"]["text"] += " Drift."
        else:
            tampered["cost"]["amount"] = "0.040000"
        with pytest.raises(ProviderArtifactError, match="identity"):
            validate_provider_artifact(tampered)


def test_provider_artifact_identity_golden_vectors_are_not_self_referential() -> None:
    _, artifacts = _valid_artifact_chain()

    for version, expected in _GOLDEN_IDENTITIES.items():
        document = next(item for item in artifacts if item["schema_version"] == version)
        field = (
            "output_occurrence_id"
            if version == OUTPUT_VERSION
            else _FULL_DOCUMENT_IDENTITIES[version]
        )
        assert document[field] == expected

    attempt = _artifact(artifacts, ATTEMPT_VERSION)
    assert attempt["request_key_sha256"] == _GOLDEN_REQUEST_KEY


def test_run_and_attempt_use_occurrence_uuids_not_content_hashes() -> None:
    _, artifacts = _valid_artifact_chain()
    run = _artifact(artifacts, RUN_VERSION)
    attempt = _artifact(artifacts, ATTEMPT_VERSION)

    assert run["generation_run_id"] == _RUN_ID
    assert attempt["attempt_id"] == _ATTEMPT_ID
    packet_id = run["intent_packet_id"]
    assert run["generation_run_id"] != packet_id
    assert len(packet_id) == 64
    validate_provider_artifact(run)
    validate_provider_artifact(attempt)


def test_output_identity_is_only_the_adr_fixed_attempt_index_key() -> None:
    _, artifacts = _valid_artifact_chain()
    output = _artifact(artifacts, OUTPUT_VERSION)

    assert output["output_occurrence_id"] == _output_id(_ATTEMPT_ID, 0)
    changed_payload = copy.deepcopy(output)
    changed_payload["original"]["content_sha256"] = _digest("b")
    changed_payload["media_validation"]["measured_content_sha256"] = _digest("b")
    assert changed_payload["output_occurrence_id"] == _output_id(_ATTEMPT_ID, 0)
    validate_provider_artifact(changed_payload)


def test_complete_artifact_chain_cross_binds_the_exact_frozen_packet() -> None:
    packet, artifacts = _valid_artifact_chain()

    assert (
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )
        is None
    )


@pytest.mark.parametrize(
    ("version", "path", "replacement"),
    (
        (RUN_VERSION, ("intent_packet_id",), _digest("1")),
        (ATTEMPT_VERSION, ("generation_run_id",), "20000000-0000-4000-8000-999999999999"),
        (ATTEMPT_VERSION, ("selected_route_id",), "route-not-confirmed"),
        (CAPABILITY_VERSION, ("provider",), "different-provider"),
        (CAPABILITY_VERSION, ("adapter_revision",), "moodboard.different-adapter.v1"),
        (REQUEST_VERSION, ("options", "seed"), 18),
        (REQUEST_VERSION, ("operation_inputs", 1, "delivery_mode"), "prompt_only"),
        (REQUEST_VERSION, ("reference_use", 0, "provider_use"), "not_sent"),
        (REQUEST_VERSION, ("destination", "privacy_class"), "different-boundary"),
        (RECEIPT_VERSION, ("attempt_id",), "20000000-0000-4000-8000-999999999999"),
        (RECEIPT_VERSION, ("actual_model", "model"), "vendor/substituted-model"),
        (OUTPUT_VERSION, ("provider_receipt_id",), _digest("2")),
        (OUTPUT_VERSION, ("lineage", "source_content_sha256"), _digest("e")),
    ),
)
def test_bundle_rejects_cross_link_drift_even_when_local_identity_is_recomputed(
    version: str,
    path: JsonPath,
    replacement: Any,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    mutated = copy.deepcopy(artifacts)
    document = _artifact(mutated, version)
    _at_path(document, path[:-1])[path[-1]] = replacement
    _refresh_if_content_addressed(document)

    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(mutated, intent_packet=intent_packet_from_json(packet))


def test_normalized_request_binds_prompt_adapter_options_inputs_and_reference_order() -> None:
    packet, artifacts = _valid_artifact_chain()
    base = _artifact(artifacts, REQUEST_VERSION)
    mutations = (
        lambda value: value["prompt"].__setitem__("text", "Different final prompt"),
        lambda value: value.__setitem__("adapter_revision", "moodboard.changed-adapter.v1"),
        lambda value: value["options"].__setitem__("resolution", "2K"),
        lambda value: value["operation_inputs"].reverse(),
        lambda value: value["reference_use"].reverse(),
        lambda value: value["provider_body"]["input_references"].reverse(),
    )
    for mutate in mutations:
        changed_artifacts = copy.deepcopy(artifacts)
        request = _artifact(changed_artifacts, REQUEST_VERSION)
        mutate(request)
        _refresh_document_id(request)
        with pytest.raises(ProviderArtifactError):
            validate_artifact_bundle(
                changed_artifacts,
                intent_packet=intent_packet_from_json(packet),
            )

    assert (
        base["reference_use"][0]["reference_occurrence_id"]
        == packet["references"][0]["reference_occurrence_id"]
    )


def test_capability_snapshot_authorizes_the_exact_confirmed_surface() -> None:
    packet, artifacts = _valid_artifact_chain()
    mutations = (
        lambda value: value["image_input_budget"].__setitem__("max_count", 1),
        lambda value: value["outputs"].__setitem__("max_count", 0),
        lambda value: value["options"].__setitem__("seed_supported", False),
        lambda value: value["operation_input_capabilities"][0]["delivery_modes"].clear(),
        lambda value: value["idempotency"].__setitem__("provider_accepts_key", True),
        lambda value: value["reconciliation"].__setitem__("supported", True),
    )
    for mutate in mutations:
        changed_artifacts = copy.deepcopy(artifacts)
        capability = _artifact(changed_artifacts, CAPABILITY_VERSION)
        mutate(capability)
        _refresh_document_id(capability)
        with pytest.raises(ProviderArtifactError):
            validate_artifact_bundle(
                changed_artifacts,
                intent_packet=intent_packet_from_json(packet),
            )


def test_openrouter_capability_must_advertise_every_dispatched_input_modality() -> None:
    packet, artifacts = _valid_artifact_chain(input_modalities=["text"])

    with pytest.raises(ProviderArtifactError, match=r"(?i)(image.*modality|modality.*image)"):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


def test_events_are_append_only_occurrences_but_transition_reduction_is_not_here() -> None:
    packet, artifacts = _valid_artifact_chain()
    events = [item for item in artifacts if item["schema_version"] == EVENT_VERSION]

    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert len({event["attempt_event_id"] for event in events}) == 4

    duplicate_sequence = copy.deepcopy(artifacts)
    changed_events = [
        item for item in duplicate_sequence if item["schema_version"] == EVENT_VERSION
    ]
    changed_events[1]["sequence"] = 1
    _refresh_document_id(changed_events[1])
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            duplicate_sequence,
            intent_packet=intent_packet_from_json(packet),
        )

    # No legal-transition assertion belongs here; that reducer is the next single-concern PR.
    synthetic_terminal = copy.deepcopy(events[-1])
    synthetic_terminal["state"] = "failed"
    synthetic_terminal["detail"] = {
        "kind": "failed",
        "failure_stage": "provider",
        "failure_code": "synthetic_failure",
    }
    _refresh_document_id(synthetic_terminal)
    validate_provider_artifact(synthetic_terminal)


def test_every_attempt_event_state_has_one_closed_detail_branch() -> None:
    cases = (
        ("prepared", {"kind": "prepared"}),
        ("submitted", {"kind": "submitted", "provider_handle": None}),
        (
            "outcome_unknown",
            {
                "kind": "outcome_unknown",
                "failure_stage": "dispatch",
                "failure_code": "ambiguous_transport",
                "provider_handle": None,
            },
        ),
        (
            "response_received",
            {"kind": "response_received", "provider_receipt_id": _digest("1")},
        ),
        (
            "succeeded",
            {"kind": "succeeded", "output_occurrence_ids": [_digest("2")]},
        ),
        (
            "failed",
            {
                "kind": "failed",
                "failure_stage": "output_validation",
                "failure_code": "invalid_image_payload",
            },
        ),
        (
            "cancelled",
            {
                "kind": "cancelled",
                "cancellation_stage": "reconciliation",
                "cancellation_code": "provider_confirmed_cancelled",
                "authority": "provider_confirmed_no_output",
            },
        ),
    )

    for sequence, (state, detail) in enumerate(cases, start=1):
        event = _event(
            attempt_id=_ATTEMPT_ID,
            sequence=sequence,
            state=state,
            recorded_at=f"2026-08-16T20:31:{sequence:02d}Z",
            detail=detail,
        )
        validate_provider_artifact(event)

        wrong_branch = copy.deepcopy(event)
        wrong_branch["detail"]["kind"] = "prepared"
        _refresh_document_id(wrong_branch)
        if state != "prepared":
            with pytest.raises(ProviderArtifactError):
                validate_provider_artifact(wrong_branch)


def test_output_media_and_receipt_bytes_must_agree_before_eligibility() -> None:
    packet, artifacts = _valid_artifact_chain()
    mutations = (
        lambda value: value["original"].__setitem__("content_sha256", _digest("b")),
        lambda value: value["original"].__setitem__("content_ref", _digest("c")),
        lambda value: value["media_validation"].__setitem__("measured_mime", "image/jpeg"),
        lambda value: value["media_validation"].__setitem__("measured_width", 1279),
        lambda value: value["admission"].__setitem__("rejection_reasons", ["provenance_mismatch"]),
    )
    for mutate in mutations:
        changed_artifacts = copy.deepcopy(artifacts)
        output = _artifact(changed_artifacts, OUTPUT_VERSION)
        mutate(output)
        with pytest.raises(ProviderArtifactError):
            validate_artifact_bundle(
                changed_artifacts,
                intent_packet=intent_packet_from_json(packet),
            )


def test_conflicting_attested_model_is_retained_as_rejected_and_cannot_be_laundered() -> None:
    packet, artifacts = _valid_artifact_chain(actual_model_disclosure="attested")
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt["actual_model"] = {
        "state": "attested",
        "model": "vendor/substituted-model",
        "source_field": "$.model",
    }
    _refresh_document_id(receipt)
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["provider_receipt_id"] = receipt["provider_receipt_id"]
    response_event = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "response_received"
    )
    response_event["detail"]["provider_receipt_id"] = receipt["provider_receipt_id"]
    _refresh_document_id(response_event)

    with pytest.raises(ProviderArtifactError, match="admission|model|provenance"):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )

    output["admission"] = {
        "state": "rejected",
        "rejection_reasons": ["actual_model_conflict"],
    }
    terminal = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "succeeded"
    )
    terminal["state"] = "failed"
    terminal["detail"] = {
        "kind": "failed",
        "failure_stage": "provenance",
        "failure_code": "actual_model_conflict",
    }
    _refresh_document_id(terminal)

    assert (
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )
        is None
    )


def test_optional_provider_media_type_claim_is_not_invented_from_detected_bytes() -> None:
    packet, artifacts = _valid_artifact_chain()
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt["outputs"][0]["media_type_claim"] = None
    _refresh_document_id(receipt)
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["provider_receipt_id"] = receipt["provider_receipt_id"]
    response_event = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "response_received"
    )
    response_event["detail"]["provider_receipt_id"] = receipt["provider_receipt_id"]
    _refresh_document_id(response_event)

    assert (
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )
        is None
    )
    assert output["original"]["mime"] == "image/png"
    assert receipt["outputs"][0]["media_type_claim"] is None


def test_same_attempt_output_key_cannot_name_two_different_payloads() -> None:
    packet, artifacts = _valid_artifact_chain()
    original = _artifact(artifacts, OUTPUT_VERSION)
    conflict = copy.deepcopy(original)
    conflict["original"]["content_sha256"] = _digest("b")
    conflict["media_validation"]["measured_content_sha256"] = _digest("b")
    assert conflict["output_occurrence_id"] == original["output_occurrence_id"]

    with pytest.raises(ProviderArtifactError, match="conflict|duplicate"):
        validate_artifact_bundle(
            [*artifacts, conflict],
            intent_packet=intent_packet_from_json(packet),
        )


def test_bundle_rejects_duplicate_singletons_and_missing_payload_references() -> None:
    packet, artifacts = _valid_artifact_chain()
    capability = _artifact(artifacts, CAPABILITY_VERSION)

    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            [*artifacts, copy.deepcopy(capability)],
            intent_packet=intent_packet_from_json(packet),
        )

    missing_receipt = [item for item in artifacts if item["schema_version"] != RECEIPT_VERSION]
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            missing_receipt,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("version", "path", "secret_field"),
    (
        (ATTEMPT_VERSION, (), "api_key"),
        (CAPABILITY_VERSION, ("provider_specific",), "authorization"),
        (REQUEST_VERSION, ("provider_body",), "credential"),
        (RECEIPT_VERSION, ("raw_response",), "headers"),
        (OUTPUT_VERSION, ("lineage",), "cookie"),
    ),
)
def test_provider_artifacts_reject_secret_bearing_fields(
    version: str,
    path: JsonPath,
    secret_field: str,
) -> None:
    _, artifacts = _valid_artifact_chain()
    document = copy.deepcopy(_artifact(artifacts, version))
    _at_path(document, path)[secret_field] = "sk-or-v1-secret-that-must-never-persist"
    _refresh_if_content_addressed(document)

    with pytest.raises(ProviderArtifactError):
        validate_provider_artifact(document)


@pytest.mark.parametrize(
    ("field", "claim"),
    (
        (
            "actual_model",
            {
                "state": "attested",
                "model": "qwen/qwen-image-3",
                "source_field": "$.model",
            },
        ),
        (
            "upstream_route",
            {
                "state": "disclosed",
                "provider_tag": "alibaba",
                "source_field": "$.provider",
            },
        ),
    ),
)
def test_receipt_cannot_claim_provenance_the_capability_cannot_attest(
    field: str,
    claim: JsonObject,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    capability = _artifact(artifacts, CAPABILITY_VERSION)
    assert capability[f"{field}_disclosure"] == "not_attested"
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt[field] = claim
    _refresh_receipt_dependents(artifacts)

    with pytest.raises(
        ProviderArtifactError,
        match=r"(?i)(capabilit.*attest|attest.*capabilit)",
    ):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("mime", r"(?i)(capabilit.*mime|mime.*capabilit)"),
        ("width", r"(?i)(capabilit.*(width|dimension)|(width|dimension).*capabilit)"),
        ("height", r"(?i)(capabilit.*(height|dimension)|(height|dimension).*capabilit)"),
    ),
)
def test_output_media_must_fit_the_captured_capability(
    mutation: str,
    expected_error: str,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    output = _artifact(artifacts, OUTPUT_VERSION)
    if mutation == "mime":
        output["original"]["mime"] = "image/jpeg"
        output["media_validation"]["measured_mime"] = "image/jpeg"
        receipt = _artifact(artifacts, RECEIPT_VERSION)
        receipt["outputs"][0]["media_type_claim"] = "image/jpeg"
        _refresh_receipt_dependents(artifacts)
    else:
        original_field = mutation
        measured_field = f"measured_{mutation}"
        output["original"][original_field] = 8192
        output["media_validation"][measured_field] = 8192

    with pytest.raises(ProviderArtifactError, match=expected_error):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    "reason",
    (
        "receipt_mismatch",
        "media_identity_mismatch",
        "media_type_mismatch",
        "media_dimensions_mismatch",
        "media_decode_failed",
        "media_active_content",
        "media_out_of_bounds",
    ),
)
def test_output_occurrence_rejection_reasons_are_provenance_only(reason: str) -> None:
    _, artifacts = _valid_artifact_chain()
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["admission"] = {"state": "rejected", "rejection_reasons": [reason]}

    with pytest.raises(ProviderArtifactError):
        validate_provider_artifact(output)


def test_capability_discovery_endpoint_names_the_exact_requested_model() -> None:
    _, artifacts = _valid_artifact_chain()
    capability = _artifact(artifacts, CAPABILITY_VERSION)
    capability["provider_specific"]["discovery_endpoint_path"] = (
        "/api/v1/images/models/vendor/a-different-model/endpoints"
    )
    _refresh_document_id(capability)

    with pytest.raises(
        ProviderArtifactError,
        match=r"(?i)(discovery.*requested.model|requested.model.*discovery)",
    ):
        validate_provider_artifact(capability)


def test_bundle_fails_fast_after_sixty_four_artifacts() -> None:
    packet, artifacts = _valid_artifact_chain()
    consumed: list[int] = []

    def over_limit() -> Any:
        for index in range(66):
            if index == 65:
                raise AssertionError("validator consumed past the first over-limit artifact")
            consumed.append(index)
            yield copy.deepcopy(artifacts[index % len(artifacts)])

    with pytest.raises(ProviderArtifactError, match=r"(?i)(64|limit|at most)"):
        validate_artifact_bundle(
            over_limit(),
            intent_packet=intent_packet_from_json(packet),
        )
    assert len(consumed) == 65


@pytest.mark.parametrize("missing_state", ("submitted", "response_received"))
def test_completed_p0_history_requires_dispatch_and_response_evidence(
    missing_state: str,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    incomplete = _drop_event_and_resequence(artifacts, state=missing_state)

    with pytest.raises(ProviderArtifactError, match=missing_state):
        validate_artifact_bundle(
            incomplete,
            intent_packet=intent_packet_from_json(packet),
        )


def test_completed_p0_history_has_one_exact_ordered_state_trace() -> None:
    packet, artifacts = _valid_artifact_chain()
    submitted = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "submitted"
    )
    response = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "response_received"
    )
    submitted["sequence"], response["sequence"] = response["sequence"], submitted["sequence"]
    _refresh_document_id(submitted)
    _refresh_document_id(response)

    with pytest.raises(
        ProviderArtifactError,
        match=r"(?i)(prepared.*submitted.*response_received|state trace)",
    ):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("state", "detail"),
    (
        (
            "outcome_unknown",
            {
                "kind": "outcome_unknown",
                "failure_stage": "dispatch",
                "failure_code": "ambiguous_transport",
                "provider_handle": None,
            },
        ),
        (
            "cancelled",
            {
                "kind": "cancelled",
                "cancellation_stage": "reconciliation",
                "cancellation_code": "provider_confirmed_cancelled",
                "authority": "provider_confirmed_no_output",
            },
        ),
    ),
)
def test_completed_p0_bundle_rejects_states_reserved_for_the_general_reducer(
    state: str,
    detail: JsonObject,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    terminal = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "succeeded"
    )
    terminal["sequence"] = 5
    _refresh_document_id(terminal)
    extra = _event(
        attempt_id=_ATTEMPT_ID,
        sequence=4,
        state=state,
        recorded_at="2026-08-16T20:30:05.500Z",
        detail=detail,
    )
    artifacts.insert(artifacts.index(terminal), extra)

    with pytest.raises(ProviderArtifactError, match=r"(?i)(completed.P0|unsupported.*state)"):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


def test_completed_p0_bundle_accepts_one_resolved_outcome_unknown() -> None:
    packet, artifacts = _valid_artifact_chain()
    response = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "response_received"
    )
    terminal = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "succeeded"
    )
    response["sequence"] = 4
    terminal["sequence"] = 5
    _refresh_document_id(response)
    _refresh_document_id(terminal)
    unknown = _event(
        attempt_id=_ATTEMPT_ID,
        sequence=3,
        state="outcome_unknown",
        recorded_at="2026-08-16T20:30:04.500Z",
        detail={
            "kind": "outcome_unknown",
            "failure_stage": "dispatch",
            "failure_code": "ambiguous_transport",
            "provider_handle": None,
        },
    )
    artifacts.insert(artifacts.index(response), unknown)

    assert (
        validate_artifact_bundle(artifacts, intent_packet=intent_packet_from_json(packet)) is None
    )

    duplicate_unknown = _event(
        attempt_id=_ATTEMPT_ID,
        sequence=4,
        state="outcome_unknown",
        recorded_at="2026-08-16T20:30:04.750Z",
        detail={
            "kind": "outcome_unknown",
            "failure_stage": "dispatch",
            "failure_code": "ambiguous_transport",
            "provider_handle": None,
        },
    )
    response["sequence"] = 5
    terminal["sequence"] = 6
    _refresh_document_id(response)
    _refresh_document_id(terminal)
    artifacts.insert(artifacts.index(response), duplicate_unknown)

    with pytest.raises(ProviderArtifactError, match=r"(?i)completed.P0"):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


def test_openrouter_input_references_record_the_exact_redacted_transport_projection() -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    assert normalized["provider_body"]["input_references"] == [
        {
            "position": 0,
            "provider_field": "input_references[0].image_url.url",
            "item_type": "image_url",
            "transport": "data_url",
            "media_type": "image/png",
            "role": "source_image",
            "source_kind": "operation_input",
            "source_id": packet["source"]["asset_id"],
            "content_sha256": packet["source"]["content_sha256"],
            "transport_value_sha256": _digest("e"),
        },
        {
            "position": 1,
            "provider_field": "input_references[1].image_url.url",
            "item_type": "image_url",
            "transport": "data_url",
            "media_type": "image/png",
            "role": "visual_context",
            "source_kind": "reference_occurrence",
            "source_id": packet["references"][0]["reference_occurrence_id"],
            "content_sha256": packet["references"][0]["content_sha256"],
            "transport_value_sha256": _digest("f"),
        },
    ]
    assert (
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )
        is None
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("source_id",), "10000000-0000-4000-8000-999999999999"),
        (("content_sha256",), _digest("1")),
        (("media_type",), "image/jpeg"),
    ),
)
def test_openrouter_source_input_transport_projection_rejects_authority_drift(
    path: JsonPath,
    replacement: Any,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    source_projection = normalized["provider_body"]["input_references"][0]
    _at_path(source_projection, path[:-1])[path[-1]] = replacement
    _refresh_normalized_request_dependents(artifacts)

    # These are locally well-formed redacted projections; the bundle join rejects their drift.
    validate_provider_artifact(normalized)
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


def test_openrouter_bundle_rejects_native_mask_without_a_registered_body_transport() -> None:
    packet, artifacts = _valid_artifact_chain(native_mask=True)

    with pytest.raises(
        ProviderArtifactError, match=r"(?i)(mask.*(not.sent|transport)|transport.*mask)"
    ):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("position",), 2),
        (("provider_field",), "input_references[2].image_url.url"),
    ),
)
def test_openrouter_source_position_and_field_path_must_agree_locally(
    path: JsonPath,
    replacement: Any,
) -> None:
    _, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    source_projection = normalized["provider_body"]["input_references"][0]
    _at_path(source_projection, path[:-1])[path[-1]] = replacement
    _refresh_document_id(normalized)

    with pytest.raises(ProviderArtifactError, match=r"(?i)(position|field path)"):
        validate_provider_artifact(normalized)


def test_openrouter_source_slot_cannot_be_relabelled_as_a_reference() -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    source_projection = normalized["provider_body"]["input_references"][0]
    source_projection["role"] = "visual_context"
    source_projection["source_kind"] = "reference_occurrence"
    _refresh_normalized_request_dependents(artifacts)

    validate_provider_artifact(normalized)
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("source_id",), "10000000-0000-4000-8000-999999999999"),
        (("content_sha256",), _digest("1")),
    ),
)
def test_openrouter_reference_transport_projection_rejects_occurrence_drift(
    path: JsonPath,
    replacement: Any,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    reference_projection = normalized["provider_body"]["input_references"][1]
    _at_path(reference_projection, path[:-1])[path[-1]] = replacement
    _refresh_normalized_request_dependents(artifacts)

    validate_provider_artifact(normalized)
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        (("position",), 0),
        (("provider_field",), "input_references[0].image_url.url"),
    ),
)
def test_openrouter_reference_position_and_field_path_must_agree_locally(
    path: JsonPath,
    replacement: Any,
) -> None:
    _, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    reference_projection = normalized["provider_body"]["input_references"][1]
    _at_path(reference_projection, path[:-1])[path[-1]] = replacement
    _refresh_document_id(normalized)

    with pytest.raises(ProviderArtifactError, match=r"(?i)(position|field path)"):
        validate_provider_artifact(normalized)


def test_openrouter_reference_slot_cannot_be_relabelled_as_operation_input() -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    reference_projection = normalized["provider_body"]["input_references"][1]
    reference_projection["role"] = "source_image"
    reference_projection["source_kind"] = "operation_input"
    _refresh_normalized_request_dependents(artifacts)

    validate_provider_artifact(normalized)
    with pytest.raises(ProviderArtifactError):
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("transport", "https_url"),
        ("transport_value_sha256", _digest("0")),
    ),
)
def test_transport_spelling_is_part_of_normalized_request_identity(
    field: str,
    replacement: str,
) -> None:
    _, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    normalized["provider_body"]["input_references"][0][field] = replacement

    with pytest.raises(ProviderArtifactError, match="identity"):
        validate_provider_artifact(normalized)


def test_https_url_is_an_explicit_supported_image_url_transport() -> None:
    packet, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    source_projection = normalized["provider_body"]["input_references"][0]
    source_projection["transport"] = "https_url"
    source_projection["transport_value_sha256"] = _digest("0")
    _refresh_normalized_request_dependents(artifacts)

    assert (
        validate_artifact_bundle(
            artifacts,
            intent_packet=intent_packet_from_json(packet),
        )
        is None
    )


@pytest.mark.parametrize("parent_field", ("retry_of", "fallback_of"))
def test_attempt_cannot_name_itself_as_retry_or_fallback_parent(parent_field: str) -> None:
    _, artifacts = _valid_artifact_chain()
    attempt = _artifact(artifacts, ATTEMPT_VERSION)
    attempt[parent_field] = attempt["attempt_id"]

    with pytest.raises(ProviderArtifactError, match=r"(?i)(itself|self)"):
        validate_provider_artifact(attempt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        (
            "actual_model",
            {"state": "undisclosed", "model": None, "source_field": None},
        ),
        (
            "actual_model",
            {
                "state": "attested",
                "model": "qwen/qwen-image-3",
                "source_field": "$.model",
            },
        ),
        (
            "upstream_route",
            {"state": "unknown", "provider_tag": None, "source_field": None},
        ),
        (
            "upstream_route",
            {
                "state": "disclosed",
                "provider_tag": "alibaba",
                "source_field": "$.provider",
            },
        ),
    ),
)
def test_receipt_provenance_states_bind_the_claim_source_field(
    field: str,
    value: JsonObject,
) -> None:
    _, artifacts = _valid_artifact_chain()
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt[field] = value
    _refresh_document_id(receipt)
    validate_provider_artifact(receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        (
            "actual_model",
            {"state": "attested", "model": "qwen/qwen-image-3", "source_field": None},
        ),
        (
            "actual_model",
            {"state": "undisclosed", "model": None, "source_field": "$.model"},
        ),
        (
            "upstream_route",
            {"state": "disclosed", "provider_tag": "alibaba", "source_field": None},
        ),
        (
            "upstream_route",
            {"state": "unknown", "provider_tag": None, "source_field": "$.provider"},
        ),
    ),
)
def test_receipt_provenance_rejects_missing_or_invented_source_fields(
    field: str,
    value: JsonObject,
) -> None:
    _, artifacts = _valid_artifact_chain()
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt[field] = value
    _refresh_document_id(receipt)

    with pytest.raises(ProviderArtifactError):
        validate_provider_artifact(receipt)


@pytest.mark.parametrize(
    "raw_response",
    (
        {
            "state": "retained",
            "content_ref": "7" * 64,
            "content_sha256": "8" * 64,
            "byte_count": 804_211,
            "privacy": "private_provider_payload",
        },
        {"state": "not_retained", "reason": "provider_terms"},
        {"state": "not_retained", "reason": "retention_policy"},
        {"state": "not_retained", "reason": "not_available"},
    ),
)
def test_receipt_raw_response_has_an_explicit_closed_retention_union(
    raw_response: JsonObject,
) -> None:
    _, artifacts = _valid_artifact_chain()
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt["raw_response"] = raw_response
    _refresh_document_id(receipt)
    validate_provider_artifact(receipt)


@pytest.mark.parametrize(
    "raw_response",
    (
        {"state": "retained", "reason": "provider_terms"},
        {
            "state": "not_retained",
            "reason": "provider_terms",
            "content_ref": "7" * 64,
        },
        {"state": "not_retained", "reason": "operator_forgot"},
    ),
)
def test_receipt_raw_response_retention_branches_cannot_be_mixed(
    raw_response: JsonObject,
) -> None:
    _, artifacts = _valid_artifact_chain()
    receipt = _artifact(artifacts, RECEIPT_VERSION)
    receipt["raw_response"] = raw_response
    _refresh_document_id(receipt)

    with pytest.raises(ProviderArtifactError):
        validate_provider_artifact(receipt)


def test_capability_output_count_range_is_ordered() -> None:
    _, artifacts = _valid_artifact_chain()
    capability = _artifact(artifacts, CAPABILITY_VERSION)
    capability["outputs"]["min_count"] = 6
    capability["outputs"]["max_count"] = 1
    _refresh_document_id(capability)

    with pytest.raises(ProviderArtifactError, match=r"(?i)(min.*max|range)"):
        validate_provider_artifact(capability)


def test_dual_missing_attestations_produce_one_representable_provenance_reason() -> None:
    packet, artifacts = _valid_artifact_chain(
        actual_model_disclosure="attested",
        upstream_route_disclosure="attested",
    )
    output = _artifact(artifacts, OUTPUT_VERSION)
    output["admission"] = {
        "state": "rejected",
        "rejection_reasons": ["provenance_mismatch"],
    }
    terminal = next(
        item
        for item in artifacts
        if item["schema_version"] == EVENT_VERSION and item["state"] == "succeeded"
    )
    terminal["state"] = "failed"
    terminal["detail"] = {
        "kind": "failed",
        "failure_stage": "provenance",
        "failure_code": "provenance_mismatch",
    }
    _refresh_document_id(terminal)

    assert (
        validate_artifact_bundle(artifacts, intent_packet=intent_packet_from_json(packet)) is None
    )


def test_provider_artifact_producer_surface_exports_contract_names() -> None:
    expected_exports = {
        "RUN_VERSION",
        "ATTEMPT_VERSION",
        "EVENT_VERSION",
        "CAPABILITY_VERSION",
        "REQUEST_VERSION",
        "RECEIPT_VERSION",
        "OUTPUT_VERSION",
        "ProviderArtifact",
        "seal_provider_artifact",
        "build_normalized_request_ref",
        "compute_provider_request_key",
    }
    assert expected_exports <= set(provider_artifacts_module.__all__)
    expected_versions = {
        "RUN_VERSION": RUN_VERSION,
        "ATTEMPT_VERSION": ATTEMPT_VERSION,
        "EVENT_VERSION": EVENT_VERSION,
        "CAPABILITY_VERSION": CAPABILITY_VERSION,
        "REQUEST_VERSION": REQUEST_VERSION,
        "RECEIPT_VERSION": RECEIPT_VERSION,
        "OUTPUT_VERSION": OUTPUT_VERSION,
    }
    for name, version in expected_versions.items():
        assert getattr(provider_artifacts_module, name) == version
        schema = json.loads(SCHEMA_PATHS[version].read_text(encoding="utf-8"))
        assert schema["properties"]["schema_version"]["const"] == version


@pytest.mark.parametrize(
    ("version", "identity_field"),
    (
        (EVENT_VERSION, "attempt_event_id"),
        (CAPABILITY_VERSION, "capability_snapshot_id"),
        (REQUEST_VERSION, "normalized_request_id"),
        (RECEIPT_VERSION, "provider_receipt_id"),
        (OUTPUT_VERSION, "output_occurrence_id"),
    ),
)
def test_seal_provider_artifact_owns_derived_identity_without_mutating_draft(
    version: str,
    identity_field: str,
) -> None:
    _, artifacts = _valid_artifact_chain()
    expected = copy.deepcopy(_first_artifact(artifacts, version))
    draft = copy.deepcopy(expected)
    del draft[identity_field]
    before = copy.deepcopy(draft)

    seal = provider_artifacts_module.seal_provider_artifact
    sealed = seal(draft)
    sealed_document = to_json_dict(sealed)

    assert draft == before
    assert sealed_document == expected
    assert sealed_document[identity_field] == _GOLDEN_IDENTITIES[version]
    validate_provider_artifact(sealed_document)


@pytest.mark.parametrize(
    "version",
    (EVENT_VERSION, CAPABILITY_VERSION, REQUEST_VERSION, RECEIPT_VERSION, OUTPUT_VERSION),
)
def test_seal_provider_artifact_rejects_a_prefilled_derived_identity(version: str) -> None:
    _, artifacts = _valid_artifact_chain()
    prefilled = copy.deepcopy(_first_artifact(artifacts, version))

    with pytest.raises(ProviderArtifactError, match=r"(?i)(prefill|identity)"):
        provider_artifacts_module.seal_provider_artifact(prefilled)


@pytest.mark.parametrize("version", (RUN_VERSION, ATTEMPT_VERSION))
def test_seal_provider_artifact_does_not_mint_occurrence_uuid_branches(version: str) -> None:
    _, artifacts = _valid_artifact_chain()
    occurrence = copy.deepcopy(_artifact(artifacts, version))

    with pytest.raises(ProviderArtifactError, match=r"(?i)(occurrence|unsupported|uuid)"):
        provider_artifacts_module.seal_provider_artifact(occurrence)


def test_seal_provider_artifact_rejects_unknown_schema_version() -> None:
    draft = {"schema_version": "moodboard.provider-surprise.v1"}
    before = copy.deepcopy(draft)

    with pytest.raises(ProviderArtifactError, match=r"(?i)(unsupported|schema.version)"):
        provider_artifacts_module.seal_provider_artifact(draft)
    assert draft == before


@pytest.mark.parametrize("schema_version", ([], None, 1))
def test_seal_provider_artifact_normalizes_malformed_schema_versions(
    schema_version: Any,
) -> None:
    with pytest.raises(ProviderArtifactError, match=r"(?i)schema.version"):
        provider_artifacts_module.seal_provider_artifact({"schema_version": schema_version})


def test_build_normalized_request_ref_uses_exact_canonical_artifact_bytes() -> None:
    _, artifacts = _valid_artifact_chain()
    normalized = _artifact(artifacts, REQUEST_VERSION)
    attempt = _artifact(artifacts, ATTEMPT_VERSION)
    before = copy.deepcopy(normalized)

    reference = provider_artifacts_module.build_normalized_request_ref(normalized)

    assert normalized == before
    assert reference == attempt["normalized_request_ref"]


def test_public_provider_request_key_helper_matches_the_golden_projection() -> None:
    _, artifacts = _valid_artifact_chain()
    attempt = _artifact(artifacts, ATTEMPT_VERSION)

    request_key = provider_artifacts_module.compute_provider_request_key(
        generation_run_id=attempt["generation_run_id"],
        attempt_id=attempt["attempt_id"],
        intent_packet_id=attempt["intent_packet_id"],
        adapter_revision=attempt["adapter_revision"],
        normalized_request_id=attempt["normalized_request_id"],
    )

    assert request_key == attempt["request_key_sha256"]
    assert request_key == _GOLDEN_REQUEST_KEY

    projection = {
        "generation_run_id": attempt["generation_run_id"],
        "attempt_id": attempt["attempt_id"],
        "intent_packet_id": attempt["intent_packet_id"],
        "adapter_revision": attempt["adapter_revision"],
        "normalized_request_id": attempt["normalized_request_id"],
    }
    replacements = {
        "generation_run_id": "20000000-0000-4000-8000-999999999991",
        "attempt_id": "20000000-0000-4000-8000-999999999992",
        "intent_packet_id": _digest("1"),
        "adapter_revision": "moodboard.openrouter.changed.v1",
        "normalized_request_id": _digest("2"),
    }
    for field, replacement in replacements.items():
        changed = {**projection, field: replacement}
        assert provider_artifacts_module.compute_provider_request_key(**changed) != request_key


@pytest.mark.parametrize("adapter_revision", (" invalid", "x" * 257, "\ud800"))
def test_public_provider_request_key_rejects_invalid_adapter_revision(
    adapter_revision: str,
) -> None:
    _, artifacts = _valid_artifact_chain()
    attempt = _artifact(artifacts, ATTEMPT_VERSION)

    with pytest.raises(ProviderArtifactError, match="adapter_revision"):
        provider_artifacts_module.compute_provider_request_key(
            generation_run_id=attempt["generation_run_id"],
            attempt_id=attempt["attempt_id"],
            intent_packet_id=attempt["intent_packet_id"],
            adapter_revision=adapter_revision,
            normalized_request_id=attempt["normalized_request_id"],
        )
