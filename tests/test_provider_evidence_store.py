"""RED contract for durable provider evidence through ``response_received``.

This slice owns only receipt/private-payload persistence and the atomic response event.  Media
decoding/admission, output occurrences, terminal ``succeeded``, and provider transport remain
outside this file.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from queue import Queue

import pytest
from blake3 import blake3

from moodboard.attempt_journal import (
    AttemptJournal,
    AttemptJournalError,
    JournalNotFoundError,
    JournalSecurityError,
    ProviderEvidenceConflictError,
    ProviderResponsePublishResult,
    StoredProviderResponse,
)
from moodboard.openrouter import (
    OpenRouterDecodedResponse,
    OpenRouterHttpResponse,
    OpenRouterPreparedRequest,
    decode_openrouter_response,
)
from moodboard.provider_artifacts import (
    ProviderReceipt,
    seal_provider_artifact,
)
from moodboard.provider_artifacts import (
    to_json_dict as provider_to_json,
)
from tests.test_openrouter_adapter import (
    _CLAIMED_AT,
    _DISPATCH_CLAIM_ID,
    _OUTPUT_BYTES,
    _RECORDED_AT,
    _RESPONSE_BODY,
    _seed_dispatch,
    _success_response,
)
from tests.test_provider_artifacts import _event


def _submitted_response(
    tmp_path: Path,
    *,
    forbidden_secrets: tuple[str, ...] = (),
    output_bytes: bytes = _OUTPUT_BYTES,
) -> tuple[
    AttemptJournal,
    OpenRouterPreparedRequest,
    OpenRouterDecodedResponse,
    str,
]:
    journal, attempt, capability, prepared = _seed_dispatch(
        tmp_path,
        forbidden_secrets=forbidden_secrets,
    )
    claim = journal.claim_non_idempotent_dispatch(
        attempt.attempt_id,
        capability,
        expected_head_event_id=journal.read_state(attempt.attempt_id).head_event_id or "",
        expected_next_sequence=2,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        wire_request_sha256=prepared.wire_body_sha256,
        wire_request_byte_count=prepared.wire_body_byte_count,
    )
    if output_bytes == _OUTPUT_BYTES:
        response = _success_response()
    else:
        body = json.dumps(
            {
                "created": 1_786_930_000,
                "data": [
                    {
                        "b64_json": base64.b64encode(output_bytes).decode("ascii"),
                    }
                ],
                "usage": {"cost": 0.03125},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        response = OpenRouterHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=body,
            elapsed_milliseconds=2450,
        )
    decoded = decode_openrouter_response(
        attempt,
        prepared,
        response,
        received_at=_RECORDED_AT,
    )
    return journal, prepared, decoded, claim.submitted_event.attempt_event_id


def _publish(
    journal: AttemptJournal,
    decoded: OpenRouterDecodedResponse,
    head_event_id: str,
    *,
    next_sequence: int = 3,
) -> ProviderResponsePublishResult:
    return journal.publish_provider_response(
        decoded.receipt,
        decoded.raw_response_bytes,
        decoded.output_bytes,
        expected_head_event_id=head_event_id,
        expected_next_sequence=next_sequence,
    )


def _not_retained(decoded: OpenRouterDecodedResponse) -> ProviderReceipt:
    draft = provider_to_json(decoded.receipt)
    draft.pop("provider_receipt_id")
    draft["raw_response"] = {
        "state": "not_retained",
        "reason": "retention_policy",
    }
    receipt = seal_provider_artifact(draft)
    assert isinstance(receipt, ProviderReceipt)
    return receipt


def test_publish_atomically_round_trips_exact_private_evidence_and_response_event(
    tmp_path: Path,
) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)

    result = _publish(journal, decoded, submitted_event_id)
    reopened = AttemptJournal(journal.path).read_provider_response(decoded.receipt.attempt_id)

    assert isinstance(result, ProviderResponsePublishResult)
    assert isinstance(reopened, StoredProviderResponse)
    assert dataclasses.is_dataclass(result) and dataclasses.is_dataclass(reopened)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.created = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        reopened.raw_response_bytes = b"changed"  # type: ignore[misc]
    assert result.created is True
    assert result.receipt == decoded.receipt
    assert result.event.state == "response_received"
    assert result.event.sequence == 3
    assert result.event.recorded_at == decoded.receipt.received_at
    assert result.event.detail == {
        "kind": "response_received",
        "provider_receipt_id": decoded.receipt.provider_receipt_id,
    }
    assert result.state.state == "response_received"
    assert reopened.receipt == decoded.receipt
    assert reopened.event == result.event
    assert reopened.raw_response_bytes == _RESPONSE_BODY
    assert reopened.output_bytes == (_OUTPUT_BYTES,)
    assert [event.state for event in journal.read_events(decoded.receipt.attempt_id)] == [
        "prepared",
        "submitted",
        "response_received",
    ]
    assert repr(reopened).find(_OUTPUT_BYTES.decode("ascii")) == -1
    assert repr(reopened).find(_RESPONSE_BODY.decode("ascii")) == -1
    journal.verify_integrity()

    with (
        sqlite3.connect(journal.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE provider_response_outputs SET payload_bytes=?",
            (b"changed",),
        )


def test_exact_replay_recovers_without_duplication_and_different_evidence_conflicts(
    tmp_path: Path,
) -> None:
    journal, prepared, decoded, submitted_event_id = _submitted_response(tmp_path)
    first = _publish(journal, decoded, submitted_event_id)

    replay = _publish(journal, decoded, submitted_event_id)

    assert replay.created is False
    assert replay.receipt == first.receipt
    assert replay.event == first.event
    assert replay.state == first.state

    attempt = journal.read_attempt(decoded.receipt.attempt_id)
    different_payload = b"different-provider-output"
    different_body = json.dumps(
        {
            "created": 1_786_930_000,
            "data": [{"b64_json": base64.b64encode(different_payload).decode("ascii")}],
            "usage": {"cost": 0.03125},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    different = decode_openrouter_response(
        attempt,
        prepared,
        OpenRouterHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=different_body,
            elapsed_milliseconds=2450,
        ),
        received_at=_RECORDED_AT,
    )
    with pytest.raises(ProviderEvidenceConflictError):
        _publish(journal, different, submitted_event_id)

    with sqlite3.connect(journal.path) as connection:
        assert connection.execute("SELECT count(*) FROM provider_responses").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM provider_response_outputs").fetchone()[0] == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM attempt_events WHERE state='response_received'"
            ).fetchone()[0]
            == 1
        )


def test_payload_identity_or_count_mismatch_leaves_no_partial_evidence(
    tmp_path: Path,
) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)

    invalid_calls = (
        (b"wrong raw response", decoded.output_bytes),
        (decoded.raw_response_bytes, (b"wrong output",)),
        (decoded.raw_response_bytes, ()),
    )
    for raw_response_bytes, output_bytes in invalid_calls:
        with pytest.raises(ProviderEvidenceConflictError):
            journal.publish_provider_response(
                decoded.receipt,
                raw_response_bytes,
                output_bytes,
                expected_head_event_id=submitted_event_id,
                expected_next_sequence=3,
            )

    assert journal.read_state(decoded.receipt.attempt_id).state == "submitted"
    assert [event.state for event in journal.read_events(decoded.receipt.attempt_id)] == [
        "prepared",
        "submitted",
    ]
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(decoded.receipt.attempt_id)
    with sqlite3.connect(journal.path) as connection:
        assert connection.execute("SELECT count(*) FROM provider_responses").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM provider_response_outputs").fetchone()[0] == 0
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("http_status", 500),
        ("received_at", "2026-08-16T20:30:03Z"),
    ),
)
def test_response_receipt_requires_success_status_and_non_regressing_time(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    draft = provider_to_json(decoded.receipt)
    draft.pop("provider_receipt_id")
    draft[field] = value
    receipt = seal_provider_artifact(draft)
    assert isinstance(receipt, ProviderReceipt)

    with pytest.raises(AttemptJournalError, match="receipt|status|time|transition"):
        journal.publish_provider_response(
            receipt,
            decoded.raw_response_bytes,
            decoded.output_bytes,
            expected_head_event_id=submitted_event_id,
            expected_next_sequence=3,
        )

    assert journal.read_state(receipt.attempt_id).state == "submitted"
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(receipt.attempt_id)


def test_response_receipt_cannot_change_an_observed_provider_handle(tmp_path: Path) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    unknown = _event(
        attempt_id=decoded.receipt.attempt_id,
        sequence=3,
        state="outcome_unknown",
        recorded_at="2026-08-16T20:30:04.5Z",
        detail={
            "kind": "outcome_unknown",
            "failure_stage": "dispatch",
            "failure_code": "ambiguous_transport",
            "provider_handle": "provider-handle-a",
        },
    )
    unknown_result = journal.append_event(
        unknown,
        expected_head_event_id=submitted_event_id,
        expected_next_sequence=3,
    )
    draft = provider_to_json(decoded.receipt)
    draft.pop("provider_receipt_id")
    draft["provider_handle"] = "provider-handle-b"
    receipt = seal_provider_artifact(draft)
    assert isinstance(receipt, ProviderReceipt)

    with pytest.raises(AttemptJournalError, match="receipt|handle"):
        journal.publish_provider_response(
            receipt,
            decoded.raw_response_bytes,
            decoded.output_bytes,
            expected_head_event_id=unknown_result.event.attempt_event_id,
            expected_next_sequence=4,
        )

    assert journal.read_state(receipt.attempt_id).state == "outcome_unknown"
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(receipt.attempt_id)


def test_generic_append_cannot_bypass_provider_evidence_gate(tmp_path: Path) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    response_event = _event(
        attempt_id=decoded.receipt.attempt_id,
        sequence=3,
        state="response_received",
        recorded_at=decoded.receipt.received_at,
        detail={
            "kind": "response_received",
            "provider_receipt_id": decoded.receipt.provider_receipt_id,
        },
    )

    with pytest.raises(AttemptJournalError, match="response_received|evidence"):
        journal.append_event(
            response_event,
            expected_head_event_id=submitted_event_id,
            expected_next_sequence=3,
        )

    assert journal.read_state(decoded.receipt.attempt_id).state == "submitted"


def test_outcome_unknown_can_atomically_reconcile_to_stored_response(tmp_path: Path) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    unknown = _event(
        attempt_id=decoded.receipt.attempt_id,
        sequence=3,
        state="outcome_unknown",
        recorded_at="2026-08-16T20:30:04.5Z",
        detail={
            "kind": "outcome_unknown",
            "failure_stage": "dispatch",
            "failure_code": "ambiguous_transport",
            "provider_handle": None,
        },
    )
    unknown_result = journal.append_event(
        unknown,
        expected_head_event_id=submitted_event_id,
        expected_next_sequence=3,
    )

    result = _publish(
        journal,
        decoded,
        unknown_result.event.attempt_event_id,
        next_sequence=4,
    )

    assert result.created is True
    assert result.event.sequence == 4
    assert result.state.state == "response_received"
    assert [event.state for event in journal.read_events(decoded.receipt.attempt_id)] == [
        "prepared",
        "submitted",
        "outcome_unknown",
        "response_received",
    ]


def test_concurrent_exact_publishers_converge_to_one_creation(tmp_path: Path) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    barrier = threading.Barrier(2)
    outcomes: Queue[bool | Exception] = Queue()

    def worker() -> None:
        try:
            barrier.wait()
            result = _publish(
                AttemptJournal(journal.path),
                decoded,
                submitted_event_id,
            )
            outcomes.put(result.created)
        except Exception as error:  # noqa: BLE001 - concurrent outcomes are asserted below
            outcomes.put(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    observed = [outcomes.get() for _ in range(2)]
    assert not [value for value in observed if isinstance(value, Exception)]
    assert sorted(value for value in observed if isinstance(value, bool)) == [False, True]
    assert journal.read_state(decoded.receipt.attempt_id).state == "response_received"


def test_lost_commit_ack_is_recovered_by_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    original_commit = AttemptJournal._commit

    def commit_then_lose_ack(connection: sqlite3.Connection) -> None:
        original_commit(connection)
        raise AttemptJournalError("simulated lost commit acknowledgement")

    monkeypatch.setattr(
        AttemptJournal,
        "_commit",
        staticmethod(commit_then_lose_ack),
    )
    with pytest.raises(AttemptJournalError, match="lost commit"):
        _publish(journal, decoded, submitted_event_id)
    monkeypatch.setattr(AttemptJournal, "_commit", staticmethod(original_commit))

    recovered = _publish(
        AttemptJournal(journal.path),
        decoded,
        submitted_event_id,
    )

    assert recovered.created is False
    assert recovered.state.state == "response_received"
    assert recovered.receipt == decoded.receipt


def test_not_retained_receipt_requires_none_and_stores_no_raw_payload(tmp_path: Path) -> None:
    journal, _, decoded, submitted_event_id = _submitted_response(tmp_path)
    receipt = _not_retained(decoded)

    with pytest.raises(AttemptJournalError):
        journal.publish_provider_response(
            receipt,
            decoded.raw_response_bytes,
            decoded.output_bytes,
            expected_head_event_id=submitted_event_id,
            expected_next_sequence=3,
        )

    result = journal.publish_provider_response(
        receipt,
        None,
        decoded.output_bytes,
        expected_head_event_id=submitted_event_id,
        expected_next_sequence=3,
    )
    stored = journal.read_provider_response(receipt.attempt_id)

    assert result.created is True
    assert stored.receipt == receipt
    assert stored.raw_response_bytes is None
    assert stored.output_bytes == decoded.output_bytes
    with sqlite3.connect(journal.path) as connection:
        row = connection.execute(
            "SELECT raw_bytes FROM provider_responses WHERE attempt_id=?",
            (receipt.attempt_id,),
        ).fetchone()
        assert row == (None,)


def test_active_secret_variants_fail_before_any_response_evidence_is_written(
    tmp_path: Path,
) -> None:
    secret = "super-secret-value"
    journal, _, decoded, submitted_event_id = _submitted_response(
        tmp_path,
        forbidden_secrets=(secret,),
        output_bytes=secret.encode("utf-8"),
    )

    with pytest.raises(JournalSecurityError):
        _publish(journal, decoded, submitted_event_id)

    assert journal.read_state(decoded.receipt.attempt_id).state == "submitted"
    with pytest.raises(JournalNotFoundError):
        journal.read_provider_response(decoded.receipt.attempt_id)
    assert secret not in repr(decoded.receipt)


def test_hostile_bytes_subclasses_cannot_bypass_evidence_bounds_or_secret_scan(
    tmp_path: Path,
) -> None:
    secret = "super-secret-value"
    journal, _, decoded, submitted_event_id = _submitted_response(
        tmp_path,
        forbidden_secrets=(secret,),
    )

    class HostileBytes(bytes):
        def __len__(self) -> int:
            return 1

        def __contains__(self, _: object) -> bool:
            return False

        def decode(self, *_: object, **__: object) -> str:
            return ""

    payload = HostileBytes(b"x" * (16 * 1024 * 1024) + secret.encode("utf-8"))
    actual_payload = bytes(payload)
    receipt_draft = provider_to_json(decoded.receipt)
    receipt_draft.pop("provider_receipt_id")
    receipt_draft["raw_response"] = {
        "state": "not_retained",
        "reason": "retention_policy",
    }
    receipt_draft["outputs"][0].update(
        {
            "content_ref": blake3(actual_payload).hexdigest(),
            "content_sha256": hashlib.sha256(actual_payload).hexdigest(),
            "byte_count": 1,
        }
    )
    receipt = seal_provider_artifact(receipt_draft)
    assert isinstance(receipt, ProviderReceipt)

    with pytest.raises(JournalSecurityError):
        journal.publish_provider_response(
            receipt,
            None,
            (payload,),
            expected_head_event_id=submitted_event_id,
            expected_next_sequence=3,
        )

    assert journal.read_state(receipt.attempt_id).state == "submitted"
    with sqlite3.connect(journal.path) as connection:
        assert connection.execute("SELECT count(*) FROM provider_responses").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM provider_response_outputs").fetchone() == (
            0,
        )
