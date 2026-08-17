from __future__ import annotations

import dataclasses
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import moodboard.studio_confirmation_ledger as ledger_module
from moodboard.contracts import compute_document_identity
from moodboard.studio_confirmation_ledger import (
    LEDGER_REVISION,
    ConfirmationConsumptionResult,
    ConfirmationLedgerEntry,
    ConfirmationRecordResult,
    StudioConfirmationLedger,
    StudioConfirmationLedgerError,
    StudioSessionAuthority,
)

_CONTEXT_VERSION = "moodboard.openrouter-real-e2e-confirmation-context.v1"
_CHALLENGE_VERSION = "moodboard.openrouter-real-e2e-confirmation-challenge.v1"
_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000001"
_STUDIO_SESSION_ID = "00000000-0000-4000-8000-000000000002"
_CREATIVE_SESSION_ID = "00000000-0000-4000-8000-000000000003"
_GENERATION_RUN_ID = "00000000-0000-4000-8000-000000000004"
_ATTEMPT_ID = "00000000-0000-4000-8000-000000000005"
_ACTIVE_FROM = "2026-08-17T00:00:00Z"
_PREPARED_AT = "2026-08-17T00:10:00Z"
_CONFIRMED_AT = "2026-08-17T00:20:00Z"
_INSPECTED_AT = "2026-08-17T00:25:00Z"
_CONSUMED_AT = "2026-08-17T00:30:00Z"
_EXPIRES_AT = "2026-08-17T01:00:00Z"
_AUTHORITY_EXPIRES_AT = "2026-08-17T02:00:00Z"


def _identity(document: dict[str, Any], *, version: str, field: str) -> str:
    return compute_document_identity(
        document,
        schema_version=version,
        identity_field=field,
    )


def _challenge_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": _CHALLENGE_VERSION,
        "challenge_id": "0" * 64,
        "compact_summary_id": "1" * 64,
        "prepared_at": _PREPARED_AT,
        "expires_at": _EXPIRES_AT,
        "directory_binding": {"absolute_path": "/private/e2e", "device": 1, "inode": 2},
        "artifacts": {},
        "quoted_cost_usd": "0.033",
        "quote_admission_limit_usd": "0.05",
        "spend_limit_kind": "quote_only_not_provider_enforced",
        "creative_session_id": _CREATIVE_SESSION_ID,
        "generation_run_id": _GENERATION_RUN_ID,
        "attempt_id": _ATTEMPT_ID,
        "capability_snapshot_id": "2" * 64,
        "wire_body_sha256": "3" * 64,
        "wire_body_byte_count": 481,
        "packet_projection": {"schema_version": "moodboard.intent-packet.v1"},
    }
    document.update(overrides)
    document["challenge_id"] = _identity(
        document,
        version=_CHALLENGE_VERSION,
        field="challenge_id",
    )
    return document


def _context_document(challenge: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": _CONTEXT_VERSION,
        "confirmation_context_id": "0" * 64,
        "challenge_id": challenge["challenge_id"],
        "compact_summary_id": challenge["compact_summary_id"],
        "decision": "approve_one_paid_call",
        "authorized_generation_post_count": 1,
        "principal_id": _PRINCIPAL_ID,
        "studio_session_id": _STUDIO_SESSION_ID,
        "creative_session_id": _CREATIVE_SESSION_ID,
        "confirmed_at": _CONFIRMED_AT,
    }
    document.update(overrides)
    document["confirmation_context_id"] = _identity(
        document,
        version=_CONTEXT_VERSION,
        field="confirmation_context_id",
    )
    return document


def _authority(*, epoch: int = 1, **overrides: Any) -> StudioSessionAuthority:
    values: dict[str, Any] = {
        "principal_id": _PRINCIPAL_ID,
        "studio_session_id": _STUDIO_SESSION_ID,
        "creative_session_id": _CREATIVE_SESSION_ID,
        "authority_epoch": epoch,
        "active_from": _ACTIVE_FROM,
        "expires_at": _AUTHORITY_EXPIRES_AT,
    }
    values.update(overrides)
    return StudioSessionAuthority(**values)


def _ledger(tmp_path: Path, name: str = "confirmations.sqlite3") -> StudioConfirmationLedger:
    parent = tmp_path / "studio-state"
    parent.mkdir(mode=0o700, exist_ok=True)
    parent.chmod(0o700)
    return StudioConfirmationLedger(parent / name)


def _issued_case(
    tmp_path: Path,
) -> tuple[StudioConfirmationLedger, dict[str, Any], dict[str, Any]]:
    ledger = _ledger(tmp_path)
    challenge = _challenge_document()
    context = _context_document(challenge)
    assert ledger.record_session_authority(_authority()) is True
    recorded = ledger.record_explicit_confirmation(context, challenge, authority_epoch=1)
    assert recorded.created is True
    return ledger, challenge, context


