"""RED contracts for ADR-0014's bounded OpenRouter P0 adapter runtime.

These tests own the exact OpenRouter wire projection, buffered response decoding, and the
durable-journal ordering around one non-idempotent provider send.  Media admission and terminal
``succeeded`` evidence remain separate concerns.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
import threading
from dataclasses import FrozenInstanceError, is_dataclass, replace
from pathlib import Path
from queue import Queue
from typing import Any

import pytest
from blake3 import blake3

import moodboard.openrouter as openrouter_module
from moodboard.attempt_journal import (
    AttemptJournal,
    AttemptJournalError,
    JournalNotFoundError,
)
from moodboard.contracts import compute_document_identity
from moodboard.openrouter import (
    ADAPTER_REVISION,
    PROMPT_COMPILER_REVISION,
    OpenRouterAdapterError,
    OpenRouterDecodedResponse,
    OpenRouterDispatchResult,
    OpenRouterHttpResponse,
    OpenRouterPreparedRequest,
    decode_openrouter_response,
    dispatch_openrouter_attempt,
    openrouter_https_transport,
    prepare_openrouter_request,
    reconcile_openrouter_attempt,
)
from moodboard.provider_artifacts import (
    CAPABILITY_VERSION,
    REQUEST_VERSION,
    GenerationAttempt,
    NormalizedProviderRequest,
    ProviderCapabilitySnapshot,
    seal_provider_artifact,
    validate_provider_artifact,
)
from moodboard.provider_artifacts import (
    from_json_dict as provider_from_json,
)
from moodboard.provider_artifacts import (
    to_json_dict as provider_to_json,
)
from tests.test_intent_packet import _refresh_packet_identity, _sync_confirmation
from tests.test_provider_artifacts import (
    _artifact,
    _event,
    _reference_use,
    _valid_artifact_chain,
    _valid_attempt,
    _valid_run,
)

JsonObject = dict[str, Any]

_SOURCE_BYTES = b"\x89PNG\r\n\x1a\nsource"
_REFERENCE_BYTES = b"\x89PNG\r\n\x1a\nreference"
_OUTPUT_BYTES = b"generated-image-v1"
_SOURCE_DATA_URL = "data:image/png;base64,iVBORw0KGgpzb3VyY2U="
_REFERENCE_DATA_URL = "data:image/png;base64,iVBORw0KGgpyZWZlcmVuY2U="
_PROMPT = (
    "Replace only the selected tree with a mature lemon tree. "
    "Reference context: Mature lemon canopy; Natural branching structure."
)
_WIRE_BODY = (
    '{"aspect_ratio":"4:3","input_references":['
    f'{{"image_url":{{"url":"{_SOURCE_DATA_URL}"}},"type":"image_url"}},'
    f'{{"image_url":{{"url":"{_REFERENCE_DATA_URL}"}},"type":"image_url"}}],'
    '"model":"qwen/qwen-image-3","n":1,'
    f'"prompt":"{_PROMPT}",'
    '"provider":{"allow_fallbacks":false,"only":["alibaba"]},'
    '"resolution":"1K","seed":17}'
).encode()
_WIRE_SHA256 = "9a459e43b55fb6659bd850ce04dd45d76e1d3b3d914a565f3648566fb631e7e9"
_RESPONSE_BODY = (
    b'{"created":1786930000,"data":[{"b64_json":"Z2VuZXJhdGVkLWltYWdlLXYx"}],'
    b'"usage":{"cost":0.031250}}'
)
_DISPATCH_CLAIM_ID = "60000000-0000-4000-8000-000000000001"
_CLAIMED_AT = "2026-08-16T20:30:04Z"
_RECORDED_AT = "2026-08-16T20:30:05Z"


def _bind_bytes(packet: JsonObject) -> dict[str, bytes]:
    """Replace placeholder fixture identities with exact resolver-owned byte identities."""

    source_ref = blake3(_SOURCE_BYTES).hexdigest()
    source_sha256 = hashlib.sha256(_SOURCE_BYTES).hexdigest()
    source = packet["source"]
    source["content_ref"] = source_ref
    source["content_sha256"] = source_sha256
    packet["operation"]["payload"]["source_raster"]["source_content_sha256"] = source_sha256

    source_input = packet["generation_request"]["operation_inputs"][0]
    source_input["original_artifact"]["content_ref"] = source_ref
    source_input["original_artifact"]["content_sha256"] = source_sha256
    source_input["delivered_artifact"]["content_ref"] = source_ref
    source_input["delivered_artifact"]["content_sha256"] = source_sha256
    source_input["delivered_artifact"]["byte_count"] = len(_SOURCE_BYTES)

    attached = packet["references"][0]
    reference_ref = blake3(_REFERENCE_BYTES).hexdigest()
    attached["content_ref"] = reference_ref
    attached["content_sha256"] = hashlib.sha256(_REFERENCE_BYTES).hexdigest()

    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    return {source_ref: _SOURCE_BYTES, reference_ref: _REFERENCE_BYTES}


def _prepared_case() -> tuple[
    JsonObject,
    JsonObject,
    OpenRouterPreparedRequest,
    list[str],
]:
    packet, artifacts = _valid_artifact_chain()
    capability = copy.deepcopy(_artifact(artifacts, CAPABILITY_VERSION))
    content = _bind_bytes(packet)
    resolved: list[str] = []

    def resolve_content(content_ref: str) -> bytes:
        resolved.append(content_ref)
        return content[content_ref]

    prepared = prepare_openrouter_request(
        packet,
        capability,
        selected_route_id="openrouter-primary",
        resolve_content=resolve_content,
    )
    return packet, capability, prepared, resolved


def _seed_dispatch(
    tmp_path: Path,
    *,
    forbidden_secrets: tuple[str, ...] = (),
) -> tuple[
    AttemptJournal,
    GenerationAttempt,
    ProviderCapabilitySnapshot,
    OpenRouterPreparedRequest,
]:
    packet, capability_document, prepared, _ = _prepared_case()
    normalized_document = provider_to_json(prepared.normalized_request)
    run = _valid_run(packet)
    attempt_document = _valid_attempt(
        packet,
        run,
        capability_document,
        normalized_document,
    )
    prepared_event = _event(
        attempt_id=attempt_document["attempt_id"],
        sequence=1,
        state="prepared",
        recorded_at=attempt_document["created_at"],
        detail={"kind": "prepared"},
    )
    journal = AttemptJournal(
        (tmp_path / "attempts.sqlite3").resolve(),
        forbidden_secrets=forbidden_secrets,
    )
    journal.register_run(run)
    attempt = journal.register_attempt(attempt_document).artifact
    journal.append_event(
        prepared_event,
        expected_head_event_id=None,
        expected_next_sequence=1,
    )
    capability = provider_from_json(capability_document)
    assert isinstance(attempt, GenerationAttempt)
    assert isinstance(capability, ProviderCapabilitySnapshot)
    return journal, attempt, capability, prepared


def _success_response() -> OpenRouterHttpResponse:
    return OpenRouterHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=_RESPONSE_BODY,
        elapsed_milliseconds=2450,
    )


def test_prepare_pins_exact_jcs_wire_body_data_urls_identity_and_fixture_order() -> None:
    packet, _, prepared, resolved = _prepared_case()
    normalized = provider_to_json(prepared.normalized_request)

    assert ADAPTER_REVISION == "moodboard.openrouter.v1"
    assert PROMPT_COMPILER_REVISION == "moodboard.openrouter-prompt.v1"
    assert isinstance(prepared, OpenRouterPreparedRequest)
    assert isinstance(prepared.normalized_request, NormalizedProviderRequest)
    assert is_dataclass(prepared)
    with pytest.raises(FrozenInstanceError):
        prepared.wire_body = b"changed"  # type: ignore[misc]

    assert prepared.wire_body == _WIRE_BODY
    assert len(prepared.wire_body) == 474
    assert prepared.wire_body_byte_count == 474
    assert prepared.wire_body_sha256 == _WIRE_SHA256
    assert hashlib.sha256(prepared.wire_body).hexdigest() == _WIRE_SHA256
    assert resolved == [packet["source"]["content_ref"], packet["references"][0]["content_ref"]]

    assert normalized["prompt"] == {
        "text": _PROMPT,
        "compiler_revision": PROMPT_COMPILER_REVISION,
    }
    assert normalized["operation_inputs"] == packet["generation_request"]["operation_inputs"]
    assert normalized["reference_use"] == _reference_use(packet)
    assert normalized["provider_body"] == {
        "schema_version": "moodboard.openrouter-images-body-projection.v1",
        "method": "POST",
        "endpoint_path": "/api/v1/images",
        "model": "qwen/qwen-image-3",
        "prompt": _PROMPT,
        "n": 1,
        "seed": 17,
        "resolution": "1K",
        "aspect_ratio": "4:3",
        "input_references": [
            {
                "position": 0,
                "provider_field": "input_references[0].image_url.url",
                "item_type": "image_url",
                "transport": "data_url",
                "media_type": "image/png",
                "role": "source_image",
                "source_kind": "operation_input",
                "source_id": packet["source"]["asset_id"],
                "content_sha256": hashlib.sha256(_SOURCE_BYTES).hexdigest(),
                "transport_value_sha256": (
                    "06e2d83b4a8b650e20aa5ad5cc35855aecce6f1c9e2f3a7821d4080f89b19190"
                ),
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
                "content_sha256": hashlib.sha256(_REFERENCE_BYTES).hexdigest(),
                "transport_value_sha256": (
                    "292bff6c66ce8ede2365b604017c20cc0df1cf4c54c48abeec800d9244051296"
                ),
            },
        ],
        "provider": {"only": ["alibaba"], "allow_fallbacks": False},
    }
    assert normalized["normalized_request_id"] == compute_document_identity(
        normalized,
        schema_version=REQUEST_VERSION,
        identity_field="normalized_request_id",
    )
    validate_provider_artifact(normalized)

    prepared_repr = repr(prepared)
    assert "data:image/" not in prepared_repr
    assert _SOURCE_DATA_URL not in prepared_repr
    assert _REFERENCE_DATA_URL not in prepared_repr


def test_success_claims_before_transport_and_atomically_persists_response_evidence(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    observed: list[str] = []

    def credential_resolver(credential_profile_id: str) -> str:
        assert credential_profile_id == "00000000-0000-4000-8000-000000000005"
        return "test-bearer-token"

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert body == _WIRE_BODY
        assert bearer_token == "test-bearer-token"
        reopened = AttemptJournal(journal.path)
        assert reopened.read_state(attempt.attempt_id).state == "submitted"
        assert [event.state for event in reopened.read_events(attempt.attempt_id)] == [
            "prepared",
            "submitted",
        ]
        observed.append("transport")
        return _success_response()

    def recorded_at() -> str:
        assert journal.read_state(attempt.attempt_id).state == "submitted"
        observed.append("clock")
        return _RECORDED_AT

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=credential_resolver,
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=recorded_at,
    )

    assert isinstance(result, OpenRouterDispatchResult)
    assert result.kind == "response_received"
    assert result.state.state == "response_received"
    assert result.event is not None and result.event.state == "response_received"
    assert isinstance(result.decoded, OpenRouterDecodedResponse)
    assert observed == ["transport", "clock"]
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "response_received",
    ]

    decoded = result.decoded
    receipt = provider_to_json(decoded.receipt)
    stored = journal.read_provider_response(attempt.attempt_id)
    assert provider_to_json(stored.receipt) == receipt
    assert stored.raw_response_bytes == _RESPONSE_BODY
    assert stored.output_bytes == (_OUTPUT_BYTES,)
    assert decoded.raw_response_bytes == _RESPONSE_BODY
    assert decoded.output_bytes == (_OUTPUT_BYTES,)
    assert receipt["actual_model"] == {
        "state": "undisclosed",
        "model": None,
        "source_field": None,
    }
    assert receipt["upstream_route"] == {
        "state": "unknown",
        "provider_tag": None,
        "source_field": None,
    }
    assert receipt["raw_response"] == {
        "state": "retained",
        "content_ref": "45a6d24ac762b671532cee763b8a4dac85b3b9a2d7079662382cf40dd0754a7a",
        "content_sha256": ("0e2605045eabaa2b5c4a0c858ad01e7850342e5667f60198bd5238af0a558ab4"),
        "byte_count": 97,
        "privacy": "private_provider_payload",
    }
    assert receipt["outputs"] == [
        {
            "output_index": 0,
            "role": "generated_image",
            "content_ref": "56c9cca7f348bf79614175082107b860d1d07450f0abf1374580e1c31c477dcd",
            "content_sha256": ("f4ac54d2f0e8afef34223fc87ad221d6b3ebfdb374425a394e4c92a6b4f837eb"),
            "byte_count": len(_OUTPUT_BYTES),
            "media_type_claim": None,
        }
    ]
    assert receipt["cost"] == {
        "state": "reported",
        "amount": "0.03125",
        "currency": "USD",
        "provenance": "provider_receipt",
    }
    assert receipt["latency"] == {
        "milliseconds": 2450,
        "boundary": "submit_to_response_received",
    }
    validate_provider_artifact(receipt)

    response_repr = repr(_success_response())
    decoded_repr = repr(decoded)
    assert "b64_json" not in response_repr
    assert "Z2VuZXJhdGVkLWltYWdlLXYx" not in response_repr
    assert "generated-image-v1" not in decoded_repr
    assert "b64_json" not in decoded_repr


def test_concurrent_and_exact_dispatch_replay_send_the_attempt_at_most_once(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    start = threading.Barrier(3)
    transport_entered = threading.Event()
    release_transport = threading.Event()
    outcomes: Queue[OpenRouterDispatchResult | BaseException] = Queue()
    sends = 0
    sends_lock = threading.Lock()

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        nonlocal sends
        assert body == _WIRE_BODY
        assert bearer_token == "test-bearer-token"
        with sends_lock:
            sends += 1
        transport_entered.set()
        assert release_transport.wait(timeout=10)
        return _success_response()

    def worker() -> None:
        try:
            start.wait()
            outcomes.put(
                dispatch_openrouter_attempt(
                    AttemptJournal(journal.path),
                    attempt,
                    capability,
                    prepared,
                    credential_resolver=lambda _: "test-bearer-token",
                    transport=transport,
                    dispatch_claim_id=_DISPATCH_CLAIM_ID,
                    claimed_at=_CLAIMED_AT,
                    recorded_at=_RECORDED_AT,
                )
            )
        except BaseException as error:  # noqa: BLE001 - race outcomes are asserted below
            outcomes.put(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    assert transport_entered.wait(timeout=10)

    loser = outcomes.get(timeout=10)
    assert isinstance(loser, OpenRouterDispatchResult)
    assert loser.kind == "not_sent"
    assert loser.event is None
    assert loser.decoded is None
    release_transport.set()
    winner = outcomes.get(timeout=10)
    assert isinstance(winner, OpenRouterDispatchResult)
    assert winner.kind == "response_received"
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    exact_replay = dispatch_openrouter_attempt(
        AttemptJournal(journal.path),
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )
    assert exact_replay.kind == "not_sent"
    assert exact_replay.state.state == "response_received"
    assert exact_replay.event is None
    assert exact_replay.decoded is None
    assert sends == 1


def test_ambiguous_transport_is_durably_unknown_and_never_retried(
    tmp_path: Path,
) -> None:
    secret = "sk-or-v1-THIS-IS-A-LITERAL-SECRET-SENTINEL-1234567890"
    journal, attempt, capability, prepared = _seed_dispatch(
        tmp_path,
        forbidden_secrets=(secret,),
    )
    sends = 0

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        nonlocal sends
        assert body == _WIRE_BODY
        assert bearer_token == secret
        sends += 1
        raise TimeoutError(f"ambiguous socket outcome carrying {secret}")

    first = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: secret,
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )
    replay = dispatch_openrouter_attempt(
        AttemptJournal(journal.path, forbidden_secrets=(secret,)),
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: secret,
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert first.kind == "outcome_unknown"
    assert first.state.state == "outcome_unknown"
    assert first.event is not None
    assert dict(first.event.detail) == {
        "kind": "outcome_unknown",
        "failure_stage": "dispatch",
        "failure_code": "ambiguous_transport",
        "provider_handle": None,
    }
    assert replay.kind == "not_sent"
    assert replay.state.state == "outcome_unknown"
    assert replay.event is None
    assert replay.decoded is None
    assert sends == 1
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "outcome_unknown",
    ]

    persisted = json.dumps(
        {
            "attempt": provider_to_json(attempt),
            "capability": provider_to_json(capability),
            "normalized_request": provider_to_json(prepared.normalized_request),
            "events": [
                provider_to_json(event) for event in journal.read_events(attempt.attempt_id)
            ],
        },
        sort_keys=True,
    )
    assert secret not in persisted
    assert secret not in repr(first)
    assert secret not in repr(replay)


def test_valid_response_recovers_from_concurrent_outcome_unknown_without_resend(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    sends = 0

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        nonlocal sends
        assert body == _WIRE_BODY
        assert bearer_token == "test-bearer-token"
        sends += 1
        submitted = journal.read_state(attempt.attempt_id)
        assert submitted.state == "submitted"
        assert submitted.head_event_id is not None
        journal.append_event(
            _event(
                attempt_id=attempt.attempt_id,
                sequence=submitted.next_sequence,
                state="outcome_unknown",
                recorded_at=_RECORDED_AT,
                detail={
                    "kind": "outcome_unknown",
                    "failure_stage": "dispatch",
                    "failure_code": "ambiguous_transport",
                    "provider_handle": None,
                },
            ),
            expected_head_event_id=submitted.head_event_id,
            expected_next_sequence=submitted.next_sequence,
        )
        return _success_response()

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "response_received"
    assert result.state.state == "response_received"
    assert result.decoded is not None
    assert sends == 1
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "outcome_unknown",
        "response_received",
    ]
    stored = journal.read_provider_response(attempt.attempt_id)
    assert stored.receipt == result.decoded.receipt
    assert stored.raw_response_bytes == _RESPONSE_BODY
    assert stored.output_bytes == (_OUTPUT_BYTES,)


def test_credential_failure_is_preflight_before_claim_or_transport_and_is_sanitized(
    tmp_path: Path,
) -> None:
    secret = "sk-or-v1-CREDENTIAL-RESOLVER-SECRET-1234567890"
    journal, attempt, capability, prepared = _seed_dispatch(
        tmp_path,
        forbidden_secrets=(secret,),
    )
    transport_calls = 0

    def credential_resolver(_: str) -> str:
        raise RuntimeError(f"keychain lookup exposed {secret}")

    def transport(**_: object) -> OpenRouterHttpResponse:
        nonlocal transport_calls
        transport_calls += 1
        return _success_response()

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=credential_resolver,
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.state.state == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "preflight",
        "failure_code": "credential_unavailable",
    }
    assert result.decoded is None
    assert transport_calls == 0
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "failed",
    ]
    assert secret not in repr(result)
    assert secret not in json.dumps(provider_to_json(result.event), sort_keys=True)


def test_complete_non_2xx_is_provider_failure_without_publishing_or_leaking_response(
    tmp_path: Path,
) -> None:
    secret = "sk-or-v1-PROVIDER-ERROR-SECRET-1234567890"
    journal, attempt, capability, prepared = _seed_dispatch(
        tmp_path,
        forbidden_secrets=(secret,),
    )
    body = b'{"error":{"code":429,"message":"rate limited with ' + secret.encode("ascii") + b'"}}'
    response = OpenRouterHttpResponse(
        status=429,
        headers={
            "authorization": f"Bearer {secret}",
            "content-type": "application/json",
            "retry-after": "30",
        },
        body=body,
        elapsed_milliseconds=17,
    )

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: secret,
        transport=lambda **_: response,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.state.state == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "provider",
        "failure_code": "openrouter_http_429",
    }
    assert result.decoded is None
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(attempt.attempt_id)
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "failed",
    ]
    for projected in (repr(response), repr(result), repr(result.event)):
        assert secret not in projected
        assert body.decode("utf-8") not in projected
        assert "authorization" not in projected.lower()


def test_fewer_outputs_fails_output_validation_without_publication_or_response_event(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    short_response = OpenRouterHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=b'{"created":1786930000,"data":[],"usage":{"cost":0.03125}}',
        elapsed_milliseconds=2200,
    )

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=lambda **_: short_response,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.state.state == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "output_validation",
        "failure_code": "output_count_mismatch",
    }
    assert result.decoded is None
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(attempt.attempt_id)
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "failed",
    ]


def test_evidence_commit_failure_is_output_validation_and_never_claims_response_received(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)

    def fail_evidence_commit(*_: object, **__: object) -> None:
        assert journal.read_state(attempt.attempt_id).state == "submitted"
        raise AttemptJournalError("durable response store unavailable")

    monkeypatch.setattr(
        AttemptJournal,
        "publish_provider_response",
        fail_evidence_commit,
    )

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=lambda **_: _success_response(),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.state.state == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "output_validation",
        "failure_code": "output_persistence_failed",
    }
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "failed",
    ]


def test_dispatch_recovers_response_received_after_lost_evidence_commit_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    original_commit = AttemptJournal._commit
    lost_ack = False
    sends = 0

    def commit_then_lose_response_ack(connection: sqlite3.Connection) -> None:
        nonlocal lost_ack
        has_response = (
            connection.execute("SELECT count(*) FROM provider_responses").fetchone()[0] == 1
        )
        original_commit(connection)
        if has_response and not lost_ack:
            lost_ack = True
            raise AttemptJournalError("simulated lost response commit acknowledgement")

    def transport(**_: object) -> OpenRouterHttpResponse:
        nonlocal sends
        sends += 1
        return _success_response()

    monkeypatch.setattr(
        AttemptJournal,
        "_commit",
        staticmethod(commit_then_lose_response_ack),
    )
    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert lost_ack is True
    assert sends == 1
    assert result.kind == "response_received"
    assert result.state.state == "response_received"
    assert result.decoded is not None
    stored = AttemptJournal(journal.path).read_provider_response(attempt.attempt_id)
    assert stored.receipt == result.decoded.receipt
    assert stored.raw_response_bytes == _RESPONSE_BODY
    assert stored.output_bytes == (_OUTPUT_BYTES,)


def test_unsupported_reconcile_does_no_io_and_appends_no_self_event(tmp_path: Path) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=lambda **_: (_ for _ in ()).throw(TimeoutError("ambiguous")),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )
    before = journal.read_events(attempt.attempt_id)
    transport_calls = 0

    def transport(*args: object, **kwargs: object) -> None:
        nonlocal transport_calls
        del args, kwargs
        transport_calls += 1

    with pytest.raises(OpenRouterAdapterError) as captured:
        reconcile_openrouter_attempt(journal, attempt.attempt_id, transport=transport)

    assert captured.value.code == "reconciliation_unsupported"
    assert transport_calls == 0
    assert journal.read_events(attempt.attempt_id) == before
    assert journal.read_state(attempt.attempt_id).state == "outcome_unknown"


@pytest.mark.parametrize(
    "body",
    (
        b"\xff",
        b'{"data":[],"data":[]}',
        b'{"data":[NaN]}',
        b'{"data":[{"b64_json":"%%%"}]}',
    ),
)
def test_decoder_rejects_non_strict_or_invalid_provider_payload_without_echoing_it(
    tmp_path: Path,
    body: bytes,
) -> None:
    _, attempt, _, prepared = _seed_dispatch(tmp_path)
    secret = "sk-or-v1-ERROR-PROJECTION-SECRET-1234567890"
    response = OpenRouterHttpResponse(
        status=200,
        headers={"authorization": f"Bearer {secret}"},
        body=body + secret.encode("ascii"),
        elapsed_milliseconds=1,
    )

    response_repr = repr(response)
    assert secret not in response_repr
    assert "authorization" not in response_repr.lower()
    decoded_body = body.decode("utf-8", errors="ignore")
    if decoded_body:
        assert decoded_body not in response_repr

    with pytest.raises(OpenRouterAdapterError) as captured:
        decode_openrouter_response(
            attempt,
            prepared,
            response,
            received_at=_RECORDED_AT,
        )

    assert captured.value.code == "invalid_provider_response"
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def test_dispatch_rejects_resealed_normalized_semantic_drift_before_claim(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    draft = provider_to_json(prepared.normalized_request)
    draft.pop("normalized_request_id")
    draft["operation_inputs"][0]["capability_id"] = "e" * 64
    forged = seal_provider_artifact(draft)
    assert isinstance(forged, NormalizedProviderRequest)
    forged_prepared = replace(prepared, normalized_request=forged)

    with pytest.raises(OpenRouterAdapterError) as raised:
        dispatch_openrouter_attempt(
            journal,
            attempt,
            capability,
            forged_prepared,
            credential_resolver=lambda _: "test-bearer-token",
            transport=lambda **_: pytest.fail("normalized drift cannot send"),
            dispatch_claim_id=_DISPATCH_CLAIM_ID,
            claimed_at=_CLAIMED_AT,
            recorded_at=_RECORDED_AT,
        )

    assert raised.value.code == "prepared_request_invalid"
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == ["prepared"]


def test_dispatch_replay_rechecks_packet_content_ref_against_wire_bytes() -> None:
    _, _, prepared, _ = _prepared_case()
    packet = openrouter_module.intent_packet_to_json(prepared.intent_packet)
    wrong_ref = "e" * 64
    packet["source"]["content_ref"] = wrong_ref
    source_input = packet["generation_request"]["operation_inputs"][0]
    source_input["original_artifact"]["content_ref"] = wrong_ref
    source_input["delivered_artifact"]["content_ref"] = wrong_ref
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    packet_artifact = openrouter_module.intent_packet_from_json(packet)

    normalized = provider_to_json(prepared.normalized_request)
    normalized.pop("normalized_request_id")
    normalized["intent_packet_id"] = packet["intent_packet_id"]
    normalized["operation_inputs"] = copy.deepcopy(packet["generation_request"]["operation_inputs"])
    normalized_artifact = seal_provider_artifact(normalized)
    assert isinstance(normalized_artifact, NormalizedProviderRequest)
    forged_prepared = replace(
        prepared,
        intent_packet=packet_artifact,
        normalized_request=normalized_artifact,
    )

    with pytest.raises(OpenRouterAdapterError) as raised:
        openrouter_module._validate_prepared_request(forged_prepared)  # noqa: SLF001

    assert raised.value.code == "prepared_request_invalid"


def test_dispatch_rejects_resealed_capability_operation_drift_before_claim(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    draft = provider_to_json(capability)
    draft.pop("capability_snapshot_id")
    draft["operation_input_capabilities"][0]["capability_id"] = "e" * 64
    forged = seal_provider_artifact(draft)
    assert isinstance(forged, ProviderCapabilitySnapshot)

    with pytest.raises(OpenRouterAdapterError) as raised:
        dispatch_openrouter_attempt(
            journal,
            attempt,
            forged,
            prepared,
            credential_resolver=lambda _: "test-bearer-token",
            transport=lambda **_: pytest.fail("capability drift cannot send"),
            dispatch_claim_id=_DISPATCH_CLAIM_ID,
            claimed_at=_CLAIMED_AT,
            recorded_at=_RECORDED_AT,
        )

    assert raised.value.code in {"capability_mismatch", "dispatch_contract_mismatch"}
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == ["prepared"]


def test_dispatch_rejects_caller_attempt_that_differs_from_journal_authority(
    tmp_path: Path,
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    forged = replace(attempt, created_at="2026-08-16T20:30:03.5Z")

    with pytest.raises(OpenRouterAdapterError) as raised:
        dispatch_openrouter_attempt(
            journal,
            forged,
            capability,
            prepared,
            credential_resolver=lambda _: "test-bearer-token",
            transport=lambda **_: pytest.fail("caller/journal drift cannot send"),
            dispatch_claim_id=_DISPATCH_CLAIM_ID,
            claimed_at=_CLAIMED_AT,
            recorded_at=_RECORDED_AT,
        )

    assert raised.value.code == "dispatch_contract_mismatch"
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == ["prepared"]


def test_response_provenance_field_is_not_laundered_as_undisclosed(tmp_path: Path) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    body = json.dumps(
        {
            "created": 1_786_930_000,
            "model": "different/model",
            "data": [{"b64_json": "Z2VuZXJhdGVkLWltYWdlLXYx"}],
        },
        separators=(",", ":"),
    ).encode()
    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=lambda **_: OpenRouterHttpResponse(200, {}, body, 1),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "provenance",
        "failure_code": "provider_provenance_conflict",
    }


def test_dispatch_rejects_retrograde_evidence_time_before_claim(tmp_path: Path) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)

    with pytest.raises(OpenRouterAdapterError) as raised:
        dispatch_openrouter_attempt(
            journal,
            attempt,
            capability,
            prepared,
            credential_resolver=lambda _: "test-bearer-token",
            transport=lambda **_: pytest.fail("retrograde evidence cannot send"),
            dispatch_claim_id=_DISPATCH_CLAIM_ID,
            claimed_at=_CLAIMED_AT,
            recorded_at="2000-01-01T00:00:00Z",
        )

    assert raised.value.code == "dispatch_timestamp_invalid"
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == ["prepared"]


@pytest.mark.parametrize("cost_lexeme", ("1e2000000", "0." + "1" * 1_000))
def test_cost_number_is_bounded_before_decimal_expansion(tmp_path: Path, cost_lexeme: str) -> None:
    _, attempt, _, prepared = _seed_dispatch(tmp_path)
    response = OpenRouterHttpResponse(
        200,
        {},
        (
            '{"created":1786930000,"data":[{"b64_json":'
            '"Z2VuZXJhdGVkLWltYWdlLXYx"}],"usage":{"cost":'
            f"{cost_lexeme}" + "}}"
        ).encode(),
        1,
    )

    with pytest.raises(OpenRouterAdapterError) as raised:
        decode_openrouter_response(attempt, prepared, response, received_at=_RECORDED_AT)

    assert raised.value.code == "invalid_provider_response"


def test_json_structural_budget_rejects_before_materializing_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"{" + b'"a":0,' * 10_100 + b'"z":0}'
    monkeypatch.setattr(
        openrouter_module.json,
        "loads",
        lambda *_args, **_kwargs: pytest.fail("oversize structure must fail before json.loads"),
    )

    with pytest.raises(ValueError):
        openrouter_module._parse_json(raw, max_bytes=len(raw))  # noqa: SLF001


def test_prepare_checks_cumulative_data_url_budget_before_second_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet, artifacts = _valid_artifact_chain()
    capability = copy.deepcopy(_artifact(artifacts, CAPABILITY_VERSION))
    content = _bind_bytes(packet)
    first_url_chars = openrouter_module._data_url_character_count(  # noqa: SLF001
        _SOURCE_BYTES, "image/png"
    )
    monkeypatch.setattr(openrouter_module, "_DATA_URL_TOTAL_MAX_CHARS", first_url_chars)
    original_data_url = openrouter_module._data_url  # noqa: SLF001
    encoded = 0

    def counted_data_url(payload: bytes, media_type: str) -> str:
        nonlocal encoded
        encoded += 1
        return original_data_url(payload, media_type)

    monkeypatch.setattr(openrouter_module, "_data_url", counted_data_url)

    with pytest.raises(OpenRouterAdapterError) as raised:
        prepare_openrouter_request(
            packet,
            capability,
            selected_route_id="openrouter-primary",
            resolve_content=content.__getitem__,
        )

    assert raised.value.code == "request_too_large"
    assert encoded == 1


def test_prepare_rejects_declared_oversize_source_before_resolver() -> None:
    packet, artifacts = _valid_artifact_chain()
    capability = copy.deepcopy(_artifact(artifacts, CAPABILITY_VERSION))
    _bind_bytes(packet)
    packet["generation_request"]["operation_inputs"][0]["delivered_artifact"]["byte_count"] = (
        16_777_217
    )
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    resolutions = 0

    def resolve_content(_: str) -> bytes:
        nonlocal resolutions
        resolutions += 1
        return _SOURCE_BYTES

    with pytest.raises(OpenRouterAdapterError) as raised:
        prepare_openrouter_request(
            packet,
            capability,
            selected_route_id="openrouter-primary",
            resolve_content=resolve_content,
        )

    assert raised.value.code == "content_too_large"
    assert resolutions == 0


def test_active_bearer_reflection_never_reaches_durable_evidence(tmp_path: Path) -> None:
    secret = "opaque-active-bearer-secret-1234567890"
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path, forbidden_secrets=(secret,))
    body = json.dumps(
        {
            "created": 1_786_930_000,
            "data": [{"b64_json": "Z2VuZXJhdGVkLWltYWdlLXYx"}],
            "echo": secret,
        },
        separators=(",", ":"),
    ).encode()
    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: secret,
        transport=lambda **_: OpenRouterHttpResponse(200, {}, body, 1),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "output_validation",
        "failure_code": "credential_material_detected",
    }
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(attempt.attempt_id)
    assert secret not in repr(result)


def test_https_transport_rejects_incomplete_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Headers:
        @staticmethod
        def get_all(name: str, default: list[str]) -> list[str]:
            return ["999"] if name.lower() == "content-length" else default

    class TruncatedResponse:
        length = 999
        headers = Headers()

        def __enter__(self) -> TruncatedResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def getcode() -> int:
            return 200

        @staticmethod
        def read(_: int) -> bytes:
            return b'{"created":1,"data":[]}'

        @staticmethod
        def isclosed() -> bool:
            return False

    class Opener:
        @staticmethod
        def open(*_: object, **__: object) -> TruncatedResponse:
            return TruncatedResponse()

    monkeypatch.setattr(openrouter_module.urllib.request, "build_opener", lambda *_: Opener())

    with pytest.raises(OpenRouterAdapterError) as raised:
        openrouter_https_transport(body=b"{}", bearer_token="test-bearer-token")

    assert raised.value.code == "transport_failed"


def test_prepare_rejects_credential_like_prompt_before_content_resolution() -> None:
    packet, artifacts = _valid_artifact_chain()
    capability = copy.deepcopy(_artifact(artifacts, CAPABILITY_VERSION))
    _bind_bytes(packet)
    packet["instruction"] = "Use credential sk-or-v1-THIS-MUST-NEVER-BE-SENT-1234567890"
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    resolutions = 0

    def resolve_content(_: str) -> bytes:
        nonlocal resolutions
        resolutions += 1
        return _SOURCE_BYTES

    with pytest.raises(OpenRouterAdapterError) as raised:
        prepare_openrouter_request(
            packet,
            capability,
            selected_route_id="openrouter-primary",
            resolve_content=resolve_content,
        )

    assert raised.value.code == "credential_material_detected"
    assert resolutions == 0


@pytest.mark.parametrize(
    "token",
    ('opaque"active-bearer-123456', "café-active-bearer-secret-123456"),
)
def test_non_bearer_grammar_credentials_fail_before_claim_or_send(
    tmp_path: Path, token: str
) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    sends = 0

    def transport(**_: object) -> OpenRouterHttpResponse:
        nonlocal sends
        sends += 1
        return _success_response()

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: token,
        transport=transport,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.event is not None
    assert result.event.detail["failure_code"] == "credential_unavailable"
    assert sends == 0
    assert [event.state for event in journal.read_events(attempt.attempt_id)] == [
        "prepared",
        "failed",
    ]


def test_active_bearer_inside_unaligned_input_bytes_is_found_before_claim() -> None:
    secret = "opaque-active-bearer-secret-1234567890"
    source_bytes = b"\x89PNG\r\n\x1a\nXX" + secret.encode()
    packet, artifacts = _valid_artifact_chain()
    capability = copy.deepcopy(_artifact(artifacts, CAPABILITY_VERSION))
    content = _bind_bytes(packet)
    original_ref = packet["source"]["content_ref"]
    del content[original_ref]
    source_ref = blake3(source_bytes).hexdigest()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    packet["source"]["content_ref"] = source_ref
    packet["source"]["content_sha256"] = source_sha256
    packet["operation"]["payload"]["source_raster"]["source_content_sha256"] = source_sha256
    source_input = packet["generation_request"]["operation_inputs"][0]
    for field in ("original_artifact", "delivered_artifact"):
        source_input[field]["content_ref"] = source_ref
        source_input[field]["content_sha256"] = source_sha256
    source_input["delivered_artifact"]["byte_count"] = len(source_bytes)
    content[source_ref] = source_bytes
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    prepared = prepare_openrouter_request(
        packet,
        capability,
        selected_route_id="openrouter-primary",
        resolve_content=content.__getitem__,
    )
    assert base64.b64encode(secret.encode()) not in prepared.wire_body

    with pytest.raises(OpenRouterAdapterError) as raised:
        openrouter_module._validate_prepared_request(  # noqa: SLF001
            prepared,
            active_credential_variants=openrouter_module._active_credential_variants(  # noqa: SLF001
                secret
            ),
        )

    assert raised.value.code == "credential_material_detected"


def test_unicode_escaped_active_bearer_is_scanned_before_durable_evidence(
    tmp_path: Path,
) -> None:
    secret = "opaque-active-bearer-secret-1234567890"
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path, forbidden_secrets=(secret,))
    body = (
        b'{"created":1786930000,"data":[{"b64_json":'
        b'"Z2VuZXJhdGVkLWltYWdlLXYx"}],"usage":{"cost":0.03,"note":"'
        b'\\u006fpaque-active-bearer-secret-1234567890"}}'
    )
    assert secret.encode() not in body
    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: secret,
        transport=lambda **_: OpenRouterHttpResponse(200, {}, body, 1),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.event is not None
    assert result.event.detail["failure_code"] == "credential_material_detected"
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(attempt.attempt_id)


def test_escaped_lone_surrogate_becomes_stable_output_failure(tmp_path: Path) -> None:
    journal, attempt, capability, prepared = _seed_dispatch(tmp_path)
    body = (
        b'{"created":1786930000,"data":[{"b64_json":'
        b'"Z2VuZXJhdGVkLWltYWdlLXYx"}],"usage":{"cost":0.03,"note":"\\ud800"}}'
    )

    result = dispatch_openrouter_attempt(
        journal,
        attempt,
        capability,
        prepared,
        credential_resolver=lambda _: "test-bearer-token",
        transport=lambda **_: OpenRouterHttpResponse(200, {}, body, 1),
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        recorded_at=_RECORDED_AT,
    )

    assert result.kind == "failed"
    assert result.event is not None
    assert dict(result.event.detail) == {
        "kind": "failed",
        "failure_stage": "output_validation",
        "failure_code": "invalid_provider_response",
    }
    assert journal.read_state(attempt.attempt_id).state == "failed"


def test_https_transport_pins_origin_headers_timeout_and_redirect_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = b'{"created":1,"data":[]}'
    observed: dict[str, object] = {}

    class Headers:
        @staticmethod
        def get_all(name: str, default: list[str]) -> list[str]:
            return [str(len(response_body))] if name.lower() == "content-length" else default

    class CompleteResponse:
        length = len(response_body)
        headers = Headers()

        def __enter__(self) -> CompleteResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def getcode() -> int:
            return 200

        @staticmethod
        def read(_: int) -> bytes:
            return response_body

        @staticmethod
        def isclosed() -> bool:
            return True

    class Opener:
        @staticmethod
        def open(request: object, *, timeout: int) -> CompleteResponse:
            observed["request"] = request
            observed["timeout"] = timeout
            return CompleteResponse()

    def build_opener(handler: object) -> Opener:
        observed["handler"] = handler
        return Opener()

    monkeypatch.setattr(openrouter_module.urllib.request, "build_opener", build_opener)
    response = openrouter_https_transport(body=b"{}", bearer_token="test-bearer-token")

    request = observed["request"]
    assert isinstance(request, openrouter_module.urllib.request.Request)
    assert request.full_url == "https://openrouter.ai/api/v1/images"
    assert request.get_method() == "POST"
    assert request.data == b"{}"
    assert request.get_header("Authorization") == "Bearer test-bearer-token"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert isinstance(observed["handler"], openrouter_module._NoRedirect)  # noqa: SLF001
    assert observed["timeout"] == 180
    assert response.status == 200
    assert response.body == response_body
