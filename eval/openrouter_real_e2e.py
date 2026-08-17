#!/usr/bin/env python3
"""Run one explicitly authorized, non-retrying OpenRouter localized-edit evaluation.

This is an opt-in evaluation harness, not an ordinary test and not a general provider CLI.  Its
model, route, output count, resolution, quote-admission limit, source, Keychain locator, and retry
policy are intentionally fixed.  Private provider bytes live only in an owner-only run directory;
the small ``result.json`` is a sanitized index into the verified local journal.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import http.client
import json
import math
import os
import resource
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import FrameType
from typing import Any, Final, NoReturn

from blake3 import blake3
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.openrouter_real_e2e_authority import (  # noqa: E402
    OpenRouterRealE2EAuthority,
    OpenRouterRealE2EAuthorityError,
)
from moodboard.attempt_journal import AttemptJournal, AttemptJournalError  # noqa: E402
from moodboard.contracts import (  # noqa: E402
    compute_document_identity,
    compute_projection_identity,
    is_canonical_utc_timestamp,
)
from moodboard.intent_packet import (  # noqa: E402
    IntentPacket,
)
from moodboard.intent_packet import (  # noqa: E402
    from_json_dict as intent_from_json,
)
from moodboard.intent_packet import (  # noqa: E402
    to_json_dict as intent_to_json,
)
from moodboard.judgment import to_json_dict as judgment_to_json  # noqa: E402
from moodboard.locality import (  # noqa: E402
    build_locality_not_run,
    compile_canonical_raster,
    compile_rectangle_mask,
    verify_output_structure,
    verify_outside_mask_rgb_exact,
)
from moodboard.locality_contracts import (  # noqa: E402
    EXACT_LOCALITY_VERIFIER_VERSION,
    CanonicalMaskArtifact,
    CanonicalRasterArtifact,
)
from moodboard.openrouter import (  # noqa: E402
    ADAPTER_REVISION,
    OpenRouterAdapterAdmissionLimits,
    OpenRouterDispatchResult,
    OpenRouterHttpResponse,
    OpenRouterPreparedRequest,
    build_openrouter_capability_snapshot,
    dispatch_openrouter_attempt,
    prepare_openrouter_request,
)
from moodboard.provider_artifacts import (  # noqa: E402
    ATTEMPT_VERSION,
    EVENT_VERSION,
    RUN_VERSION,
    GenerationAttempt,
    GenerationRun,
    OutputOccurrence,
    ProviderCapabilitySnapshot,
    build_normalized_request_ref,
    compute_provider_request_key,
    seal_provider_artifact,
)
from moodboard.provider_artifacts import (  # noqa: E402
    from_json_dict as provider_from_json,
)
from moodboard.provider_artifacts import (  # noqa: E402
    to_json_dict as provider_to_json,
)
from moodboard.provider_media import (  # noqa: E402
    ProviderMediaAdmissionError,
    build_provider_success_candidates,
)

JsonObject = dict[str, Any]

QUOTE_ADMISSION_LIMIT_USD: Final = Decimal("0.05")
# Compatibility alias for the first RED contract.  This is a quote-admission threshold, never a
# provider-enforced spending limit.
MAX_COST_USD: Final = QUOTE_ADMISSION_LIMIT_USD
_QUOTE_CONTEXT: Final = Context(prec=128)
MODEL: Final = "qwen/qwen-image-3"
PROVIDER_TAG: Final = "alibaba"
ROUTE_ID: Final = "openrouter-primary"
RESOLUTION: Final = "1K"
ASPECT_RATIO: Final = "4:3"
OUTPUT_COUNT: Final = 1
SEED: Final = 20_260_817

KEYCHAIN_SERVICE: Final = "OPENROUTER_API_KEY"
KEYCHAIN_ACCOUNT: Final = "khive"
CREDENTIAL_PROFILE_ID: Final = "00000000-0000-4000-8000-000000000005"

DISCOVERY_HOST: Final = "openrouter.ai"
DISCOVERY_PATH: Final = "/api/v1/images/models/qwen/qwen-image-3/endpoints"
DISPATCH_PATH: Final = "/api/v1/images"
SOURCE_HOST: Final = "upload.wikimedia.org"
SOURCE_PATH: Final = (
    "/wikipedia/commons/thumb/b/b5/Apple_tree_in_a_garden.JPG/"
    "1280px-Apple_tree_in_a_garden.JPG"
    "?utm_source=commons.wikimedia.org&utm_campaign=index&utm_content=thumbnail"
)
SOURCE_PAGE_URL: Final = "https://commons.wikimedia.org/wiki/File:Apple_tree_in_a_garden.JPG"
SOURCE_SHA256: Final = "3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23"
SOURCE_CONTENT_REF: Final = "d9c1a0e3e6a5a72a9da252a0ea9fb4616c9099dd20cdc65ea00ffc29d14f23a8"
SOURCE_BYTE_COUNT: Final = 645_201

_PACKET_VERSION: Final = "moodboard.intent-packet.v1"
_OPERATION_VERSION: Final = "moodboard.operation.localized-edit.v1"
_POLICY_VERSION: Final = "moodboard.verification-policy.v1"
_E2E_ID_DOMAIN: Final = "moodboard.openrouter-real-e2e.fixture.v1"
_SUMMARY_VERSION: Final = "moodboard.openrouter-real-e2e-summary.v1"
_CHALLENGE_VERSION: Final = "moodboard.openrouter-real-e2e-confirmation-challenge.v1"
_CONFIRMATION_CONTEXT_VERSION: Final = "moodboard.openrouter-real-e2e-confirmation-context.v1"
_COMPACT_SUMMARY_VERSION: Final = "moodboard.openrouter-real-e2e-compact-summary.v1"
_CHALLENGE_TTL_SECONDS: Final = 30 * 60
_REFERENCE_CONTENT_REF: Final = "cf72f06b425eb52039d6926e057f7f5720f16435341625ce2fc9b92f5b52069d"
_DISCOVERY_MAX_BYTES: Final = 4 * 1024 * 1024
_SOURCE_MAX_BYTES: Final = 1024 * 1024
_HTTP_RESPONSE_MAX_BYTES: Final = 23_418_200
_JSON_MAX_DEPTH: Final = 32
_JSON_MAX_NODES: Final = 10_000
_JSON_STRUCTURAL_TOKEN_MAX: Final = 2 * _JSON_MAX_NODES + _JSON_MAX_DEPTH
_CONNECT_TIMEOUT_SECONDS: Final = 10.0
_PREPARE_FETCH_TIMEOUT_SECONDS: Final = 30.0
_TOTAL_TIMEOUT_SECONDS: Final = 210.0
_SAFE_AUTHORITY_ERROR_CODES: Final = frozenset(
    {
        "artifact_snapshot_failed",
        "authority_context_unavailable",
        "board_artifact_invalid",
        "board_artifact_unavailable",
        "board_artifact_unverified",
        "eligible_corpus_invalid",
        "local_replace_references_invalid",
        "local_replace_route_invalid",
        "pixel_rag_artifact_invalid",
        "pixel_rag_artifact_unavailable",
        "pixel_rag_evidence_not_measured",
        "pixel_rag_projection_invalid",
    }
)


class OpenRouterRealE2EError(RuntimeError):
    """The one-shot evaluation stopped at a stable, secret-free boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OpenRouterRealE2EResult:
    generation_run_id: str
    attempt_id: str
    provider_receipt_id: str | None
    output_occurrence_id: str | None
    quoted_cost_usd: Decimal
    reported_cost_usd: Decimal | None
    cost_telemetry_status: str
    states: tuple[str, ...]
    generation_post_count: int
    provider_media_admission_result: str
    raw_structural_result: str
    raw_structural_reason: str | None
    raw_locality_result: str


@dataclass(frozen=True, slots=True)
class OpenRouterRealE2EChallenge:
    """Public, secret-free handle for one credential-free confirmation bundle."""

    challenge_id: str
    compact_summary_id: str
    prepared_at: str
    expires_at: str
    quoted_cost_usd: Decimal
    quote_admission_limit_usd: Decimal
    wire_body_sha256: str
    wire_body_byte_count: int
    directory: Path


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    document: JsonObject
    payload: bytes = field(repr=False)
    board_artifact_bytes: bytes | None = field(default=None, repr=False)
    pixel_rag_artifact_bytes: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _FinalizationInputs:
    challenge: JsonObject
    packet: IntentPacket
    capability: ProviderCapabilitySnapshot
    prepared: OpenRouterPreparedRequest
    run: GenerationRun
    attempt: GenerationAttempt
    source_bytes: bytes = field(repr=False)
    source_raster: CanonicalRasterArtifact
    mask: CanonicalMaskArtifact
    quote: Decimal
    discovery_body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class _FinalizedProviderEvidence:
    result: OpenRouterRealE2EResult
    document: JsonObject
    output_bytes: bytes | None = field(default=None, repr=False)
    output_suffix: str | None = None


def _fail(code: str) -> NoReturn:
    raise OpenRouterRealE2EError(code) from None


def _call_sanitized(operation: Callable[[], Any]) -> tuple[bool, Any | None]:
    """Run an untrusted boundary without retaining its exception in a public chain."""

    try:
        return True, operation()
    except BaseException:
        return False, None


def _enforce_no_core_dumps() -> None:
    ok, _ = _call_sanitized(lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
    if not ok:
        _fail("core_dump_policy_unavailable")
    ok, measured = _call_sanitized(lambda: resource.getrlimit(resource.RLIMIT_CORE))
    if not ok or measured != (0, 0):
        _fail("core_dump_policy_unavailable")


def _bounded_decimal(token: str) -> Decimal:
    if not isinstance(token, str) or not 1 <= len(token) <= 64:
        _fail("quote_invalid")
    try:
        value = Decimal(token)
    except (InvalidOperation, ValueError):
        _fail("quote_invalid")
    if not value.is_finite() or value < 0 or value.adjusted() > 20:
        _fail("quote_invalid")
    return value


def _bounded_integer(token: str) -> int:
    if not isinstance(token, str) or not 1 <= len(token) <= 32:
        _fail("discovery_invalid")
    try:
        return int(token)
    except ValueError:
        _fail("discovery_invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            _fail("discovery_invalid")
        result[key] = value
    return result


def _bounded_tree(value: Any, *, code: str = "discovery_invalid") -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _JSON_MAX_NODES or depth > _JSON_MAX_DEPTH:
            _fail(code)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, float):
            if not math.isfinite(current):
                _fail(code)
        elif current is not None and not isinstance(current, (bool, int, Decimal, str)):
            _fail(code)


def _preflight_json_structure(raw: bytes, *, code: str) -> None:
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
                _fail(code)
        elif byte in {0x5D, 0x7D}:
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                _fail(code)
        elif byte in {0x2C, 0x3A}:
            structural_tokens += 1
        if structural_tokens > _JSON_STRUCTURAL_TOKEN_MAX:
            _fail(code)
    if in_string or depth != 0:
        _fail(code)


def _parse_json(raw: bytes, *, code: str, max_bytes: int) -> JsonObject:
    if type(raw) is not bytes or not 1 <= len(raw) <= max_bytes:
        _fail(code)
    try:
        _preflight_json_structure(raw, code=code)
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_float=_bounded_decimal,
            parse_int=_bounded_integer,
            parse_constant=lambda _: _fail(code),
            object_pairs_hook=_unique_object,
        )
    except OpenRouterRealE2EError:
        raise
    except Exception:
        _fail(code)
    _bounded_tree(value)
    if not isinstance(value, dict):
        _fail(code)
    return value


def parse_openrouter_quote(
    raw_body: bytes,
    *,
    input_count: int,
    output_count: int,
    resolution: str,
) -> Decimal:
    """Derive the exact applicable image quote from one live endpoint response."""

    if (
        type(input_count) is not int
        or type(output_count) is not int
        or input_count != 1
        or output_count != 1
        or resolution != RESOLUTION
    ):
        _fail("quote_unsupported")
    document = _parse_json(raw_body, code="discovery_invalid", max_bytes=_DISCOVERY_MAX_BYTES)
    endpoints = document.get("endpoints")
    if document.get("id") != MODEL or not isinstance(endpoints, list):
        _fail("quote_ambiguous")
    selected = [
        endpoint
        for endpoint in endpoints
        if isinstance(endpoint, dict) and endpoint.get("provider_tag") == PROVIDER_TAG
    ]
    if len(selected) != 1:
        _fail("quote_ambiguous")
    pricing = selected[0].get("pricing")
    if not isinstance(pricing, list) or not pricing:
        _fail("quote_ambiguous")
    input_prices: list[Decimal] = []
    output_prices: list[Decimal] = []
    for row in pricing:
        if not isinstance(row, dict) or row.get("unit") != "image":
            _fail("quote_ambiguous")
        price = row.get("cost_usd")
        if not isinstance(price, Decimal) or not price.is_finite() or price < 0:
            _fail("quote_ambiguous")
        billable = row.get("billable")
        variant = row.get("variant")
        if billable == "input_image" and variant is None:
            if set(row) != {"billable", "unit", "cost_usd"}:
                _fail("quote_ambiguous")
            input_prices.append(price)
        elif billable == "output_image" and variant in {"1k", "2k"}:
            if set(row) != {"billable", "unit", "cost_usd", "variant"}:
                _fail("quote_ambiguous")
            if variant == resolution.lower():
                output_prices.append(price)
        else:
            _fail("quote_ambiguous")
    if len(input_prices) != 1 or len(output_prices) != 1:
        _fail("quote_ambiguous")
    # Decimal arithmetic otherwise inherits process-global precision and rounding.  A caller that
    # lowered precision could round 0.051 down to the 0.05 admission threshold before comparison.
    with localcontext(_QUOTE_CONTEXT):
        return input_prices[0] * input_count + output_prices[0] * output_count


