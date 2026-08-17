"""RED contract tests for ADR-0014's frozen generation input.

The ADR fixes the data that must be frozen but leaves several nested wire names unstated.
This test selects the smallest closed v1 vocabulary for those fields: ``provider_route_policy``,
``destination``, ``idempotency``, ``reconciliation``, and the four ``confirmation`` projections.
Those names are an implementation wire choice, not an extra product claim.
The source and mask inputs are a role-discriminated union: ADR-0016's registered mask modes
(``native_mask`` and ``attached_overlay`` included) override ADR-0014's shorter generic-mode list.

Run/attempt state transitions, provider I/O, normalized requests, output occurrences, raster
compilation, and locality execution intentionally do not belong in this packet-only test.
"""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, is_dataclass
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

from moodboard.contracts import compute_document_identity, compute_projection_identity
from moodboard.intent_packet import (
    OPERATION_SCHEMA_PATH,
    SCHEMA_PATH,
    SCHEMA_VERSION,
    VERIFICATION_POLICY_SCHEMA_PATH,
    IntentPacketError,
    from_json_dict,
    to_json_dict,
    validate_intent_packet,
)
from moodboard.locality_contracts import SCHEMA_PATHS as LOCALITY_SCHEMA_PATHS

JsonObject = dict[str, Any]
JsonPath = tuple[str | int, ...]

_PACKET_KEYS = frozenset(
    {
        "schema_version",
        "intent_packet_id",
        "creative_session_id",
        "operation",
        "board",
        "source",
        "instruction",
        "retrieval_route",
        "references",
        "generation_request",
        "verification_policy",
        "confirmation",
    }
)
_PACKET_VERSION = "moodboard.intent-packet.v1"
_OPERATION_VERSION = "moodboard.operation.localized-edit.v1"
_POLICY_VERSION = "moodboard.verification-policy.v1"
_EXACT_LOCALITY = "moodboard.verifier.outside-mask-rgb-exact.v1"

_CREATIVE_SESSION_ID = "00000000-0000-4000-8000-000000000001"
_SOURCE_ASSET_ID = "00000000-0000-4000-8000-000000000002"
_STUDIO_SESSION_ID = "00000000-0000-4000-8000-000000000003"
_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000004"
_CREDENTIAL_PROFILE_ID = "00000000-0000-4000-8000-000000000005"


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _reference(
    *,
    occurrence: int,
    source_rank: int,
    similarity: float,
    provider_use: str,
) -> JsonObject:
    value: JsonObject = {
        "reference_occurrence_id": f"00000000-0000-4000-8000-{occurrence:012d}",
        "role": "visual_context",
        "asset_id": f"10000000-0000-4000-8000-{occurrence:012d}",
        "content_ref": _digest(str(occurrence)),
        "source_search_rank": source_rank,
        "routed_rank": occurrence,
        "source_similarity": similarity,
        "route_reason": "declared_collection_match",
        "provider_use": provider_use,
    }
    if provider_use == "attached_image":
        value["content_sha256"] = _digest("8")
    if provider_use == "prompt_context_only":
        value["prompt_context"] = {
            "compiler_revision": "moodboard.reference-prompt.v1",
            "text_items": ["Mature lemon canopy", "Natural branching structure"],
        }
    return value


def _source_operation_input() -> JsonObject:
    return {
        "role": "source_image",
        "original_artifact": {
            "asset_id": _SOURCE_ASSET_ID,
            "content_ref": _digest("2"),
            "content_sha256": _digest("3"),
        },
        "delivery_mode": "native_input",
        "provider_field": "input_references[0]",
        "provider_role": "source_image",
        "capability_id": _digest("8"),
        "delivered_artifact": {
            "content_ref": _digest("2"),
            "content_sha256": _digest("3"),
            "byte_count": 482_931,
            "width": 1280,
            "height": 960,
        },
        "derivative": None,
        "prompt_text": None,
    }


def _mask_operation_input() -> JsonObject:
    return {
        "role": "locality_mask",
        "original_artifact": {
            "mask_sha256": _digest("5"),
            "content_ref": _digest("6"),
            "content_sha256": _digest("7"),
        },
        "delivery_mode": "not_sent",
        "provider_field": None,
        "provider_role": None,
        "capability_id": _digest("9"),
        "delivered_artifact": None,
        "derivative": None,
        "prompt_text": None,
    }


