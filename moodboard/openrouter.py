"""Bounded OpenRouter Image API adapter for ADR-0014.

This module owns four deliberately narrow boundaries:

* projection of one exact endpoint-discovery response into a frozen capability snapshot;
* preparation of one secret-free, byte-exact ``POST /api/v1/images`` request;
* decoding of one buffered provider response into a receipt plus private payload bytes; and
* ordering one non-idempotent send behind :class:`~moodboard.attempt_journal.AttemptJournal`.

It does not mint output occurrences or append ``succeeded``.  A caller-supplied response
publisher must durably store the receipt, raw response, and output bytes before this adapter will
append ``response_received``.  Media admission and the atomic terminal-success gate are separate
concerns.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, cast

from blake3 import blake3

from moodboard.attempt_journal import (
    AttemptJournal,
    AttemptJournalError,
    DispatchClaimConflictError,
    StaleAttemptHeadError,
)
from moodboard.attempt_state import AttemptState
from moodboard.contracts import canonical_json_bytes, is_canonical_utc_timestamp
from moodboard.intent_packet import IntentPacket, validate_intent_packet
from moodboard.intent_packet import from_json_dict as intent_packet_from_json
from moodboard.intent_packet import to_json_dict as intent_packet_to_json
from moodboard.provider_artifacts import (
    CAPABILITY_VERSION,
    EVENT_VERSION,
    RECEIPT_VERSION,
    REQUEST_VERSION,
    GenerationAttempt,
    GenerationAttemptEvent,
    NormalizedProviderRequest,
    ProviderArtifact,
    ProviderCapabilitySnapshot,
    ProviderReceipt,
    seal_provider_artifact,
    validate_provider_artifact,
)
from moodboard.provider_artifacts import (
    from_json_dict as provider_from_json,
)
from moodboard.provider_artifacts import (
    to_json_dict as provider_to_json,
)

__all__ = [
    "ADAPTER_REVISION",
    "DISCOVERY_RESPONSE_MAX_BYTES",
    "PROMPT_COMPILER_REVISION",
    "OpenRouterAdapterAdmissionLimits",
    "OpenRouterAdapterError",
    "OpenRouterDecodedResponse",
    "OpenRouterDiscoveryError",
    "OpenRouterDispatchResult",
    "OpenRouterHttpResponse",
    "OpenRouterPreparedRequest",
    "build_openrouter_capability_snapshot",
    "decode_openrouter_response",
    "dispatch_openrouter_attempt",
    "openrouter_https_transport",
    "prepare_openrouter_request",
    "reconcile_openrouter_attempt",
]

ADAPTER_REVISION = "moodboard.openrouter.v1"
PROMPT_COMPILER_REVISION = "moodboard.openrouter-prompt.v1"
DISCOVERY_RESPONSE_MAX_BYTES = 4 * 1024 * 1024

_PROVIDER = "openrouter"
_ORIGIN = "https://openrouter.ai"
_IMAGE_ENDPOINT = "/api/v1/images"
_IMAGE_URL = f"{_ORIGIN}{_IMAGE_ENDPOINT}"
_WIRE_REQUEST_MAX_BYTES = 32 * 1024 * 1024
_ENCODED_OUTPUT_MAX_BYTES = 16_777_216
_INPUT_IMAGE_MAX_BYTES = 16_777_216
_WIRE_BODY_OVERHEAD_RESERVE_BYTES = 1_048_576
_DATA_URL_TOTAL_MAX_CHARS = _WIRE_REQUEST_MAX_BYTES - _WIRE_BODY_OVERHEAD_RESERVE_BYTES
_BASE64_OUTPUT_MAX_CHARS = ((_ENCODED_OUTPUT_MAX_BYTES + 2) // 3) * 4
_RESPONSE_OVERHEAD_MAX_BYTES = 1_048_576
_HTTP_RESPONSE_MAX_BYTES = _RESPONSE_OVERHEAD_MAX_BYTES + _BASE64_OUTPUT_MAX_CHARS
_JSON_MAX_DEPTH = 32
_JSON_MAX_NODES = 10_000
_JSON_NUMBER_MAX_CHARS = 128
_JSON_STRUCTURAL_TOKEN_MAX = 2 * _JSON_MAX_NODES + _JSON_MAX_DEPTH
_DISCOVERY_MAX_ENDPOINTS = 128
_REGISTERED_RESOLUTIONS = ("1K", "2K", "4K")
_REGISTERED_ASPECT_RATIOS = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)
_MIME_PATTERN = re.compile(r"image/[a-z0-9][a-z0-9.+-]{0,119}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]+=*\Z")
_HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)[?&](?:api_key|access_token|signature|sig|x-amz-signature)=[^&\s]+"),
)


class OpenRouterAdapterError(RuntimeError):
    """A stable, secret-free OpenRouter adapter failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OpenRouterDiscoveryError(OpenRouterAdapterError):
    """Endpoint discovery could not be admitted as one capability snapshot."""


@dataclass(frozen=True, slots=True)
class OpenRouterAdapterAdmissionLimits:
    """Moodboard-owned output admission limits, not provider attestations."""

    mime_types: tuple[str, ...]
    max_width: int
    max_height: int
    max_encoded_output_bytes: int


@dataclass(frozen=True, slots=True)
class OpenRouterPreparedRequest:
    """One frozen normalized request and the exact bytes claimed before dispatch."""

    intent_packet: IntentPacket = field(repr=False)
    normalized_request: NormalizedProviderRequest = field(repr=False)
    wire_body: bytes = field(repr=False)
    wire_body_sha256: str
    wire_body_byte_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.intent_packet, IntentPacket) or not isinstance(
            self.normalized_request, NormalizedProviderRequest
        ):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
        try:
            intent_packet_to_json(self.intent_packet)
        except Exception:
            raise OpenRouterAdapterError(
                "prepared_request_invalid", "prepared request is invalid"
            ) from None
        if not isinstance(self.wire_body, bytes):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
        if (
            not 1 <= len(self.wire_body) <= _WIRE_REQUEST_MAX_BYTES
            or self.wire_body_byte_count != len(self.wire_body)
            or not isinstance(self.wire_body_sha256, str)
            or hashlib.sha256(self.wire_body).hexdigest() != self.wire_body_sha256
        ):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")


@dataclass(frozen=True, slots=True)
class OpenRouterHttpResponse:
    """Transient HTTP envelope. Headers and body are never represented or persisted here."""

    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    elapsed_milliseconds: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, int)
            or isinstance(self.status, bool)
            or not 100 <= self.status <= 599
        ):
            raise OpenRouterAdapterError("http_response_invalid", "HTTP response is invalid")
        if (
            not isinstance(self.elapsed_milliseconds, int)
            or isinstance(self.elapsed_milliseconds, bool)
            or not 0 <= self.elapsed_milliseconds <= 9_007_199_254_740_991
        ):
            raise OpenRouterAdapterError("http_response_invalid", "HTTP response is invalid")
        if not isinstance(self.body, bytes):
            raise OpenRouterAdapterError("http_response_invalid", "HTTP response is invalid")
        try:
            if not isinstance(self.headers, Mapping) or len(self.headers) > 128:
                raise TypeError
            frozen_headers: dict[str, str] = {}
            for key, value in self.headers.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or not 1 <= len(key) <= 256
                    or len(value) > 8192
                    or any(character in "\r\n\0" for character in key + value)
                ):
                    raise TypeError
                frozen_headers[key] = value
        except Exception:
            raise OpenRouterAdapterError(
                "http_response_invalid", "HTTP response is invalid"
            ) from None
        object.__setattr__(self, "headers", MappingProxyType(frozen_headers))


@dataclass(frozen=True, slots=True)
class OpenRouterDecodedResponse:
    """Validated receipt plus exact private response and output bytes."""

    receipt: ProviderReceipt = field(repr=False)
    output_bytes: tuple[bytes, ...] = field(repr=False)
    raw_response_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        try:
            receipt = provider_to_json(self.receipt)
        except Exception:
            raise OpenRouterAdapterError(
                "decoded_response_invalid", "decoded response is invalid"
            ) from None
        if not isinstance(self.receipt, ProviderReceipt) or not isinstance(
            self.raw_response_bytes, bytes
        ):
            raise OpenRouterAdapterError("decoded_response_invalid", "decoded response is invalid")
        if not isinstance(self.output_bytes, tuple) or any(
            not isinstance(payload, bytes) for payload in self.output_bytes
        ):
            raise OpenRouterAdapterError("decoded_response_invalid", "decoded response is invalid")
        if len(self.output_bytes) != len(receipt["outputs"]):
            raise OpenRouterAdapterError("decoded_response_invalid", "decoded response is invalid")
        raw = receipt["raw_response"]
        if (
            raw["state"] != "retained"
            or raw["content_ref"] != blake3(self.raw_response_bytes).hexdigest()
            or raw["content_sha256"] != hashlib.sha256(self.raw_response_bytes).hexdigest()
            or raw["byte_count"] != len(self.raw_response_bytes)
        ):
            raise OpenRouterAdapterError("decoded_response_invalid", "decoded response is invalid")
        for index, payload in enumerate(self.output_bytes):
            output = receipt["outputs"][index]
            if (
                output["output_index"] != index
                or output["content_ref"] != blake3(payload).hexdigest()
                or output["content_sha256"] != hashlib.sha256(payload).hexdigest()
                or output["byte_count"] != len(payload)
            ):
                raise OpenRouterAdapterError(
                    "decoded_response_invalid", "decoded response is invalid"
                )