def _resolve_keychain_token_sanitized() -> tuple[bool, str | None]:
    """Keep subprocess output and any diagnostic exception below a non-raising frame."""

    ok, completed = _call_sanitized(
        lambda: subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    )
    if not ok or not isinstance(completed, subprocess.CompletedProcess):
        return False, None
    if completed.returncode != 0:
        return False, None
    token = completed.stdout.strip()
    if not 16 <= len(token) <= 4096 or any(
        ord(character) < 33 or ord(character) > 126 for character in token
    ):
        return False, None
    return True, token


def load_openrouter_keychain_token(credential_profile_id: str) -> str:
    """Resolve the sole approved profile directly from macOS Keychain into memory."""

    if credential_profile_id != CREDENTIAL_PROFILE_ID:
        _fail("credential_profile_unsupported")
    ok, token = _resolve_keychain_token_sanitized()
    if not ok or token is None:
        _fail("credential_unavailable")
    return token


def _label_digest(label: str) -> str:
    return compute_projection_identity({"label": label}, domain_tag=_E2E_ID_DOMAIN)


def _canonical_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_value(value: object, *, code: str) -> datetime:
    if not is_canonical_utc_timestamp(value):
        _fail(code)
    assert isinstance(value, str)
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)


def _timestamp_after(value: str, *, seconds: int) -> str:
    measured = _timestamp_value(value, code="clock_invalid") + timedelta(seconds=seconds)
    return measured.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid_text(factory: Callable[[], str | uuid.UUID]) -> str:
    try:
        measured = str(uuid.UUID(str(factory())))
    except Exception:
        _fail("uuid_source_invalid")
    return measured