def _native_mask_operation_input() -> JsonObject:
    value = _mask_operation_input()
    value.update(
        {
            "delivery_mode": "native_mask",
            "provider_field": "mask",
            "provider_role": "locality_mask",
            "capability_id": _digest("a"),
        }
    )
    value["delivered_artifact"] = {
        "content_ref": _digest("6"),
        "content_sha256": _digest("7"),
        "byte_count": 1_228_800,
        "width": 1280,
        "height": 960,
    }
    value["derivative"] = {
        "compiler_revision": "moodboard.mask-native.v1",
        "source_identity_sha256": _digest("5"),
        "parameters": {},
        "byte_count": 1_228_800,
        "width": 1280,
        "height": 960,
        "content_ref": _digest("6"),
        "content_sha256": _digest("7"),
    }
    return value


def _localized_payload() -> JsonObject:
    return {
        "source_raster": {
            "schema_version": "moodboard.raster.srgb-u8.v1",
            "compiler_revision": "pillow-12.3-srgb.v1",
            "width": 1280,
            "height": 960,
            "mode": "RGB",
            "byte_count": 3_686_400,
            "source_content_sha256": _digest("3"),
            "raster_sha256": _digest("4"),
        },
        "region": {
            "selection_tool_revision": "studio.rectangle.v1",
            "left": 320,
            "top": 240,
            "right": 960,
            "bottom": 720,
        },
        "mask": {
            "schema_version": "moodboard.mask.u8.v1",
            "compiler_revision": "moodboard.rect-mask.v1",
            "width": 1280,
            "height": 960,
            "byte_count": 1_228_800,
            "editable_count": 307_200,
            "protected_count": 921_600,
            "source_raster_sha256": _digest("4"),
            "mask_sha256": _digest("5"),
        },
        "raw_diagnostic_verifiers": [],
        "insert_compiler_policy": "raw_crop_nearest.v1",
        "compositor_policy": "source_backed_rect_replace.v1",
    }


def _refresh_nested_identities(document: JsonObject) -> None:
    operation = document["operation"]
    operation["payload_sha256"] = compute_projection_identity(
        operation["payload"],
        domain_tag=_OPERATION_VERSION,
    )
    policy = document["verification_policy"]
    policy["policy_id"] = "0" * 64
    policy["policy_id"] = compute_document_identity(
        policy,
        schema_version=_POLICY_VERSION,
        identity_field="policy_id",
    )


def _refresh_packet_identity(document: JsonObject) -> JsonObject:
    _refresh_nested_identities(document)
    document["intent_packet_id"] = "0" * 64
    document["intent_packet_id"] = compute_document_identity(
        document,
        schema_version=_PACKET_VERSION,
        identity_field="intent_packet_id",
    )
    return document


def _sync_confirmation(document: JsonObject) -> None:
    """Mirror mutable fixture fields so one test can isolate a non-confirmation invariant."""

    references = document["references"]
    request = document["generation_request"]
    policy = document["verification_policy"]
    confirmation = document["confirmation"]
    confirmation["references_shown"] = copy.deepcopy(references)
    confirmation["reference_use"] = [
        {
            "reference_occurrence_id": reference["reference_occurrence_id"],
            "provider_use": reference["provider_use"],
        }
        for reference in references
    ]
    confirmation["operation_inputs_shown"] = copy.deepcopy(request["operation_inputs"])
    confirmation["dispatch_shown"] = {
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
    }