def _assert_code(call: Callable[[], object], code: str) -> StudioConfirmationLedgerError:
    with pytest.raises(StudioConfirmationLedgerError) as raised:
        call()
    assert raised.value.code == code
    assert raised.value.__cause__ is None
    return raised.value


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def test_public_contract_is_frozen_and_sensitive_rows_have_safe_repr(tmp_path: Path) -> None:
    assert LEDGER_REVISION == "moodboard.studio-confirmation-ledger.v1"
    for kind in (
        StudioSessionAuthority,
        ConfirmationLedgerEntry,
        ConfirmationRecordResult,
        ConfirmationConsumptionResult,
    ):
        assert dataclasses.is_dataclass(kind)
        assert kind.__dataclass_params__.frozen is True

    ledger, challenge, context = _issued_case(tmp_path)
    inspected = ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT)
    assert inspected.state == "available"
    assert inspected.consumed_at is None
    assert inspected.confirmation_context_id == context["confirmation_context_id"]
    assert inspected.challenge_id == challenge["challenge_id"]
    assert inspected.authority_epoch == 1
    assert "packet_projection" not in repr(inspected)


def test_first_consume_is_durable_and_exact_replay_never_authorizes(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    consumed = ledger.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT)
    assert consumed.created is True
    assert consumed.generation_post_authorized is True
    assert consumed.entry.state == "consumed"
    assert consumed.entry.consumed_at == _CONSUMED_AT

    reopened = StudioConfirmationLedger(ledger.path)
    replay = reopened.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT)
    assert replay.created is False
    assert replay.generation_post_authorized is False
    assert replay.entry == consumed.entry
    assert (
        reopened.inspect_confirmation(
            context,
            challenge,
            inspected_at=_CONSUMED_AT,
        ).state
        == "consumed"
    )
    reopened.verify_integrity()


def test_self_minted_confirmation_absent_from_studio_rows_is_denied(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert ledger.record_session_authority(_authority()) is True
    challenge = _challenge_document()
    context = _context_document(challenge)

    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "confirmation_not_registered",
    )
    _assert_code(
        lambda: ledger.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT),
        "confirmation_not_registered",
    )


def test_one_challenge_cannot_be_authorized_by_two_contexts(tmp_path: Path) -> None:
    ledger, challenge, _context = _issued_case(tmp_path)
    second = _context_document(challenge, confirmed_at="2026-08-17T00:21:00Z")

    _assert_code(
        lambda: ledger.record_explicit_confirmation(second, challenge, authority_epoch=1),
        "confirmation_conflict",
    )


def test_two_connections_converge_to_one_consuming_cas(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    contenders = (StudioConfirmationLedger(ledger.path), StudioConfirmationLedger(ledger.path))
    barrier = threading.Barrier(2)
    results: list[ConfirmationConsumptionResult] = []
    failures: list[BaseException] = []

    def consume(contender: StudioConfirmationLedger) -> None:
        try:
            barrier.wait(timeout=2)
            results.append(
                contender.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT)
            )
        except BaseException as error:  # pragma: no cover - surfaced by the assertion
            failures.append(error)

    threads = tuple(threading.Thread(target=consume, args=(item,)) for item in contenders)
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert failures == []
    assert len(results) == 2
    assert sorted(result.created for result in results) == [False, True]
    assert sorted(result.generation_post_authorized for result in results) == [False, True]


@pytest.mark.parametrize("transition", ["revoke", "advance_epoch"])
def test_consume_rechecks_session_authority_after_read_only_inspection(
    tmp_path: Path,
    transition: str,
) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    assert ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT).state == (
        "available"
    )

    if transition == "revoke":
        assert (
            ledger.revoke_session(
                _STUDIO_SESSION_ID,
                authority_epoch=1,
                revoked_at="2026-08-17T00:26:00Z",
            )
            is True
        )
    else:
        assert (
            ledger.record_session_authority(
                _authority(
                    epoch=2,
                    active_from="2026-08-17T00:26:00Z",
                )
            )
            is True
        )

    _assert_code(
        lambda: ledger.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT),
        "authority_inactive",
    )


def test_expired_consume_does_not_create_a_tombstone(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    _assert_code(
        lambda: ledger.consume_confirmation(
            context,
            challenge,
            consumed_at="2026-08-17T01:00:01Z",
        ),
        "confirmation_expired",
    )
    assert ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT).state == (
        "available"
    )