DispatchKind: TypeAlias = Literal["response_received", "failed", "outcome_unknown", "not_sent"]


@dataclass(frozen=True, slots=True)
class OpenRouterDispatchResult:
    """One observable adapter outcome; it never implies terminal success."""

    kind: DispatchKind
    state: AttemptState
    event: GenerationAttemptEvent | None
    decoded: OpenRouterDecodedResponse | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.kind not in {"response_received", "failed", "outcome_unknown", "not_sent"}:
            raise OpenRouterAdapterError("dispatch_result_invalid", "dispatch result is invalid")
        if not isinstance(self.state, AttemptState):
            raise OpenRouterAdapterError("dispatch_result_invalid", "dispatch result is invalid")
        if self.kind == "not_sent":
            if self.event is not None or self.decoded is not None:
                raise OpenRouterAdapterError(
                    "dispatch_result_invalid", "dispatch result is invalid"
                )
            return
        if not isinstance(self.event, GenerationAttemptEvent) or self.event.state != self.kind:
            raise OpenRouterAdapterError("dispatch_result_invalid", "dispatch result is invalid")
        if (self.kind == "response_received") != isinstance(
            self.decoded, OpenRouterDecodedResponse
        ):
            raise OpenRouterAdapterError("dispatch_result_invalid", "dispatch result is invalid")


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError


def _parse_bounded_decimal(value: str) -> Decimal:
    if not 1 <= len(value) <= _JSON_NUMBER_MAX_CHARS:
        raise ValueError
    return Decimal(value)


def _parse_bounded_int(value: str) -> int:
    if not 1 <= len(value) <= _JSON_NUMBER_MAX_CHARS:
        raise ValueError
    return int(value)


def _bounded_tree(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > _JSON_MAX_DEPTH or nodes > _JSON_MAX_NODES:
            raise ValueError
        if isinstance(current, dict):
            for key, item in current.items():
                key.encode("utf-8", errors="strict")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, str):
            current.encode("utf-8", errors="strict")
        elif not isinstance(current, (type(None), bool, int, str, Decimal)):
            raise ValueError


def _preflight_json_structure(raw: bytes) -> None:
    in_string = False
    escaped = False
    depth = 0
    structural_tokens = 0
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in {0x5B, 0x7B}:
            depth += 1
            structural_tokens += 1
            if depth > _JSON_MAX_DEPTH:
                raise ValueError
        elif byte in {0x5D, 0x7D}:
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                raise ValueError
        elif byte in {0x2C, 0x3A}:
            structural_tokens += 1
        if structural_tokens > _JSON_STRUCTURAL_TOKEN_MAX:
            raise ValueError