def _valid_packet() -> JsonObject:
    references = [
        _reference(
            occurrence=1,
            source_rank=4,
            similarity=0.8432995826005936,
            provider_use="attached_image",
        ),
        _reference(
            occurrence=2,
            source_rank=5,
            similarity=0.7920756787061691,
            provider_use="prompt_context_only",
        ),
        _reference(
            occurrence=3,
            source_rank=9,
            similarity=0.7569498121738434,
            provider_use="not_sent",
        ),
    ]
    operation_inputs = [_source_operation_input(), _mask_operation_input()]
    provider_route_policy = {
        "schema_version": "moodboard.provider-route-policy.v1",
        "provider_route_policy_id": _digest("a"),
        "permitted_routes": [
            {
                "route_id": "openrouter-primary",
                "provider": "openrouter",
                "model": "qwen/qwen-image-3",
                "upstream_provider_tag": "alibaba",
                "privacy_class": "external_public_demo",
                "retention_class": "provider_terms_apply",
            }
        ],
        "moodboard_fallback_permitted": False,
        "undisclosed_upstream_routing_permitted": True,
    }
    destination = {
        "privacy_class": "external_public_demo",
        "retention_class": "provider_terms_apply",
        "credential_profile_id": _CREDENTIAL_PROFILE_ID,
    }
    options = {
        "schema_version": "moodboard.openrouter-images-options.v1",
        "seed": 17,
        "resolution": "1K",
        "aspect_ratio": "4:3",
    }
    verification_policy = {
        "schema_version": _POLICY_VERSION,
        "policy_id": "0" * 64,
        "required_verifiers": [_EXACT_LOCALITY],
    }
    generation_request = {
        "requested_provider": "openrouter",
        "requested_model": "qwen/qwen-image-3",
        "adapter_revision": "moodboard.openrouter.v1",
        "capability_snapshot_id": _digest("b"),
        "output_count": 1,
        "options": options,
        "operation_inputs": operation_inputs,
        "provider_route_policy": provider_route_policy,
        "destination": destination,
        "actual_model_policy": "requested_only_permitted",
        "idempotency": {
            "provider_accepts_key": False,
            "deduplication_scope": None,
            "retention_seconds": None,
            "ambiguous_transport_retransmit_safe": False,
        },
        "reconciliation": {
            "supported": False,
            "provider_handle_kind": None,
        },
    }
    confirmation = {
        "mode": "explicit",
        "references_shown": copy.deepcopy(references),
        "reference_use": [
            {
                "reference_occurrence_id": reference["reference_occurrence_id"],
                "provider_use": reference["provider_use"],
            }
            for reference in references
        ],
        "operation_inputs_shown": copy.deepcopy(operation_inputs),
        "dispatch_shown": {
            "requested_provider": generation_request["requested_provider"],
            "requested_model": generation_request["requested_model"],
            "output_count": generation_request["output_count"],
            "destination": copy.deepcopy(destination),
            "adapter_revision": generation_request["adapter_revision"],
            "capability_snapshot_id": generation_request["capability_snapshot_id"],
            "options": copy.deepcopy(options),
            "provider_route_policy": copy.deepcopy(provider_route_policy),
            "actual_model_policy": generation_request["actual_model_policy"],
            "idempotency": copy.deepcopy(generation_request["idempotency"]),
            "reconciliation": copy.deepcopy(generation_request["reconciliation"]),
            "verification_policy_id": verification_policy["policy_id"],
            "required_verifiers": copy.deepcopy(verification_policy["required_verifiers"]),
        },
        "compact_summary_id": _digest("c"),
        "confirmed_at": "2026-08-16T20:30:00Z",
        "studio_session_id": _STUDIO_SESSION_ID,
        "principal_id": _PRINCIPAL_ID,
    }
    packet: JsonObject = {
        "schema_version": _PACKET_VERSION,
        "intent_packet_id": "0" * 64,
        "creative_session_id": _CREATIVE_SESSION_ID,
        "operation": {
            "kind": "localized_edit",
            "schema_version": _OPERATION_VERSION,
            "payload_sha256": "0" * 64,
            "payload": _localized_payload(),
        },
        "board": {
            "board_id": _digest("d"),
            "representation_id": _digest("a"),
            "fit_policy_id": _digest("b"),
        },
        "source": {
            "asset_id": _SOURCE_ASSET_ID,
            "content_ref": _digest("2"),
            "content_sha256": _digest("3"),
            "mime": "image/png",
            "width": 1280,
            "height": 960,
        },
        "instruction": "Replace only the selected tree with a mature lemon tree.",
        "retrieval_route": {
            "schema_version": "moodboard.intent-route.collection-gate.v1",
            "route_policy_id": _digest("e"),
            "eligible_corpus_sha256": _digest("f"),
            "empty_result_policy": "no_ungated_fallback",
            "evidence_artifact_id": _digest("0"),
        },
        "references": references,
        "generation_request": generation_request,
        "verification_policy": verification_policy,
        "confirmation": confirmation,
    }
    _refresh_nested_identities(packet)
    # The confirmation binds the final verifier-policy identity, not its placeholder.
    packet["confirmation"]["dispatch_shown"]["verification_policy_id"] = packet[
        "verification_policy"
    ]["policy_id"]
    return _refresh_packet_identity(packet)


