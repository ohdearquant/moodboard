from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from blake3 import blake3

import moodboard.openrouter as openrouter_module
from moodboard.openrouter import (
    OpenRouterAdapterAdmissionLimits,
    OpenRouterDiscoveryError,
    build_openrouter_capability_snapshot,
)
from moodboard.provider_artifacts import ProviderCapabilitySnapshot
from moodboard.provider_artifacts import to_json_dict as provider_to_json

JsonObject = dict[str, Any]

_MODEL = "qwen/qwen-image-3"
_PROVIDER_TAG = "alibaba"
_CAPTURED_AT = "2026-08-17T03:15:00Z"
_ADAPTER_REVISION = "moodboard.openrouter.v1"
_SOURCE_CAPABILITY_ID = "a" * 64
_MASK_CAPABILITY_ID = "b" * 64

# Exact minified bytes returned by the model-endpoint discovery surface on 2026-08-17.  Pricing
# is retained in the evidence bytes, but is deliberately not projected as a stable capability.
_DISCOVERY_BODY = (
    b'{"id":"qwen/qwen-image-3","endpoints":[{"provider_name":"Alibaba Cloud Int.",'
    b'"provider_slug":"alibaba","provider_tag":"alibaba","supported_parameters":{'
    b'"resolution":{"type":"enum","values":["1K","2K"]},"aspect_ratio":{"type":'
    b'"enum","values":["1:1","1:2","1:4","2:1","2:3","3:2","3:4","4:1","4:3",'
    b'"4:5","5:4","9:16","16:9"]},"n":{"type":"range","min":1,"max":6},'
    b'"input_references":{"type":"range","min":0,"max":4},"seed":{"type":"boolean"}'
    b'},"allowed_passthrough_parameters":[],"supports_streaming":false,"pricing":['
    b'{"billable":"input_image","unit":"image","cost_usd":0.003},{"billable":'
    b'"output_image","unit":"image","cost_usd":0.03,"variant":"1k"},{"billable":'
    b'"output_image","unit":"image","cost_usd":0.03,"variant":"2k"}]}]}'
)

_DISCOVERY_SHA256 = "0dc3f36b77ac30144cc21fedd809b7105512297bec889bcfbe7c4e74ad23b3e4"
_DISCOVERY_CONTENT_REF = "7ee91a657671d82faf55d146d55e51be82e4f71f9886b581bfca2d077f7222c8"
_DISCOVERY_BYTE_COUNT = 724

_REGISTERED_ASPECT_INTERSECTION = [
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
]


def _limits(
    *,
    mime_types: tuple[str, ...] = ("image/png",),
    max_width: int = 8_192,
    max_height: int = 8_192,
    max_encoded_output_bytes: int = 16_777_216,
) -> OpenRouterAdapterAdmissionLimits:
    return OpenRouterAdapterAdmissionLimits(
        mime_types=mime_types,
        max_width=max_width,
        max_height=max_height,
        max_encoded_output_bytes=max_encoded_output_bytes,
    )


def _build(
    raw_body: bytes = _DISCOVERY_BODY,
    *,
    requested_model: str = _MODEL,
    selected_provider_tag: str = _PROVIDER_TAG,
    adapter_admission_limits: OpenRouterAdapterAdmissionLimits | None = None,
) -> ProviderCapabilitySnapshot:
    return build_openrouter_capability_snapshot(
        raw_body,
        requested_model=requested_model,
        selected_provider_tag=selected_provider_tag,
        captured_at=_CAPTURED_AT,
        adapter_revision=_ADAPTER_REVISION,
        source_capability_id=_SOURCE_CAPABILITY_ID,
        locality_mask_capability_id=_MASK_CAPABILITY_ID,
        adapter_admission_limits=adapter_admission_limits or _limits(),
    )


def _body_document() -> JsonObject:
    value = json.loads(_DISCOVERY_BODY)
    assert isinstance(value, dict)
    return value