def _parse_json(raw: bytes, *, max_bytes: int) -> Any:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= max_bytes:
        raise ValueError
    try:
        _preflight_json_structure(raw)
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_parse_bounded_decimal,
            parse_int=_parse_bounded_int,
            parse_constant=_reject_json_constant,
        )
        _bounded_tree(value)
        return value
    except (
        _DuplicateJsonKey,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        raise
    except Exception:
        raise ValueError from None


def _validate_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST_PATTERN.fullmatch(value) is not None


def _packet_document(value: IntentPacket | Mapping[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(value, IntentPacket):
            document = intent_packet_to_json(value)
        elif isinstance(value, Mapping):
            document = copy.deepcopy(dict(value))
            validate_intent_packet(document)
        else:
            raise TypeError
    except Exception:
        raise OpenRouterAdapterError("packet_invalid", "intent packet is invalid") from None
    return document


def _provider_document(
    value: ProviderCapabilitySnapshot
    | GenerationAttempt
    | NormalizedProviderRequest
    | Mapping[str, Any],
    expected_type: type[Any],
    code: str,
) -> tuple[Any, dict[str, Any]]:
    try:
        if isinstance(value, expected_type):
            artifact = value
            document = provider_to_json(cast(ProviderArtifact, value))
        elif isinstance(value, Mapping):
            document = copy.deepcopy(dict(value))
            artifact = provider_from_json(document)
        else:
            raise TypeError
    except Exception:
        raise OpenRouterAdapterError(code, "provider artifact is invalid") from None
    if not isinstance(artifact, expected_type):
        raise OpenRouterAdapterError(code, "provider artifact has the wrong schema")
    return artifact, document


def _discovery_fail(code: str) -> OpenRouterDiscoveryError:
    return OpenRouterDiscoveryError(code, "OpenRouter endpoint discovery is invalid")


def _validate_admission_limits(limits: OpenRouterAdapterAdmissionLimits) -> None:
    if not isinstance(limits, OpenRouterAdapterAdmissionLimits):
        raise _discovery_fail("adapter_admission_invalid")
    if (
        not isinstance(limits.mime_types, tuple)
        or not limits.mime_types
        or len(limits.mime_types) > 16
        or len(set(limits.mime_types)) != len(limits.mime_types)
        or any(
            not isinstance(item, str) or _MIME_PATTERN.fullmatch(item) is None
            for item in limits.mime_types
        )
        or not isinstance(limits.max_width, int)
        or isinstance(limits.max_width, bool)
        or not 1 <= limits.max_width <= 32_768
        or not isinstance(limits.max_height, int)
        or isinstance(limits.max_height, bool)
        or not 1 <= limits.max_height <= 32_768
        or limits.max_encoded_output_bytes != _ENCODED_OUTPUT_MAX_BYTES
    ):
        raise _discovery_fail("adapter_admission_invalid")


def _enum_descriptor(parameters: Mapping[str, Any], name: str) -> list[str]:
    descriptor = parameters.get(name)
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != {"type", "values"}
        or descriptor.get("type") != "enum"
        or not isinstance(descriptor.get("values"), list)
        or not descriptor["values"]
        or any(not isinstance(item, str) for item in descriptor["values"])
        or len(descriptor["values"]) != len(set(descriptor["values"]))
    ):
        raise _discovery_fail("discovery_capability_unsupported")
    return descriptor["values"]


def _range_descriptor(parameters: Mapping[str, Any], name: str) -> tuple[int, int]:
    descriptor = parameters.get(name)
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != {"type", "min", "max"}
        or descriptor.get("type") != "range"
        or not isinstance(descriptor.get("min"), int)
        or isinstance(descriptor.get("min"), bool)
        or not isinstance(descriptor.get("max"), int)
        or isinstance(descriptor.get("max"), bool)
        or descriptor["min"] > descriptor["max"]
    ):
        raise _discovery_fail("discovery_capability_unsupported")
    return descriptor["min"], descriptor["max"]


def build_openrouter_capability_snapshot(
    raw_body: bytes,
    *,
    requested_model: str,
    selected_provider_tag: str,
    captured_at: str,
    adapter_revision: str,
    source_capability_id: str,
    locality_mask_capability_id: str,
    adapter_admission_limits: OpenRouterAdapterAdmissionLimits,
) -> ProviderCapabilitySnapshot:
    """Project one exact endpoint-discovery body into the registered P0 capability."""

    if not isinstance(raw_body, bytes) or not raw_body:
        raise _discovery_fail("discovery_body_empty")
    if len(raw_body) > DISCOVERY_RESPONSE_MAX_BYTES:
        raise _discovery_fail("discovery_body_too_large")
    try:
        discovery = _parse_json(raw_body, max_bytes=DISCOVERY_RESPONSE_MAX_BYTES)
    except _DuplicateJsonKey:
        raise _discovery_fail("discovery_duplicate_key") from None
    except Exception:
        raise _discovery_fail("discovery_json_invalid") from None
    if not isinstance(discovery, dict):
        raise _discovery_fail("discovery_json_invalid")
    if discovery.get("id") != requested_model:
        raise _discovery_fail("discovery_model_mismatch")
    endpoints = discovery.get("endpoints")
    if (
        not isinstance(endpoints, list)
        or not endpoints
        or len(endpoints) > _DISCOVERY_MAX_ENDPOINTS
    ):
        raise _discovery_fail("discovery_endpoint_invalid")
    required_endpoint_fields = {
        "provider_name",
        "provider_slug",
        "provider_tag",
        "supported_parameters",
        "allowed_passthrough_parameters",
        "supports_streaming",
        "pricing",
    }
    if any(
        not isinstance(endpoint, dict) or not required_endpoint_fields <= set(endpoint)
        for endpoint in endpoints
    ):
        raise _discovery_fail("discovery_endpoint_invalid")
    matches = [
        endpoint
        for endpoint in endpoints
        if isinstance(endpoint, dict) and endpoint.get("provider_tag") == selected_provider_tag
    ]
    if not matches:
        raise _discovery_fail("discovery_route_missing")
    if len(matches) != 1:
        raise _discovery_fail("discovery_route_ambiguous")
    endpoint = matches[0]
    if (
        endpoint["provider_tag"] != selected_provider_tag
        or not isinstance(endpoint["provider_name"], str)
        or not 1 <= len(endpoint["provider_name"]) <= 256
        or not isinstance(endpoint["provider_slug"], str)
        or not 1 <= len(endpoint["provider_slug"]) <= 256
        or not isinstance(endpoint["supported_parameters"], dict)
        or not isinstance(endpoint["allowed_passthrough_parameters"], list)
        or any(not isinstance(item, str) for item in endpoint["allowed_passthrough_parameters"])
        or not isinstance(endpoint["supports_streaming"], bool)
        or not isinstance(endpoint["pricing"], list)
        or len(endpoint["pricing"]) > 128
        or any(not isinstance(item, dict) for item in endpoint["pricing"])
    ):
        raise _discovery_fail("discovery_endpoint_invalid")

    parameters = endpoint["supported_parameters"]
    advertised_resolutions = _enum_descriptor(parameters, "resolution")
    resolutions = [value for value in _REGISTERED_RESOLUTIONS if value in advertised_resolutions]
    aspects = [
        value
        for value in _REGISTERED_ASPECT_RATIOS
        if value in _enum_descriptor(parameters, "aspect_ratio")
    ]
    output_min, output_max = _range_descriptor(parameters, "n")
    reference_min, reference_max = _range_descriptor(parameters, "input_references")
    seed = parameters.get("seed")
    if (
        "1K" not in resolutions
        or not aspects
        or output_min != 1
        or output_max < 1
        or reference_min != 0
        or reference_max < 1
        or not isinstance(seed, dict)
        or set(seed) != {"type"}
        or seed.get("type") != "boolean"
        or endpoint["supports_streaming"] is not False
    ):
        raise _discovery_fail("discovery_capability_unsupported")
    effective_output_max = min(output_max, 8)
    effective_reference_max = min(reference_max, 16)

    _validate_admission_limits(adapter_admission_limits)
    if adapter_revision != ADAPTER_REVISION:
        raise _discovery_fail("adapter_revision_unsupported")
    if not _validate_digest(source_capability_id) or not _validate_digest(
        locality_mask_capability_id
    ):
        raise _discovery_fail("operation_capability_id_invalid")

    draft = {
        "schema_version": CAPABILITY_VERSION,
        "captured_at": captured_at,
        "adapter_revision": adapter_revision,
        "provider": _PROVIDER,
        "requested_model": requested_model,
        "input_modalities": ["text", "image"],
        "image_input_budget": {
            "supported": True,
            "max_count": effective_reference_max,
            "ordered": True,
            "source_and_references_share_budget": True,
            "provider_roles": ["source_image", "visual_context"],
        },
        "outputs": {
            "min_count": 1,
            "max_count": effective_output_max,
            "mime_types": list(adapter_admission_limits.mime_types),
            "resolutions": resolutions,
            "aspect_ratios": aspects,
            "max_width": adapter_admission_limits.max_width,
            "max_height": adapter_admission_limits.max_height,
        },
        "options": {
            "schema_version": "moodboard.openrouter-images-options-capability.v1",
            "seed_supported": True,
            "resolutions": resolutions,
            "aspect_ratios": aspects,
        },
        "operation_input_capabilities": [
            {
                "capability_id": source_capability_id,
                "role": "source_image",
                "delivery_modes": ["native_input"],
                "provider_roles": ["source_image"],
                "provider_fields": ["input_references[0]"],
            },
            {
                "capability_id": locality_mask_capability_id,
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
            "endpoint_path": _IMAGE_ENDPOINT,
            "discovery_endpoint_path": f"/api/v1/images/models/{requested_model}/endpoints",
            "upstream_provider_tags": [selected_provider_tag],
            "input_reference_parameter": "input_references",
            "supports_streaming": False,
            "allowed_passthrough_parameters": copy.deepcopy(
                endpoint["allowed_passthrough_parameters"]
            ),
            "discovery_response": {
                "content_ref": blake3(raw_body).hexdigest(),
                "content_sha256": hashlib.sha256(raw_body).hexdigest(),
                "byte_count": len(raw_body),
            },
        },
    }
    try:
        artifact = seal_provider_artifact(draft)
    except Exception:
        raise _discovery_fail("discovery_capability_invalid") from None
    if not isinstance(artifact, ProviderCapabilitySnapshot):
        raise _discovery_fail("discovery_capability_invalid")
    return artifact


def _detect_media_type(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    raise OpenRouterAdapterError("content_media_unsupported", "input image media is unsupported")


def _resolved_bytes(
    resolve_content: Callable[[str], bytes],
    *,
    content_ref: str,
    content_sha256: str,
    byte_count: int | None = None,
    declared_media_type: str | None = None,
) -> tuple[bytes, str]:
    if byte_count is not None and (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or not 1 <= byte_count <= _INPUT_IMAGE_MAX_BYTES
    ):
        raise OpenRouterAdapterError("content_too_large", "input image exceeds the bound")
    try:
        payload = resolve_content(content_ref)
    except Exception:
        raise OpenRouterAdapterError("content_unavailable", "input image is unavailable") from None
    if not isinstance(payload, bytes) or not payload:
        raise OpenRouterAdapterError("content_invalid", "input image bytes are invalid")
    if len(payload) > _INPUT_IMAGE_MAX_BYTES:
        raise OpenRouterAdapterError("content_too_large", "input image exceeds the bound")
    if (
        blake3(payload).hexdigest() != content_ref
        or hashlib.sha256(payload).hexdigest() != content_sha256
        or (byte_count is not None and len(payload) != byte_count)
    ):
        raise OpenRouterAdapterError("content_identity_mismatch", "input image identity mismatch")
    measured_media_type = _detect_media_type(payload)
    if declared_media_type is not None and measured_media_type != declared_media_type:
        raise OpenRouterAdapterError("content_media_mismatch", "input image media mismatch")
    return payload, measured_media_type


def _data_url(payload: bytes, media_type: str) -> str:
    return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _data_url_character_count(payload: bytes, media_type: str) -> int:
    return len(f"data:{media_type};base64,") + ((len(payload) + 2) // 3) * 4


def _active_credential_variants(value: str) -> tuple[bytes, ...]:
    raw = value.encode("utf-8", errors="strict")
    standard = base64.b64encode(raw)
    urlsafe = base64.urlsafe_b64encode(raw)
    hexadecimal = raw.hex()
    return tuple(
        sorted(
            {
                raw,
                standard,
                standard.rstrip(b"="),
                urlsafe,
                urlsafe.rstrip(b"="),
                hexadecimal.encode("ascii"),
                hexadecimal.upper().encode("ascii"),
            },
            key=len,
            reverse=True,
        )
    )


def _is_valid_bearer_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 8 <= len(value) <= 4096
        and _BEARER_TOKEN_PATTERN.fullmatch(value) is not None
    )


def _contains_active_credential(values: tuple[bytes, ...], variants: tuple[bytes, ...]) -> bool:
    return any(secret in value for value in values for secret in variants)


def _contains_credential_like_bytes(*values: bytes) -> bool:
    for value in values:
        text = value.decode("utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS):
            return True
    return False


def _tree_contains_credentials(value: Any, active_variants: tuple[bytes, ...] = ()) -> bool:
    active_text = tuple(variant.decode("ascii") for variant in active_variants)
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            if any(pattern.search(current) for pattern in _HIGH_CONFIDENCE_SECRET_PATTERNS) or any(
                secret in current for secret in active_text
            ):
                return True
    return False


def _compile_prompt(packet: Mapping[str, Any]) -> str:
    text_items: list[str] = []
    for reference in packet["references"]:
        if reference["provider_use"] == "prompt_context_only":
            text_items.extend(reference["prompt_context"]["text_items"])
    prompt = packet["instruction"]
    if text_items:
        prompt = f"{prompt} Reference context: {'; '.join(text_items)}."
    if not 1 <= len(prompt) <= 32_768:
        raise OpenRouterAdapterError("prompt_invalid", "compiled prompt is outside the bound")
    return prompt


def _expected_reference_use(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    provider_position = 1
    for reference in packet["references"]:
        use = reference["provider_use"]
        item: dict[str, Any] = {
            "reference_occurrence_id": reference["reference_occurrence_id"],
            "provider_use": use,
            "provider_position": None,
            "provider_field": None,
            "provider_role": None,
            "content_sha256": None,
            "prompt_context": None,
        }
        if use == "attached_image":
            item.update(
                {
                    "provider_position": provider_position,
                    "provider_field": f"input_references[{provider_position}]",
                    "provider_role": reference["role"],
                    "content_sha256": reference["content_sha256"],
                }
            )
            provider_position += 1
        elif use == "prompt_context_only":
            item["prompt_context"] = copy.deepcopy(reference["prompt_context"])
        expected.append(item)
    return expected


def _validate_normalized_packet_bindings(
    packet: Mapping[str, Any], normalized: Mapping[str, Any]
) -> None:
    request = packet["generation_request"]
    expected_references = _expected_reference_use(packet)
    routes = [
        route
        for route in request["provider_route_policy"]["permitted_routes"]
        if route["route_id"] == normalized["selected_route_id"]
    ]
    if (
        len(routes) != 1
        or normalized["intent_packet_id"] != packet["intent_packet_id"]
        or normalized["requested_provider"] != request["requested_provider"]
        or normalized["requested_model"] != request["requested_model"]
        or normalized["provider_route_policy_id"]
        != request["provider_route_policy"]["provider_route_policy_id"]
        or normalized["adapter_revision"] != request["adapter_revision"]
        or normalized["capability_snapshot_id"] != request["capability_snapshot_id"]
        or normalized["prompt"]
        != {"text": _compile_prompt(packet), "compiler_revision": PROMPT_COMPILER_REVISION}
        or normalized["output_count"] != request["output_count"]
        or normalized["options"] != request["options"]
        or normalized["operation_inputs"] != request["operation_inputs"]
        or normalized["reference_use"] != expected_references
        or normalized["destination"] != request["destination"]
    ):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")

    route = routes[0]
    body = normalized["provider_body"]
    if (
        body["model"] != request["requested_model"]
        or body["prompt"] != normalized["prompt"]["text"]
        or body["n"] != request["output_count"]
        or body["seed"] != request["options"]["seed"]
        or body["resolution"] != request["options"]["resolution"]
        or body["aspect_ratio"] != request["options"]["aspect_ratio"]
        or body["provider"] != {"only": [route["upstream_provider_tag"]], "allow_fallbacks": False}
    ):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")

    source_input = request["operation_inputs"][0]
    delivered = source_input["delivered_artifact"]
    expected_inputs: list[dict[str, Any]] = [
        {
            "position": 0,
            "provider_field": "input_references[0].image_url.url",
            "item_type": "image_url",
            "transport": "data_url",
            "media_type": packet["source"]["mime"],
            "role": "source_image",
            "source_kind": "operation_input",
            "source_id": source_input["original_artifact"]["asset_id"],
            "content_sha256": delivered["content_sha256"],
        }
    ]
    for reference, reference_projection in zip(
        packet["references"], expected_references, strict=True
    ):
        if reference["provider_use"] != "attached_image":
            continue
        expected_inputs.append(
            {
                "position": reference_projection["provider_position"],
                "provider_field": (
                    f"input_references[{reference_projection['provider_position']}].image_url.url"
                ),
                "item_type": "image_url",
                "transport": "data_url",
                "media_type": None,
                "role": "visual_context",
                "source_kind": "reference_occurrence",
                "source_id": reference["reference_occurrence_id"],
                "content_sha256": reference["content_sha256"],
            }
        )
    authorities = body["input_references"]
    if len(authorities) != len(expected_inputs):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    for authority, expected in zip(authorities, expected_inputs, strict=True):
        for field_name, expected_value in expected.items():
            if field_name == "media_type" and expected_value is None:
                continue
            if authority[field_name] != expected_value:
                raise OpenRouterAdapterError(
                    "prepared_request_invalid", "prepared request is invalid"
                )


def _validate_packet_capability(
    packet: Mapping[str, Any], capability: Mapping[str, Any], selected_route_id: str
) -> Mapping[str, Any]:
    request = packet["generation_request"]
    if (
        request["requested_provider"] != _PROVIDER
        or request["adapter_revision"] != ADAPTER_REVISION
        or request["output_count"] != 1
        or capability["provider"] != _PROVIDER
        or capability["requested_model"] != request["requested_model"]
        or capability["adapter_revision"] != ADAPTER_REVISION
        or capability["capability_snapshot_id"] != request["capability_snapshot_id"]
        or capability["idempotency"] != request["idempotency"]
        or capability["reconciliation"] != request["reconciliation"]
        or capability["actual_model_disclosure"] != "not_attested"
        or capability["upstream_route_disclosure"] != "not_attested"
        or request["actual_model_policy"] != "requested_only_permitted"
        or request["idempotency"]
        != {
            "provider_accepts_key": False,
            "deduplication_scope": None,
            "retention_seconds": None,
            "ambiguous_transport_retransmit_safe": False,
        }
        or request["reconciliation"] != {"supported": False, "provider_handle_kind": None}
        or not request["provider_route_policy"]["undisclosed_upstream_routing_permitted"]
    ):
        raise OpenRouterAdapterError("capability_mismatch", "capability does not bind packet")
    routes = [
        route
        for route in request["provider_route_policy"]["permitted_routes"]
        if route["route_id"] == selected_route_id
    ]
    if len(routes) != 1:
        raise OpenRouterAdapterError("route_not_confirmed", "selected route is not confirmed")
    route = routes[0]
    if (
        route["provider"] != _PROVIDER
        or route["model"] != request["requested_model"]
        or route["upstream_provider_tag"]
        not in capability["provider_specific"]["upstream_provider_tags"]
        or capability["provider_specific"]["endpoint_path"] != _IMAGE_ENDPOINT
        or capability["provider_specific"]["supports_streaming"] is not False
        or capability["provider_specific"]["allowed_passthrough_parameters"]
    ):
        raise OpenRouterAdapterError("route_capability_mismatch", "route is not authorized")
    options = request["options"]
    option_capability = capability["options"]
    outputs = capability["outputs"]
    if (
        not option_capability["seed_supported"]
        or options["resolution"] not in option_capability["resolutions"]
        or options["aspect_ratio"] not in option_capability["aspect_ratios"]
        or options["resolution"] not in outputs["resolutions"]
        or options["aspect_ratio"] not in outputs["aspect_ratios"]
        or not outputs["min_count"] <= 1 <= outputs["max_count"]
    ):
        raise OpenRouterAdapterError("option_not_supported", "confirmed option is unsupported")
    operation_inputs = request["operation_inputs"]
    source, mask = operation_inputs
    by_id = {item["capability_id"]: item for item in capability["operation_input_capabilities"]}
    source_capability = by_id.get(source["capability_id"])
    mask_capability = by_id.get(mask["capability_id"])
    if (
        source["role"] != "source_image"
        or source["delivery_mode"] != "native_input"
        or source["provider_field"] != "input_references[0]"
        or source["provider_role"] != "source_image"
        or source["delivered_artifact"] is None
        or source["derivative"] is not None
        or mask["role"] != "locality_mask"
        or mask["delivery_mode"] != "not_sent"
        or mask["provider_field"] is not None
        or mask["provider_role"] is not None
        or mask["delivered_artifact"] is not None
        or mask["derivative"] is not None
        or not isinstance(source_capability, dict)
        or source_capability["role"] != "source_image"
        or "native_input" not in source_capability["delivery_modes"]
        or source["provider_field"] not in source_capability["provider_fields"]
        or source["provider_role"] not in source_capability["provider_roles"]
        or not isinstance(mask_capability, dict)
        or mask_capability["role"] != "locality_mask"
        or "not_sent" not in mask_capability["delivery_modes"]
    ):
        raise OpenRouterAdapterError(
            "operation_input_not_supported", "operation input is unsupported"
        )
    attached_count = sum(
        reference["provider_use"] == "attached_image" for reference in packet["references"]
    )
    budget = capability["image_input_budget"]
    required_roles = {"source_image"}
    if attached_count:
        required_roles.add("visual_context")
    if (
        not budget["supported"]
        or not budget["ordered"]
        or not budget["source_and_references_share_budget"]
        or 1 + attached_count > budget["max_count"]
        or not required_roles.issubset(set(budget["provider_roles"]))
        or not {"text", "image"}.issubset(set(capability["input_modalities"]))
    ):
        raise OpenRouterAdapterError(
            "image_budget_unsupported", "image input budget is unsupported"
        )
    return route


def prepare_openrouter_request(
    intent_packet: IntentPacket | Mapping[str, Any],
    capability: ProviderCapabilitySnapshot | Mapping[str, Any],
    *,
    selected_route_id: str,
    resolve_content: Callable[[str], bytes],
) -> OpenRouterPreparedRequest:
    """Prepare one exact, secret-free request without touching the attempt journal."""

    packet = _packet_document(intent_packet)
    try:
        packet_artifact = intent_packet_from_json(packet)
    except Exception:
        raise OpenRouterAdapterError("packet_invalid", "intent packet is invalid") from None
    try:
        packet_bytes = canonical_json_bytes(packet)
    except Exception:
        raise OpenRouterAdapterError("packet_invalid", "intent packet is invalid") from None
    if _contains_credential_like_bytes(packet_bytes):
        raise OpenRouterAdapterError(
            "credential_material_detected", "packet contains credential-like material"
        )
    _, capability_document = _provider_document(
        capability, ProviderCapabilitySnapshot, "capability_invalid"
    )
    if not isinstance(selected_route_id, str) or not selected_route_id:
        raise OpenRouterAdapterError("route_not_confirmed", "selected route is not confirmed")
    if not callable(resolve_content):
        raise OpenRouterAdapterError("content_resolver_invalid", "content resolver is invalid")
    route = _validate_packet_capability(packet, capability_document, selected_route_id)
    request = packet["generation_request"]
    source_input = request["operation_inputs"][0]
    delivered = source_input["delivered_artifact"]
    source_bytes, source_media = _resolved_bytes(
        resolve_content,
        content_ref=delivered["content_ref"],
        content_sha256=delivered["content_sha256"],
        byte_count=delivered["byte_count"],
        declared_media_type=packet["source"]["mime"],
    )
    if _contains_credential_like_bytes(source_bytes):
        raise OpenRouterAdapterError(
            "credential_material_detected", "input contains credential-like material"
        )
    data_url_characters = _data_url_character_count(source_bytes, source_media)
    if data_url_characters > _DATA_URL_TOTAL_MAX_CHARS:
        raise OpenRouterAdapterError("request_too_large", "provider request exceeds the bound")
    source_url = _data_url(source_bytes, source_media)
    wire_inputs: list[dict[str, Any]] = [{"type": "image_url", "image_url": {"url": source_url}}]
    body_inputs: list[dict[str, Any]] = [
        {
            "position": 0,
            "provider_field": "input_references[0].image_url.url",
            "item_type": "image_url",
            "transport": "data_url",
            "media_type": source_media,
            "role": "source_image",
            "source_kind": "operation_input",
            "source_id": source_input["original_artifact"]["asset_id"],
            "content_sha256": delivered["content_sha256"],
            "transport_value_sha256": hashlib.sha256(source_url.encode("utf-8")).hexdigest(),
        }
    ]
    reference_use: list[dict[str, Any]] = []
    provider_position = 1
    for reference in packet["references"]:
        use = reference["provider_use"]
        item: dict[str, Any] = {
            "reference_occurrence_id": reference["reference_occurrence_id"],
            "provider_use": use,
            "provider_position": None,
            "provider_field": None,
            "provider_role": None,
            "content_sha256": None,
            "prompt_context": None,
        }
        if use == "attached_image":
            payload, media_type = _resolved_bytes(
                resolve_content,
                content_ref=reference["content_ref"],
                content_sha256=reference["content_sha256"],
            )
            if _contains_credential_like_bytes(payload):
                raise OpenRouterAdapterError(
                    "credential_material_detected", "input contains credential-like material"
                )
            data_url_characters += _data_url_character_count(payload, media_type)
            if data_url_characters > _DATA_URL_TOTAL_MAX_CHARS:
                raise OpenRouterAdapterError(
                    "request_too_large", "provider request exceeds the bound"
                )
            url = _data_url(payload, media_type)
            wire_inputs.append({"type": "image_url", "image_url": {"url": url}})
            item.update(
                {
                    "provider_position": provider_position,
                    "provider_field": f"input_references[{provider_position}]",
                    "provider_role": reference["role"],
                    "content_sha256": reference["content_sha256"],
                }
            )
            body_inputs.append(
                {
                    "position": provider_position,
                    "provider_field": (f"input_references[{provider_position}].image_url.url"),
                    "item_type": "image_url",
                    "transport": "data_url",
                    "media_type": media_type,
                    "role": "visual_context",
                    "source_kind": "reference_occurrence",
                    "source_id": reference["reference_occurrence_id"],
                    "content_sha256": reference["content_sha256"],
                    "transport_value_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                }
            )
            provider_position += 1
        elif use == "prompt_context_only":
            item["prompt_context"] = copy.deepcopy(reference["prompt_context"])
        reference_use.append(item)

    prompt = _compile_prompt(packet)
    if _contains_credential_like_bytes(prompt.encode("utf-8")):
        raise OpenRouterAdapterError(
            "credential_material_detected", "prompt contains credential-like material"
        )
    options = request["options"]
    projection = {
        "schema_version": "moodboard.openrouter-images-body-projection.v1",
        "method": "POST",
        "endpoint_path": _IMAGE_ENDPOINT,
        "model": request["requested_model"],
        "prompt": prompt,
        "n": request["output_count"],
        "seed": options["seed"],
        "resolution": options["resolution"],
        "aspect_ratio": options["aspect_ratio"],
        "input_references": body_inputs,
        "provider": {"only": [route["upstream_provider_tag"]], "allow_fallbacks": False},
    }
    normalized_draft = {
        "schema_version": REQUEST_VERSION,
        "intent_packet_id": packet["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "selected_route_id": selected_route_id,
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": capability_document["capability_snapshot_id"],
        "prompt": {"text": prompt, "compiler_revision": PROMPT_COMPILER_REVISION},
        "output_count": request["output_count"],
        "options": copy.deepcopy(options),
        "operation_inputs": copy.deepcopy(request["operation_inputs"]),
        "reference_use": reference_use,
        "destination": copy.deepcopy(request["destination"]),
        "provider_body": projection,
    }
    try:
        normalized = seal_provider_artifact(normalized_draft)
    except Exception:
        raise OpenRouterAdapterError(
            "normalized_request_invalid", "normalized request is invalid"
        ) from None
    if not isinstance(normalized, NormalizedProviderRequest):
        raise OpenRouterAdapterError("normalized_request_invalid", "normalized request is invalid")
    wire_document = {
        "model": request["requested_model"],
        "prompt": prompt,
        "n": request["output_count"],
        "seed": options["seed"],
        "resolution": options["resolution"],
        "aspect_ratio": options["aspect_ratio"],
        "input_references": wire_inputs,
        "provider": {"only": [route["upstream_provider_tag"]], "allow_fallbacks": False},
    }
    wire_body = canonical_json_bytes(wire_document)
    if _contains_credential_like_bytes(wire_body):
        raise OpenRouterAdapterError(
            "credential_material_detected", "provider request contains credential-like material"
        )
    prepared = OpenRouterPreparedRequest(
        intent_packet=packet_artifact,
        normalized_request=normalized,
        wire_body=wire_body,
        wire_body_sha256=hashlib.sha256(wire_body).hexdigest(),
        wire_body_byte_count=len(wire_body),
    )
    _validate_prepared_request(prepared)
    return prepared


def _validate_prepared_request(
    prepared: OpenRouterPreparedRequest,
    *,
    active_credential_variants: tuple[bytes, ...] = (),
) -> dict[str, Any]:
    if not isinstance(prepared, OpenRouterPreparedRequest):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    try:
        packet = _packet_document(prepared.intent_packet)
        normalized = provider_to_json(prepared.normalized_request)
        validate_provider_artifact(normalized)
        wire = _parse_json(prepared.wire_body, max_bytes=_WIRE_REQUEST_MAX_BYTES)
    except Exception:
        raise OpenRouterAdapterError(
            "prepared_request_invalid", "prepared request is invalid"
        ) from None
    try:
        wire_is_canonical = canonical_json_bytes(wire) == prepared.wire_body
    except Exception:
        wire_is_canonical = False
    if not isinstance(wire, dict) or not wire_is_canonical:
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    _validate_normalized_packet_bindings(packet, normalized)
    try:
        normalized_bytes = canonical_json_bytes(normalized)
    except Exception:
        raise OpenRouterAdapterError(
            "prepared_request_invalid", "prepared request is invalid"
        ) from None
    if _contains_credential_like_bytes(normalized_bytes, prepared.wire_body):
        raise OpenRouterAdapterError(
            "credential_material_detected", "provider request contains credential-like material"
        )
    if active_credential_variants and (
        _tree_contains_credentials(packet, active_credential_variants)
        or _tree_contains_credentials(normalized, active_credential_variants)
    ):
        raise OpenRouterAdapterError(
            "credential_material_detected", "provider request contains credential material"
        )
    projection = normalized["provider_body"]
    locality_mask = next(
        (item for item in normalized["operation_inputs"] if item["role"] == "locality_mask"),
        None,
    )
    if (
        normalized["requested_provider"] != _PROVIDER
        or normalized["adapter_revision"] != ADAPTER_REVISION
        or normalized["output_count"] != 1
        or normalized["prompt"]["compiler_revision"] != PROMPT_COMPILER_REVISION
        or projection["schema_version"] != "moodboard.openrouter-images-body-projection.v1"
        or projection["method"] != "POST"
        or projection["endpoint_path"] != _IMAGE_ENDPOINT
        or not isinstance(locality_mask, dict)
        or locality_mask["delivery_mode"] != "not_sent"
    ):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    expected_keys = {
        "model",
        "prompt",
        "n",
        "seed",
        "resolution",
        "aspect_ratio",
        "input_references",
        "provider",
    }
    if set(wire) != expected_keys:
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    for field_name in ("model", "prompt", "n", "seed", "resolution", "aspect_ratio", "provider"):
        if wire[field_name] != projection[field_name]:
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    wire_inputs = wire["input_references"]
    authorities = projection["input_references"]
    expected_content_refs = [
        packet["generation_request"]["operation_inputs"][0]["delivered_artifact"]["content_ref"],
        *[
            reference["content_ref"]
            for reference in packet["references"]
            if reference["provider_use"] == "attached_image"
        ],
    ]
    if not isinstance(wire_inputs, list) or len(wire_inputs) != len(authorities):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    if len(expected_content_refs) != len(authorities):
        raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
    for wire_item, authority, expected_content_ref in zip(
        wire_inputs, authorities, expected_content_refs, strict=True
    ):
        if (
            not isinstance(wire_item, dict)
            or set(wire_item) != {"type", "image_url"}
            or wire_item.get("type") != "image_url"
            or not isinstance(wire_item.get("image_url"), dict)
            or set(wire_item["image_url"]) != {"url"}
            or not isinstance(wire_item["image_url"]["url"], str)
        ):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
        url = wire_item["image_url"]["url"]
        prefix = f"data:{authority['media_type']};base64,"
        if (
            not url.startswith(prefix)
            or hashlib.sha256(url.encode("utf-8")).hexdigest()
            != authority["transport_value_sha256"]
        ):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
        try:
            decoded = base64.b64decode(url[len(prefix) :], validate=True)
        except (ValueError, binascii.Error):
            raise OpenRouterAdapterError(
                "prepared_request_invalid", "prepared request is invalid"
            ) from None
        try:
            measured_media_type = _detect_media_type(decoded)
        except OpenRouterAdapterError:
            raise OpenRouterAdapterError(
                "prepared_request_invalid", "prepared request is invalid"
            ) from None
        if (
            hashlib.sha256(decoded).hexdigest() != authority["content_sha256"]
            or blake3(decoded).hexdigest() != expected_content_ref
            or measured_media_type != authority["media_type"]
        ):
            raise OpenRouterAdapterError("prepared_request_invalid", "prepared request is invalid")
        if active_credential_variants and _contains_active_credential(
            (decoded,), active_credential_variants
        ):
            raise OpenRouterAdapterError(
                "credential_material_detected", "provider request contains credential material"
            )
    return normalized


def _validate_attempt_normalized_bindings(
    attempt: GenerationAttempt, normalized: Mapping[str, Any]
) -> None:
    try:
        normalized_bytes = canonical_json_bytes(dict(normalized))
    except Exception:
        raise OpenRouterAdapterError(
            "dispatch_contract_mismatch", "dispatch inputs disagree"
        ) from None
    normalized_ref = attempt.normalized_request_ref
    if (
        attempt.normalized_request_id != normalized["normalized_request_id"]
        or attempt.intent_packet_id != normalized["intent_packet_id"]
        or attempt.requested_provider != normalized["requested_provider"]
        or attempt.requested_model != normalized["requested_model"]
        or attempt.selected_route_id != normalized["selected_route_id"]
        or attempt.provider_route_policy_id != normalized["provider_route_policy_id"]
        or attempt.adapter_revision != ADAPTER_REVISION
        or attempt.capability_snapshot_id != normalized["capability_snapshot_id"]
        or normalized_ref["schema_version"] != REQUEST_VERSION
        or normalized_ref["artifact_id"] != normalized["normalized_request_id"]
        or normalized_ref["content_ref"] != blake3(normalized_bytes).hexdigest()
        or normalized_ref["content_sha256"] != hashlib.sha256(normalized_bytes).hexdigest()
        or normalized_ref["byte_count"] != len(normalized_bytes)
    ):
        raise OpenRouterAdapterError("dispatch_contract_mismatch", "dispatch inputs disagree")


def _validate_dispatch_bindings(
    attempt: GenerationAttempt,
    normalized: Mapping[str, Any],
    capability: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> None:
    _validate_attempt_normalized_bindings(attempt, normalized)
    route = _validate_packet_capability(packet, capability, normalized["selected_route_id"])
    if (
        attempt.capability_snapshot_id != capability["capability_snapshot_id"]
        or capability["provider"] != _PROVIDER
        or capability["requested_model"] != attempt.requested_model
        or capability["adapter_revision"] != ADAPTER_REVISION
        or capability["actual_model_disclosure"] != "not_attested"
        or capability["upstream_route_disclosure"] != "not_attested"
        or capability["idempotency"]
        != {
            "provider_accepts_key": False,
            "deduplication_scope": None,
            "retention_seconds": None,
            "ambiguous_transport_retransmit_safe": False,
        }
        or capability["reconciliation"] != {"supported": False, "provider_handle_kind": None}
    ):
        raise OpenRouterAdapterError("dispatch_contract_mismatch", "dispatch inputs disagree")
    options = normalized["options"]
    option_capability = capability["options"]
    outputs = capability["outputs"]
    body = normalized["provider_body"]
    selected_tags = body["provider"]["only"]
    if (
        normalized["output_count"] != 1
        or not outputs["min_count"] <= 1 <= outputs["max_count"]
        or not option_capability["seed_supported"]
        or options["resolution"] not in option_capability["resolutions"]
        or options["aspect_ratio"] not in option_capability["aspect_ratios"]
        or options["resolution"] not in outputs["resolutions"]
        or options["aspect_ratio"] not in outputs["aspect_ratios"]
        or len(body["input_references"]) > capability["image_input_budget"]["max_count"]
        or selected_tags != [route["upstream_provider_tag"]]
        or body["endpoint_path"] != capability["provider_specific"]["endpoint_path"]
        or capability["provider_specific"]["supports_streaming"] is not False
        or capability["provider_specific"]["allowed_passthrough_parameters"]
    ):
        raise OpenRouterAdapterError("dispatch_contract_mismatch", "dispatch inputs disagree")


def _canonical_attempt(value: GenerationAttempt | Mapping[str, Any]) -> GenerationAttempt:
    artifact, _ = _provider_document(value, GenerationAttempt, "attempt_invalid")
    assert isinstance(artifact, GenerationAttempt)
    return artifact


def _decimal_cost(value: Any) -> dict[str, Any]:
    if value is None:
        return {
            "state": "unavailable",
            "amount": None,
            "currency": None,
            "provenance": "not_reported",
        }
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    try:
        measured = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise OpenRouterAdapterError(
            "invalid_provider_response", "provider response is invalid"
        ) from None
    if not measured.is_finite() or measured < 0:
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    if measured.is_zero():
        amount = "0"
    else:
        _, digits, exponent = measured.as_tuple()
        if not isinstance(exponent, int):
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            )
        trailing_zeroes = 0
        for digit in reversed(digits):
            if digit != 0:
                break
            trailing_zeroes += 1
        effective_digits = len(digits) - trailing_zeroes
        effective_exponent = exponent + trailing_zeroes
        integer_digits = max(1, effective_digits + effective_exponent)
        fractional_digits = max(0, -effective_exponent)
        if integer_digits > 21 or fractional_digits > 18:
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            )
        amount = format(measured, "f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    if amount in {"", "-0"}:
        amount = "0"
    integer, _, fraction = amount.partition(".")
    if len(integer) > 21 or len(fraction) > 18:
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    return {
        "state": "reported",
        "amount": amount,
        "currency": "USD",
        "provenance": "provider_receipt",
    }


def _timestamp_key(value: str) -> tuple[str, int]:
    fraction = value[20:-1] if len(value) > 20 else ""
    return value[:19], int(fraction.ljust(9, "0") or "0")


def _sample_recorded_at(source: str | Callable[[], str], *, not_before: str | None) -> str:
    try:
        value = source() if callable(source) else source
    except Exception:
        raise OpenRouterAdapterError(
            "dispatch_timestamp_invalid", "dispatch timestamp is invalid"
        ) from None
    if not is_canonical_utc_timestamp(value) or (
        not_before is not None and _timestamp_key(value) < _timestamp_key(not_before)
    ):
        raise OpenRouterAdapterError("dispatch_timestamp_invalid", "dispatch timestamp is invalid")
    return value


def decode_openrouter_response(
    attempt: GenerationAttempt | Mapping[str, Any],
    prepared: OpenRouterPreparedRequest,
    response: OpenRouterHttpResponse,
    *,
    received_at: str,
    _active_credential_variants: tuple[bytes, ...] = (),
) -> OpenRouterDecodedResponse:
    """Decode one complete buffered 200 response without publishing an output occurrence."""

    descriptor = _canonical_attempt(attempt)
    normalized = _validate_prepared_request(prepared)
    try:
        _validate_attempt_normalized_bindings(descriptor, normalized)
    except OpenRouterAdapterError:
        raise OpenRouterAdapterError(
            "attempt_request_mismatch", "attempt and request disagree"
        ) from None
    if not isinstance(response, OpenRouterHttpResponse) or response.status != 200:
        raise OpenRouterAdapterError("http_status_not_success", "HTTP response is not successful")
    if not is_canonical_utc_timestamp(received_at):
        raise OpenRouterAdapterError("received_at_invalid", "received_at is invalid")
    try:
        document = _parse_json(response.body, max_bytes=_HTTP_RESPONSE_MAX_BYTES)
    except Exception:
        raise OpenRouterAdapterError(
            "invalid_provider_response", "provider response is invalid"
        ) from None
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("created"), int)
        or isinstance(document.get("created"), bool)
        or not 0 <= document["created"] <= 9_007_199_254_740_991
        or not isinstance(document.get("data"), list)
    ):
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    if _tree_contains_credentials(document, _active_credential_variants):
        raise OpenRouterAdapterError(
            "credential_material_detected", "provider response contains credential-like material"
        )
    if not set(document) <= {"created", "data", "usage"}:
        raise OpenRouterAdapterError(
            "provider_provenance_conflict", "provider response provenance is unsupported"
        )
    data = document["data"]
    if len(data) != normalized["output_count"]:
        raise OpenRouterAdapterError(
            "output_count_mismatch", "provider output count differs from request"
        )
    outputs: list[dict[str, Any]] = []
    payloads: list[bytes] = []
    for index, item in enumerate(data):
        if (
            not isinstance(item, dict)
            or not {"b64_json"} <= set(item) <= {"b64_json", "media_type"}
            or not isinstance(item.get("b64_json"), str)
            or not 1 <= len(item["b64_json"]) <= _BASE64_OUTPUT_MAX_CHARS
        ):
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            )
        media_type = item.get("media_type")
        if media_type is not None and (
            not isinstance(media_type, str) or _MIME_PATTERN.fullmatch(media_type) is None
        ):
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            )
        try:
            encoded = item["b64_json"].encode("ascii")
            payload = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error):
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            ) from None
        if not payload or len(payload) > _ENCODED_OUTPUT_MAX_BYTES:
            raise OpenRouterAdapterError(
                "invalid_provider_response", "provider response is invalid"
            )
        if _contains_credential_like_bytes(payload) or (
            _active_credential_variants
            and _contains_active_credential((payload,), _active_credential_variants)
        ):
            raise OpenRouterAdapterError(
                "credential_material_detected",
                "provider response contains credential-like material",
            )
        payloads.append(payload)
        outputs.append(
            {
                "output_index": index,
                "role": "generated_image",
                "content_ref": blake3(payload).hexdigest(),
                "content_sha256": hashlib.sha256(payload).hexdigest(),
                "byte_count": len(payload),
                "media_type_claim": media_type,
            }
        )
    usage = document.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    cost = _decimal_cost(None if usage is None else usage.get("cost"))
    draft = {
        "schema_version": RECEIPT_VERSION,
        "attempt_id": descriptor.attempt_id,
        "normalized_request_id": normalized["normalized_request_id"],
        "received_at": received_at,
        "requested_provider": descriptor.requested_provider,
        "requested_model": descriptor.requested_model,
        "selected_route_id": descriptor.selected_route_id,
        "http_status": 200,
        "provider_handle": None,
        "actual_model": {"state": "undisclosed", "model": None, "source_field": None},
        "upstream_route": {"state": "unknown", "provider_tag": None, "source_field": None},
        "raw_response": {
            "state": "retained",
            "content_ref": blake3(response.body).hexdigest(),
            "content_sha256": hashlib.sha256(response.body).hexdigest(),
            "byte_count": len(response.body),
            "privacy": "private_provider_payload",
        },
        "outputs": outputs,
        "cost": cost,
        "latency": {
            "milliseconds": response.elapsed_milliseconds,
            "boundary": "submit_to_response_received",
        },
    }
    try:
        receipt = seal_provider_artifact(draft)
    except Exception:
        raise OpenRouterAdapterError(
            "invalid_provider_response", "provider response is invalid"
        ) from None
    if not isinstance(receipt, ProviderReceipt):
        raise OpenRouterAdapterError("invalid_provider_response", "provider response is invalid")
    return OpenRouterDecodedResponse(receipt, tuple(payloads), response.body)


def _event(
    attempt_id: str,
    sequence: int,
    state: str,
    recorded_at: str,
    detail: Mapping[str, Any],
) -> GenerationAttemptEvent:
    try:
        artifact = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt_id,
                "sequence": sequence,
                "state": state,
                "recorded_at": recorded_at,
                "detail": dict(detail),
            }
        )
    except Exception:
        raise OpenRouterAdapterError("event_invalid", "attempt event is invalid") from None
    if not isinstance(artifact, GenerationAttemptEvent):
        raise OpenRouterAdapterError("event_invalid", "attempt event is invalid")
    return artifact