def _schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    registry = Registry()
    for path in (
        OPERATION_SCHEMA_PATH,
        VERIFICATION_POLICY_SCHEMA_PATH,
        *LOCALITY_SCHEMA_PATHS.values(),
    ):
        dependency = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(dependency["$id"], Resource.from_contents(dependency))
    return jsonschema.Draft202012Validator(
        schema,
        registry=registry,
        format_checker=jsonschema.FormatChecker(),
    )


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


def _at_path(document: JsonObject, path: JsonPath) -> Any:
    value: Any = document
    for segment in path:
        value = value[segment]
    return value


def test_packet_schema_is_closed_draft_2020_12_and_exposes_v1() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert SCHEMA_VERSION == _PACKET_VERSION
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.Draft202012Validator.check_schema(schema)


def test_complete_packet_round_trips_as_one_immutable_value() -> None:
    packet = _valid_packet()

    assert frozenset(packet) == _PACKET_KEYS
    assert validate_intent_packet(packet) is None
    _schema_validator().validate(packet)

    frozen = from_json_dict(packet)
    assert is_dataclass(frozen)
    assert to_json_dict(frozen) == packet
    with pytest.raises(FrozenInstanceError):
        frozen.schema_version = "tampered"  # type: ignore[misc]

    emitted = to_json_dict(frozen)
    emitted["instruction"] = "mutated copy"
    assert to_json_dict(frozen) == packet


def test_packet_identity_is_full_document_minus_id_rfc8785_golden() -> None:
    packet = _valid_packet()

    assert packet["intent_packet_id"] == (
        "a27f669748ead079f5933827542a3b3cece1c715db6d5b6af05f7bf0936d2bf9"
    )
    assert packet["intent_packet_id"] == compute_document_identity(
        packet,
        schema_version=_PACKET_VERSION,
        identity_field="intent_packet_id",
    )

    for path, replacement in (
        (("instruction",), "A different declared instruction."),
        (("generation_request", "requested_model"), "vendor/different-model"),
        (("generation_request", "options", "seed"), 18),
        (
            (
                "generation_request",
                "provider_route_policy",
                "provider_route_policy_id",
            ),
            _digest("b"),
        ),
        (("confirmation", "mode"), "default_trust"),
    ):
        tampered = copy.deepcopy(packet)
        _at_path(tampered, path[:-1])[path[-1]] = replacement
        with pytest.raises(IntentPacketError, match="identity mismatch"):
            validate_intent_packet(tampered)


def test_operation_and_verifier_policy_have_independent_domain_separated_identities() -> None:
    packet = _valid_packet()

    assert packet["operation"]["payload_sha256"] == compute_projection_identity(
        packet["operation"]["payload"],
        domain_tag=_OPERATION_VERSION,
    )
    assert packet["verification_policy"]["policy_id"] == compute_document_identity(
        packet["verification_policy"],
        schema_version=_POLICY_VERSION,
        identity_field="policy_id",
    )

    changed_payload = copy.deepcopy(packet)
    changed_payload["operation"]["payload"]["region"]["right"] = 961
    _refresh_packet_identity(changed_payload)
    # Put back the reviewed digest to prove the operation-owned identity catches payload drift.
    changed_payload["operation"]["payload_sha256"] = packet["operation"]["payload_sha256"]
    changed_payload["intent_packet_id"] = compute_document_identity(
        changed_payload,
        schema_version=_PACKET_VERSION,
        identity_field="intent_packet_id",
    )
    with pytest.raises(IntentPacketError):
        validate_intent_packet(changed_payload)

    changed_policy = copy.deepcopy(packet)
    changed_policy["verification_policy"]["required_verifiers"] = []
    _refresh_packet_identity(changed_policy)
    changed_policy["verification_policy"]["policy_id"] = packet["verification_policy"]["policy_id"]
    changed_policy["confirmation"]["dispatch_shown"]["verification_policy_id"] = packet[
        "verification_policy"
    ]["policy_id"]
    changed_policy["intent_packet_id"] = compute_document_identity(
        changed_policy,
        schema_version=_PACKET_VERSION,
        identity_field="intent_packet_id",
    )
    with pytest.raises(IntentPacketError):
        validate_intent_packet(changed_policy)


