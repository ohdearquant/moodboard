"""Pure ADR-0014 generation-attempt state reduction.

This module validates one immutable attempt descriptor and its supplied append-order event
history, then derives a small immutable head snapshot.  It does not persist events, claim a
dispatch slot, perform provider I/O, reconcile a call, or make a compare-and-append operation
atomic.  ``(attempt_id, head_event_id, next_sequence)`` is only a precondition token for that
later durable layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

from moodboard.provider_artifacts import (
    GenerationAttempt,
    GenerationAttemptEvent,
    from_json_dict,
    to_json_dict,
)

__all__ = [
    "AttemptState",
    "AttemptStateError",
    "AttemptStateName",
    "reduce_attempt_events",
]

AttemptStateName: TypeAlias = Literal[
    "prepared",
    "submitted",
    "outcome_unknown",
    "response_received",
    "succeeded",
    "failed",
    "cancelled",
]

_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_LEGAL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "prepared": frozenset({"failed", "cancelled", "submitted"}),
    "submitted": frozenset({"response_received", "failed", "cancelled", "outcome_unknown"}),
    "outcome_unknown": frozenset({"response_received", "failed", "cancelled"}),
    "response_received": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_MAX_EVENTS = 5


class AttemptStateError(ValueError):
    """An attempt descriptor or append-order event history is not reducible."""


@dataclass(frozen=True, slots=True)
class AttemptState:
    """Immutable derived head; its identity tuple is a later store CAS precondition only."""

    attempt_id: str
    state: AttemptStateName | None
    terminal: bool
    head_event_id: str | None
    next_sequence: int
    last_recorded_at: str | None
    provider_handle: str | None


AttemptInput: TypeAlias = GenerationAttempt | Mapping[str, Any]
EventInput: TypeAlias = GenerationAttemptEvent | Mapping[str, Any]


def _validated_attempt(value: AttemptInput) -> GenerationAttempt:
    if not isinstance(value, (GenerationAttempt, Mapping)):
        raise AttemptStateError("attempt must be a generation-attempt artifact")
    try:
        if isinstance(value, GenerationAttempt):
            document = to_json_dict(value)
            artifact = from_json_dict(document)
        else:
            artifact = from_json_dict(dict(value))
    except (RecursionError, TypeError, ValueError) as error:
        raise AttemptStateError(f"invalid generation attempt: {error}") from error
    if not isinstance(artifact, GenerationAttempt):
        raise AttemptStateError("attempt must use moodboard.generation-attempt.v1")
    return artifact


def _validated_event(value: EventInput, position: int) -> GenerationAttemptEvent:
    if not isinstance(value, (GenerationAttemptEvent, Mapping)):
        raise AttemptStateError(f"event {position} must be an attempt-event artifact")
    try:
        if isinstance(value, GenerationAttemptEvent):
            document = to_json_dict(value)
            artifact = from_json_dict(document)
        else:
            artifact = from_json_dict(dict(value))
    except (RecursionError, TypeError, ValueError) as error:
        raise AttemptStateError(f"invalid attempt event {position}: {error}") from error
    if not isinstance(artifact, GenerationAttemptEvent):
        raise AttemptStateError(f"event {position} must use moodboard.generation-attempt-event.v1")
    return artifact


def _event_provider_handle(event: GenerationAttemptEvent) -> str | None:
    if event.state not in {"submitted", "outcome_unknown"}:
        return None
    value = event.detail["provider_handle"]
    if value is None or isinstance(value, str):
        return value
    # Closed event validation makes this unreachable, but retaining one fail-closed guard keeps
    # the reducer safe if a future provider-artifact branch changes independently.
    raise AttemptStateError("attempt event provider handle is not a string or null")


def _validate_cancellation_authority(
    predecessor: str,
    event: GenerationAttemptEvent,
) -> None:
    if event.state != "cancelled":
        return
    authority = event.detail["authority"]
    expected = "local_pre_dispatch" if predecessor == "prepared" else "provider_confirmed_no_output"
    if authority != expected:
        raise AttemptStateError(f"cancellation after {predecessor} requires {expected} authority")


def reduce_attempt_events(
    attempt: AttemptInput,
    events: Iterable[EventInput],
) -> AttemptState:
    """Validate and reduce one supplied append-order event stream.

    Empty history is valid and returns the pre-``prepared`` head.  Event order is never sorted:
    sequence numbers must already be exactly ``1..N`` in the supplied append order.  The longest
    legal v1 trace has five events, so a sixth item is rejected before it can be validated.

    A topological transition to ``succeeded`` does not prove receipt/output eligibility.  The
    provider relational validator and the later durable terminal gate own those checks.
    """

    descriptor = _validated_attempt(attempt)
    try:
        iterator = iter(events)
    except TypeError as error:
        raise AttemptStateError("events must be an iterable of attempt-event artifacts") from error

    bounded_events: list[GenerationAttemptEvent] = []
    for position, raw_event in enumerate(iterator, start=1):
        if position > _MAX_EVENTS:
            raise AttemptStateError("an ADR-0014 v1 attempt history contains at most five events")
        bounded_events.append(_validated_event(raw_event, position))

    current_state: AttemptStateName | None = None
    head_event_id: str | None = None
    last_recorded_at: str | None = None
    provider_handle: str | None = None
    event_count = 0

    for position, event in enumerate(bounded_events, start=1):
        event_count = position
        if event.attempt_id != descriptor.attempt_id:
            raise AttemptStateError(f"event {position} belongs to another attempt")
        if event.sequence != position:
            raise AttemptStateError(
                "attempt event sequence must be contiguous from one in supplied append order"
            )

        event_state = event.state
        if event_state not in _LEGAL_TRANSITIONS:
            # Closed event schema makes this unreachable; keep the reducer table fail-closed.
            raise AttemptStateError(f"attempt event {position} has an unknown state")
        typed_event_state = cast(AttemptStateName, event_state)

        if current_state is None:
            if typed_event_state != "prepared":
                raise AttemptStateError("the first attempt event must be prepared")
        else:
            if current_state in _TERMINAL_STATES:
                raise AttemptStateError(f"terminal state {current_state} rejects later events")
            if typed_event_state not in _LEGAL_TRANSITIONS[current_state]:
                raise AttemptStateError(
                    f"illegal attempt transition {current_state} -> {typed_event_state}"
                )
            _validate_cancellation_authority(current_state, event)

        observed_handle = _event_provider_handle(event)
        if observed_handle is not None:
            if provider_handle is not None and observed_handle != provider_handle:
                raise AttemptStateError("provider handle changed within one immutable attempt")
            provider_handle = observed_handle

        current_state = typed_event_state
        head_event_id = event.attempt_event_id
        last_recorded_at = event.recorded_at

    return AttemptState(
        attempt_id=descriptor.attempt_id,
        state=current_state,
        terminal=current_state in _TERMINAL_STATES,
        head_event_id=head_event_id,
        next_sequence=event_count + 1,
        last_recorded_at=last_recorded_at,
        provider_handle=provider_handle,
    )