def test_commit_ack_ambiguity_spends_confirmation_but_never_authorizes_current_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    original_commit = ledger_module._commit

    def commit_then_raise(connection: sqlite3.Connection) -> None:
        original_commit(connection)
        raise RuntimeError("private-provider-material-must-not-escape")

    monkeypatch.setattr(ledger_module, "_commit", commit_then_raise)
    error = _assert_code(
        lambda: ledger.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT),
        "confirmation_persistence_ambiguous",
    )
    assert "private-provider-material" not in str(error)
    assert all(
        "private-provider-material" not in f"{item!s} {item!r}" for item in _exception_chain(error)
    )

    monkeypatch.setattr(ledger_module, "_commit", original_commit)
    replay = ledger.consume_confirmation(context, challenge, consumed_at=_CONSUMED_AT)
    assert replay.created is False
    assert replay.generation_post_authorized is False


def test_hostile_nested_mapping_exception_is_removed_from_the_public_error_chain(
    tmp_path: Path,
) -> None:
    secret = "HOSTILE-MAPPING-SECRET-MUST-NOT-ESCAPE"

    class HostileMapping(dict[str, Any]):
        def __deepcopy__(self, _memo: dict[int, Any]) -> dict[str, Any]:
            raise RuntimeError(secret)

    ledger = _ledger(tmp_path)
    ledger.record_session_authority(_authority())
    challenge = _challenge_document()
    challenge["packet_projection"] = HostileMapping(challenge["packet_projection"])
    context = _context_document(challenge)

    error = _assert_code(
        lambda: ledger.record_explicit_confirmation(context, challenge, authority_epoch=1),
        "confirmation_invalid",
    )
    assert all(secret not in f"{item!s} {item!r}" for item in _exception_chain(error))
    assert error.__context__ is None


def test_unsafe_or_corrupt_ledger_fails_closed_without_reinitialization(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    ledger.path.chmod(0o644)
    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "ledger_security_error",
    )


def test_schema_sql_fingerprint_drift_fails_closed(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    trigger = "confirmation_grants_update_immutable"
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON confirmation_grants BEGIN SELECT 1; END"
        )

    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "ledger_corruption",
    )


def test_redundant_grant_drift_cannot_widen_the_challenge_expiry(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    trigger = "confirmation_grants_update_immutable"
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            "UPDATE confirmation_grants SET expires_at=?",
            ("2026-08-17T02:00:00Z",),
        )
        connection.execute(ledger_module._immutable_trigger_sql("confirmation_grants", "update"))

    _assert_code(
        lambda: ledger.consume_confirmation(
            context,
            challenge,
            consumed_at="2026-08-17T01:30:00Z",
        ),
        "ledger_corruption",
    )
    _assert_code(ledger.verify_integrity, "ledger_corruption")