def test_packet_rejects_nonfinite_numbers_before_identity_admission() -> None:
    packet = _valid_packet()
    packet["references"][0]["source_similarity"] = float("nan")

    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)


def test_every_packet_object_is_closed_including_operation_and_confirmation() -> None:
    packet = _valid_packet()
    validator = _schema_validator()

    for path in _object_paths(packet):
        tampered = copy.deepcopy(packet)
        _at_path(tampered, path)["unexpected"] = "closed means closed"
        _refresh_packet_identity(tampered)

        assert not validator.is_valid(tampered), f"schema left object {path!r} open"
        with pytest.raises(IntentPacketError):
            validate_intent_packet(tampered)


@pytest.mark.parametrize(
    ("path", "unknown"),
    (
        (("operation", "kind"), "whole_frame_restyle"),
        (("operation", "schema_version"), "moodboard.operation.unknown.v1"),
        (("references", 0, "provider_use"), "selected_in_studio"),
        (("generation_request", "actual_model_policy"), "best_effort"),
        (("generation_request", "operation_inputs", 0, "delivery_mode"), "upload"),
        (("generation_request", "operation_inputs", 1, "delivery_mode"), "soft_mask"),
        (("confirmation", "mode"), "implicit"),
    ),
)
def test_all_registered_discriminators_and_delivery_modes_fail_closed(
    path: JsonPath, unknown: str
) -> None:
    packet = _valid_packet()
    _at_path(packet, path[:-1])[path[-1]] = unknown
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)

    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)


def test_localized_edit_requires_source_mask_and_the_exact_locality_gate() -> None:
    base = _valid_packet()
    mutations = (
        lambda value: value["generation_request"]["operation_inputs"].pop(0),
        lambda value: value["generation_request"]["operation_inputs"].pop(1),
        lambda value: value["generation_request"]["operation_inputs"][1].__setitem__(
            "role", "source_image"
        ),
        lambda value: value["verification_policy"]["required_verifiers"].clear(),
        lambda value: value["operation"]["payload"]["source_raster"].__setitem__(
            "source_content_sha256", _digest("8")
        ),
        lambda value: value["generation_request"]["operation_inputs"][0][
            "original_artifact"
        ].__setitem__("content_sha256", _digest("8")),
        lambda value: value["generation_request"]["operation_inputs"][0][
            "delivered_artifact"
        ].__setitem__("width", 1279),
        lambda value: value["generation_request"]["operation_inputs"][1][
            "original_artifact"
        ].__setitem__("mask_sha256", _digest("8")),
    )
    for mutate in mutations:
        packet = copy.deepcopy(base)
        mutate(packet)
        _sync_confirmation(packet)
        _refresh_packet_identity(packet)
        with pytest.raises(IntentPacketError):
            validate_intent_packet(packet)


