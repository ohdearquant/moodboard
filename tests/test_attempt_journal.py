"""RED contract for the durable ADR-0014 attempt journal.

The journal owns immutable registration, compare-and-append, and the one permanent local
dispatch claim.  Provider I/O and terminal-success eligibility deliberately remain outside this
layer.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import gc
import hashlib
import json
import os
import sqlite3
import stat
import threading
from pathlib import Path
from queue import Queue
from typing import Any

import pytest

from moodboard.attempt_journal import (
    AttemptJournal,
    AttemptJournalError,
    DispatchClaimConflictError,
    DispatchClaimResult,
    EventAppendResult,
    ImmutableRecordConflictError,
    JournalCorruptionError,
    JournalNotFoundError,
    JournalSecurityError,
    JournalVersionError,
    RegistrationResult,
    StaleAttemptHeadError,
)
from moodboard.contracts import canonical_json_bytes
from moodboard.provider_artifacts import (
    ATTEMPT_VERSION,
    CAPABILITY_VERSION,
    EVENT_VERSION,
    RUN_VERSION,
    GenerationRun,
    to_json_dict,
)
from tests.test_provider_artifacts import (
    _event as provider_event,
)
from tests.test_provider_artifacts import (
    _refresh_document_id,
    _valid_artifact_chain,
)

JsonObject = dict[str, Any]

_CLAIM_ID = "40000000-0000-4000-8000-000000000001"
_WIRE_SHA256 = "e" * 64


def _chain() -> tuple[JsonObject, JsonObject, JsonObject, JsonObject, list[JsonObject]]:
    _, artifacts = _valid_artifact_chain()
    run = next(item for item in artifacts if item["schema_version"] == RUN_VERSION)
    attempt = next(item for item in artifacts if item["schema_version"] == ATTEMPT_VERSION)
    capability = next(item for item in artifacts if item["schema_version"] == CAPABILITY_VERSION)
    events = [item for item in artifacts if item["schema_version"] == EVENT_VERSION]
    prepared = next(item for item in events if item["state"] == "prepared")
    return tuple(copy.deepcopy(item) for item in (run, attempt, capability, prepared, events))  # type: ignore[return-value]


def _append_prepared(
    journal: AttemptJournal,
    run: JsonObject,
    attempt: JsonObject,
    prepared: JsonObject,
) -> EventAppendResult:
    journal.register_run(run)
    journal.register_attempt(attempt)
    return journal.append_event(
        prepared,
        expected_head_event_id=None,
        expected_next_sequence=1,
    )


def _claim(
    journal: AttemptJournal,
    attempt: JsonObject,
    capability: JsonObject,
    prepared: JsonObject,
) -> DispatchClaimResult:
    return journal.claim_non_idempotent_dispatch(
        attempt["attempt_id"],
        capability,
        expected_head_event_id=prepared["attempt_event_id"],
        expected_next_sequence=2,
        dispatch_claim_id=_CLAIM_ID,
        claimed_at="2026-08-16T20:30:04Z",
        wire_request_sha256=_WIRE_SHA256,
        wire_request_byte_count=4096,
    )


def _cancelled_event(attempt_id: str) -> JsonObject:
    return provider_event(
        attempt_id=attempt_id,
        sequence=2,
        state="cancelled",
        recorded_at="2026-08-16T20:30:04Z",
        detail={
            "kind": "cancelled",
            "cancellation_stage": "preflight",
            "cancellation_code": "user_cancelled",
            "authority": "local_pre_dispatch",
        },
    )


def test_public_error_taxonomy_is_closed_under_the_journal_error() -> None:
    error_types = (
        JournalSecurityError,
        JournalCorruptionError,
        JournalVersionError,
        JournalNotFoundError,
        ImmutableRecordConflictError,
        StaleAttemptHeadError,
        DispatchClaimConflictError,
    )

    assert all(issubclass(error_type, AttemptJournalError) for error_type in error_types)


def test_bootstrap_is_private_wal_and_full_synchronous(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, _, _, _, _ = _chain()

    result = AttemptJournal(path, busy_timeout_ms=4321).register_run(run)

    assert isinstance(result, RegistrationResult)
    assert result.created is True
    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.created = False  # type: ignore[misc]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(candidate.stat().st_mode) == 0o600
        for candidate in path.parent.glob(f"{path.name}*")
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_concurrent_first_registration_converges_without_transient_version_failure(
    tmp_path: Path,
) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, _, _, _, _ = _chain()
    barrier = threading.Barrier(2)
    outcomes: Queue[bool | Exception] = Queue()

    def worker() -> None:
        try:
            journal = AttemptJournal(path)
            barrier.wait()
            outcomes.put(journal.register_run(copy.deepcopy(run)).created)
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


def test_run_and_attempt_registration_are_exact_replay_or_conflict(tmp_path: Path) -> None:
    run, attempt, _, _, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())

    assert journal.register_run(run).created is True
    assert journal.register_run(copy.deepcopy(run)).created is False
    changed_run = copy.deepcopy(run)
    changed_run["created_at"] = "2026-08-16T20:31:02Z"
    with pytest.raises(ImmutableRecordConflictError):
        journal.register_run(changed_run)

    assert journal.register_attempt(attempt).created is True
    assert journal.register_attempt(copy.deepcopy(attempt)).created is False
    changed_attempt = copy.deepcopy(attempt)
    changed_attempt["created_at"] = "2026-08-16T20:31:03Z"
    with pytest.raises(ImmutableRecordConflictError):
        journal.register_attempt(changed_attempt)


def test_attempt_must_bind_an_existing_matching_run(tmp_path: Path) -> None:
    run, attempt, _, _, _ = _chain()
    missing = AttemptJournal((tmp_path / "missing.sqlite3").resolve())
    with pytest.raises(JournalNotFoundError):
        missing.register_attempt(attempt)

    path = (tmp_path / "mismatch.sqlite3").resolve()
    journal = AttemptJournal(path)
    journal.register_run(run)
    mismatched = copy.deepcopy(attempt)
    mismatched["requested_model"] = "another/model"
    with pytest.raises(AttemptJournalError, match="run|bind|model"):
        journal.register_attempt(mismatched)

    mismatched_bytes = canonical_json_bytes(mismatched)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO generation_attempts VALUES (?, ?, ?, ?, ?)",
            (
                mismatched["attempt_id"],
                mismatched["generation_run_id"],
                mismatched["ordinal"],
                hashlib.sha256(mismatched_bytes).hexdigest(),
                mismatched_bytes,
            ),
        )
    with pytest.raises(JournalCorruptionError, match="run|bind|model"):
        journal.read_attempt(attempt["attempt_id"])
    prepared = _chain()[3]
    with pytest.raises(JournalCorruptionError, match="run|bind|model"):
        journal.append_event(
            prepared,
            expected_head_event_id=None,
            expected_next_sequence=1,
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM attempt_events").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM non_idempotent_dispatch_claims").fetchone()[0]
            == 0
        )


def test_p0_journal_rejects_unimplemented_retry_and_fallback_attempts(tmp_path: Path) -> None:
    run, attempt, _, _, _ = _chain()
    path = (tmp_path / "attempts.sqlite3").resolve()
    journal = AttemptJournal(path)
    journal.register_run(run)
    retry = copy.deepcopy(attempt)
    retry["ordinal"] = 2
    retry["retry_of"] = "50000000-0000-4000-8000-000000000001"

    with pytest.raises(AttemptJournalError, match="retry|fallback|ordinal|P0"):
        journal.register_attempt(retry)
    with pytest.raises(JournalNotFoundError):
        journal.read_attempt(attempt["attempt_id"])

    retry_bytes = canonical_json_bytes(retry)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO generation_attempts VALUES (?, ?, ?, ?, ?)",
            (
                retry["attempt_id"],
                retry["generation_run_id"],
                retry["ordinal"],
                hashlib.sha256(retry_bytes).hexdigest(),
                retry_bytes,
            ),
        )
    with pytest.raises(JournalCorruptionError, match="retry|fallback|ordinal|P0"):
        journal.read_attempt(attempt["attempt_id"])


def test_registration_validates_and_freezes_before_retaining_aliases(tmp_path: Path) -> None:
    run, attempt, _, _, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    journal.register_run(run)
    original_byte_count = attempt["normalized_request_ref"]["byte_count"]

    result = journal.register_attempt(attempt)
    attempt["normalized_request_ref"]["byte_count"] = 1

    assert (
        to_json_dict(result.artifact)["normalized_request_ref"]["byte_count"] == original_byte_count
    )
    assert to_json_dict(journal.read_attempt(attempt["attempt_id"])) == to_json_dict(
        result.artifact
    )
    with pytest.raises((AttributeError, TypeError)):
        result.artifact.normalized_request_ref["byte_count"] = 1  # type: ignore[index]


def test_prepared_append_uses_cas_and_exact_replay_precedes_staleness(tmp_path: Path) -> None:
    run, attempt, _, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    first = _append_prepared(journal, run, attempt, prepared)

    replay = journal.append_event(
        copy.deepcopy(prepared),
        expected_head_event_id=None,
        expected_next_sequence=1,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.event == first.event
    assert replay.state.state == "prepared"
    assert replay.state.head_event_id == prepared["attempt_event_id"]
    assert tuple(journal.read_events(attempt["attempt_id"])) == (first.event,)


@pytest.mark.parametrize("invalid_sequence", [True, 1.0])
def test_append_rejects_non_integer_cas_sequence_before_writing(
    tmp_path: Path,
    invalid_sequence: object,
) -> None:
    run, attempt, _, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    journal.register_run(run)
    journal.register_attempt(attempt)

    with pytest.raises(AttemptJournalError, match="sequence"):
        journal.append_event(
            prepared,
            expected_head_event_id=None,
            expected_next_sequence=invalid_sequence,  # type: ignore[arg-type]
        )
    assert journal.read_events(attempt["attempt_id"]) == ()


def test_event_slot_conflict_stale_head_and_terminal_reopen_fail_closed(tmp_path: Path) -> None:
    run, attempt, _, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)
    competing = copy.deepcopy(prepared)
    competing["recorded_at"] = "2026-08-16T20:30:09Z"
    _refresh_document_id(competing)
    with pytest.raises(ImmutableRecordConflictError):
        journal.append_event(
            competing,
            expected_head_event_id=None,
            expected_next_sequence=1,
        )

    failed = provider_event(
        attempt_id=attempt["attempt_id"],
        sequence=2,
        state="failed",
        recorded_at="2026-08-16T20:30:04Z",
        detail={"kind": "failed", "failure_stage": "preflight", "failure_code": "invalid"},
    )
    with pytest.raises(StaleAttemptHeadError):
        journal.append_event(failed, expected_head_event_id=None, expected_next_sequence=2)
    terminal = journal.append_event(
        failed,
        expected_head_event_id=prepared["attempt_event_id"],
        expected_next_sequence=2,
    )
    later = provider_event(
        attempt_id=attempt["attempt_id"],
        sequence=3,
        state="cancelled",
        recorded_at="2026-08-16T20:30:05Z",
        detail={
            "kind": "cancelled",
            "cancellation_stage": "provider",
            "cancellation_code": "late",
            "authority": "provider_confirmed_no_output",
        },
    )
    with pytest.raises(AttemptJournalError, match="terminal|transition"):
        journal.append_event(
            later,
            expected_head_event_id=terminal.event.attempt_event_id,
            expected_next_sequence=3,
        )


def test_generic_append_rejects_submitted_and_succeeded(tmp_path: Path) -> None:
    run, attempt, capability, prepared, events = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)
    submitted = next(event for event in events if event["state"] == "submitted")
    with pytest.raises(DispatchClaimConflictError):
        journal.append_event(
            submitted,
            expected_head_event_id=prepared["attempt_event_id"],
            expected_next_sequence=2,
        )

    claim = _claim(journal, attempt, capability, prepared)
    response = next(event for event in events if event["state"] == "response_received")
    response_result = journal.append_event(
        response,
        expected_head_event_id=claim.submitted_event.attempt_event_id,
        expected_next_sequence=3,
    )
    succeeded = next(event for event in events if event["state"] == "succeeded")
    with pytest.raises(AttemptJournalError, match="succeeded|terminal gate"):
        journal.append_event(
            succeeded,
            expected_head_event_id=response_result.event.attempt_event_id,
            expected_next_sequence=4,
        )


def test_stored_succeeded_is_corruption_until_the_evidence_gate_exists(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, attempt, capability, prepared, events = _chain()
    journal = AttemptJournal(path)
    _append_prepared(journal, run, attempt, prepared)
    claim = _claim(journal, attempt, capability, prepared)
    response = next(event for event in events if event["state"] == "response_received")
    journal.append_event(
        response,
        expected_head_event_id=claim.submitted_event.attempt_event_id,
        expected_next_sequence=3,
    )
    succeeded = next(event for event in events if event["state"] == "succeeded")
    succeeded_bytes = canonical_json_bytes(succeeded)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO attempt_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                attempt["attempt_id"],
                succeeded["sequence"],
                succeeded["attempt_event_id"],
                succeeded["state"],
                hashlib.sha256(succeeded_bytes).hexdigest(),
                succeeded_bytes,
            ),
        )

    with pytest.raises(JournalCorruptionError, match="succeeded|evidence|terminal"):
        journal.read_state(attempt["attempt_id"])
    with pytest.raises(JournalCorruptionError, match="succeeded|evidence|terminal"):
        journal.verify_integrity()


def test_non_idempotent_dispatch_claim_authorizes_once_then_replays_without_authority(
    tmp_path: Path,
) -> None:
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)

    first = _claim(journal, attempt, capability, prepared)
    replay = _claim(journal, attempt, copy.deepcopy(capability), prepared)

    assert first.created is True
    assert first.send_authorized is True
    assert first.dispatch_claim_id == _CLAIM_ID
    assert first.submitted_event.state == "submitted"
    assert first.submitted_event.detail["provider_handle"] is None
    assert first.state.state == "submitted"
    assert replay.created is False
    assert replay.send_authorized is False
    assert replay.dispatch_claim_id == first.dispatch_claim_id
    assert replay.submitted_event == first.submitted_event


def test_dispatch_claim_rejects_a_capability_not_bound_to_the_attempt(tmp_path: Path) -> None:
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)
    mismatched = copy.deepcopy(capability)
    mismatched["requested_model"] = "another/model"
    _refresh_document_id(mismatched)

    with pytest.raises(DispatchClaimConflictError, match="capability|model|bind"):
        _claim(journal, attempt, mismatched, prepared)
    assert journal.read_state(attempt["attempt_id"]).state == "prepared"


@pytest.mark.parametrize(
    "claimed_at",
    ("2026-99-99T99:99:99Z", "2026-08-16T24:00:00Z"),
)
def test_dispatch_claim_rejects_an_impossible_calendar_timestamp(
    tmp_path: Path,
    claimed_at: str,
) -> None:
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)

    with pytest.raises(AttemptJournalError, match="timestamp"):
        journal.claim_non_idempotent_dispatch(
            attempt["attempt_id"],
            capability,
            expected_head_event_id=prepared["attempt_event_id"],
            expected_next_sequence=2,
            dispatch_claim_id=_CLAIM_ID,
            claimed_at=claimed_at,
            wire_request_sha256=_WIRE_SHA256,
            wire_request_byte_count=4096,
        )
    assert journal.read_state(attempt["attempt_id"]).state == "prepared"


def test_claim_and_local_cancel_race_have_exactly_one_winner(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, attempt, capability, prepared, _ = _chain()
    seed = AttemptJournal(path)
    _append_prepared(seed, run, attempt, prepared)
    barrier = threading.Barrier(2)
    outcomes: Queue[tuple[str, bool | Exception]] = Queue()

    def claim_worker() -> None:
        try:
            journal = AttemptJournal(path)
            barrier.wait()
            outcomes.put(("claim", _claim(journal, attempt, capability, prepared).send_authorized))
        except Exception as error:  # noqa: BLE001 - the race result is asserted below
            outcomes.put(("claim", error))

    def cancel_worker() -> None:
        try:
            journal = AttemptJournal(path)
            barrier.wait()
            result = journal.append_event(
                _cancelled_event(attempt["attempt_id"]),
                expected_head_event_id=prepared["attempt_event_id"],
                expected_next_sequence=2,
            )
            outcomes.put(("cancel", result.created))
        except Exception as error:  # noqa: BLE001 - the race result is asserted below
            outcomes.put(("cancel", error))

    threads = [threading.Thread(target=claim_worker), threading.Thread(target=cancel_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    observed = dict(outcomes.get() for _ in range(2))
    winners = sum(value is True for value in observed.values())
    assert winners == 1
    assert all(
        value is True or isinstance(value, AttemptJournalError) for value in observed.values()
    )
    assert AttemptJournal(path).read_state(attempt["attempt_id"]).state in {
        "submitted",
        "cancelled",
    }


def test_reopen_preserves_claim_and_never_reauthorizes(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal(path)
    _append_prepared(journal, run, attempt, prepared)
    assert _claim(journal, attempt, capability, prepared).send_authorized is True
    del journal
    gc.collect()

    reopened = AttemptJournal(path)
    assert reopened.read_state(attempt["attempt_id"]).state == "submitted"
    assert len(tuple(reopened.read_events(attempt["attempt_id"]))) == 2
    assert _claim(reopened, attempt, capability, prepared).send_authorized is False


def test_claim_replay_with_changed_wire_identity_is_a_conflict(tmp_path: Path) -> None:
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    _append_prepared(journal, run, attempt, prepared)
    assert _claim(journal, attempt, capability, prepared).send_authorized is True

    with pytest.raises(DispatchClaimConflictError):
        journal.claim_non_idempotent_dispatch(
            attempt["attempt_id"],
            capability,
            expected_head_event_id=prepared["attempt_event_id"],
            expected_next_sequence=2,
            dispatch_claim_id=_CLAIM_ID,
            claimed_at="2026-08-16T20:30:04Z",
            wire_request_sha256="f" * 64,
            wire_request_byte_count=4096,
        )


def test_integrity_rejects_a_submitted_event_whose_claim_was_removed(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal(path)
    _append_prepared(journal, run, attempt, prepared)
    _claim(journal, attempt, capability, prepared)

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER non_idempotent_dispatch_claims_delete_immutable")
        connection.execute(
            "DELETE FROM non_idempotent_dispatch_claims WHERE attempt_id=?",
            (attempt["attempt_id"],),
        )
        connection.execute(
            "CREATE TRIGGER non_idempotent_dispatch_claims_delete_immutable "
            "BEFORE DELETE ON non_idempotent_dispatch_claims "
            "BEGIN SELECT RAISE(ABORT, 'immutable journal row'); END"
        )

    with pytest.raises(JournalCorruptionError, match="claim|submitted"):
        journal.verify_integrity()


def test_integrity_cross_checks_claimed_head_against_the_real_predecessor(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, attempt, capability, prepared, _ = _chain()
    journal = AttemptJournal(path)
    _append_prepared(journal, run, attempt, prepared)
    _claim(journal, attempt, capability, prepared)

    with sqlite3.connect(path) as connection:
        claim = json.loads(
            connection.execute(
                "SELECT claim_json FROM non_idempotent_dispatch_claims WHERE attempt_id=?",
                (attempt["attempt_id"],),
            ).fetchone()[0]
        )
        claim["expected_head_event_id"] = "f" * 64
        claim_bytes = canonical_json_bytes(claim)
        connection.execute("DROP TRIGGER non_idempotent_dispatch_claims_update_immutable")
        connection.execute(
            "UPDATE non_idempotent_dispatch_claims "
            "SET expected_head_event_id=?, claim_sha256=?, claim_json=? WHERE attempt_id=?",
            (
                "f" * 64,
                hashlib.sha256(claim_bytes).hexdigest(),
                claim_bytes,
                attempt["attempt_id"],
            ),
        )
        connection.execute(
            "CREATE TRIGGER non_idempotent_dispatch_claims_update_immutable "
            "BEFORE UPDATE ON non_idempotent_dispatch_claims "
            "BEGIN SELECT RAISE(ABORT, 'immutable journal row'); END"
        )

    with pytest.raises(JournalCorruptionError, match="head|predecessor|claim"):
        journal.verify_integrity()


def test_exact_and_high_confidence_secrets_are_rejected_before_database_or_wal(
    tmp_path: Path,
) -> None:
    run, _, _, _, _ = _chain()
    exact = "literal-provider-secret-1234567890"
    exact_bytes = exact.encode("utf-8")
    cases = (
        ("exact.sqlite3", exact, (exact,)),
        (
            "base64.sqlite3",
            base64.b64encode(exact_bytes).rstrip(b"=").decode("ascii"),
            (exact,),
        ),
        ("hex.sqlite3", exact_bytes.hex().upper(), (exact,)),
        ("pattern.sqlite3", f"sk-or-v1-{'A' * 64}", ()),
    )

    for filename, secret, forbidden in cases:
        path = (tmp_path / filename).resolve()
        tainted = copy.deepcopy(run)
        tainted["requested_model"] = secret
        journal = AttemptJournal(path, forbidden_secrets=forbidden)
        with pytest.raises(JournalSecurityError) as captured:
            journal.register_run(tainted)
        assert secret not in str(captured.value)
        assert not path.exists()
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()


def test_direct_dataclass_secret_is_scanned_before_validation_can_quote_it(
    tmp_path: Path,
) -> None:
    run, _, _, _, _ = _chain()
    secret = "literal provider secret with spaces 1234567890"
    tainted = copy.deepcopy(run)
    tainted["requested_model"] = secret
    value = GenerationRun(**tainted)
    path = (tmp_path / "dataclass.sqlite3").resolve()

    with pytest.raises(JournalSecurityError) as captured:
        AttemptJournal(path, forbidden_secrets=(secret,)).register_run(value)

    current: BaseException | None = captured.value
    while current is not None:
        assert secret not in str(current)
        current = current.__cause__ or current.__context__
    assert not path.exists()


def test_noncanonical_path_is_rejected_without_touching_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(JournalSecurityError):
        AttemptJournal(Path("attempts.sqlite3"))
    assert not (tmp_path / "attempts.sqlite3").exists()


def test_rejected_unrelated_sqlite_file_is_not_switched_to_wal(tmp_path: Path) -> None:
    path = (tmp_path / "unrelated.sqlite3").resolve()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    path.chmod(0o600)

    with pytest.raises(JournalVersionError):
        AttemptJournal(path).read_run("20000000-0000-4000-8000-000000000001")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_existing_symlink_hardlink_and_insecure_mode_are_rejected(tmp_path: Path) -> None:
    path = (tmp_path / "attempts.sqlite3").resolve()
    run, _, _, _, _ = _chain()
    AttemptJournal(path).register_run(run)

    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(path)
    with pytest.raises(JournalSecurityError):
        AttemptJournal(symlink)

    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(path, hardlink)
    with pytest.raises(JournalSecurityError):
        AttemptJournal(hardlink.resolve())
    hardlink.unlink()

    path.chmod(0o644)
    with pytest.raises(JournalSecurityError):
        AttemptJournal(path)