def _append_result_event(
    journal: AttemptJournal,
    state: AttemptState,
    event: GenerationAttemptEvent,
) -> AttemptState:
    try:
        return journal.append_event(
            event,
            expected_head_event_id=state.head_event_id,
            expected_next_sequence=state.next_sequence,
        ).state
    except Exception:
        raise OpenRouterAdapterError(
            "event_persistence_failed", "attempt event could not be persisted"
        ) from None


def _terminal_result(
    journal: AttemptJournal,
    state: AttemptState,
    *,
    kind: Literal["failed", "outcome_unknown"],
    recorded_at: str,
    detail: Mapping[str, Any],
) -> OpenRouterDispatchResult:
    event = _event(state.attempt_id, state.next_sequence, kind, recorded_at, detail)
    next_state = _append_result_event(journal, state, event)
    return OpenRouterDispatchResult(kind, next_state, event, None)


def _not_sent(journal: AttemptJournal, attempt_id: str) -> OpenRouterDispatchResult:
    try:
        state = journal.read_state(attempt_id)
    except Exception:
        raise OpenRouterAdapterError(
            "journal_read_failed", "attempt state is unavailable"
        ) from None
    return OpenRouterDispatchResult("not_sent", state, None, None)


def dispatch_openrouter_attempt(
    journal: AttemptJournal,
    attempt: GenerationAttempt | Mapping[str, Any],
    capability: ProviderCapabilitySnapshot | Mapping[str, Any],
    prepared: OpenRouterPreparedRequest,
    *,
    credential_resolver: Callable[[str], str],
    transport: Callable[..., OpenRouterHttpResponse],
    response_publisher: Callable[[OpenRouterDecodedResponse], None],
    dispatch_claim_id: str,
    claimed_at: str,
    recorded_at: str | Callable[[], str],
) -> OpenRouterDispatchResult:
    """Authorize at most one local send and record its non-success terminal boundary."""

    if not isinstance(journal, AttemptJournal):
        raise OpenRouterAdapterError("journal_invalid", "attempt journal is invalid")
    descriptor = _canonical_attempt(attempt)
    capability_artifact, capability_document = _provider_document(
        capability, ProviderCapabilitySnapshot, "capability_invalid"
    )
    assert isinstance(capability_artifact, ProviderCapabilitySnapshot)
    normalized = _validate_prepared_request(prepared)
    packet = _packet_document(prepared.intent_packet)
    _validate_dispatch_bindings(descriptor, normalized, capability_document, packet)
    if not is_canonical_utc_timestamp(claimed_at) or not (
        isinstance(recorded_at, str) or callable(recorded_at)
    ):
        raise OpenRouterAdapterError("dispatch_timestamp_invalid", "dispatch timestamp is invalid")
    if not all(callable(value) for value in (credential_resolver, transport, response_publisher)):
        raise OpenRouterAdapterError("dispatch_callable_invalid", "dispatch callable is invalid")
    try:
        stored_attempt = journal.read_attempt(descriptor.attempt_id)
        stored_attempt_document = provider_to_json(stored_attempt)
        caller_attempt_document = provider_to_json(descriptor)
    except Exception:
        raise OpenRouterAdapterError(
            "journal_read_failed", "attempt state is unavailable"
        ) from None
    if stored_attempt_document != caller_attempt_document:
        raise OpenRouterAdapterError("dispatch_contract_mismatch", "dispatch inputs disagree")
    try:
        state = journal.read_state(descriptor.attempt_id)
    except Exception:
        raise OpenRouterAdapterError(
            "journal_read_failed", "attempt state is unavailable"
        ) from None
    if state.state != "prepared":
        return OpenRouterDispatchResult("not_sent", state, None, None)
    if state.last_recorded_at is not None and _timestamp_key(claimed_at) < _timestamp_key(
        state.last_recorded_at
    ):
        raise OpenRouterAdapterError("dispatch_timestamp_invalid", "dispatch timestamp is invalid")
    if isinstance(recorded_at, str):
        _sample_recorded_at(recorded_at, not_before=claimed_at)

    credential_profile_id = normalized["destination"]["credential_profile_id"]
    credential_failure_code = "credential_unavailable"
    active_credential_variants: tuple[bytes, ...] = ()
    try:
        bearer_token = credential_resolver(credential_profile_id)
        if not _is_valid_bearer_token(bearer_token):
            raise ValueError
        active_credential_variants = _active_credential_variants(bearer_token)
        try:
            _validate_prepared_request(
                prepared, active_credential_variants=active_credential_variants
            )
        except OpenRouterAdapterError as error:
            if error.code == "credential_material_detected":
                credential_failure_code = "credential_material_detected"
            raise ValueError from None
    except Exception:
        failed_at = _sample_recorded_at(recorded_at, not_before=state.last_recorded_at)
        failed = _event(
            descriptor.attempt_id,
            state.next_sequence,
            "failed",
            failed_at,
            {
                "kind": "failed",
                "failure_stage": "preflight",
                "failure_code": credential_failure_code,
            },
        )
        try:
            failed_state = journal.append_event(
                failed,
                expected_head_event_id=state.head_event_id,
                expected_next_sequence=state.next_sequence,
            ).state
        except (StaleAttemptHeadError, AttemptJournalError):
            current = journal.read_state(descriptor.attempt_id)
            if current.state != "prepared":
                return OpenRouterDispatchResult("not_sent", current, None, None)
            raise OpenRouterAdapterError(
                "credential_failure_persistence_failed", "credential failure was not persisted"
            ) from None
        return OpenRouterDispatchResult("failed", failed_state, failed, None)

    try:
        claim = journal.claim_non_idempotent_dispatch(
            descriptor.attempt_id,
            capability_document,
            expected_head_event_id=state.head_event_id or "",
            expected_next_sequence=state.next_sequence,
            dispatch_claim_id=dispatch_claim_id,
            claimed_at=claimed_at,
            wire_request_sha256=prepared.wire_body_sha256,
            wire_request_byte_count=prepared.wire_body_byte_count,
        )
    except (DispatchClaimConflictError, AttemptJournalError):
        current = journal.read_state(descriptor.attempt_id)
        if current.state != "prepared":
            return OpenRouterDispatchResult("not_sent", current, None, None)
        raise OpenRouterAdapterError(
            "dispatch_claim_conflict", "dispatch claim could not be acquired"
        ) from None
    if not claim.send_authorized:
        return _not_sent(journal, descriptor.attempt_id)
    submitted_state = claim.state

    try:
        response = transport(body=prepared.wire_body, bearer_token=bearer_token)
    except Exception:
        bearer_token = ""
        event_at = _sample_recorded_at(recorded_at, not_before=submitted_state.last_recorded_at)
        return _terminal_result(
            journal,
            submitted_state,
            kind="outcome_unknown",
            recorded_at=event_at,
            detail={
                "kind": "outcome_unknown",
                "failure_stage": "dispatch",
                "failure_code": "ambiguous_transport",
                "provider_handle": None,
            },
        )
    finally:
        bearer_token = ""
    if not isinstance(response, OpenRouterHttpResponse):
        event_at = _sample_recorded_at(recorded_at, not_before=submitted_state.last_recorded_at)
        return _terminal_result(
            journal,
            submitted_state,
            kind="outcome_unknown",
            recorded_at=event_at,
            detail={
                "kind": "outcome_unknown",
                "failure_stage": "dispatch",
                "failure_code": "ambiguous_transport",
                "provider_handle": None,
            },
        )
    response_at = _sample_recorded_at(recorded_at, not_before=submitted_state.last_recorded_at)
    if response.status != 200:
        return _terminal_result(
            journal,
            submitted_state,
            kind="failed",
            recorded_at=response_at,
            detail={
                "kind": "failed",
                "failure_stage": "provider",
                "failure_code": f"openrouter_http_{response.status}",
            },
        )
    try:
        decoded = decode_openrouter_response(
            descriptor,
            prepared,
            response,
            received_at=response_at,
            _active_credential_variants=active_credential_variants,
        )
    except OpenRouterAdapterError as error:
        if error.code == "provider_provenance_conflict":
            return _terminal_result(
                journal,
                submitted_state,
                kind="failed",
                recorded_at=response_at,
                detail={
                    "kind": "failed",
                    "failure_stage": "provenance",
                    "failure_code": "provider_provenance_conflict",
                },
            )
        failure_code = (
            "output_count_mismatch"
            if error.code == "output_count_mismatch"
            else (
                "credential_material_detected"
                if error.code == "credential_material_detected"
                else "invalid_provider_response"
            )
        )
        return _terminal_result(
            journal,
            submitted_state,
            kind="failed",
            recorded_at=response_at,
            detail={
                "kind": "failed",
                "failure_stage": "output_validation",
                "failure_code": failure_code,
            },
        )
    if _contains_active_credential(
        (decoded.raw_response_bytes, *decoded.output_bytes), active_credential_variants
    ):
        return _terminal_result(
            journal,
            submitted_state,
            kind="failed",
            recorded_at=response_at,
            detail={
                "kind": "failed",
                "failure_stage": "output_validation",
                "failure_code": "credential_material_detected",
            },
        )
    try:
        response_publisher(decoded)
    except Exception:
        return _terminal_result(
            journal,
            submitted_state,
            kind="failed",
            recorded_at=response_at,
            detail={
                "kind": "failed",
                "failure_stage": "output_validation",
                "failure_code": "output_persistence_failed",
            },
        )
    response_event = _event(
        descriptor.attempt_id,
        submitted_state.next_sequence,
        "response_received",
        response_at,
        {
            "kind": "response_received",
            "provider_receipt_id": decoded.receipt.provider_receipt_id,
        },
    )
    response_state = _append_result_event(journal, submitted_state, response_event)
    return OpenRouterDispatchResult("response_received", response_state, response_event, decoded)