def test_role_discriminated_input_union_accepts_a_provider_with_native_mask() -> None:
    packet = _valid_packet()
    request = packet["generation_request"]
    request["operation_inputs"][1] = _native_mask_operation_input()
    request["requested_provider"] = "synthetic-native-mask-provider"
    request["requested_model"] = "synthetic/image-edit-v1"
    request["adapter_revision"] = "moodboard.synthetic-native-mask.v1"
    request["capability_snapshot_id"] = _digest("c")
    request["actual_model_policy"] = "exact_required"
    request["options"] = {"schema_version": "moodboard.generation-options.none.v1"}
    route_policy = request["provider_route_policy"]
    route_policy["provider_route_policy_id"] = _digest("d")
    route_policy["permitted_routes"] = [
        {
            "route_id": "synthetic-native-mask-primary",
            "provider": request["requested_provider"],
            "model": request["requested_model"],
            "upstream_provider_tag": "synthetic-upstream",
            "privacy_class": request["destination"]["privacy_class"],
            "retention_class": request["destination"]["retention_class"],
        }
    ]
    route_policy["undisclosed_upstream_routing_permitted"] = False
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)

    validate_intent_packet(packet)


def test_non_attached_references_may_omit_source_byte_sha256() -> None:
    packet = _valid_packet()

    assert "content_sha256" in packet["references"][0]
    assert "content_sha256" not in packet["references"][1]
    assert "content_sha256" not in packet["references"][2]
    validate_intent_packet(packet)


def test_reference_occurrences_retain_routing_order_and_explicit_provider_use() -> None:
    base = _valid_packet()
    mutations = (
        lambda value: value["references"].reverse(),
        lambda value: value["references"][1].__setitem__("routed_rank", 3),
        lambda value: value["references"][1].__setitem__("source_search_rank", 3),
        lambda value: value["references"][1].__setitem__("source_similarity", 0.9),
        lambda value: value["references"][1].__setitem__(
            "reference_occurrence_id",
            value["references"][0]["reference_occurrence_id"],
        ),
        lambda value: value["references"][1].__setitem__(
            "asset_id", value["references"][0]["asset_id"]
        ),
        lambda value: value["references"][1].__setitem__(
            "content_ref", value["references"][0]["content_ref"]
        ),
        lambda value: value["references"][1].pop("prompt_context"),
        lambda value: value["references"][0].__setitem__(
            "prompt_context",
            {
                "compiler_revision": "moodboard.reference-prompt.v1",
                "text_items": ["must not exist for attached bytes"],
            },
        ),
        lambda value: value["references"][0].pop("content_sha256"),
    )
    for mutate in mutations:
        packet = copy.deepcopy(base)
        mutate(packet)
        _sync_confirmation(packet)
        _refresh_packet_identity(packet)
        with pytest.raises(IntentPacketError):
            validate_intent_packet(packet)


def test_confirmation_is_an_exact_copy_of_what_will_cross_the_dispatch_boundary() -> None:
    base = _valid_packet()
    mutations = (
        lambda value: value["confirmation"]["references_shown"].reverse(),
        lambda value: value["confirmation"]["reference_use"][0].__setitem__(
            "provider_use", "not_sent"
        ),
        lambda value: value["confirmation"]["operation_inputs_shown"][1].__setitem__(
            "delivery_mode", "native_mask"
        ),
        lambda value: value["confirmation"]["dispatch_shown"].__setitem__(
            "requested_model", "vendor/different-model"
        ),
        lambda value: value["confirmation"]["dispatch_shown"]["destination"].__setitem__(
            "privacy_class", "different-privacy-boundary"
        ),
        lambda value: value["confirmation"]["dispatch_shown"]["options"].__setitem__("seed", 18),
        lambda value: value["confirmation"]["dispatch_shown"]["provider_route_policy"].__setitem__(
            "moodboard_fallback_permitted", True
        ),
        lambda value: value["confirmation"]["dispatch_shown"]["idempotency"].__setitem__(
            "provider_accepts_key", True
        ),
        lambda value: value["confirmation"]["dispatch_shown"]["reconciliation"].__setitem__(
            "supported", True
        ),
    )
    for mutate in mutations:
        packet = copy.deepcopy(base)
        mutate(packet)
        _refresh_packet_identity(packet)
        with pytest.raises(IntentPacketError):
            validate_intent_packet(packet)