def _encoded(document: JsonObject) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_qwen_endpoint_projects_one_closed_capability_snapshot() -> None:
    capability = _build()

    assert isinstance(capability, ProviderCapabilitySnapshot)
    document = provider_to_json(capability)
    assert document == {
        "schema_version": "moodboard.provider-capability-snapshot.v1",
        "capability_snapshot_id": document["capability_snapshot_id"],
        "captured_at": _CAPTURED_AT,
        "adapter_revision": _ADAPTER_REVISION,
        "provider": "openrouter",
        "requested_model": _MODEL,
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
            "aspect_ratios": _REGISTERED_ASPECT_INTERSECTION,
            "max_width": 8_192,
            "max_height": 8_192,
        },
        "options": {
            "schema_version": "moodboard.openrouter-images-options-capability.v1",
            "seed_supported": True,
            "resolutions": ["1K", "2K"],
            "aspect_ratios": _REGISTERED_ASPECT_INTERSECTION,
        },
        "operation_input_capabilities": [
            {
                "capability_id": _SOURCE_CAPABILITY_ID,
                "role": "source_image",
                "delivery_modes": ["native_input"],
                "provider_roles": ["source_image"],
                "provider_fields": ["input_references[0]"],
            },
            {
                "capability_id": _MASK_CAPABILITY_ID,
                "role": "locality_mask",
                "delivery_modes": ["not_sent"],
                "provider_roles": [],
                "provider_fields": [],
            },
        ],
        "actual_model_disclosure": "not_attested",
        "upstream_route_disclosure": "not_attested",
        "idempotency": {
            "provider_accepts_key": False,
            "deduplication_scope": None,
            "retention_seconds": None,
            "ambiguous_transport_retransmit_safe": False,
        },
        "reconciliation": {"supported": False, "provider_handle_kind": None},
        "provider_specific": {
            "schema_version": "moodboard.openrouter-images-capability.v1",
            "endpoint_path": "/api/v1/images",
            "discovery_endpoint_path": ("/api/v1/images/models/qwen/qwen-image-3/endpoints"),
            "upstream_provider_tags": ["alibaba"],
            "input_reference_parameter": "input_references",
            "supports_streaming": False,
            "allowed_passthrough_parameters": [],
            "discovery_response": {
                "content_ref": _DISCOVERY_CONTENT_REF,
                "content_sha256": _DISCOVERY_SHA256,
                "byte_count": _DISCOVERY_BYTE_COUNT,
            },
        },
    }
    assert len(document["capability_snapshot_id"]) == 64


def test_discovery_evidence_binds_the_exact_raw_response_bytes() -> None:
    assert len(_DISCOVERY_BODY) == _DISCOVERY_BYTE_COUNT
    assert hashlib.sha256(_DISCOVERY_BODY).hexdigest() == _DISCOVERY_SHA256
    assert blake3(_DISCOVERY_BODY).hexdigest() == _DISCOVERY_CONTENT_REF

    compact = _build()
    pretty_body = json.dumps(_body_document(), indent=2).encode("utf-8")
    pretty = _build(pretty_body)

    compact_document = provider_to_json(compact)
    pretty_document = provider_to_json(pretty)
    assert pretty_document["provider_specific"]["discovery_response"] == {
        "content_ref": blake3(pretty_body).hexdigest(),
        "content_sha256": hashlib.sha256(pretty_body).hexdigest(),
        "byte_count": len(pretty_body),
    }
    assert compact_document["capability_snapshot_id"] != pretty_document["capability_snapshot_id"]


def test_output_media_bounds_are_explicit_adapter_admission_limits() -> None:
    first_limits = _limits(
        mime_types=("image/png",),
        max_width=8_192,
        max_height=8_192,
        max_encoded_output_bytes=16_777_216,
    )
    second_limits = _limits(
        mime_types=("image/jpeg",),
        max_width=4_096,
        max_height=2_048,
        max_encoded_output_bytes=16_777_216,
    )

    first = provider_to_json(_build(adapter_admission_limits=first_limits))
    second = provider_to_json(_build(adapter_admission_limits=second_limits))

    assert first["outputs"]["mime_types"] == ["image/png"]
    assert first["outputs"]["max_width"] == 8_192
    assert first["outputs"]["max_height"] == 8_192
    assert second["outputs"]["mime_types"] == ["image/jpeg"]
    assert second["outputs"]["max_width"] == 4_096
    assert second["outputs"]["max_height"] == 2_048
    assert "max_encoded_output_bytes" not in second["outputs"]
    assert first["capability_snapshot_id"] != second["capability_snapshot_id"]
    with pytest.raises(FrozenInstanceError):
        second_limits.max_width = 1  # type: ignore[misc]