def test_ancestor_symlink_is_rejected_before_ledger_creation(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    nested = real_parent / "nested"
    nested.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    _assert_code(
        lambda: StudioConfirmationLedger(alias / "nested" / "confirmations.sqlite3"),
        "ledger_security_error",
    )
    assert not (nested / "confirmations.sqlite3").exists()


def test_parent_disappearance_does_not_survive_in_the_public_exception_chain(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    parent_text = str(ledger.path.parent)
    ledger.path.parent.rmdir()

    error = _assert_code(
        lambda: ledger.record_session_authority(_authority()),
        "ledger_security_error",
    )
    assert error.__context__ is None
    assert all(parent_text not in f"{item!s} {item!r}" for item in _exception_chain(error))


def test_concurrent_first_authority_registration_has_one_creator_and_one_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _ledger(tmp_path)
    contenders = (StudioConfirmationLedger(ledger.path), StudioConfirmationLedger(ledger.path))
    entered = threading.Event()
    release = threading.Event()
    original_initialize = StudioConfirmationLedger._initialize_schema

    def delayed_initialize(connection: sqlite3.Connection) -> None:
        entered.set()
        assert release.wait(timeout=5)
        original_initialize(connection)

    monkeypatch.setattr(
        StudioConfirmationLedger,
        "_initialize_schema",
        staticmethod(delayed_initialize),
    )
    results: list[bool] = []
    failures: list[BaseException] = []

    def register(contender: StudioConfirmationLedger) -> None:
        try:
            results.append(contender.record_session_authority(_authority()))
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    first = threading.Thread(target=register, args=(contenders[0],))
    second = threading.Thread(target=register, args=(contenders[1],))
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert failures == []
    assert sorted(results) == [False, True]
    StudioConfirmationLedger(ledger.path).verify_integrity()
    ledger.path.chmod(0o600)

    with sqlite3.connect(ledger.path) as connection:
        connection.execute("PRAGMA user_version=999")
    _assert_code(ledger.verify_integrity, "ledger_version_unsupported")


def test_symlink_and_hardlink_paths_are_rejected(tmp_path: Path) -> None:
    ledger, challenge, context = _issued_case(tmp_path)
    alias = ledger.path.parent / "alias.sqlite3"
    alias.symlink_to(ledger.path.name)
    _assert_code(
        lambda: StudioConfirmationLedger(alias),
        "ledger_security_error",
    )
    alias.unlink()

    os.link(ledger.path, alias)
    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "ledger_security_error",
    )


def test_zero_byte_bootstrap_remnant_is_adopted_by_the_next_bootstrap(tmp_path: Path) -> None:
    """An interrupted first bootstrap must not require out-of-band deletion to recover."""
    parent = tmp_path / "studio-state"
    parent.mkdir(mode=0o700)
    remnant = parent / "confirmations.sqlite3"
    descriptor = os.open(remnant, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    assert remnant.stat().st_size == 0

    ledger = StudioConfirmationLedger(remnant)
    challenge = _challenge_document()
    context = _context_document(challenge)
    # Reads on the remnant deny exactly like a missing file.
    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "ledger_security_error",
    )
    # The next bootstrap adopts and re-initializes it in place.
    assert ledger.record_session_authority(_authority()) is True
    assert remnant.stat().st_size > 0
    recorded = ledger.record_explicit_confirmation(context, challenge, authority_epoch=1)
    assert recorded.created is True
    ledger.verify_integrity()


def test_header_only_bootstrap_remnant_is_adopted_by_the_next_bootstrap(tmp_path: Path) -> None:
    """The durability pragmas write the header before the schema commits; that remnant heals."""
    parent = tmp_path / "studio-state"
    parent.mkdir(mode=0o700)
    remnant = parent / "confirmations.sqlite3"
    descriptor = os.open(remnant, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    interrupted = sqlite3.connect(remnant, isolation_level=None)
    interrupted.execute("PRAGMA journal_mode=WAL")
    interrupted.close()
    assert remnant.stat().st_size > 0

    ledger = StudioConfirmationLedger(remnant)
    challenge = _challenge_document()
    context = _context_document(challenge)
    # Reads on the schema-less remnant deny exactly like a missing file, not as corruption.
    _assert_code(
        lambda: ledger.inspect_confirmation(context, challenge, inspected_at=_INSPECTED_AT),
        "ledger_security_error",
    )
    # The next bootstrap adopts and re-initializes it in place.
    assert ledger.record_session_authority(_authority()) is True
    recorded = ledger.record_explicit_confirmation(context, challenge, authority_epoch=1)
    assert recorded.created is True
    ledger.verify_integrity()


def test_garbage_nonzero_remnant_stays_ledger_corruption(tmp_path: Path) -> None:
    """Unreadable bytes cannot prove they hold no evidence, so they are never adopted."""
    parent = tmp_path / "studio-state"
    parent.mkdir(mode=0o700)
    remnant = parent / "confirmations.sqlite3"
    descriptor = os.open(remnant, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, b"this is not a database and must never be silently discarded")
    os.close(descriptor)

    ledger = StudioConfirmationLedger(remnant)
    _assert_code(lambda: ledger.record_session_authority(_authority()), "ledger_corruption")
    assert remnant.read_bytes().startswith(b"this is not a database")


def test_committed_schema_in_an_uncheckpointed_wal_is_never_adopted(tmp_path: Path) -> None:
    """Adoption reads the database, so a commit still living in the WAL is recognized."""
    parent = tmp_path / "wal-source"
    parent.mkdir(mode=0o700)
    source = parent / "confirmations.sqlite3"
    descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    writer = sqlite3.connect(source, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("CREATE TABLE committed_evidence(value)")
    writer.execute("COMMIT")

    def copy_to(destination_parent: Path, *, with_wal: bool) -> Path:
        destination_parent.mkdir(mode=0o700)
        destination = destination_parent / "confirmations.sqlite3"
        suffixes = ("", "-wal") if with_wal else ("",)
        for suffix in suffixes:
            origin = Path(f"{source}{suffix}")
            replica = Path(f"{destination}{suffix}")
            replica.write_bytes(origin.read_bytes())
            replica.chmod(0o600)
        return destination

    main_and_wal = copy_to(tmp_path / "wal-copy", with_wal=True)
    main_only = copy_to(tmp_path / "main-only-copy", with_wal=False)
    writer.close()

    # Control: without the WAL the same main file is provably schema-less and adoptable.
    assert StudioConfirmationLedger(main_only)._is_uninitialized_remnant() is True
    # With the WAL present the committed schema is visible and must never be discarded.
    assert StudioConfirmationLedger(main_and_wal)._is_uninitialized_remnant() is False