def reconcile_openrouter_attempt(
    journal: AttemptJournal,
    attempt_id: str,
    *,
    transport: Callable[..., Any] | None = None,
) -> None:
    """Fail closed: this Image API revision has no authoritative reconciliation surface."""

    del transport
    try:
        state = journal.read_state(attempt_id)
    except Exception:
        raise OpenRouterAdapterError(
            "journal_read_failed", "attempt state is unavailable"
        ) from None
    if state.state == "outcome_unknown":
        raise OpenRouterAdapterError(
            "reconciliation_unsupported", "OpenRouter Image API reconciliation is unsupported"
        )
    raise OpenRouterAdapterError(
        "reconciliation_not_applicable", "attempt does not have an unknown outcome"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def openrouter_https_transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
    """Perform the registered single buffered POST with redirects disabled.

    The token remains in process memory and is never included in returned headers, exceptions,
    or representations.  Operational callers should still apply an outer total deadline.
    """

    if (
        not isinstance(body, bytes)
        or not 1 <= len(body) <= _WIRE_REQUEST_MAX_BYTES
        or not _is_valid_bearer_token(bearer_token)
    ):
        raise OpenRouterAdapterError("transport_input_invalid", "transport input is invalid")
    request = urllib.request.Request(
        _IMAGE_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    started = time.monotonic_ns()
    try:
        try:
            response = opener.open(request, timeout=180)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status_value = response.getcode()
            if not isinstance(status_value, int):
                raise ValueError
            status = status_value
            declared_length = getattr(response, "length", None)
            if declared_length is not None and (
                not isinstance(declared_length, int)
                or isinstance(declared_length, bool)
                or not 0 <= declared_length <= _HTTP_RESPONSE_MAX_BYTES
            ):
                raise ValueError
            response_headers = getattr(response, "headers", None)
            content_length_values = (
                response_headers.get_all("Content-Length", [])
                if response_headers is not None
                and callable(getattr(response_headers, "get_all", None))
                else []
            )
            if len(content_length_values) > 1:
                raise ValueError
            if content_length_values:
                content_length_text = content_length_values[0].strip()
                if not content_length_text.isascii() or not content_length_text.isdecimal():
                    raise ValueError
                header_length = int(content_length_text)
                if header_length > _HTTP_RESPONSE_MAX_BYTES or (
                    declared_length is not None and header_length != declared_length
                ):
                    raise ValueError
                declared_length = header_length
            response_body = response.read(_HTTP_RESPONSE_MAX_BYTES + 1)
            if declared_length is not None and len(response_body) != declared_length:
                raise ValueError
            is_closed = getattr(response, "isclosed", None)
            if callable(is_closed) and not is_closed():
                raise ValueError
    except Exception:
        raise OpenRouterAdapterError("transport_failed", "OpenRouter transport failed") from None
    if len(response_body) > _HTTP_RESPONSE_MAX_BYTES:
        raise OpenRouterAdapterError("transport_failed", "OpenRouter response exceeded the bound")
    elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
    return OpenRouterHttpResponse(status, {}, response_body, elapsed)