def _mime_for_bytes(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    _fail("source_invalid")


def _raster_document(raster: CanonicalRasterArtifact) -> JsonObject:
    return {
        "schema_version": raster.schema_version,
        "compiler_revision": raster.compiler_revision,
        "width": raster.width,
        "height": raster.height,
        "mode": raster.mode,
        "byte_count": raster.byte_count,
        "source_content_sha256": raster.source_content_sha256,
        "raster_sha256": raster.raster_sha256,
    }


def _mask_document(mask: CanonicalMaskArtifact) -> JsonObject:
    return {
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


def _source_asset_id(source_sha256: str) -> str:
    name = SOURCE_PAGE_URL if source_sha256 == SOURCE_SHA256 else f"urn:sha256:{source_sha256}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _mask_bounds(raster: CanonicalRasterArtifact) -> tuple[int, int, int, int]:
    if raster.width == 1280 and raster.height == 960:
        return 230, 48, 1152, 912
    left = max(0, raster.width // 4)
    top = max(0, raster.height // 6)
    right = min(raster.width, max(left + 1, raster.width * 3 // 4))
    bottom = min(raster.height, max(top + 1, raster.height * 5 // 6))
    if left == 0 and top == 0 and right == raster.width and bottom == raster.height:
        _fail("source_dimensions_unsupported")
    return left, top, right, bottom


def _render_mask_overlay(
    raster: CanonicalRasterArtifact,
    bounds: tuple[int, int, int, int],
) -> bytes:
    """Render the exact integer rectangle as a deterministic, inspectable PNG overlay."""

    left, top, right, bottom = bounds
    try:
        source = Image.frombytes("RGB", (raster.width, raster.height), raster.rgb_bytes)
        overlay = Image.new("RGBA", source.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        line_width = max(2, min(raster.width, raster.height) // 240)
        draw.rectangle(
            (left, top, right - 1, bottom - 1),
            fill=(255, 0, 128, 72),
            outline=(255, 0, 128, 255),
            width=line_width,
        )
        rendered = Image.alpha_composite(source.convert("RGBA"), overlay).convert("RGB")
        from io import BytesIO

        output = BytesIO()
        rendered.save(output, format="PNG", compress_level=9, optimize=False)
        payload = output.getvalue()
    except Exception:
        _fail("overlay_render_failed")
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        _fail("overlay_render_failed")
    return payload


def _build_packet(
    *,
    source_bytes: bytes,
    source_raster: CanonicalRasterArtifact,
    mask: CanonicalMaskArtifact,
    capability: ProviderCapabilitySnapshot,
    authority: Mapping[str, Any],
    creative_session_id: str,
    confirmation_identity: Mapping[str, Any],
) -> IntentPacket:
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    source_ref = blake3(source_bytes).hexdigest()
    source_mime = _mime_for_bytes(source_bytes)
    source_asset_id = _source_asset_id(source_sha256)
    source_capability_id = _label_digest("source-capability")
    mask_capability_id = _label_digest("mask-capability")
    mask_content_ref = blake3(mask.mask_bytes).hexdigest()
    mask_content_sha256 = hashlib.sha256(mask.mask_bytes).hexdigest()

    references = copy.deepcopy(authority["references"])
    operation_inputs: list[JsonObject] = [
        {
            "role": "source_image",
            "original_artifact": {
                "asset_id": source_asset_id,
                "content_ref": source_ref,
                "content_sha256": source_sha256,
            },
            "delivery_mode": "native_input",
            "provider_field": "input_references[0]",
            "provider_role": "source_image",
            "capability_id": source_capability_id,
            "delivered_artifact": {
                "content_ref": source_ref,
                "content_sha256": source_sha256,
                "byte_count": len(source_bytes),
                "width": source_raster.width,
                "height": source_raster.height,
            },
            "derivative": None,
            "prompt_text": None,
        },
        {
            "role": "locality_mask",
            "original_artifact": {
                "mask_sha256": mask.mask_sha256,
                "content_ref": mask_content_ref,
                "content_sha256": mask_content_sha256,
            },
            "delivery_mode": "not_sent",
            "provider_field": None,
            "provider_role": None,
            "capability_id": mask_capability_id,
            "delivered_artifact": None,
            "derivative": None,
            "prompt_text": None,
        },
    ]
    route_policy: JsonObject = {
        "schema_version": "moodboard.provider-route-policy.v1",
        "provider_route_policy_id": _label_digest("openrouter-alibaba-only-route"),
        "permitted_routes": [
            {
                "route_id": ROUTE_ID,
                "provider": "openrouter",
                "model": MODEL,
                "upstream_provider_tag": PROVIDER_TAG,
                "privacy_class": "external_public_demo",
                "retention_class": "provider_terms_apply",
            }
        ],
        "moodboard_fallback_permitted": False,
        "undisclosed_upstream_routing_permitted": True,
    }
    destination: JsonObject = {
        "privacy_class": "external_public_demo",
        "retention_class": "provider_terms_apply",
        "credential_profile_id": CREDENTIAL_PROFILE_ID,
    }
    options: JsonObject = {
        "schema_version": "moodboard.openrouter-images-options.v1",
        "seed": SEED,
        "resolution": RESOLUTION,
        "aspect_ratio": ASPECT_RATIO,
    }
    idempotency: JsonObject = {
        "provider_accepts_key": False,
        "deduplication_scope": None,
        "retention_seconds": None,
        "ambiguous_transport_retransmit_safe": False,
    }
    reconciliation: JsonObject = {
        "supported": False,
        "provider_handle_kind": None,
    }
    policy: JsonObject = {
        "schema_version": _POLICY_VERSION,
        "policy_id": "0" * 64,
        "required_verifiers": [EXACT_LOCALITY_VERIFIER_VERSION],
    }
    policy["policy_id"] = compute_document_identity(
        policy,
        schema_version=_POLICY_VERSION,
        identity_field="policy_id",
    )
    generation_request: JsonObject = {
        "requested_provider": "openrouter",
        "requested_model": MODEL,
        "adapter_revision": ADAPTER_REVISION,
        "capability_snapshot_id": capability.capability_snapshot_id,
        "output_count": OUTPUT_COUNT,
        "options": options,
        "operation_inputs": operation_inputs,
        "provider_route_policy": route_policy,
        "destination": destination,
        "actual_model_policy": "requested_only_permitted",
        "idempotency": idempotency,
        "reconciliation": reconciliation,
    }
    left, top, right, bottom = _mask_bounds(source_raster)
    operation_payload: JsonObject = {
        "source_raster": _raster_document(source_raster),
        "region": {
            "selection_tool_revision": "studio.rectangle.v1",
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        },
        "mask": _mask_document(mask),
        "raw_diagnostic_verifiers": [],
        "insert_compiler_policy": "raw_crop_nearest.v1",
        "compositor_policy": "source_backed_rect_replace.v1",
    }
    operation: JsonObject = {
        "kind": "localized_edit",
        "schema_version": _OPERATION_VERSION,
        "payload_sha256": compute_projection_identity(
            operation_payload,
            domain_tag=_OPERATION_VERSION,
        ),
        "payload": operation_payload,
    }
    confirmation: JsonObject = {
        "mode": "explicit",
        "references_shown": copy.deepcopy(references),
        "reference_use": [
            {
                "reference_occurrence_id": reference["reference_occurrence_id"],
                "provider_use": "prompt_context_only",
            }
            for reference in references
        ],
        "operation_inputs_shown": copy.deepcopy(operation_inputs),
        "dispatch_shown": {
            "requested_provider": "openrouter",
            "requested_model": MODEL,
            "output_count": OUTPUT_COUNT,
            "destination": copy.deepcopy(destination),
            "adapter_revision": ADAPTER_REVISION,
            "capability_snapshot_id": capability.capability_snapshot_id,
            "options": copy.deepcopy(options),
            "provider_route_policy": copy.deepcopy(route_policy),
            "actual_model_policy": "requested_only_permitted",
            "idempotency": copy.deepcopy(idempotency),
            "reconciliation": copy.deepcopy(reconciliation),
            "verification_policy_id": policy["policy_id"],
            "required_verifiers": [EXACT_LOCALITY_VERIFIER_VERSION],
        },
        "compact_summary_id": confirmation_identity["compact_summary_id"],
        "confirmed_at": confirmation_identity["confirmed_at"],
        "studio_session_id": confirmation_identity["studio_session_id"],
        "principal_id": confirmation_identity["principal_id"],
    }
    packet_document: JsonObject = {
        "schema_version": _PACKET_VERSION,
        "intent_packet_id": "0" * 64,
        "creative_session_id": creative_session_id,
        "operation": operation,
        "board": {
            "board_id": authority["board"]["board_id"],
            "representation_id": authority["board"]["representation_id"],
            "fit_policy_id": authority["board"]["fit_policy_id"],
        },
        "source": {
            "asset_id": source_asset_id,
            "content_ref": source_ref,
            "content_sha256": source_sha256,
            "mime": source_mime,
            "width": source_raster.width,
            "height": source_raster.height,
        },
        "instruction": (
            "Replace only the selected apple tree with a mature lemon tree; preserve the water, "
            "ground, camera, lighting, and every pixel outside the selection."
        ),
        "retrieval_route": copy.deepcopy(authority["retrieval_route"]),
        "references": references,
        "generation_request": generation_request,
        "verification_policy": policy,
        "confirmation": confirmation,
    }
    packet_document["intent_packet_id"] = compute_document_identity(
        packet_document,
        schema_version=_PACKET_VERSION,
        identity_field="intent_packet_id",
    )
    try:
        return intent_from_json(packet_document)
    except Exception:
        _fail("intent_packet_invalid")


def _build_run_and_attempt(
    *,
    packet: IntentPacket,
    capability: ProviderCapabilitySnapshot,
    prepared: OpenRouterPreparedRequest,
    timestamp: str,
    uuid4: Callable[[], str | uuid.UUID],
    generation_run_id: str | None = None,
    attempt_id: str | None = None,
) -> tuple[GenerationRun, GenerationAttempt]:
    packet_document = intent_to_json(packet)
    request = packet_document["generation_request"]
    generation_run_id = generation_run_id or _uuid_text(uuid4)
    attempt_id = attempt_id or _uuid_text(uuid4)
    run_document: JsonObject = {
        "schema_version": RUN_VERSION,
        "generation_run_id": generation_run_id,
        "creative_session_id": packet_document["creative_session_id"],
        "intent_packet_id": packet_document["intent_packet_id"],
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "created_at": timestamp,
    }
    normalized = prepared.normalized_request
    attempt_document: JsonObject = {
        "schema_version": ATTEMPT_VERSION,
        "attempt_id": attempt_id,
        "generation_run_id": generation_run_id,
        "intent_packet_id": packet_document["intent_packet_id"],
        "ordinal": 1,
        "retry_of": None,
        "fallback_of": None,
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "provider_route_policy_id": request["provider_route_policy"]["provider_route_policy_id"],
        "selected_route_id": ROUTE_ID,
        "adapter_revision": ADAPTER_REVISION,
        "capability_snapshot_id": capability.capability_snapshot_id,
        "normalized_request_id": normalized.normalized_request_id,
        "normalized_request_ref": build_normalized_request_ref(normalized),
        "request_key_sha256": compute_provider_request_key(
            generation_run_id=generation_run_id,
            attempt_id=attempt_id,
            intent_packet_id=packet_document["intent_packet_id"],
            adapter_revision=ADAPTER_REVISION,
            normalized_request_id=normalized.normalized_request_id,
        ),
        "created_at": timestamp,
    }
    try:
        run = provider_from_json(run_document)
        attempt = provider_from_json(attempt_document)
    except Exception:
        _fail("attempt_artifact_invalid")
    if not isinstance(run, GenerationRun) or not isinstance(attempt, GenerationAttempt):
        _fail("attempt_artifact_invalid")
    return run, attempt


def _tls_context() -> ssl.SSLContext:
    for name in ("SSLKEYLOGFILE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(name):
            _fail("ambient_tls_configuration_forbidden")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_default_certs()
    context.set_alpn_protocols(["http/1.1"])
    return context


def _response_framing(response: http.client.HTTPResponse) -> tuple[int | None, bool]:
    content_lengths = [
        value for name, value in response.getheaders() if name.lower() == "content-length"
    ]
    transfer_encodings = [
        value for name, value in response.getheaders() if name.lower() == "transfer-encoding"
    ]
    content_encodings = [
        value for name, value in response.getheaders() if name.lower() == "content-encoding"
    ]
    if len(content_lengths) > 1 or len(transfer_encodings) > 1 or len(content_encodings) > 1:
        raise RuntimeError("invalid HTTP response framing")
    if content_lengths and transfer_encodings:
        raise RuntimeError("invalid HTTP response framing")
    if content_encodings and content_encodings[0].strip().lower() not in {"", "identity"}:
        raise RuntimeError("encoded HTTP responses are forbidden")
    expected: int | None = None
    if content_lengths:
        raw = content_lengths[0]
        if not raw.isascii() or not raw.isdecimal():
            raise RuntimeError("invalid HTTP response length")
        expected = int(raw)
    chunked = False
    if transfer_encodings:
        chunked = transfer_encodings[0].strip().lower() == "chunked"
        if not chunked:
            raise RuntimeError("unsupported HTTP transfer encoding")
    return expected, chunked


def _read_http_body(response: http.client.HTTPResponse, *, limit: int) -> bytes:
    expected, _ = _response_framing(response)
    if expected is not None and expected > limit:
        raise RuntimeError("HTTP response exceeds the registered byte limit")
    chunks: list[bytes] = []
    measured = 0
    while True:
        chunk = response.read1(min(65_536, limit + 1 - measured))
        if not chunk:
            break
        chunks.append(chunk)
        measured += len(chunk)
        if measured > limit:
            raise RuntimeError("HTTP response exceeds the registered byte limit")
    body = b"".join(chunks)
    if expected is not None and len(body) != expected:
        raise RuntimeError("HTTP response ended before Content-Length")
    return body


def _fixed_https_get(host: str, path: str, *, limit: int, code: str) -> bytes:
    connection = http.client.HTTPSConnection(
        host,
        443,
        timeout=_CONNECT_TIMEOUT_SECONDS,
        context=_tls_context(),
    )
    connection.set_debuglevel(0)
    try:
        with _wall_deadline(_PREPARE_FETCH_TIMEOUT_SECONDS):
            connection.connect()
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/json" if host == DISCOVERY_HOST else "image/jpeg",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                _fail(code)
            return _read_http_body(response, limit=limit)
    except OpenRouterRealE2EError:
        raise
    except Exception:
        _fail(code)
    finally:
        with contextlib.suppress(Exception):
            connection.close()


def fetch_live_discovery() -> bytes:
    return _fixed_https_get(
        DISCOVERY_HOST,
        DISCOVERY_PATH,
        limit=_DISCOVERY_MAX_BYTES,
        code="discovery_unavailable",
    )


def _validate_pinned_source(payload: bytes) -> bytes:
    if (
        type(payload) is not bytes
        or len(payload) != SOURCE_BYTE_COUNT
        or hashlib.sha256(payload).hexdigest() != SOURCE_SHA256
        or blake3(payload).hexdigest() != SOURCE_CONTENT_REF
    ):
        _fail("source_identity_mismatch")
    return payload


def load_pinned_source() -> bytes:
    local = ROOT / ".cache" / "openrouter-real-e2e" / "source" / "fruit_apple_garden.jpg"
    if local.is_file():
        return _validate_pinned_source(
            _secure_read_private_file(
                local,
                limit=_SOURCE_MAX_BYTES,
                code="source_unavailable",
            )
        )
    payload = _fixed_https_get(
        SOURCE_HOST,
        SOURCE_PATH,
        limit=_SOURCE_MAX_BYTES,
        code="source_unavailable",
    )
    return _validate_pinned_source(payload)


class _DeadlineExpired(TimeoutError):
    pass


@contextlib.contextmanager
def _wall_deadline(seconds: float):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("one-shot transport requires the main thread")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise RuntimeError("one-shot transport refuses a pre-existing wall timer")

    def expired(_signum: int, _frame: FrameType | None) -> None:
        raise _DeadlineExpired("OpenRouter transport deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _preflight_direct_transport_environment() -> None:
    """Reject deterministic local transport failures before authorization is consumed."""

    if threading.current_thread() is not threading.main_thread():
        _fail("direct_transport_environment_invalid")
    try:
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            _fail("direct_transport_environment_invalid")
        _tls_context()
    except OpenRouterRealE2EError:
        raise
    except BaseException:
        _fail("direct_transport_environment_invalid")


def _direct_openrouter_https_transport_secret_scope(
    body: bytes,
    bearer_token: str,
) -> tuple[str, OpenRouterHttpResponse | None]:
    """Run the credential-bearing socket work without propagating its exception object."""

    if type(body) is not bytes or not body or not isinstance(bearer_token, str):
        return "invalid", None
    connection: http.client.HTTPSConnection | None = None
    try:
        started = time.monotonic()
        connection = http.client.HTTPSConnection(
            DISCOVERY_HOST,
            443,
            timeout=_CONNECT_TIMEOUT_SECONDS,
            context=_tls_context(),
        )
        connection.set_debuglevel(0)
        with _wall_deadline(_TOTAL_TIMEOUT_SECONDS):
            connection.connect()
            if connection.sock is None:
                raise RuntimeError("OpenRouter TLS connection is unavailable")
            remaining = _TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise _DeadlineExpired("OpenRouter transport deadline exceeded")
            connection.sock.settimeout(remaining)
            connection.putrequest("POST", DISPATCH_PATH, skip_accept_encoding=True)
            connection.putheader("Authorization", f"Bearer {bearer_token}")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Accept", "application/json")
            connection.putheader("Accept-Encoding", "identity")
            connection.putheader("Connection", "close")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            response_body = _read_http_body(response, limit=_HTTP_RESPONSE_MAX_BYTES)
            content_type = response.getheader("Content-Type")
            headers = {"content-type": content_type} if content_type is not None else {}
            elapsed = max(0, math.ceil((time.monotonic() - started) * 1000))
            return (
                "ok",
                OpenRouterHttpResponse(
                    status=response.status,
                    headers=headers,
                    body=response_body,
                    elapsed_milliseconds=elapsed,
                ),
            )
    except _DeadlineExpired:
        return "timeout", None
    except BaseException:
        return "failed", None
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.close()


def direct_openrouter_https_transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
    """One fixed-origin POST with no proxy, redirect, retry, or resettable wall deadline."""

    outcome, response = _direct_openrouter_https_transport_secret_scope(body, bearer_token)
    # A raised public error must not retain credential-bearing arguments in traceback locals.
    body = b""
    bearer_token = ""
    if outcome == "timeout":
        raise TimeoutError("OpenRouter transport deadline exceeded") from None
    if outcome != "ok" or response is None:
        raise RuntimeError("OpenRouter transport failed") from None
    return response


def _ensure_new_output_dir(path: Path) -> None:
    if not path.is_absolute():
        _fail("output_dir_must_be_absolute")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        _fail("output_dir_exists")
    except OSError:
        _fail("output_dir_unavailable")
    try:
        metadata = path.lstat()
    except OSError:
        _fail("output_dir_unavailable")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("output_dir_insecure")


def _directory_identity(path: Path, *, code: str) -> JsonObject:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError:
        _fail(code)
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return {
        "absolute_path": str(path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _secure_read_private_file(path: Path, *, limit: int, code: str) -> bytes:
    """Read one owner-only, single-link regular file without following its final component."""

    descriptor: int | None = None
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 <= before.st_size <= limit
        ):
            _fail(code)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _fail(code)
        chunks: list[bytes] = []
        measured = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - measured))
            if not chunk:
                break
            chunks.append(chunk)
            measured += len(chunk)
            if measured > limit:
                _fail(code)
        return b"".join(chunks)
    except OpenRouterRealE2EError:
        raise
    except Exception:
        _fail(code)
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _strict_json_object(raw: bytes, *, limit: int, code: str) -> JsonObject:
    if type(raw) is not bytes or not 1 <= len(raw) <= limit:
        _fail(code)

    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def bounded_float(token: str) -> float:
        if not 1 <= len(token) <= 64:
            raise ValueError
        measured = float(token)
        if not math.isfinite(measured):
            raise ValueError
        return measured

    def bounded_int(token: str) -> int:
        if not 1 <= len(token) <= 32:
            raise ValueError
        return int(token)

    _preflight_json_structure(raw, code=code)

    def parse() -> Any:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=unique,
            parse_float=bounded_float,
            parse_int=bounded_int,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("JSON constant")),
        )

    ok, value = _call_sanitized(parse)
    if not ok or not isinstance(value, dict):
        _fail(code)
    _bounded_tree(value, code=code)
    return value


def _write_private_bytes(path: Path, payload: bytes) -> None:
    if type(payload) is not bytes:
        _fail("artifact_write_failed")
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if stat.S_IMODE(path.lstat().st_mode) != 0o600:
            _fail("artifact_write_failed")
    except OpenRouterRealE2EError:
        raise
    except Exception:
        _fail("artifact_write_failed")
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except Exception:
        _fail("artifact_serialization_failed")


def _write_private_json(path: Path, document: Mapping[str, Any]) -> None:
    _write_private_bytes(path, _json_bytes(document))


def _compatible_private_artifact(path: Path, payload: bytes) -> bool:
    """Return whether an existing derived artifact is byte-identical and owner-private."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        _fail("finalization_artifact_conflict")
    if metadata.st_nlink > 1:
        # Recover the narrow crash window between atomically linking a staged file at its final
        # no-clobber name and removing our private staging link.
        staged_links: list[Path] = []
        try:
            for candidate in path.parent.iterdir():
                if not candidate.name.startswith(".openrouter-finalize-"):
                    continue
                candidate_metadata = candidate.lstat()
                if (candidate_metadata.st_dev, candidate_metadata.st_ino) == (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    staged_links.append(candidate)
            if metadata.st_nlink != len(staged_links) + 1 or not staged_links:
                _fail("finalization_artifact_conflict")
            for candidate in staged_links:
                # Another exact replay may have claimed the same crash-window cleanup.
                with contextlib.suppress(FileNotFoundError):
                    candidate.unlink()
            directory_descriptor = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OpenRouterRealE2EError:
            raise
        except OSError:
            _fail("finalization_artifact_conflict")
    measured = _secure_read_private_file(
        path,
        limit=max(1, len(payload)),
        code="finalization_artifact_conflict",
    )
    if measured != payload:
        _fail("finalization_artifact_conflict")
    return True


def _write_compatible_private_artifact(path: Path, payload: bytes) -> None:
    """Stage, fsync, and atomically no-clobber-link one derived artifact."""

    if _compatible_private_artifact(path, payload):
        return
    descriptor: int | None = None
    staged_path: Path | None = None
    try:
        descriptor, staged_name = tempfile.mkstemp(
            prefix=".openrouter-finalize-",
            dir=path.parent,
        )
        staged_path = Path(staged_name)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                _fail("finalization_artifact_conflict")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(staged_path, path, follow_symlinks=False)
        except FileExistsError:
            if not _compatible_private_artifact(path, payload):
                _fail("finalization_artifact_conflict")
            return
        try:
            staged_path.unlink()
        except FileNotFoundError:
            # A concurrent exact reader may have completed this crash-window cleanup after the
            # no-clobber link became visible. The final bytes remain the authority.
            if not _compatible_private_artifact(path, payload):
                _fail("finalization_artifact_conflict")
        staged_path = None
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OpenRouterRealE2EError:
        raise
    except BaseException:
        _fail("finalization_artifact_conflict")
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if staged_path is not None:
            with contextlib.suppress(OSError):
                staged_path.unlink()


def _materialize_finalized_evidence(
    target: Path,
    evidence: _FinalizedProviderEvidence,
) -> None:
    """No-clobber the private output first and the sanitized result index last."""

    result_payload = _json_bytes(evidence.document)
    output_path: Path | None = None
    if (evidence.output_bytes is None) != (evidence.output_suffix is None):
        _fail("finalization_artifact_invalid")
    if evidence.output_bytes is not None and evidence.output_suffix is not None:
        output_path = target / f"provider-output-0{evidence.output_suffix}"

    expected_output_name = output_path.name if output_path is not None else None
    try:
        observed_outputs = tuple(
            path for path in target.iterdir() if path.name.startswith("provider-output-0.")
        )
    except OSError:
        _fail("finalization_artifact_conflict")
    if any(path.name != expected_output_name for path in observed_outputs):
        _fail("finalization_artifact_conflict")

    output_exists = False
    if output_path is not None and evidence.output_bytes is not None:
        output_exists = _compatible_private_artifact(output_path, evidence.output_bytes)
    result_path = target / "result.json"
    result_exists = _compatible_private_artifact(result_path, result_payload)

    if output_path is not None and evidence.output_bytes is not None and not output_exists:
        _write_compatible_private_artifact(output_path, evidence.output_bytes)
    if not result_exists:
        _write_compatible_private_artifact(result_path, result_payload)


def _artifact_descriptor(payload: bytes, relative_path: str) -> JsonObject:
    if type(payload) is not bytes or not payload or not relative_path:
        _fail("challenge_artifact_invalid")
    return {
        "relative_path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
    }


def _canonical_uuid(value: object, *, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    try:
        measured = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        _fail(code)
    if measured != value:
        _fail(code)
    return measured


def _verify_document_identity(
    document: Mapping[str, Any],
    *,
    schema_version: str,
    identity_field: str,
    code: str,
) -> None:
    identity = document.get(identity_field)
    if not isinstance(identity, str) or len(identity) != 64:
        _fail(code)
    try:
        expected = compute_document_identity(
            document,
            schema_version=schema_version,
            identity_field=identity_field,
        )
    except Exception:
        _fail(code)
    if identity != expected:
        _fail(code)


def _validate_authority_document(document: Mapping[str, Any]) -> JsonObject:
    expected_keys = {
        "schema_version",
        "authority_id",
        "creative_session_id",
        "board",
        "retrieval_route",
        "references",
    }
    if set(document) != expected_keys or document.get("schema_version") != (
        "moodboard.openrouter-real-e2e-authority.v1"
    ):
        _fail("authority_invalid")
    _verify_document_identity(
        document,
        schema_version="moodboard.openrouter-real-e2e-authority.v1",
        identity_field="authority_id",
        code="authority_invalid",
    )
    _canonical_uuid(document.get("creative_session_id"), code="authority_invalid")
    board = document.get("board")
    route = document.get("retrieval_route")
    references = document.get("references")
    if (
        not isinstance(board, dict)
        or set(board) != {"board_id", "representation_id", "fit_policy_id"}
        or not all(isinstance(board.get(name), str) and len(board[name]) == 64 for name in board)
        or not isinstance(route, dict)
        or set(route)
        != {
            "schema_version",
            "route_policy_id",
            "eligible_corpus_sha256",
            "empty_result_policy",
            "evidence_artifact_id",
        }
        or route.get("schema_version") != "moodboard.intent-route.collection-gate.v1"
        or route.get("empty_result_policy") != "no_ungated_fallback"
        or not all(
            isinstance(route.get(name), str) and len(route[name]) == 64
            for name in (
                "route_policy_id",
                "eligible_corpus_sha256",
                "evidence_artifact_id",
            )
        )
        or not isinstance(references, list)
        or not 1 <= len(references) <= 32
    ):
        _fail("authority_invalid")
    # IntentPacket validation remains the authority for the closed per-reference shape.  Here we
    # only reject values that could make projection or copying unsafe before that validation.
    if any(not isinstance(reference, dict) for reference in references):
        _fail("authority_invalid")
    return copy.deepcopy(dict(document))


def _load_authority_snapshot(
    authority_bundle: bytes | None,
    authority_loader: Callable[[], OpenRouterRealE2EAuthority | _AuthoritySnapshot] | None,
) -> _AuthoritySnapshot:
    if authority_bundle is not None:
        if type(authority_bundle) is not bytes or not 1 <= len(authority_bundle) <= 4 * 1024 * 1024:
            _fail("authority_invalid")
        document = _strict_json_object(
            authority_bundle,
            limit=4 * 1024 * 1024,
            code="authority_invalid",
        )
        return _AuthoritySnapshot(_validate_authority_document(document), authority_bundle)
    if authority_loader is None:
        _fail("authority_unavailable")
    authority_error_code: str | None = None
    try:
        loaded = authority_loader()
        ok = True
    except OpenRouterRealE2EAuthorityError as error:
        authority_error_code = (
            error.code
            if isinstance(error.code, str) and error.code in _SAFE_AUTHORITY_ERROR_CODES
            else "authority_unavailable"
        )
        loaded = None
        ok = False
    except BaseException:
        loaded = None
        ok = False
    if not ok:
        # Raise outside the exception handler so no authority-reader traceback or path is retained.
        _fail(authority_error_code or "authority_unavailable")
    if isinstance(loaded, _AuthoritySnapshot):
        return _AuthoritySnapshot(
            _validate_authority_document(loaded.document),
            loaded.payload,
            loaded.board_artifact_bytes,
            loaded.pixel_rag_artifact_bytes,
        )
    if isinstance(loaded, OpenRouterRealE2EAuthority):
        # The board/Pixel-RAG helper deliberately does not invent an enrolled creative session.
        # A trusted Studio boundary must wrap it in the closed authority bundle above.
        _fail("authority_context_unavailable")
    _fail("authority_invalid")


def _packet_projection(packet: IntentPacket) -> JsonObject:
    document = intent_to_json(packet)
    document.pop("intent_packet_id", None)
    document.pop("confirmation", None)
    return document


def _compact_confirmation_projection(packet_projection: Mapping[str, Any]) -> JsonObject:
    """Project every confirmation-renewal authority shown in the compact summary."""

    try:
        operation = packet_projection["operation"]
        references = packet_projection["references"]
        request = packet_projection["generation_request"]
        policy = packet_projection["verification_policy"]
        if (
            not isinstance(operation, Mapping)
            or not isinstance(references, list)
            or not isinstance(request, Mapping)
            or not isinstance(policy, Mapping)
        ):
            raise TypeError("confirmation projection authorities are not objects")
        dispatch = {
            name: copy.deepcopy(request[name])
            for name in (
                "requested_provider",
                "requested_model",
                "output_count",
                "destination",
                "adapter_revision",
                "capability_snapshot_id",
                "options",
                "provider_route_policy",
                "actual_model_policy",
                "idempotency",
                "reconciliation",
            )
        }
        dispatch["verification_policy_id"] = copy.deepcopy(policy["policy_id"])
        dispatch["required_verifiers"] = copy.deepcopy(policy["required_verifiers"])
        return {
            "operation": {
                "kind": copy.deepcopy(operation["kind"]),
                "schema_version": copy.deepcopy(operation["schema_version"]),
            },
            "reference_count": len(references),
            "operation_inputs": copy.deepcopy(request["operation_inputs"]),
            "dispatch_confirmation": dispatch,
        }
    except Exception:
        _fail("challenge_artifact_drift")


def _build_capability(discovery_body: bytes, *, captured_at: str) -> ProviderCapabilitySnapshot:
    try:
        return build_openrouter_capability_snapshot(
            discovery_body,
            requested_model=MODEL,
            selected_provider_tag=PROVIDER_TAG,
            captured_at=captured_at,
            adapter_revision=ADAPTER_REVISION,
            source_capability_id=_label_digest("source-capability"),
            locality_mask_capability_id=_label_digest("mask-capability"),
            adapter_admission_limits=OpenRouterAdapterAdmissionLimits(
                mime_types=("image/png", "image/jpeg"),
                max_width=8192,
                max_height=8192,
                max_encoded_output_bytes=16_777_216,
            ),
        )
    except Exception:
        _fail("discovery_capability_invalid")


def _prepare_request(
    packet: IntentPacket,
    capability: ProviderCapabilitySnapshot,
    source_bytes: bytes,
) -> OpenRouterPreparedRequest:
    source_ref = blake3(source_bytes).hexdigest()

    def resolve_content(content_ref: str) -> bytes:
        if content_ref != source_ref:
            _fail("unexpected_content_resolution")
        return source_bytes

    try:
        prepared = prepare_openrouter_request(
            packet,
            capability,
            selected_route_id=ROUTE_ID,
            resolve_content=resolve_content,
        )
    except Exception:
        _fail("request_preparation_failed")
    _assert_wire(prepared)
    return prepared


def _dummy_confirmation(prepared_at: str) -> JsonObject:
    """A never-persisted placeholder used only to derive confirmation-neutral request bytes."""

    return {
        "compact_summary_id": "0" * 64,
        "confirmed_at": prepared_at,
        "studio_session_id": "00000000-0000-4000-8000-000000000001",
        "principal_id": "00000000-0000-4000-8000-000000000002",
    }


def prepare_openrouter_real_e2e(
    challenge_dir: Path,
    *,
    _discovery_fetcher: Callable[[], bytes] = fetch_live_discovery,
    _source_fetcher: Callable[[], bytes] = load_pinned_source,
    _authority_bundle: bytes | None = None,
    _authority_loader: Callable[[], OpenRouterRealE2EAuthority | _AuthoritySnapshot] | None = None,
    _clock: Callable[[], str] = _canonical_timestamp,
    _uuid4: Callable[[], str | uuid.UUID] = uuid.uuid4,
) -> OpenRouterRealE2EChallenge:
    """Create one credential-free, content-bound confirmation challenge.

    This function cannot authorize or dispatch.  It freezes the exact preview and proposal that a
    trusted Studio boundary must show and confirm in a separate closed context document.
    """

    target = Path(challenge_dir)
    if target.exists() or target.is_symlink():
        _fail("output_dir_exists")
    if not target.is_absolute():
        _fail("output_dir_must_be_absolute")
    if (_authority_bundle is None) == (_authority_loader is None):
        _fail("authority_unavailable" if _authority_bundle is None else "authority_ambiguous")
    if not all(callable(value) for value in (_discovery_fetcher, _source_fetcher, _clock, _uuid4)):
        _fail("evaluation_callable_invalid")
    if _authority_loader is not None and not callable(_authority_loader):
        _fail("evaluation_callable_invalid")

    ok, discovery_body = _call_sanitized(_discovery_fetcher)
    if not ok or type(discovery_body) is not bytes:
        _fail("discovery_unavailable")
    quote = parse_openrouter_quote(
        discovery_body,
        input_count=1,
        output_count=OUTPUT_COUNT,
        resolution=RESOLUTION,
    )
    with localcontext(_QUOTE_CONTEXT):
        if quote > QUOTE_ADMISSION_LIMIT_USD:
            _fail("quote_exceeds_cap")

    ok, prepared_at_value = _call_sanitized(_clock)
    if not ok or not isinstance(prepared_at_value, str):
        _fail("clock_invalid")
    prepared_at = prepared_at_value
    _timestamp_value(prepared_at, code="clock_invalid")
    expires_at = _timestamp_after(prepared_at, seconds=_CHALLENGE_TTL_SECONDS)
    capability = _build_capability(discovery_body, captured_at=prepared_at)

    ok, source_value = _call_sanitized(_source_fetcher)
    if not ok or type(source_value) is not bytes:
        _fail("source_unavailable")
    source_bytes = source_value
    if not 1 <= len(source_bytes) <= _SOURCE_MAX_BYTES:
        _fail("source_invalid")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        source_raster = compile_canonical_raster(
            source_bytes,
            source_content_sha256=source_sha256,
        )
        bounds = _mask_bounds(source_raster)
        mask = compile_rectangle_mask(
            source_raster,
            left=bounds[0],
            top=bounds[1],
            right=bounds[2],
            bottom=bounds[3],
        )
    except Exception:
        _fail("source_media_invalid")
    overlay_bytes = _render_mask_overlay(source_raster, bounds)
    authority = _load_authority_snapshot(_authority_bundle, _authority_loader)
    creative_session_id = _canonical_uuid(
        authority.document["creative_session_id"], code="authority_invalid"
    )
    provisional_packet = _build_packet(
        source_bytes=source_bytes,
        source_raster=source_raster,
        mask=mask,
        capability=capability,
        authority=authority.document,
        creative_session_id=creative_session_id,
        confirmation_identity=_dummy_confirmation(prepared_at),
    )
    prepared = _prepare_request(provisional_packet, capability, source_bytes)
    packet_projection = _packet_projection(provisional_packet)
    generation_run_id = _uuid_text(_uuid4)
    attempt_id = _uuid_text(_uuid4)

    _ensure_new_output_dir(target)
    directory_binding = _directory_identity(target, code="challenge_binding_mismatch")
    source_suffix = ".jpg" if _mime_for_bytes(source_bytes) == "image/jpeg" else ".png"
    artifact_payloads: dict[str, tuple[str, bytes]] = {
        "discovery": ("discovery.json", discovery_body),
        "source": (f"source{source_suffix}", source_bytes),
        "authority": ("authority.json", authority.payload),
        "mask": ("mask.u8", mask.mask_bytes),
        "overlay": ("overlay.png", overlay_bytes),
    }
    artifacts: JsonObject = {
        name: _artifact_descriptor(payload, relative_path)
        for name, (relative_path, payload) in artifact_payloads.items()
    }
    confirmation_projection = _compact_confirmation_projection(packet_projection)
    compact_summary: JsonObject = {
        "schema_version": _COMPACT_SUMMARY_VERSION,
        "compact_summary_id": "0" * 64,
        "instruction": packet_projection["instruction"],
        "quoted_cost_usd": str(quote),
        "quote_admission_limit_usd": str(QUOTE_ADMISSION_LIMIT_USD),
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "requested_model_notice": "requested model only; actual model is not attested",
        "upstream_route_notice": "request is pinned; serving upstream remains unknown",
        "compositor_notice": "not authorized and not run",
        "semantic_aesthetic_notice": (
            "not run; provider lifecycle success is not aesthetic acceptance"
        ),
        "source_preview": {
            "content_sha256": source_sha256,
            "width": source_raster.width,
            "height": source_raster.height,
            "mime": _mime_for_bytes(source_bytes),
        },
        "mask_overlay": {
            "mask_sha256": mask.mask_sha256,
            "bounds": {
                "left": bounds[0],
                "top": bounds[1],
                "right": bounds[2],
                "bottom": bounds[3],
            },
            "editable_count": mask.editable_count,
            "protected_count": mask.protected_count,
        },
        "board": copy.deepcopy(authority.document["board"]),
        "retrieval_route": copy.deepcopy(authority.document["retrieval_route"]),
        "references": copy.deepcopy(authority.document["references"]),
        **confirmation_projection,
        "dispatch": {
            "provider": "openrouter",
            "model": MODEL,
            "provider_only": [PROVIDER_TAG],
            "allow_fallbacks": False,
            "n": OUTPUT_COUNT,
            "resolution": RESOLUTION,
            "aspect_ratio": ASPECT_RATIO,
            "wire_body_sha256": prepared.wire_body_sha256,
            "wire_body_byte_count": prepared.wire_body_byte_count,
        },
        "artifacts": copy.deepcopy(artifacts),
    }
    compact_summary["compact_summary_id"] = compute_document_identity(
        compact_summary,
        schema_version=_COMPACT_SUMMARY_VERSION,
        identity_field="compact_summary_id",
    )
    summary_bytes = _json_bytes(compact_summary)
    artifacts["compact_summary"] = _artifact_descriptor(summary_bytes, "compact-summary.json")
    challenge: JsonObject = {
        "schema_version": _CHALLENGE_VERSION,
        "challenge_id": "0" * 64,
        "compact_summary_id": compact_summary["compact_summary_id"],
        "prepared_at": prepared_at,
        "expires_at": expires_at,
        "directory_binding": directory_binding,
        "artifacts": copy.deepcopy(artifacts),
        "quoted_cost_usd": str(quote),
        "quote_admission_limit_usd": str(QUOTE_ADMISSION_LIMIT_USD),
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "creative_session_id": creative_session_id,
        "generation_run_id": generation_run_id,
        "attempt_id": attempt_id,
        "capability_snapshot_id": capability.capability_snapshot_id,
        "wire_body_sha256": prepared.wire_body_sha256,
        "wire_body_byte_count": prepared.wire_body_byte_count,
        "packet_projection": packet_projection,
    }
    challenge["challenge_id"] = compute_document_identity(
        challenge,
        schema_version=_CHALLENGE_VERSION,
        identity_field="challenge_id",
    )
    for _name, (relative_path, payload) in artifact_payloads.items():
        _write_private_bytes(target / relative_path, payload)
    _write_private_bytes(target / "compact-summary.json", summary_bytes)
    _write_private_json(target / "challenge.json", challenge)
    return OpenRouterRealE2EChallenge(
        challenge_id=str(challenge["challenge_id"]),
        compact_summary_id=str(compact_summary["compact_summary_id"]),
        prepared_at=prepared_at,
        expires_at=expires_at,
        quoted_cost_usd=quote,
        quote_admission_limit_usd=QUOTE_ADMISSION_LIMIT_USD,
        wire_body_sha256=prepared.wire_body_sha256,
        wire_body_byte_count=prepared.wire_body_byte_count,
        directory=target,
    )


def _challenge_snapshot(
    target: Path,
    *,
    require_unconsumed: bool = True,
) -> tuple[JsonObject, dict[str, bytes], JsonObject]:
    consumed = target / "consumed.json"
    if require_unconsumed and (consumed.exists() or consumed.is_symlink()):
        _fail("challenge_consumed")
    try:
        current_binding = _directory_identity(target, code="challenge_binding_mismatch")
    except OpenRouterRealE2EError:
        raise
    challenge_bytes = _secure_read_private_file(
        target / "challenge.json",
        limit=4 * 1024 * 1024,
        code="challenge_artifact_drift",
    )
    challenge = _strict_json_object(
        challenge_bytes,
        limit=4 * 1024 * 1024,
        code="challenge_artifact_drift",
    )
    expected_keys = {
        "schema_version",
        "challenge_id",
        "compact_summary_id",
        "prepared_at",
        "expires_at",
        "directory_binding",
        "artifacts",
        "quoted_cost_usd",
        "quote_admission_limit_usd",
        "spend_limit_kind",
        "creative_session_id",
        "generation_run_id",
        "attempt_id",
        "capability_snapshot_id",
        "wire_body_sha256",
        "wire_body_byte_count",
        "packet_projection",
    }
    if (
        set(challenge) != expected_keys
        or challenge.get("schema_version") != _CHALLENGE_VERSION
        or challenge.get("directory_binding") != current_binding
    ):
        _fail("challenge_binding_mismatch")
    _verify_document_identity(
        challenge,
        schema_version=_CHALLENGE_VERSION,
        identity_field="challenge_id",
        code="challenge_artifact_drift",
    )
    _canonical_uuid(challenge.get("creative_session_id"), code="challenge_artifact_drift")
    _canonical_uuid(challenge.get("generation_run_id"), code="challenge_artifact_drift")
    _canonical_uuid(challenge.get("attempt_id"), code="challenge_artifact_drift")
    artifacts = challenge.get("artifacts")
    required_artifacts = {
        "discovery",
        "source",
        "authority",
        "mask",
        "overlay",
        "compact_summary",
    }
    if not isinstance(artifacts, dict) or set(artifacts) != required_artifacts:
        _fail("challenge_artifact_drift")
    limits = {
        "discovery": _DISCOVERY_MAX_BYTES,
        "source": _SOURCE_MAX_BYTES,
        "authority": 4 * 1024 * 1024,
        "mask": 64 * 1024 * 1024,
        "overlay": 64 * 1024 * 1024,
        "compact_summary": 4 * 1024 * 1024,
    }
    payloads: dict[str, bytes] = {}
    for name in sorted(required_artifacts):
        descriptor = artifacts[name]
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "relative_path",
            "sha256",
            "byte_count",
        }:
            _fail("challenge_artifact_drift")
        relative_path = descriptor.get("relative_path")
        if not isinstance(relative_path, str):
            _fail("challenge_artifact_drift")
        relative = Path(relative_path)
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name != relative_path:
            _fail("challenge_artifact_drift")
        payload = _secure_read_private_file(
            target / relative,
            limit=limits[name],
            code="challenge_artifact_drift",
        )
        if (
            descriptor.get("byte_count") != len(payload)
            or descriptor.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            _fail("challenge_artifact_drift")
        payloads[name] = payload
    summary = _strict_json_object(
        payloads["compact_summary"],
        limit=4 * 1024 * 1024,
        code="challenge_artifact_drift",
    )
    expected_summary_keys = {
        "schema_version",
        "compact_summary_id",
        "instruction",
        "quoted_cost_usd",
        "quote_admission_limit_usd",
        "spend_limit_kind",
        "requested_model_notice",
        "upstream_route_notice",
        "compositor_notice",
        "semantic_aesthetic_notice",
        "source_preview",
        "mask_overlay",
        "board",
        "retrieval_route",
        "references",
        "operation",
        "reference_count",
        "operation_inputs",
        "dispatch_confirmation",
        "dispatch",
        "artifacts",
    }
    packet_projection = challenge.get("packet_projection")
    if not isinstance(packet_projection, Mapping):
        _fail("challenge_artifact_drift")
    expected_confirmation = _compact_confirmation_projection(packet_projection)
    if (
        set(summary) != expected_summary_keys
        or summary.get("schema_version") != _COMPACT_SUMMARY_VERSION
        or summary.get("compact_summary_id") != challenge.get("compact_summary_id")
        or summary.get("artifacts")
        != {name: artifacts[name] for name in required_artifacts if name != "compact_summary"}
        or any(summary.get(name) != value for name, value in expected_confirmation.items())
    ):
        _fail("challenge_artifact_drift")
    _verify_document_identity(
        summary,
        schema_version=_COMPACT_SUMMARY_VERSION,
        identity_field="compact_summary_id",
        code="challenge_artifact_drift",
    )
    return challenge, payloads, summary


def _confirmation_snapshot(
    path: Path,
    *,
    challenge: Mapping[str, Any],
    now: str,
) -> tuple[JsonObject, bytes]:
    try:
        path.lstat()
    except FileNotFoundError:
        _fail("confirmation_context_unavailable")
    except OSError:
        _fail("confirmation_context_invalid")
    payload = _secure_read_private_file(
        path,
        limit=64 * 1024,
        code="confirmation_context_invalid",
    )
    context = _strict_json_object(
        payload,
        limit=64 * 1024,
        code="confirmation_context_invalid",
    )
    expected_keys = {
        "schema_version",
        "confirmation_context_id",
        "challenge_id",
        "compact_summary_id",
        "decision",
        "authorized_generation_post_count",
        "principal_id",
        "studio_session_id",
        "creative_session_id",
        "confirmed_at",
    }
    if (
        set(context) != expected_keys
        or context.get("schema_version") != _CONFIRMATION_CONTEXT_VERSION
        or context.get("challenge_id") != challenge.get("challenge_id")
        or context.get("compact_summary_id") != challenge.get("compact_summary_id")
        or context.get("decision") != "approve_one_paid_call"
        or type(context.get("authorized_generation_post_count")) is not int
        or context.get("authorized_generation_post_count") != 1
        or context.get("creative_session_id") != challenge.get("creative_session_id")
    ):
        _fail("confirmation_context_invalid")
    _verify_document_identity(
        context,
        schema_version=_CONFIRMATION_CONTEXT_VERSION,
        identity_field="confirmation_context_id",
        code="confirmation_context_invalid",
    )
    for name in ("principal_id", "studio_session_id", "creative_session_id"):
        _canonical_uuid(context.get(name), code="confirmation_context_invalid")
    prepared_at = _timestamp_value(
        challenge.get("prepared_at"), code="confirmation_context_invalid"
    )
    expires_at = _timestamp_value(challenge.get("expires_at"), code="confirmation_context_invalid")
    confirmed_at = _timestamp_value(
        context.get("confirmed_at"), code="confirmation_context_invalid"
    )
    measured_now = _timestamp_value(now, code="clock_invalid")
    if confirmed_at < prepared_at or confirmed_at > expires_at or confirmed_at > measured_now:
        _fail("confirmation_context_invalid")
    if measured_now > expires_at:
        _fail("challenge_expired")
    return context, payload


def _historical_confirmation_snapshot(
    target: Path,
    *,
    challenge: Mapping[str, Any],
) -> JsonObject:
    """Validate the frozen confirmation identity without applying a new expiry gate."""

    payload = _secure_read_private_file(
        target / "confirmation-context.snapshot.json",
        limit=64 * 1024,
        code="finalization_artifact_invalid",
    )
    context = _strict_json_object(
        payload,
        limit=64 * 1024,
        code="finalization_artifact_invalid",
    )
    expected_keys = {
        "schema_version",
        "confirmation_context_id",
        "challenge_id",
        "compact_summary_id",
        "decision",
        "authorized_generation_post_count",
        "principal_id",
        "studio_session_id",
        "creative_session_id",
        "confirmed_at",
    }
    if (
        set(context) != expected_keys
        or context.get("schema_version") != _CONFIRMATION_CONTEXT_VERSION
        or context.get("challenge_id") != challenge.get("challenge_id")
        or context.get("compact_summary_id") != challenge.get("compact_summary_id")
        or context.get("decision") != "approve_one_paid_call"
        or type(context.get("authorized_generation_post_count")) is not int
        or context.get("authorized_generation_post_count") != 1
        or context.get("creative_session_id") != challenge.get("creative_session_id")
    ):
        _fail("finalization_artifact_invalid")
    _verify_document_identity(
        context,
        schema_version=_CONFIRMATION_CONTEXT_VERSION,
        identity_field="confirmation_context_id",
        code="finalization_artifact_invalid",
    )
    for name in ("principal_id", "studio_session_id", "creative_session_id"):
        _canonical_uuid(context.get(name), code="finalization_artifact_invalid")
    prepared_at = _timestamp_value(
        challenge.get("prepared_at"), code="finalization_artifact_invalid"
    )
    expires_at = _timestamp_value(challenge.get("expires_at"), code="finalization_artifact_invalid")
    confirmed_at = _timestamp_value(
        context.get("confirmed_at"), code="finalization_artifact_invalid"
    )
    if confirmed_at < prepared_at or confirmed_at > expires_at:
        _fail("finalization_artifact_invalid")
    return context


def _consumption_snapshot(
    target: Path,
    *,
    challenge: Mapping[str, Any],
    context: Mapping[str, Any],
) -> JsonObject:
    payload = _secure_read_private_file(
        target / "consumed.json",
        limit=64 * 1024,
        code="finalization_artifact_invalid",
    )
    consumed = _strict_json_object(
        payload,
        limit=64 * 1024,
        code="finalization_artifact_invalid",
    )
    if (
        set(consumed)
        != {
            "schema_version",
            "challenge_id",
            "confirmation_context_id",
            "consumed_at",
        }
        or consumed.get("schema_version") != "moodboard.openrouter-real-e2e-consumption.v1"
        or consumed.get("challenge_id") != challenge.get("challenge_id")
        or consumed.get("confirmation_context_id") != context.get("confirmation_context_id")
    ):
        _fail("finalization_artifact_invalid")
    confirmed_at = _timestamp_value(
        context.get("confirmed_at"), code="finalization_artifact_invalid"
    )
    consumed_at = _timestamp_value(
        consumed.get("consumed_at"), code="finalization_artifact_invalid"
    )
    expires_at = _timestamp_value(challenge.get("expires_at"), code="finalization_artifact_invalid")
    if consumed_at < confirmed_at or consumed_at > expires_at:
        _fail("finalization_artifact_invalid")
    return consumed


def _rebuild_finalization_inputs(
    target: Path,
    *,
    challenge: JsonObject,
    payloads: Mapping[str, bytes],
) -> _FinalizationInputs:
    """Rebuild every plan authority solely from the frozen paid-call artifacts."""

    context = _historical_confirmation_snapshot(target, challenge=challenge)
    _consumption_snapshot(target, challenge=challenge, context=context)
    plan_payload = _secure_read_private_file(
        target / "plan.json",
        limit=16 * 1024 * 1024,
        code="finalization_artifact_invalid",
    )
    plan = _strict_json_object(
        plan_payload,
        limit=16 * 1024 * 1024,
        code="finalization_artifact_invalid",
    )
    plan_keys = {
        "schema_version",
        "challenge_id",
        "confirmation_context_id",
        "quoted_cost_usd",
        "quote_admission_limit_usd",
        "spend_limit_kind",
        "intent_packet",
        "capability",
        "normalized_request",
        "generation_run",
        "generation_attempt",
    }
    if (
        set(plan) != plan_keys
        or plan.get("schema_version") != "moodboard.openrouter-real-e2e-plan.v2"
        or plan.get("challenge_id") != challenge.get("challenge_id")
        or plan.get("confirmation_context_id") != context.get("confirmation_context_id")
    ):
        _fail("finalization_artifact_invalid")

    discovery_body = payloads["discovery"]
    quote = parse_openrouter_quote(
        discovery_body,
        input_count=1,
        output_count=OUTPUT_COUNT,
        resolution=RESOLUTION,
    )
    if (
        str(quote) != challenge.get("quoted_cost_usd")
        or plan.get("quoted_cost_usd") != str(quote)
        or plan.get("quote_admission_limit_usd") != str(QUOTE_ADMISSION_LIMIT_USD)
        or plan.get("spend_limit_kind") != "quote_only_not_provider_enforced"
    ):
        _fail("finalization_artifact_invalid")
    with localcontext(_QUOTE_CONTEXT):
        if quote > QUOTE_ADMISSION_LIMIT_USD:
            _fail("finalization_artifact_invalid")

    prepared_at = challenge.get("prepared_at")
    if not isinstance(prepared_at, str):
        _fail("finalization_artifact_invalid")
    capability = _build_capability(discovery_body, captured_at=prepared_at)
    if capability.capability_snapshot_id != challenge.get("capability_snapshot_id"):
        _fail("finalization_artifact_invalid")

    source_bytes = payloads["source"]
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        source_raster = compile_canonical_raster(
            source_bytes,
            source_content_sha256=source_sha256,
        )
        bounds = _mask_bounds(source_raster)
        mask = compile_rectangle_mask(
            source_raster,
            left=bounds[0],
            top=bounds[1],
            right=bounds[2],
            bottom=bounds[3],
        )
    except Exception:
        _fail("finalization_artifact_invalid")
    if (
        mask.mask_bytes != payloads["mask"]
        or _render_mask_overlay(source_raster, bounds) != payloads["overlay"]
    ):
        _fail("finalization_artifact_invalid")
    authority = _validate_authority_document(
        _strict_json_object(
            payloads["authority"],
            limit=4 * 1024 * 1024,
            code="finalization_artifact_invalid",
        )
    )
    confirmation_identity = {
        "compact_summary_id": context["compact_summary_id"],
        "confirmed_at": context["confirmed_at"],
        "studio_session_id": context["studio_session_id"],
        "principal_id": context["principal_id"],
    }
    packet = _build_packet(
        source_bytes=source_bytes,
        source_raster=source_raster,
        mask=mask,
        capability=capability,
        authority=authority,
        creative_session_id=str(context["creative_session_id"]),
        confirmation_identity=confirmation_identity,
    )
    if _packet_projection(packet) != challenge.get("packet_projection"):
        _fail("finalization_artifact_invalid")
    prepared = _prepare_request(packet, capability, source_bytes)
    if prepared.wire_body_sha256 != challenge.get(
        "wire_body_sha256"
    ) or prepared.wire_body_byte_count != challenge.get("wire_body_byte_count"):
        _fail("finalization_artifact_invalid")
    run, attempt = _build_run_and_attempt(
        packet=packet,
        capability=capability,
        prepared=prepared,
        timestamp=prepared_at,
        uuid4=uuid.uuid4,
        generation_run_id=str(challenge.get("generation_run_id")),
        attempt_id=str(challenge.get("attempt_id")),
    )
    expected_plan: JsonObject = {
        "schema_version": "moodboard.openrouter-real-e2e-plan.v2",
        "challenge_id": challenge["challenge_id"],
        "confirmation_context_id": context["confirmation_context_id"],
        "quoted_cost_usd": str(quote),
        "quote_admission_limit_usd": str(QUOTE_ADMISSION_LIMIT_USD),
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "intent_packet": intent_to_json(packet),
        "capability": provider_to_json(capability),
        "normalized_request": provider_to_json(prepared.normalized_request),
        "generation_run": provider_to_json(run),
        "generation_attempt": provider_to_json(attempt),
    }
    if plan != expected_plan:
        _fail("finalization_artifact_invalid")
    return _FinalizationInputs(
        challenge=challenge,
        packet=packet,
        capability=capability,
        prepared=prepared,
        run=run,
        attempt=attempt,
        source_bytes=source_bytes,
        source_raster=source_raster,
        mask=mask,
        quote=quote,
        discovery_body=discovery_body,
    )


def _receipt_cost_telemetry(receipt_value: object | None) -> tuple[Decimal | None, str]:
    """Read honest post-hoc USD telemetry from one immutable provider receipt."""

    if receipt_value is None:
        return None, "not_reported"
    try:
        receipt = provider_to_json(receipt_value)  # type: ignore[arg-type]
        cost = receipt.get("cost")
        if (
            not isinstance(cost, dict)
            or cost.get("state") != "reported"
            or not isinstance(cost.get("amount"), str)
        ):
            return None, "not_reported"
        if cost.get("currency") != "USD":
            return None, "reported_non_usd"
        measured = Decimal(cost["amount"])
        if not measured.is_finite() or measured < 0:
            return None, "not_reported"
        return measured, "reported"
    except Exception:
        return None, "not_reported"


def _reported_cost_telemetry(dispatch: OpenRouterDispatchResult) -> tuple[Decimal | None, str]:
    """Return honest post-hoc USD telemetry without turning it into an admission gate."""

    receipt = dispatch.decoded.receipt if dispatch.decoded is not None else None
    return _receipt_cost_telemetry(receipt)


def _finalized_provider_evidence(
    inputs: _FinalizationInputs,
    journal: AttemptJournal,
    *,
    succeeded_at: str | None,
) -> _FinalizedProviderEvidence:
    """Derive terminal/local judgments from the journal's immutable response package."""

    attempt_id = inputs.attempt.attempt_id
    state = journal.read_state(attempt_id)
    if state.state not in {"response_received", "succeeded"}:
        _fail("finalization_not_ready")
    stored_response = journal.read_provider_response(attempt_id)
    reported_cost, cost_telemetry_status = _receipt_cost_telemetry(stored_response.receipt)

    admission_error: ProviderMediaAdmissionError | None = None
    if state.state == "response_received":
        if succeeded_at is None:
            # A completed invalid-media report must replay without inventing a new timestamp.
            # Run the same pure candidate derivation used by the journal; valid media still needs
            # the journal CAS path and therefore reports not-ready to this read-only branch.
            try:
                build_provider_success_candidates(
                    intent_packet=inputs.packet,
                    generation_run=inputs.run,
                    attempt=inputs.attempt,
                    capability=inputs.capability,
                    normalized_request=inputs.prepared.normalized_request,
                    receipt=stored_response.receipt,
                    prior_events=journal.read_events(attempt_id),
                    output_bytes=stored_response.output_bytes,
                    succeeded_at=stored_response.event.recorded_at,
                )
            except ProviderMediaAdmissionError as error:
                admission_error = error
            else:
                _fail("finalization_not_ready")
        else:
            if state.head_event_id is None:
                _fail("finalization_artifact_invalid")
            try:
                journal.publish_provider_success(
                    attempt_id,
                    inputs.packet,
                    inputs.prepared.normalized_request,
                    succeeded_at=succeeded_at,
                    expected_head_event_id=state.head_event_id,
                    expected_next_sequence=state.next_sequence,
                )
            except ProviderMediaAdmissionError as error:
                admission_error = error
            except BaseException:
                # A commit may be durable even when its acknowledgement is lost. Re-read the
                # journal in this same call; only a complete success package authorizes recovery.
                recovered = journal.read_state(attempt_id)
                if recovered.state == "succeeded":
                    pass
                elif recovered.state != "response_received":
                    _fail("finalization_not_ready")
                else:
                    raise

    if admission_error is not None:
        structural_document: JsonObject | None = None
        locality_document: JsonObject | None = None
        structural_state = "not_run"
        structural_reason: str | None = None
        locality_state = "not_run"
        if len(stored_response.output_bytes) == 1:
            try:
                structural = verify_output_structure(
                    inputs.source_raster,
                    provider_receipt=stored_response.receipt,
                    output_index=0,
                    output_bytes=stored_response.output_bytes[0],
                    output_occurrence=None,
                )
                structural_document = judgment_to_json(structural.judgment)
                structural_state = str(structural_document["result"]["state"])
                reason = structural_document["result"].get("reason")
                structural_reason = reason if isinstance(reason, str) else None
                locality = build_locality_not_run(structural.judgment, inputs.mask)
                locality_document = judgment_to_json(locality)
                locality_state = str(locality_document["result"]["state"])
            except Exception:
                structural_document = None
                locality_document = None
                structural_state = "not_run"
                structural_reason = None
                locality_state = "not_run"
        journal.verify_integrity()
        result = OpenRouterRealE2EResult(
            generation_run_id=inputs.run.generation_run_id,
            attempt_id=attempt_id,
            provider_receipt_id=stored_response.receipt.provider_receipt_id,
            output_occurrence_id=None,
            quoted_cost_usd=inputs.quote,
            reported_cost_usd=reported_cost,
            cost_telemetry_status=cost_telemetry_status,
            states=tuple(event.state for event in journal.read_events(attempt_id)),
            generation_post_count=1,
            provider_media_admission_result=admission_error.code,
            raw_structural_result=structural_state,
            raw_structural_reason=structural_reason,
            raw_locality_result=locality_state,
        )
        return _FinalizedProviderEvidence(
            result=result,
            document=_result_document(
                result,
                source_sha256=hashlib.sha256(inputs.source_bytes).hexdigest(),
                source_content_ref=blake3(inputs.source_bytes).hexdigest(),
                mask_sha256=inputs.mask.mask_sha256,
                discovery_sha256=hashlib.sha256(inputs.discovery_body).hexdigest(),
                wire_sha256=inputs.prepared.wire_body_sha256,
                wire_byte_count=inputs.prepared.wire_body_byte_count,
                structural=structural_document,
                locality=locality_document,
            ),
        )

    success = journal.read_provider_success(attempt_id)
    journal.verify_integrity()
    if len(success.occurrences) != 1 or not isinstance(success.occurrences[0], OutputOccurrence):
        _fail("finalization_artifact_invalid")
    occurrence = success.occurrences[0]
    if len(stored_response.output_bytes) != 1:
        _fail("finalization_artifact_invalid")
    output_bytes = stored_response.output_bytes[0]
    try:
        structural = verify_output_structure(
            inputs.source_raster,
            provider_receipt=stored_response.receipt,
            output_index=0,
            output_bytes=output_bytes,
            output_occurrence=occurrence,
        )
        structural_document = judgment_to_json(structural.judgment)
        structural_state = str(structural_document["result"]["state"])
        structural_reason: str | None = None
        if structural_state == "pass":
            if structural.output_raster is None:
                _fail("finalization_artifact_invalid")
            locality_judgment = verify_outside_mask_rgb_exact(
                inputs.source_raster,
                structural.output_raster,
                inputs.mask,
                output_occurrence=occurrence,
                structural_pass=structural.judgment,
            )
        else:
            locality_judgment = build_locality_not_run(structural.judgment, inputs.mask)
            reason = structural_document["result"].get("reason")
            structural_reason = reason if isinstance(reason, str) else None
        locality_document = judgment_to_json(locality_judgment)
        locality_state = str(locality_document["result"]["state"])
    except OpenRouterRealE2EError:
        raise
    except Exception:
        _fail("finalization_artifact_invalid")
    result = OpenRouterRealE2EResult(
        generation_run_id=inputs.run.generation_run_id,
        attempt_id=attempt_id,
        provider_receipt_id=stored_response.receipt.provider_receipt_id,
        output_occurrence_id=occurrence.output_occurrence_id,
        quoted_cost_usd=inputs.quote,
        reported_cost_usd=reported_cost,
        cost_telemetry_status=cost_telemetry_status,
        states=tuple(event.state for event in journal.read_events(attempt_id)),
        generation_post_count=1,
        provider_media_admission_result="pass",
        raw_structural_result=structural_state,
        raw_structural_reason=structural_reason,
        raw_locality_result=locality_state,
    )
    output_mime = occurrence.original.get("mime")
    if output_mime not in {"image/png", "image/jpeg"}:
        _fail("finalization_artifact_invalid")
    output_suffix = ".png" if output_mime == "image/png" else ".jpg"
    return _FinalizedProviderEvidence(
        result=result,
        document=_result_document(
            result,
            source_sha256=hashlib.sha256(inputs.source_bytes).hexdigest(),
            source_content_ref=blake3(inputs.source_bytes).hexdigest(),
            mask_sha256=inputs.mask.mask_sha256,
            discovery_sha256=hashlib.sha256(inputs.discovery_body).hexdigest(),
            wire_sha256=inputs.prepared.wire_body_sha256,
            wire_byte_count=inputs.prepared.wire_body_byte_count,
            structural=structural_document,
            locality=locality_document,
        ),
        output_bytes=output_bytes,
        output_suffix=output_suffix,
    )


def _claim_challenge_consumption(
    target: Path,
    *,
    challenge: Mapping[str, Any],
    context: Mapping[str, Any],
    consumed_at: str,
) -> None:
    if _directory_identity(target, code="challenge_binding_mismatch") != challenge.get(
        "directory_binding"
    ):
        _fail("challenge_binding_mismatch")
    payload = _json_bytes(
        {
            "schema_version": "moodboard.openrouter-real-e2e-consumption.v1",
            "challenge_id": challenge["challenge_id"],
            "confirmation_context_id": context["confirmation_context_id"],
            "consumed_at": consumed_at,
        }
    )
    descriptor: int | None = None
    directory_descriptor: int | None = None
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        directory_descriptor = os.open(target, directory_flags)
        opened_directory = os.fstat(directory_descriptor)
        binding = challenge["directory_binding"]
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or opened_directory.st_uid != os.getuid()
            or stat.S_IMODE(opened_directory.st_mode) != 0o700
            or opened_directory.st_dev != binding["device"]
            or opened_directory.st_ino != binding["inode"]
        ):
            _fail("challenge_binding_mismatch")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open("consumed.json", flags, 0o600, dir_fd=directory_descriptor)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                _fail("challenge_consumption_failed")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.fsync(directory_descriptor)
    except FileExistsError:
        _fail("challenge_consumed")
    except OpenRouterRealE2EError:
        raise
    except BaseException:
        _fail("challenge_consumption_failed")
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if directory_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(directory_descriptor)


def _execute_with_token(
    *,
    target: Path,
    token: str,
    packet: IntentPacket,
    capability: ProviderCapabilitySnapshot,
    prepared: OpenRouterPreparedRequest,
    run: GenerationRun,
    attempt: GenerationAttempt,
    source_bytes: bytes,
    source_raster: CanonicalRasterArtifact,
    mask: CanonicalMaskArtifact,
    quote: Decimal,
    discovery_body: bytes,
    expires_at: str,
    directory_binding: Mapping[str, Any],
    transport: Callable[..., OpenRouterHttpResponse],
    clock: Callable[[], str],
    uuid4_factory: Callable[[], str | uuid.UUID],
) -> tuple[str, OpenRouterRealE2EResult | None]:
    """Keep every token-bearing object below a frame that never raises to the caller."""

    journal: AttemptJournal | None = None
    try:
        if not isinstance(token, str):
            return "credential_unavailable", None
        if _directory_identity(target, code="challenge_binding_mismatch") != directory_binding:
            return "challenge_binding_mismatch", None
        sampled = clock()
        if _timestamp_value(sampled, code="clock_invalid") > _timestamp_value(
            expires_at, code="challenge_artifact_drift"
        ):
            return "challenge_expired", None
        journal = AttemptJournal(
            (target / "attempts.sqlite3").resolve(),
            forbidden_secrets=(token,),
        )
        journal.register_run(run)
        registered_attempt = journal.register_attempt(attempt).artifact
        if not isinstance(registered_attempt, GenerationAttempt):
            return "journal_registration_failed", None
        prepared_event = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt.attempt_id,
                "sequence": 1,
                "state": "prepared",
                "recorded_at": attempt.created_at,
                "detail": {"kind": "prepared"},
            }
        )
        journal.append_event(
            prepared_event,
            expected_head_event_id=None,
            expected_next_sequence=1,
        )
        post_count = 0

        if _directory_identity(target, code="challenge_binding_mismatch") != directory_binding:
            return "challenge_binding_mismatch", None
        immediately_before_claim = clock()
        if _timestamp_value(immediately_before_claim, code="clock_invalid") > _timestamp_value(
            expires_at, code="challenge_artifact_drift"
        ):
            return "challenge_expired", None

        def one_shot_transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
            nonlocal post_count
            if post_count != 0:
                raise RuntimeError("one-shot transport replay was refused")
            post_count += 1
            return transport(body=body, bearer_token=bearer_token)

        dispatch = dispatch_openrouter_attempt(
            journal,
            attempt,
            capability,
            prepared,
            credential_resolver=lambda profile: token if profile == CREDENTIAL_PROFILE_ID else "",
            transport=one_shot_transport,
            dispatch_claim_id=_uuid_text(uuid4_factory),
            claimed_at=immediately_before_claim,
            recorded_at=clock,
        )
        receipt_id = (
            dispatch.decoded.receipt.provider_receipt_id if dispatch.decoded is not None else None
        )
        reported_cost, cost_telemetry_status = _reported_cost_telemetry(dispatch)
        if dispatch.kind != "response_received":
            journal.verify_integrity()
            result = _partial_result(
                run=run,
                attempt=attempt,
                journal=journal,
                quote=quote,
                post_count=post_count,
                receipt_id=receipt_id,
                reported_cost=reported_cost,
                cost_telemetry_status=cost_telemetry_status,
            )
            _materialize_finalized_evidence(
                target,
                _FinalizedProviderEvidence(
                    result=result,
                    document=_result_document(
                        result,
                        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                        source_content_ref=blake3(source_bytes).hexdigest(),
                        mask_sha256=mask.mask_sha256,
                        discovery_sha256=hashlib.sha256(discovery_body).hexdigest(),
                        wire_sha256=prepared.wire_body_sha256,
                        wire_byte_count=prepared.wire_body_byte_count,
                    ),
                ),
            )
            _scan_private_artifacts(target, token)
            return "ok", result

        if dispatch.state.head_event_id is None:
            return "response_state_invalid", None
        stored_response = journal.read_provider_response(attempt.attempt_id)
        terminal_at = clock()
        try:
            success = journal.publish_provider_success(
                attempt.attempt_id,
                packet,
                prepared.normalized_request,
                succeeded_at=terminal_at,
                expected_head_event_id=dispatch.state.head_event_id,
                expected_next_sequence=dispatch.state.next_sequence,
            )
        except ProviderMediaAdmissionError as admission_error:
            structural_document: JsonObject | None = None
            locality_document: JsonObject | None = None
            structural_state = "not_run"
            structural_reason: str | None = None
            locality_state = "not_run"
            if len(stored_response.output_bytes) == 1:
                try:
                    structural = verify_output_structure(
                        source_raster,
                        provider_receipt=stored_response.receipt,
                        output_index=0,
                        output_bytes=stored_response.output_bytes[0],
                        output_occurrence=None,
                    )
                    structural_document = judgment_to_json(structural.judgment)
                    structural_state = str(structural_document["result"]["state"])
                    reason = structural_document["result"].get("reason")
                    structural_reason = reason if isinstance(reason, str) else None
                    locality = build_locality_not_run(structural.judgment, mask)
                    locality_document = judgment_to_json(locality)
                    locality_state = str(locality_document["result"]["state"])
                except Exception:
                    structural_document = None
                    locality_document = None
                    structural_state = "not_run"
                    structural_reason = None
                    locality_state = "not_run"
            # ADR-0014 keeps media/provenance rejection at response_received.  The structural
            # judgment and locality not_run evidence remain visible without forging a terminal
            # provider failure or a selectable output occurrence.
            journal.verify_integrity()
            result = OpenRouterRealE2EResult(
                generation_run_id=run.generation_run_id,
                attempt_id=attempt.attempt_id,
                provider_receipt_id=stored_response.receipt.provider_receipt_id,
                output_occurrence_id=None,
                quoted_cost_usd=quote,
                reported_cost_usd=reported_cost,
                cost_telemetry_status=cost_telemetry_status,
                states=tuple(event.state for event in journal.read_events(attempt.attempt_id)),
                generation_post_count=post_count,
                provider_media_admission_result=admission_error.code,
                raw_structural_result=structural_state,
                raw_structural_reason=structural_reason,
                raw_locality_result=locality_state,
            )
            _materialize_finalized_evidence(
                target,
                _FinalizedProviderEvidence(
                    result=result,
                    document=_result_document(
                        result,
                        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                        source_content_ref=blake3(source_bytes).hexdigest(),
                        mask_sha256=mask.mask_sha256,
                        discovery_sha256=hashlib.sha256(discovery_body).hexdigest(),
                        wire_sha256=prepared.wire_body_sha256,
                        wire_byte_count=prepared.wire_body_byte_count,
                        structural=structural_document,
                        locality=locality_document,
                    ),
                ),
            )
            _scan_private_artifacts(target, token)
            return "ok", result
        journal.verify_integrity()
        if len(success.occurrences) != 1 or not isinstance(
            success.occurrences[0], OutputOccurrence
        ):
            return "terminal_occurrence_invalid", None
        occurrence = success.occurrences[0]
        if len(stored_response.output_bytes) != 1:
            return "terminal_occurrence_invalid", None
        output_bytes = stored_response.output_bytes[0]
        structural = verify_output_structure(
            source_raster,
            provider_receipt=stored_response.receipt,
            output_index=0,
            output_bytes=output_bytes,
            output_occurrence=occurrence,
        )
        structural_document = judgment_to_json(structural.judgment)
        structural_state = structural_document["result"]["state"]
        structural_reason: str | None = None
        if structural_state == "pass":
            if structural.output_raster is None:
                return "structural_verification_invalid", None
            locality_judgment = verify_outside_mask_rgb_exact(
                source_raster,
                structural.output_raster,
                mask,
                output_occurrence=occurrence,
                structural_pass=structural.judgment,
            )
        else:
            locality_judgment = build_locality_not_run(structural.judgment, mask)
            reason = structural_document["result"].get("reason")
            structural_reason = reason if isinstance(reason, str) else None
        locality_document = judgment_to_json(locality_judgment)
        locality_result = str(locality_document["result"]["state"])
        states = tuple(event.state for event in journal.read_events(attempt.attempt_id))
        result = OpenRouterRealE2EResult(
            generation_run_id=run.generation_run_id,
            attempt_id=attempt.attempt_id,
            provider_receipt_id=stored_response.receipt.provider_receipt_id,
            output_occurrence_id=occurrence.output_occurrence_id,
            quoted_cost_usd=quote,
            reported_cost_usd=reported_cost,
            cost_telemetry_status=cost_telemetry_status,
            states=states,
            generation_post_count=post_count,
            provider_media_admission_result="pass",
            raw_structural_result=str(structural_state),
            raw_structural_reason=structural_reason,
            raw_locality_result=locality_result,
        )
        output_mime = str(occurrence.original["mime"])
        output_suffix = ".png" if output_mime == "image/png" else ".jpg"
        _materialize_finalized_evidence(
            target,
            _FinalizedProviderEvidence(
                result=result,
                document=_result_document(
                    result,
                    source_sha256=hashlib.sha256(source_bytes).hexdigest(),
                    source_content_ref=blake3(source_bytes).hexdigest(),
                    mask_sha256=mask.mask_sha256,
                    discovery_sha256=hashlib.sha256(discovery_body).hexdigest(),
                    wire_sha256=prepared.wire_body_sha256,
                    wire_byte_count=prepared.wire_body_byte_count,
                    structural=structural_document,
                    locality=locality_document,
                ),
                output_bytes=output_bytes,
                output_suffix=output_suffix,
            ),
        )
        _scan_private_artifacts(target, token)
        return "ok", result
    except BaseException:
        return "execution_failed", None
    finally:
        journal = None
        token = ""


def execute_openrouter_real_e2e(
    challenge_dir: Path,
    confirmation_context_path: Path,
    *,
    _discovery_fetcher: Callable[[], bytes] = fetch_live_discovery,
    _credential_loader: Callable[[str], str] = load_openrouter_keychain_token,
    _transport: Callable[..., OpenRouterHttpResponse] = direct_openrouter_https_transport,
    _confirmation_consumer: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None,
    _clock: Callable[[], str] = _canonical_timestamp,
    _uuid4: Callable[[], str | uuid.UUID] = uuid.uuid4,
) -> OpenRouterRealE2EResult:
    """Execute exactly one previously prepared and independently confirmed challenge."""

    target = Path(challenge_dir)
    context_path = Path(confirmation_context_path)
    if not all(
        callable(value)
        for value in (_discovery_fetcher, _credential_loader, _transport, _clock, _uuid4)
    ):
        _fail("evaluation_callable_invalid")
    if _transport is direct_openrouter_https_transport:
        _preflight_direct_transport_environment()
    challenge, payloads, _summary = _challenge_snapshot(target)
    ok, now_value = _call_sanitized(_clock)
    if not ok or not isinstance(now_value, str):
        _fail("clock_invalid")
    now = now_value
    context, context_bytes = _confirmation_snapshot(context_path, challenge=challenge, now=now)
    if _confirmation_consumer is None:
        _fail("confirmation_authority_unavailable")

    ok, fresh_discovery = _call_sanitized(_discovery_fetcher)
    if not ok or type(fresh_discovery) is not bytes:
        _fail("discovery_unavailable")
    if fresh_discovery != payloads["discovery"]:
        _fail("challenge_discovery_drift")
    quote = parse_openrouter_quote(
        fresh_discovery,
        input_count=1,
        output_count=OUTPUT_COUNT,
        resolution=RESOLUTION,
    )
    if str(quote) != challenge.get("quoted_cost_usd"):
        _fail("challenge_discovery_drift")
    with localcontext(_QUOTE_CONTEXT):
        if quote > QUOTE_ADMISSION_LIMIT_USD:
            _fail("quote_exceeds_cap")

    prepared_at = challenge["prepared_at"]
    assert isinstance(prepared_at, str)
    capability = _build_capability(fresh_discovery, captured_at=prepared_at)
    if capability.capability_snapshot_id != challenge.get("capability_snapshot_id"):
        _fail("challenge_discovery_drift")
    source_bytes = payloads["source"]
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        source_raster = compile_canonical_raster(
            source_bytes,
            source_content_sha256=source_sha256,
        )
        bounds = _mask_bounds(source_raster)
        mask = compile_rectangle_mask(
            source_raster,
            left=bounds[0],
            top=bounds[1],
            right=bounds[2],
            bottom=bounds[3],
        )
    except Exception:
        _fail("challenge_artifact_drift")
    if mask.mask_bytes != payloads["mask"]:
        _fail("challenge_artifact_drift")
    authority_document = _validate_authority_document(
        _strict_json_object(
            payloads["authority"],
            limit=4 * 1024 * 1024,
            code="challenge_artifact_drift",
        )
    )
    confirmation_identity = {
        "compact_summary_id": context["compact_summary_id"],
        "confirmed_at": context["confirmed_at"],
        "studio_session_id": context["studio_session_id"],
        "principal_id": context["principal_id"],
    }
    packet = _build_packet(
        source_bytes=source_bytes,
        source_raster=source_raster,
        mask=mask,
        capability=capability,
        authority=authority_document,
        creative_session_id=str(context["creative_session_id"]),
        confirmation_identity=confirmation_identity,
    )
    if _packet_projection(packet) != challenge.get("packet_projection"):
        _fail("challenge_artifact_drift")
    prepared = _prepare_request(packet, capability, source_bytes)
    if prepared.wire_body_sha256 != challenge.get(
        "wire_body_sha256"
    ) or prepared.wire_body_byte_count != challenge.get("wire_body_byte_count"):
        _fail("challenge_artifact_drift")
    run, attempt = _build_run_and_attempt(
        packet=packet,
        capability=capability,
        prepared=prepared,
        timestamp=prepared_at,
        uuid4=_uuid4,
        generation_run_id=str(challenge["generation_run_id"]),
        attempt_id=str(challenge["attempt_id"]),
    )
    plan = {
        "schema_version": "moodboard.openrouter-real-e2e-plan.v2",
        "challenge_id": challenge["challenge_id"],
        "confirmation_context_id": context["confirmation_context_id"],
        "quoted_cost_usd": str(quote),
        "quote_admission_limit_usd": str(QUOTE_ADMISSION_LIMIT_USD),
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "intent_packet": intent_to_json(packet),
        "capability": provider_to_json(capability),
        "normalized_request": provider_to_json(prepared.normalized_request),
        "generation_run": provider_to_json(run),
        "generation_attempt": provider_to_json(attempt),
    }
    ok, preclaim_now_value = _call_sanitized(_clock)
    if not ok or not isinstance(preclaim_now_value, str):
        _fail("clock_invalid")
    preclaim_now = preclaim_now_value
    if _timestamp_value(preclaim_now, code="clock_invalid") > _timestamp_value(
        challenge["expires_at"], code="challenge_artifact_drift"
    ):
        _fail("challenge_expired")
    authority_ok, consumed = _call_sanitized(
        lambda: _confirmation_consumer(copy.deepcopy(context), copy.deepcopy(challenge))
    )
    if not authority_ok or consumed is not True:
        _fail("confirmation_authority_invalid")
    ok, post_authority_now_value = _call_sanitized(_clock)
    if not ok or not isinstance(post_authority_now_value, str):
        _fail("clock_invalid")
    post_authority_now = post_authority_now_value
    if _timestamp_value(post_authority_now, code="clock_invalid") > _timestamp_value(
        challenge["expires_at"], code="challenge_artifact_drift"
    ):
        _fail("challenge_expired")
    _claim_challenge_consumption(
        target,
        challenge=challenge,
        context=context,
        consumed_at=post_authority_now,
    )
    _write_private_json(target / "plan.json", plan)
    _write_private_bytes(target / "confirmation-context.snapshot.json", context_bytes)

    _enforce_no_core_dumps()
    ok, credential_boundary_now_value = _call_sanitized(_clock)
    if not ok or not isinstance(credential_boundary_now_value, str):
        _fail("clock_invalid")
    if _timestamp_value(credential_boundary_now_value, code="clock_invalid") > _timestamp_value(
        challenge["expires_at"], code="challenge_artifact_drift"
    ):
        _fail("challenge_expired")
    credential_ok, token_value = _call_sanitized(lambda: _credential_loader(CREDENTIAL_PROFILE_ID))
    if not credential_ok or not isinstance(token_value, str):
        _fail("credential_unavailable")
    token = token_value
    status, result = _execute_with_token(
        target=target,
        token=token,
        packet=packet,
        capability=capability,
        prepared=prepared,
        run=run,
        attempt=attempt,
        source_bytes=source_bytes,
        source_raster=source_raster,
        mask=mask,
        quote=quote,
        discovery_body=fresh_discovery,
        expires_at=str(challenge["expires_at"]),
        directory_binding=challenge["directory_binding"],
        transport=_transport,
        clock=_clock,
        uuid4_factory=_uuid4,
    )
    token = ""
    token_value = None
    if status != "ok" or result is None:
        _fail(status)
    return result


def _finalize_openrouter_real_e2e_local_scope(
    target: Path,
    *,
    clock: Callable[[], str],
) -> tuple[str, OpenRouterRealE2EResult | None]:
    """Contain local provider evidence so public failures retain only a stable code."""

    try:
        challenge, payloads, _summary = _challenge_snapshot(
            target,
            require_unconsumed=False,
        )
        try:
            inputs = _rebuild_finalization_inputs(
                target,
                challenge=challenge,
                payloads=payloads,
            )
        except OpenRouterRealE2EError as error:
            if error.code in {"challenge_artifact_drift", "challenge_binding_mismatch"}:
                raise
            _fail("finalization_artifact_invalid")

        journal_path = target / "attempts.sqlite3"
        try:
            journal_metadata = journal_path.lstat()
        except OSError:
            _fail("finalization_artifact_invalid")
        if (
            not stat.S_ISREG(journal_metadata.st_mode)
            or journal_metadata.st_uid != os.getuid()
            or journal_metadata.st_nlink != 1
            or stat.S_IMODE(journal_metadata.st_mode) != 0o600
            or journal_metadata.st_size <= 0
        ):
            _fail("finalization_artifact_invalid")
        try:
            journal = AttemptJournal(journal_path.resolve())
            if (
                journal.read_run(inputs.run.generation_run_id) != inputs.run
                or journal.read_attempt(inputs.attempt.attempt_id) != inputs.attempt
            ):
                _fail("finalization_artifact_invalid")
            state = journal.read_state(inputs.attempt.attempt_id)
            if state.state == "succeeded":
                evidence = _finalized_provider_evidence(inputs, journal, succeeded_at=None)
            elif state.state == "response_received":
                result_path = target / "result.json"
                try:
                    observed_outputs = tuple(
                        path
                        for path in target.iterdir()
                        if path.name.startswith("provider-output-0.")
                    )
                except OSError:
                    _fail("finalization_artifact_conflict")
                if observed_outputs:
                    refreshed = journal.read_state(inputs.attempt.attempt_id)
                    if refreshed.state == "succeeded":
                        evidence = _finalized_provider_evidence(
                            inputs,
                            journal,
                            succeeded_at=None,
                        )
                    elif refreshed.state == "response_received":
                        _fail("finalization_artifact_conflict")
                    else:
                        _fail("finalization_not_ready")
                elif result_path.exists() or result_path.is_symlink():
                    refreshed = journal.read_state(inputs.attempt.attempt_id)
                    if refreshed.state == "succeeded":
                        evidence = _finalized_provider_evidence(
                            inputs,
                            journal,
                            succeeded_at=None,
                        )
                    elif refreshed.state != "response_received":
                        _fail("finalization_not_ready")
                    else:
                        try:
                            evidence = _finalized_provider_evidence(
                                inputs,
                                journal,
                                succeeded_at=None,
                            )
                        except OpenRouterRealE2EError as error:
                            if error.code == "finalization_not_ready":
                                _fail("finalization_artifact_conflict")
                            raise
                else:
                    ok, succeeded_at_value = _call_sanitized(clock)
                    if not ok or not isinstance(succeeded_at_value, str):
                        _fail("clock_invalid")
                    succeeded_at_time = _timestamp_value(
                        succeeded_at_value,
                        code="clock_invalid",
                    )
                    if state.last_recorded_at is None or succeeded_at_time < _timestamp_value(
                        state.last_recorded_at,
                        code="finalization_artifact_invalid",
                    ):
                        _fail("clock_invalid")
                    evidence = _finalized_provider_evidence(
                        inputs,
                        journal,
                        succeeded_at=succeeded_at_value,
                    )
            else:
                _fail("finalization_not_ready")
            fresh_states = tuple(
                event.state for event in journal.read_events(inputs.attempt.attempt_id)
            )
            if fresh_states != evidence.result.states:
                _fail("finalization_not_ready")
            _materialize_finalized_evidence(target, evidence)
            return "ok", evidence.result
        except AttemptJournalError:
            _fail("finalization_artifact_invalid")
    except OpenRouterRealE2EError as error:
        return error.code, None
    except BaseException:
        return "finalization_failed", None


def finalize_openrouter_real_e2e(
    challenge_dir: Path,
    *,
    _clock: Callable[[], str] = _canonical_timestamp,
) -> OpenRouterRealE2EResult:
    """Finish a paid response using only frozen local artifacts and the durable journal."""

    if not callable(_clock):
        _fail("evaluation_callable_invalid")
    path_ok, target = _call_sanitized(lambda: Path(challenge_dir))
    if not path_ok or not isinstance(target, Path):
        _fail("finalization_artifact_invalid")
    status, result = _finalize_openrouter_real_e2e_local_scope(
        target,
        clock=_clock,
    )
    if status != "ok" or result is None:
        _fail(status)
    return result


def _result_document(
    result: OpenRouterRealE2EResult,
    *,
    source_sha256: str,
    source_content_ref: str,
    mask_sha256: str,
    discovery_sha256: str,
    wire_sha256: str,
    wire_byte_count: int,
    structural: Mapping[str, Any] | None = None,
    locality: Mapping[str, Any] | None = None,
) -> JsonObject:
    document: JsonObject = {
        "schema_version": _SUMMARY_VERSION,
        "generation_run_id": result.generation_run_id,
        "attempt_id": result.attempt_id,
        "provider_receipt_id": result.provider_receipt_id,
        "output_occurrence_id": result.output_occurrence_id,
        "quoted_cost_usd": str(result.quoted_cost_usd),
        "quote_admission_limit_usd": str(QUOTE_ADMISSION_LIMIT_USD),
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "reported_cost_usd": (
            str(result.reported_cost_usd) if result.reported_cost_usd is not None else None
        ),
        "cost_telemetry_status": (
            (
                "reported_above_quote_admission_limit"
                if result.reported_cost_usd > QUOTE_ADMISSION_LIMIT_USD
                else "reported"
            )
            if result.cost_telemetry_status == "reported" and result.reported_cost_usd is not None
            else result.cost_telemetry_status
        ),
        "states": list(result.states),
        "provider_lifecycle_state": result.states[-1] if result.states else "not_started",
        "generation_post_count": result.generation_post_count,
        "provider_media_admission_result": result.provider_media_admission_result,
        "raw_structural_result": result.raw_structural_result,
        "raw_structural_reason": result.raw_structural_reason,
        "raw_locality_result": result.raw_locality_result,
        "localized_edit_gate_status": (
            "eligible_exact_pass"
            if result.raw_structural_result == "pass" and result.raw_locality_result == "pass"
            else (
                "not_run"
                if result.raw_structural_result == "not_run"
                and result.raw_locality_result == "not_run"
                else "not_eligible"
            )
        ),
        "workflow_acceptance": "not_recorded",
        "semantic_aesthetic_result": "not_run",
        "compositor_result": "not_run",
        "source": {
            "asset_id": _source_asset_id(source_sha256),
            "content_sha256": source_sha256,
            "content_ref": source_content_ref,
        },
        "mask_sha256": mask_sha256,
        "discovery_sha256": discovery_sha256,
        "wire_sha256": wire_sha256,
        "wire_byte_count": wire_byte_count,
        "actual_model": "undisclosed" if result.provider_receipt_id is not None else "not_reported",
        "upstream_route": "unknown" if result.provider_receipt_id is not None else "not_reported",
        "private_payloads": {
            "committed_to_repository": False,
            "publication": "withheld_private_local_evidence",
            "provider_response": (
                "retained_private_local_evidence"
                if result.provider_receipt_id is not None
                else "absent"
            ),
            "outputs": (
                "retained_private_local_evidence"
                if result.provider_receipt_id is not None
                else "absent"
            ),
        },
    }
    if structural is not None:
        document["raw_structural_judgment"] = copy.deepcopy(dict(structural))
    if locality is not None:
        document["raw_locality_judgment"] = copy.deepcopy(dict(locality))
    return document


def _secret_variants(token: str) -> tuple[bytes, ...]:
    raw = token.encode("ascii")
    values = {
        raw,
        base64.b64encode(raw),
        base64.b64encode(raw).rstrip(b"="),
        base64.urlsafe_b64encode(raw),
        base64.urlsafe_b64encode(raw).rstrip(b"="),
        raw.hex().encode("ascii"),
        raw.hex().upper().encode("ascii"),
        json.dumps(token, ensure_ascii=True)[1:-1].encode("ascii"),
    }
    return tuple(sorted(values, key=lambda item: (len(item), item)))


def _scan_private_artifacts(output_dir: Path, token: str) -> None:
    variants = _secret_variants(token)
    try:
        entries = sorted(output_dir.iterdir(), key=lambda path: path.name)
        total = 0
        for path in entries:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                _fail("artifact_secret_scan_failed")
            payload = _secure_read_private_file(
                path,
                limit=128 * 1024 * 1024,
                code="artifact_secret_scan_failed",
            )
            total += len(payload)
            if total > 256 * 1024 * 1024:
                _fail("artifact_secret_scan_failed")
            if any(variant and variant in payload for variant in variants):
                _fail("credential_material_persisted")
    except OpenRouterRealE2EError:
        raise
    except BaseException:
        _fail("artifact_secret_scan_failed")


def _assert_wire(prepared: OpenRouterPreparedRequest) -> JsonObject:
    try:
        wire = json.loads(prepared.wire_body, object_pairs_hook=_unique_object)
    except Exception:
        _fail("wire_contract_mismatch")
    if not isinstance(wire, dict):
        _fail("wire_contract_mismatch")
    if (
        wire.get("model") != MODEL
        or wire.get("n") != OUTPUT_COUNT
        or wire.get("resolution") != RESOLUTION
        or wire.get("aspect_ratio") != ASPECT_RATIO
        or wire.get("provider") != {"only": [PROVIDER_TAG], "allow_fallbacks": False}
        or not isinstance(wire.get("input_references"), list)
        or len(wire["input_references"]) != 1
        or prepared.wire_body_byte_count != len(prepared.wire_body)
        or hashlib.sha256(prepared.wire_body).hexdigest() != prepared.wire_body_sha256
    ):
        _fail("wire_contract_mismatch")
    return wire


def _partial_result(
    *,
    run: GenerationRun,
    attempt: GenerationAttempt,
    journal: AttemptJournal,
    quote: Decimal,
    post_count: int,
    receipt_id: str | None = None,
    reported_cost: Decimal | None = None,
    cost_telemetry_status: str = "not_reported",
) -> OpenRouterRealE2EResult:
    states = tuple(event.state for event in journal.read_events(attempt.attempt_id))
    return OpenRouterRealE2EResult(
        generation_run_id=run.generation_run_id,
        attempt_id=attempt.attempt_id,
        provider_receipt_id=receipt_id,
        output_occurrence_id=None,
        quoted_cost_usd=quote,
        reported_cost_usd=reported_cost,
        cost_telemetry_status=cost_telemetry_status,
        states=states,
        generation_post_count=post_count,
        provider_media_admission_result="not_run",
        raw_structural_result="not_run",
        raw_structural_reason=None,
        raw_locality_result="not_run",
    )


def run_openrouter_real_e2e(
    output_dir: Path,
    *,
    authorize_one_paid_call: bool,
    _discovery_fetcher: Callable[[], bytes] = fetch_live_discovery,
    _source_fetcher: Callable[[], bytes] = load_pinned_source,
    _credential_loader: Callable[[str], str] = load_openrouter_keychain_token,
    _transport: Callable[..., OpenRouterHttpResponse] = direct_openrouter_https_transport,
    _clock: Callable[[], str] = _canonical_timestamp,
    _uuid4: Callable[[], str | uuid.UUID] = uuid.uuid4,
) -> OpenRouterRealE2EResult:
    """Refuse the retired Boolean authorization API.

    A truthy command-line flag cannot stand in for an exact Studio confirmation. Only
    :func:`prepare_openrouter_real_e2e` followed by :func:`execute_openrouter_real_e2e` may
    reach dispatch.
    """

    del (
        output_dir,
        authorize_one_paid_call,
        _discovery_fetcher,
        _source_fetcher,
        _credential_loader,
        _transport,
        _clock,
        _uuid4,
    )
    _fail("two_phase_confirmation_required")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser(
        "prepare",
        help="report the currently blocked production-preparation prerequisites",
    )
    prepare.add_argument("--challenge-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    os.umask(0o077)
    try:
        if args.command != "prepare":
            _fail("command_unsupported")
        _fail("trusted_authority_integration_required")
    except OpenRouterRealE2EError as error:
        print(json.dumps({"ok": False, "code": error.code}, sort_keys=True))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "code": "unexpected_failure"}, sort_keys=True))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