def test_dispatch_capability_changes_require_renewed_confirmation() -> None:
    idempotent = _valid_packet()
    idempotent["generation_request"]["idempotency"] = {
        "provider_accepts_key": True,
        "deduplication_scope": "provider-account",
        "retention_seconds": 3600,
        "ambiguous_transport_retransmit_safe": True,
    }
    _refresh_packet_identity(idempotent)
    with pytest.raises(IntentPacketError, match="confirmation dispatch_shown"):
        validate_intent_packet(idempotent)

    reconcilable = _valid_packet()
    reconcilable["generation_request"]["reconciliation"] = {
        "supported": True,
        "provider_handle_kind": "generation-id",
    }
    _refresh_packet_identity(reconcilable)
    with pytest.raises(IntentPacketError, match="confirmation dispatch_shown"):
        validate_intent_packet(reconcilable)


def test_fallback_routes_must_preserve_exact_model_and_privacy_boundary() -> None:
    packet = _valid_packet()
    policy = packet["generation_request"]["provider_route_policy"]
    policy["moodboard_fallback_permitted"] = True
    policy["permitted_routes"].append(
        {
            "route_id": "openrouter-secondary",
            "provider": "openrouter",
            "model": packet["generation_request"]["requested_model"],
            "upstream_provider_tag": "alibaba",
            "privacy_class": packet["generation_request"]["destination"]["privacy_class"],
            "retention_class": packet["generation_request"]["destination"]["retention_class"],
        }
    )
    packet["confirmation"]["dispatch_shown"]["provider_route_policy"] = copy.deepcopy(policy)
    _refresh_packet_identity(packet)
    validate_intent_packet(packet)

    for field, value in (
        ("model", "vendor/substituted-model"),
        ("privacy_class", "different-privacy-boundary"),
        ("retention_class", "different-retention-boundary"),
    ):
        escaped = copy.deepcopy(packet)
        escaped["generation_request"]["provider_route_policy"]["permitted_routes"][1][field] = value
        escaped["confirmation"]["dispatch_shown"]["provider_route_policy"] = copy.deepcopy(
            escaped["generation_request"]["provider_route_policy"]
        )
        _refresh_packet_identity(escaped)
        with pytest.raises(IntentPacketError):
            validate_intent_packet(escaped)


def test_retrieval_route_and_provider_route_are_distinct_frozen_authorities() -> None:
    packet = _valid_packet()

    assert (
        packet["retrieval_route"]["route_policy_id"]
        != packet["generation_request"]["provider_route_policy"]["provider_route_policy_id"]
    )

    packet["retrieval_route"]["provider_route_policy_id"] = _digest("1")
    _refresh_packet_identity(packet)
    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)

    same_identity = _valid_packet()
    same_identity["retrieval_route"]["route_policy_id"] = same_identity["generation_request"][
        "provider_route_policy"
    ]["provider_route_policy_id"]
    _refresh_packet_identity(same_identity)
    with pytest.raises(IntentPacketError, match="must be distinct"):
        validate_intent_packet(same_identity)


def test_empty_retrieval_cannot_produce_a_dispatch_ready_packet() -> None:
    packet = _valid_packet()
    packet["references"] = []
    packet["confirmation"]["references_shown"] = []
    packet["confirmation"]["reference_use"] = []
    _refresh_packet_identity(packet)

    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)


@pytest.mark.parametrize(
    ("path", "secret_field"),
    (
        (("generation_request",), "api_key"),
        (("generation_request", "destination"), "credential"),
        (("generation_request", "options"), "authorization"),
        (("confirmation", "dispatch_shown"), "cookie"),
    ),
)
def test_packet_rejects_credential_material_and_keeps_only_a_profile_id(
    path: JsonPath, secret_field: str
) -> None:
    packet = _valid_packet()
    _at_path(packet, path)[secret_field] = "sk-or-v1-secret-that-must-never-persist"
    _refresh_packet_identity(packet)

    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)


def test_credential_profile_id_is_an_opaque_uuid_not_a_key_shaped_string() -> None:
    packet = _valid_packet()
    packet["generation_request"]["destination"]["credential_profile_id"] = (
        "sk-or-v1-secret-that-must-never-persist"
    )
    packet["confirmation"]["dispatch_shown"]["destination"] = copy.deepcopy(
        packet["generation_request"]["destination"]
    )
    _refresh_packet_identity(packet)

    with pytest.raises(IntentPacketError):
        validate_intent_packet(packet)