def test_unserialized_encoded_output_limit_is_fixed_by_adapter_revision() -> None:
    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(
            adapter_admission_limits=_limits(
                max_encoded_output_bytes=16_777_215,
            )
        )

    assert raised.value.code == "adapter_admission_invalid"


@pytest.mark.parametrize(
    ("raw_body", "code"),
    [
        (b"", "discovery_body_empty"),
        (b"not-json", "discovery_json_invalid"),
        (
            b'{"id":"qwen/qwen-image-3","id":"other","endpoints":[]}',
            "discovery_duplicate_key",
        ),
    ],
)
def test_discovery_body_is_nonempty_unambiguous_json(raw_body: bytes, code: str) -> None:
    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(raw_body)

    assert raised.value.code == code


def test_discovery_body_has_a_hard_byte_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openrouter_module,
        "DISCOVERY_RESPONSE_MAX_BYTES",
        len(_DISCOVERY_BODY) - 1,
    )

    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build()

    assert raised.value.code == "discovery_body_too_large"


def test_discovery_model_identity_must_match_the_requested_model() -> None:
    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(requested_model="qwen/a-different-model")

    assert raised.value.code == "discovery_model_mismatch"


@pytest.mark.parametrize("matching_routes", [0, 2])
def test_selected_provider_tag_must_resolve_to_exactly_one_endpoint(
    matching_routes: int,
) -> None:
    document = _body_document()
    endpoint = document["endpoints"][0]
    if matching_routes == 0:
        endpoint["provider_tag"] = "different-provider"
    else:
        document["endpoints"].append(copy.deepcopy(endpoint))

    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(_encoded(document))

    assert raised.value.code == (
        "discovery_route_missing" if matching_routes == 0 else "discovery_route_ambiguous"
    )


@pytest.mark.parametrize(
    ("parameter", "replacement"),
    [
        ("resolution", {"type": "enum", "values": ["4K"]}),
        ("aspect_ratio", {"type": "enum", "values": ["1:2", "1:4"]}),
        ("n", {"type": "range", "min": 0, "max": 6}),
        ("input_references", {"type": "range", "min": 1, "max": 4}),
        ("seed", {"type": "enum", "values": [True]}),
    ],
)
def test_required_parameter_descriptors_fail_closed(
    parameter: str,
    replacement: JsonObject,
) -> None:
    document = _body_document()
    document["endpoints"][0]["supported_parameters"][parameter] = replacement

    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(_encoded(document))

    assert raised.value.code == "discovery_capability_unsupported"


@pytest.mark.parametrize(
    "field",
    [
        "provider_name",
        "provider_slug",
        "provider_tag",
        "supported_parameters",
        "allowed_passthrough_parameters",
        "supports_streaming",
        "pricing",
    ],
)
def test_required_endpoint_fields_cannot_be_omitted(field: str) -> None:
    document = _body_document()
    del document["endpoints"][0][field]

    with pytest.raises(OpenRouterDiscoveryError) as raised:
        _build(_encoded(document))

    assert raised.value.code == "discovery_endpoint_invalid"


def test_provider_maxima_are_intersected_with_registered_adapter_bounds() -> None:
    document = _body_document()
    parameters = document["endpoints"][0]["supported_parameters"]
    parameters["n"]["max"] = 10
    parameters["input_references"]["max"] = 20

    capability = provider_to_json(_build(_encoded(document)))

    assert capability["outputs"]["max_count"] == 8
    assert capability["image_input_budget"]["max_count"] == 16
