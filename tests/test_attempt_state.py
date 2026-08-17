"""RED tests for the ADR-0014 generation-attempt transition reducer.

The reducer is intentionally pure.  Durable compare-and-append, dispatch claims, provider
reconciliation, retries, and fallbacks remain later single-concern layers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

import pytest

from moodboard.attempt_state import AttemptState, AttemptStateError, reduce_attempt_events
from moodboard.provider_artifacts import (
    ATTEMPT_VERSION,
    EVENT_VERSION,
    GenerationAttempt,
    GenerationAttemptEvent,
    compute_provider_request_key,
    from_json_dict,
    seal_provider_artifact,
    to_json_dict,
)

JsonObject = dict[str, Any]

_ATTEMPT_ID = "30000000-0000-4000-8000-000000000001"
_OTHER_ATTEMPT_ID = "30000000-0000-4000-8000-000000000002"
_RUN_ID = "30000000-0000-4000-8000-000000000003"

_LEGAL_EDGES = {
    ("prepared", "failed"),
    ("prepared", "cancelled"),
    ("prepared", "submitted"),
    ("submitted", "response_received"),
    ("submitted", "failed"),
    ("submitted", "cancelled"),
    ("submitted", "outcome_unknown"),
    ("outcome_unknown", "response_received"),
    ("outcome_unknown", "failed"),
    ("outcome_unknown", "cancelled"),
    ("response_received", "succeeded"),
    ("response_received", "failed"),
}
_STATE_NAMES = (
    "prepared",
    "submitted",
    "outcome_unknown",
    "response_received",
    "succeeded",
    "failed",
    "cancelled",
)
_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _attempt_document(attempt_id: str = _ATTEMPT_ID) -> JsonObject:
    normalized_request_id = _digest("2")
    adapter_revision = "moodboard.openrouter-images.v1"
    return {
        "schema_version": ATTEMPT_VERSION,
        "attempt_id": attempt_id,
        "generation_run_id": _RUN_ID,
        "intent_packet_id": _digest("1"),
        "ordinal": 1,
        "retry_of": None,
        "fallback_of": None,
        "requested_provider": "openrouter",
        "requested_model": "qwen/qwen-image-3",
        "provider_route_policy_id": _digest("3"),
        "selected_route_id": "openrouter-alibaba",
        "adapter_revision": adapter_revision,
        "capability_snapshot_id": _digest("4"),
        "normalized_request_id": normalized_request_id,
        "normalized_request_ref": {
            "schema_version": "moodboard.normalized-provider-request.v1",
            "artifact_id": normalized_request_id,
            "content_ref": _digest("5"),
            "content_sha256": _digest("6"),
            "byte_count": 1024,
        },
        "request_key_sha256": compute_provider_request_key(
            generation_run_id=_RUN_ID,
            attempt_id=attempt_id,
            intent_packet_id=_digest("1"),
            adapter_revision=adapter_revision,
            normalized_request_id=normalized_request_id,
        ),
        "created_at": "2026-08-16T21:00:00Z",
    }


def _detail(
    state: str,
    *,
    predecessor: str | None = None,
    provider_handle: str | None = None,
) -> JsonObject:
    if state == "prepared":
        return {"kind": "prepared"}
    if state == "submitted":
        return {"kind": "submitted", "provider_handle": provider_handle}
    if state == "outcome_unknown":
        return {
            "kind": "outcome_unknown",
            "failure_stage": "dispatch",
            "failure_code": "transport_ambiguous",
            "provider_handle": provider_handle,
        }
    if state == "response_received":
        return {"kind": "response_received", "provider_receipt_id": _digest("7")}
    if state == "succeeded":
        return {"kind": "succeeded", "output_occurrence_ids": [_digest("8")]}
    if state == "failed":
        return {
            "kind": "failed",
            "failure_stage": "provider",
            "failure_code": "provider_failed",
        }
    if state == "cancelled":
        return {
            "kind": "cancelled",
            "cancellation_stage": "provider",
            "cancellation_code": "cancelled_without_output",
            "authority": (
                "local_pre_dispatch"
                if predecessor == "prepared"
                else "provider_confirmed_no_output"
            ),
        }
    raise AssertionError(f"unknown fixture state {state!r}")


def _event(
    state: str,
    sequence: int,
    *,
    attempt_id: str = _ATTEMPT_ID,
    predecessor: str | None = None,
    provider_handle: str | None = None,
    detail: JsonObject | None = None,
) -> JsonObject:
    sealed = seal_provider_artifact(
        {
            "schema_version": EVENT_VERSION,
            "attempt_id": attempt_id,
            "sequence": sequence,
            "state": state,
            "recorded_at": f"2026-08-16T21:00:{sequence:02d}Z",
            "detail": detail
            if detail is not None
            else _detail(
                state,
                predecessor=predecessor,
                provider_handle=provider_handle,
            ),
        }
    )
    return to_json_dict(sealed)


def _prefix(state: str, *, submitted_handle: str | None = None) -> list[JsonObject]:
    events = [_event("prepared", 1)]
    if state == "prepared":
        return events
    events.append(
        _event(
            "submitted",
            2,
            predecessor="prepared",
            provider_handle=submitted_handle,
        )
    )
    if state == "submitted":
        return events
    if state == "outcome_unknown":
        events.append(
            _event(
                "outcome_unknown",
                3,
                predecessor="submitted",
                provider_handle=submitted_handle,
            )
        )
        return events
    if state == "response_received":
        events.append(_event("response_received", 3, predecessor="submitted"))
        return events
    if state in _TERMINAL_STATES:
        if state == "succeeded":
            events.append(_event("response_received", 3, predecessor="submitted"))
            events.append(_event("succeeded", 4, predecessor="response_received"))
        else:
            events.append(_event(state, 3, predecessor="submitted"))
        return events
    raise AssertionError(f"unknown fixture state {state!r}")


def _candidate(state: str, sequence: int, predecessor: str) -> JsonObject:
    return _event(state, sequence, predecessor=predecessor)


def test_empty_history_returns_the_unclaimed_initial_head() -> None:
    snapshot = reduce_attempt_events(_attempt_document(), [])

    assert snapshot == AttemptState(
        attempt_id=_ATTEMPT_ID,
        state=None,
        terminal=False,
        head_event_id=None,
        next_sequence=1,
        last_recorded_at=None,
        provider_handle=None,
    )


@pytest.mark.parametrize(
    ("source", "target"),
    sorted(_LEGAL_EDGES),
)
def test_every_adr_0014_legal_transition_is_accepted(source: str, target: str) -> None:
    history = _prefix(source)
    history.append(_candidate(target, len(history) + 1, source))

    snapshot = reduce_attempt_events(_attempt_document(), history)

    assert snapshot.state == target
    assert snapshot.terminal is (target in _TERMINAL_STATES)
    assert snapshot.next_sequence == len(history) + 1
    assert snapshot.head_event_id == history[-1]["attempt_event_id"]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source in _STATE_NAMES
        for target in _STATE_NAMES
        if (source, target) not in _LEGAL_EDGES
    ],
)
def test_every_transition_outside_the_closed_adr_graph_is_rejected(
    source: str,
    target: str,
) -> None:
    history = _prefix(source)
    history.append(_candidate(target, len(history) + 1, source))

    with pytest.raises(AttemptStateError, match="transition|terminal"):
        reduce_attempt_events(_attempt_document(), history)


@pytest.mark.parametrize("first_state", [state for state in _STATE_NAMES if state != "prepared"])
def test_first_event_must_be_prepared_at_sequence_one(first_state: str) -> None:
    with pytest.raises(AttemptStateError, match="first.*prepared"):
        reduce_attempt_events(
            _attempt_document(),
            [_event(first_state, 1, predecessor=None)],
        )


@pytest.mark.parametrize(
    "history",
    [
        [_event("prepared", 2)],
        [_event("prepared", 1), _event("submitted", 1, predecessor="prepared")],
        [_event("prepared", 1), _event("submitted", 3, predecessor="prepared")],
        [_event("submitted", 2, predecessor="prepared"), _event("prepared", 1)],
    ],
)
def test_sequence_order_is_supplied_append_order_not_a_sortable_hint(
    history: list[JsonObject],
) -> None:
    with pytest.raises(AttemptStateError, match="sequence"):
        reduce_attempt_events(_attempt_document(), history)


def test_event_for_another_attempt_fails_closed() -> None:
    with pytest.raises(AttemptStateError, match="another attempt"):
        reduce_attempt_events(
            _attempt_document(),
            [_event("prepared", 1, attempt_id=_OTHER_ATTEMPT_ID)],
        )


@pytest.mark.parametrize("source", ["prepared", "submitted", "outcome_unknown"])
def test_cancellation_authority_is_predecessor_sensitive(source: str) -> None:
    history = _prefix(source)
    wrong_authority = (
        "provider_confirmed_no_output" if source == "prepared" else "local_pre_dispatch"
    )
    history.append(
        _event(
            "cancelled",
            len(history) + 1,
            predecessor=source,
            detail={
                "kind": "cancelled",
                "cancellation_stage": "reconciliation",
                "cancellation_code": "wrong_authority",
                "authority": wrong_authority,
            },
        )
    )

    with pytest.raises(AttemptStateError, match="authority"):
        reduce_attempt_events(_attempt_document(), history)


@pytest.mark.parametrize(
    ("source", "failure_stage"),
    [
        ("prepared", "output_validation"),
        ("submitted", "preflight"),
        ("outcome_unknown", "provider"),
        ("response_received", "dispatch"),
    ],
)
def test_failure_stage_is_evidence_not_an_unwritten_transition_guard(
    source: str,
    failure_stage: str,
) -> None:
    history = _prefix(source)
    history.append(
        _event(
            "failed",
            len(history) + 1,
            predecessor=source,
            detail={
                "kind": "failed",
                "failure_stage": failure_stage,
                "failure_code": "registered_failure",
            },
        )
    )

    assert reduce_attempt_events(_attempt_document(), history).state == "failed"


@pytest.mark.parametrize(
    ("source", "cancellation_stage"),
    [
        ("prepared", "reconciliation"),
        ("submitted", "preflight"),
        ("outcome_unknown", "dispatch"),
    ],
)
def test_cancellation_stage_is_evidence_while_authority_remains_normative(
    source: str,
    cancellation_stage: str,
) -> None:
    history = _prefix(source)
    history.append(
        _event(
            "cancelled",
            len(history) + 1,
            predecessor=source,
            detail={
                "kind": "cancelled",
                "cancellation_stage": cancellation_stage,
                "cancellation_code": "registered_cancellation",
                "authority": (
                    "local_pre_dispatch" if source == "prepared" else "provider_confirmed_no_output"
                ),
            },
        )
    )

    assert reduce_attempt_events(_attempt_document(), history).state == "cancelled"


def test_known_provider_handle_cannot_switch_calls_but_survives_later_nulls() -> None:
    drift = _prefix("submitted", submitted_handle="provider-call-a")
    drift.append(
        _event(
            "outcome_unknown",
            3,
            predecessor="submitted",
            provider_handle="provider-call-b",
        )
    )
    with pytest.raises(AttemptStateError, match="provider handle"):
        reduce_attempt_events(_attempt_document(), drift)

    learned_later = _prefix("submitted", submitted_handle=None)
    learned_later.append(
        _event(
            "outcome_unknown",
            3,
            predecessor="submitted",
            provider_handle="provider-call-a",
        )
    )
    learned_later.append(_event("response_received", 4, predecessor="outcome_unknown"))
    snapshot = reduce_attempt_events(_attempt_document(), learned_later)
    assert snapshot.provider_handle == "provider-call-a"


def test_history_is_bounded_before_an_untrusted_iterable_can_run_forever() -> None:
    yielded = 0

    def too_long() -> Iterable[JsonObject]:
        nonlocal yielded
        while True:
            yielded += 1
            yield _event("prepared", yielded)

    with pytest.raises(AttemptStateError, match="at most five"):
        reduce_attempt_events(_attempt_document(), too_long())
    assert yielded == 6


def test_each_yielded_mapping_is_validated_and_frozen_before_the_iterator_advances() -> None:
    shared = _event("prepared", 1)

    def aliased_history() -> Iterable[JsonObject]:
        yield shared
        shared.clear()
        shared.update(_event("submitted", 2, predecessor="prepared"))
        yield shared

    snapshot = reduce_attempt_events(_attempt_document(), aliased_history())
    assert snapshot.state == "submitted"
    assert snapshot.next_sequence == 3


def test_invalid_early_event_is_not_masked_by_a_later_iterator_failure() -> None:
    invalid = _event("prepared", 1)
    invalid["attempt_event_id"] = _digest("f")

    def broken_history() -> Iterable[JsonObject]:
        yield invalid
        raise RuntimeError("must never advance past the invalid first event")

    with pytest.raises(AttemptStateError, match="invalid attempt event 1"):
        reduce_attempt_events(_attempt_document(), broken_history())


def test_snapshot_is_frozen_deterministic_and_exposes_a_later_cas_precondition() -> None:
    history = _prefix("submitted", submitted_handle="provider-call-a")
    left = reduce_attempt_events(_attempt_document(), history)
    typed_attempt = from_json_dict(_attempt_document())
    assert isinstance(typed_attempt, GenerationAttempt)
    right = reduce_attempt_events(typed_attempt, history)

    assert left == right
    assert (left.attempt_id, left.head_event_id, left.next_sequence) == (
        _ATTEMPT_ID,
        history[-1]["attempt_event_id"],
        3,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        left.state = "failed"  # type: ignore[misc]


def test_typed_inputs_are_revalidated_instead_of_trusted() -> None:
    malformed_attempt = GenerationAttempt(
        **{
            **_attempt_document(),
            "schema_version": "moodboard.generation-attempt.v2",
        }
    )
    with pytest.raises(AttemptStateError, match="attempt"):
        reduce_attempt_events(malformed_attempt, [])

    good_event = from_json_dict(_event("prepared", 1))
    assert isinstance(good_event, GenerationAttemptEvent)
    malformed_event = GenerationAttemptEvent(
        **{
            **to_json_dict(good_event),
            "attempt_event_id": _digest("f"),
        }
    )
    with pytest.raises(AttemptStateError, match="event"):
        reduce_attempt_events(_attempt_document(), [malformed_event])


def test_cyclic_typed_inputs_are_normalized_to_the_public_error_surface() -> None:
    cyclic: JsonObject = {}
    cyclic["self"] = cyclic
    malformed_attempt = GenerationAttempt(
        **{
            **_attempt_document(),
            "normalized_request_ref": cyclic,
        }
    )
    with pytest.raises(AttemptStateError, match="invalid generation attempt"):
        reduce_attempt_events(malformed_attempt, [])

    good_event = _event("prepared", 1)
    malformed_event = GenerationAttemptEvent(
        **{
            **good_event,
            "detail": cyclic,
        }
    )
    with pytest.raises(AttemptStateError, match="invalid attempt event 1"):
        reduce_attempt_events(_attempt_document(), [malformed_event])


def test_recorded_at_is_audit_data_not_transition_authority() -> None:
    history = _prefix("submitted")
    history[1] = {
        **history[1],
        "recorded_at": "2026-08-16T20:59:59Z",
    }
    resealed = dict(history[1])
    resealed.pop("attempt_event_id")
    history[1] = to_json_dict(seal_provider_artifact(resealed))

    snapshot = reduce_attempt_events(_attempt_document(), history)
    assert snapshot.state == "submitted"
    assert snapshot.last_recorded_at == "2026-08-16T20:59:59Z"
