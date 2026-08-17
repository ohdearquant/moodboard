"""RED contracts for credential-free OpenRouter post-response finalization.

The paid boundary ends when exact provider-response evidence reaches ``response_received``.
Finalization must therefore be locally replayable after a crash without discovery, confirmation,
Keychain, transport, or another generation POST.  These tests create the two important durable
crash points with the existing injected offline executor and exercise only private local bytes.
"""

from __future__ import annotations

import base64
import dataclasses
import errno
import hashlib
import inspect
import json
import os
import shutil
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import eval.openrouter_real_e2e as real_e2e
from moodboard.attempt_journal import AttemptJournal
from moodboard.openrouter import OpenRouterHttpResponse
from tests.test_openrouter_real_e2e import _TOKEN, _http_response
from tests.test_openrouter_real_e2e_confirmation import (
    _assert_error_code,
    _execute,
    _prepare,
    _read_json,
    _write_context,
)
from tests.test_openrouter_real_e2e_transport import _exception_graph_material

JsonObject = dict[str, Any]
_REAL_E2E: Any = real_e2e

_FINALIZED_AT = "2026-08-17T03:18:00Z"
_REPLAYED_AT = "2026-08-17T03:19:00Z"


def _finalize(challenge_dir: Path, *, finalized_at: str = _FINALIZED_AT) -> Any:
    finalize = _REAL_E2E.finalize_openrouter_real_e2e
    return finalize(challenge_dir, _clock=lambda: finalized_at)


def _stage_durable_response(
    challenge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: OpenRouterHttpResponse | None = None,
) -> tuple[JsonObject, AttemptJournal]:
    """Crash before success publication, leaving verified response evidence durable."""

    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    def crash_before_success(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise KeyboardInterrupt("injected crash after response_received")

    with monkeypatch.context() as crash:
        crash.setattr(AttemptJournal, "publish_provider_success", crash_before_success)
        with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
            _execute(
                challenge_dir,
                context_path,
                transport=(lambda **_: response) if response is not None else None,
            )

    _assert_error_code(raised, "execution_failed")
    challenge = _read_json(challenge_dir / "challenge.json")
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    journal.read_provider_response(challenge["attempt_id"])
    journal.verify_integrity()
    assert not (challenge_dir / "result.json").exists()
    assert not tuple(challenge_dir.glob("provider-output-0.*"))
    return challenge, journal


def _stage_committed_success_without_ack(
    challenge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[JsonObject, AttemptJournal]:
    """Commit success, then lose its acknowledgement before the report is written."""

    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    original = AttemptJournal.publish_provider_success

    def lose_success_ack(self: AttemptJournal, *args: Any, **kwargs: Any) -> Any:
        published = original(self, *args, **kwargs)
        assert published.state.state == "succeeded"
        raise OSError("injected lost success commit acknowledgement")

    with monkeypatch.context() as crash:
        crash.setattr(AttemptJournal, "publish_provider_success", lose_success_ack)
        with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
            _execute(challenge_dir, context_path)

    _assert_error_code(raised, "execution_failed")
    challenge = _read_json(challenge_dir / "challenge.json")
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    assert journal.read_state(challenge["attempt_id"]).state == "succeeded"
    journal.read_provider_success(challenge["attempt_id"])
    journal.verify_integrity()
    assert not (challenge_dir / "result.json").exists()
    assert not tuple(challenge_dir.glob("provider-output-0.*"))
    return challenge, journal


def _invalid_media_response() -> OpenRouterHttpResponse:
    body = json.dumps(
        {
            "created": 1_786_930_000,
            "data": [
                {
                    "b64_json": base64.b64encode(b"not-a-supported-raster").decode("ascii"),
                    "media_type": "image/png",
                }
            ],
            "usage": {"cost": 0.033},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return OpenRouterHttpResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=body,
        elapsed_milliseconds=3210,
    )


def _unexpected_calls() -> tuple[list[str], Callable[[str], Callable[..., Any]]]:
    calls: list[str] = []

    def unexpected(name: str) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"finalizer crossed the {name} boundary")

        return called

    return calls, unexpected


def _artifact_fingerprint(challenge_dir: Path) -> JsonObject:
    fingerprint: JsonObject = {}
    for path in sorted(challenge_dir.iterdir(), key=lambda item: item.name):
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            fingerprint[path.name] = {
                "mode": stat.S_IMODE(metadata.st_mode),
                "inode": metadata.st_ino,
                "size": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        else:
            fingerprint[path.name] = {"mode": metadata.st_mode}
    return fingerprint


def _rewrite_json(path: Path, mutate: Callable[[JsonObject], None]) -> None:
    document = _read_json(path)
    mutate(document)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_finalize_api_is_credential_free_and_has_no_external_boundary_parameters() -> None:
    finalize = _REAL_E2E.finalize_openrouter_real_e2e
    parameters = inspect.signature(finalize).parameters

    assert tuple(parameters) == ("challenge_dir", "_clock")
    assert parameters["challenge_dir"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["_clock"].kind is inspect.Parameter.KEYWORD_ONLY
    for forbidden in (
        "confirmation_context_path",
        "_confirmation_consumer",
        "_confirmation_ledger",
        "_credential_loader",
        "_discovery_fetcher",
        "_source_fetcher",
        "_transport",
        "_uuid4",
        "authorize_one_paid_call",
    ):
        assert forbidden not in parameters


def test_finalize_recovers_crash_after_durable_response_without_second_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "response-crash"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)

    result = _finalize(challenge_dir)

    assert result.generation_run_id == challenge["generation_run_id"]
    assert result.attempt_id == challenge["attempt_id"]
    assert result.generation_post_count == 1
    assert result.states == ("prepared", "submitted", "response_received", "succeeded")
    assert result.provider_receipt_id is not None
    assert result.output_occurrence_id is not None
    assert result.provider_media_admission_result == "pass"
    assert journal.read_state(result.attempt_id).state == "succeeded"
    journal.verify_integrity()
    assert len(tuple(challenge_dir.glob("provider-output-0.*"))) == 1


@pytest.mark.parametrize("invalid_media", [False, True], ids=("success", "invalid-media"))
def test_finalizer_recovers_immediate_execution_crash_before_atomic_derived_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_media: bool,
) -> None:
    challenge_dir = tmp_path / f"pre-link-crash-{invalid_media}"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    original = real_e2e._write_compatible_private_artifact
    crashed = False

    def crash_before_link(path: Path, payload: bytes) -> None:
        nonlocal crashed
        target_name = "result.json" if invalid_media else "provider-output-0.png"
        if not crashed and path.name == target_name:
            crashed = True
            orphan = path.parent / ".openrouter-finalize-injected-pre-link-orphan"
            orphan.write_bytes(payload[: max(1, len(payload) // 2)])
            orphan.chmod(0o600)
            raise KeyboardInterrupt("injected hard stop before canonical link")
        original(path, payload)

    with monkeypatch.context() as crash:
        crash.setattr(real_e2e, "_write_compatible_private_artifact", crash_before_link)
        with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
            _execute(
                challenge_dir,
                context_path,
                transport=(lambda **_: _invalid_media_response()) if invalid_media else None,
            )

    _assert_error_code(raised, "execution_failed")
    assert crashed is True
    assert not (challenge_dir / "result.json").exists()
    assert not tuple(challenge_dir.glob("provider-output-0.*"))
    recovered = _finalize(challenge_dir)

    expected_state = "response_received" if invalid_media else "succeeded"
    assert recovered.states[-1] == expected_state
    assert (challenge_dir / "result.json").is_file()
    if invalid_media:
        assert recovered.output_occurrence_id is None
        assert not tuple(challenge_dir.glob("provider-output-0.*"))
    else:
        assert recovered.output_occurrence_id is not None
        assert len(tuple(challenge_dir.glob("provider-output-0.*"))) == 1


def test_finalize_invalid_media_records_structural_failure_and_locality_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "invalid-media-crash"
    challenge, journal = _stage_durable_response(
        challenge_dir,
        monkeypatch,
        response=_invalid_media_response(),
    )

    result = _finalize(challenge_dir)

    assert result.attempt_id == challenge["attempt_id"]
    assert result.states == ("prepared", "submitted", "response_received")
    assert result.provider_receipt_id is not None
    assert result.output_occurrence_id is None
    assert result.provider_media_admission_result == "unsupported_format"
    assert result.raw_structural_result == "fail"
    assert result.raw_structural_reason == "unsupported_format"
    assert result.raw_locality_result == "not_run"
    report = _read_json(challenge_dir / "result.json")
    assert report["provider_lifecycle_state"] == "response_received"
    assert report["raw_structural_judgment"]["result"]["state"] == "fail"
    assert report["raw_locality_judgment"]["result"]["state"] == "not_run"
    assert report["localized_edit_gate_status"] == "not_eligible"
    assert not tuple(challenge_dir.glob("provider-output-0.*"))
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    journal.verify_integrity()


def test_finalize_invalid_media_exact_replay_does_not_sample_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "invalid-media-clock-free-replay"
    _stage_durable_response(
        challenge_dir,
        monkeypatch,
        response=_invalid_media_response(),
    )
    first = _finalize(challenge_dir)
    before = _artifact_fingerprint(challenge_dir)

    def forbidden_clock() -> str:
        raise AssertionError("a completed invalid-media result must replay without a new clock")

    second = _REAL_E2E.finalize_openrouter_real_e2e(
        challenge_dir,
        _clock=forbidden_clock,
    )

    assert second == first
    assert _artifact_fingerprint(challenge_dir) == before


def test_finalize_invalid_media_fails_closed_when_verification_raises_and_recovers_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised verification must not publish as not_run and must not wedge exact replay."""
    challenge_dir = tmp_path / "invalid-media-verifier-raises"
    challenge, journal = _stage_durable_response(
        challenge_dir,
        monkeypatch,
        response=_invalid_media_response(),
    )

    real_verifier = real_e2e.verify_output_structure
    calls = {"count": 0}

    def flaky_verifier(*args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 1:
            raise MemoryError("decoder under memory pressure")
        return real_verifier(*args, **kwargs)

    monkeypatch.setattr(real_e2e, "verify_output_structure", flaky_verifier)

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)
    _assert_error_code(raised, "finalization_artifact_invalid")
    assert not (challenge_dir / "result.json").exists()
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"

    recovered = _finalize(challenge_dir)
    assert recovered.raw_structural_result == "fail"
    assert recovered.raw_structural_reason == "unsupported_format"
    report = _read_json(challenge_dir / "result.json")
    assert report["raw_structural_judgment"]["result"]["state"] == "fail"


def test_finalize_replays_a_completed_invalid_media_execute_run(tmp_path: Path) -> None:
    """The production recovery sequence: execute completes its rejected-media branch and
    writes result.json; a later finalize on the same directory converges byte-identically."""
    challenge_dir = tmp_path / "completed-invalid-media"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    executed = _execute(
        challenge_dir, context_path, transport=lambda **_: _invalid_media_response()
    )
    before = _artifact_fingerprint(challenge_dir)

    finalized = _finalize(challenge_dir)

    assert finalized == executed
    assert _artifact_fingerprint(challenge_dir) == before


def test_execute_fails_conflict_on_a_divergent_preexisting_result_artifact(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "planted-divergent-result"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    planted = challenge_dir / "result.json"
    planted.write_bytes(b'{"forged": true}')
    planted.chmod(0o600)

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir, context_path, transport=lambda **_: _invalid_media_response()
        )
    _assert_error_code(raised, "finalization_artifact_conflict")


def test_invalid_media_finalizer_detects_concurrent_terminal_writer_before_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "invalid-media-concurrent-terminal"
    challenge, journal = _stage_durable_response(
        challenge_dir,
        monkeypatch,
        response=_invalid_media_response(),
    )
    original = real_e2e._finalized_provider_evidence

    def append_failed_before_return(*args: Any, **kwargs: Any) -> Any:
        evidence = original(*args, **kwargs)
        active_journal = args[1]
        state = active_journal.read_state(challenge["attempt_id"])
        assert state.state == "response_received"
        failed = real_e2e.seal_provider_artifact(
            {
                "schema_version": real_e2e.EVENT_VERSION,
                "attempt_id": challenge["attempt_id"],
                "sequence": state.next_sequence,
                "state": "failed",
                "recorded_at": _REPLAYED_AT,
                "detail": {
                    "kind": "failed",
                    "failure_stage": "output_validation",
                    "failure_code": "concurrent_terminal_writer",
                },
            }
        )
        active_journal.append_event(
            failed,
            expected_head_event_id=state.head_event_id,
            expected_next_sequence=state.next_sequence,
        )
        return evidence

    monkeypatch.setattr(
        real_e2e,
        "_finalized_provider_evidence",
        append_failed_before_return,
    )
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_not_ready")
    assert journal.read_state(challenge["attempt_id"]).state == "failed"
    assert not (challenge_dir / "result.json").exists()


def test_finalize_recovers_succeeded_after_lost_commit_ack_without_republishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "lost-success-ack"
    challenge, journal = _stage_committed_success_without_ack(challenge_dir, monkeypatch)
    calls, unexpected = _unexpected_calls()
    monkeypatch.setattr(AttemptJournal, "publish_provider_success", unexpected("success publish"))

    result = _finalize(challenge_dir)

    assert calls == []
    assert result.generation_run_id == challenge["generation_run_id"]
    assert result.attempt_id == challenge["attempt_id"]
    assert result.states[-1] == "succeeded"
    stored = journal.read_provider_success(result.attempt_id)
    assert result.output_occurrence_id == stored.occurrences[0].output_occurrence_id
    assert len(tuple(challenge_dir.glob("provider-output-0.*"))) == 1
    journal.verify_integrity()


def test_finalize_recovers_its_own_lost_success_commit_ack_in_same_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "finalizer-lost-success-ack"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    original = AttemptJournal.publish_provider_success

    def lose_ack(self: AttemptJournal, *args: Any, **kwargs: Any) -> Any:
        published = original(self, *args, **kwargs)
        assert published.state.state == "succeeded"
        raise OSError("injected finalizer success commit acknowledgement loss")

    monkeypatch.setattr(AttemptJournal, "publish_provider_success", lose_ack)
    result = _finalize(challenge_dir)

    assert result.attempt_id == challenge["attempt_id"]
    assert result.states[-1] == "succeeded"
    assert journal.read_state(result.attempt_id).state == "succeeded"
    assert (challenge_dir / "result.json").is_file()
    assert len(tuple(challenge_dir.glob("provider-output-0.*"))) == 1
    journal.verify_integrity()


def test_finalize_exact_replay_returns_same_result_without_mutating_any_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "exact-replay"
    _stage_durable_response(challenge_dir, monkeypatch)
    first = _finalize(challenge_dir)
    before = _artifact_fingerprint(challenge_dir)

    second = _finalize(challenge_dir, finalized_at=_REPLAYED_AT)

    assert second == first
    assert _artifact_fingerprint(challenge_dir) == before


def test_finalize_succeeded_replay_does_not_sample_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "clock-free-replay"
    _stage_durable_response(challenge_dir, monkeypatch)
    first = _finalize(challenge_dir)
    before = _artifact_fingerprint(challenge_dir)

    def forbidden_clock() -> str:
        raise AssertionError("a stored success must not need a new finalization timestamp")

    second = _REAL_E2E.finalize_openrouter_real_e2e(
        challenge_dir,
        _clock=forbidden_clock,
    )

    assert second == first
    assert _artifact_fingerprint(challenge_dir) == before


def test_finalize_paid_response_remains_recoverable_after_challenge_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "expired-paid-response"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)

    result = _finalize(challenge_dir, finalized_at="2026-08-18T03:18:00Z")

    assert result.attempt_id == challenge["attempt_id"]
    assert result.states[-1] == "succeeded"
    assert journal.read_state(result.attempt_id).state == "succeeded"
    journal.verify_integrity()


def test_concurrent_exact_finalizers_converge_on_one_success_and_one_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "concurrent-finalizers"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    original_rebuild = real_e2e._rebuild_finalization_inputs
    ready = threading.Barrier(2)

    def synchronized_rebuild(*args: Any, **kwargs: Any) -> Any:
        rebuilt = original_rebuild(*args, **kwargs)
        ready.wait(timeout=10)
        return rebuilt

    monkeypatch.setattr(real_e2e, "_rebuild_finalization_inputs", synchronized_rebuild)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: _finalize(challenge_dir), range(2)))

    assert results[0] == results[1]
    events = journal.read_events(challenge["attempt_id"])
    assert tuple(event.state for event in events) == (
        "prepared",
        "submitted",
        "response_received",
        "succeeded",
    )
    assert len([event for event in events if event.state == "succeeded"]) == 1
    assert len(tuple(challenge_dir.glob("provider-output-0.*"))) == 1
    journal.verify_integrity()


def test_stale_response_reader_converges_after_peer_finishes_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "stale-response-reader"
    _stage_durable_response(challenge_dir, monkeypatch)
    original_read_state = AttemptJournal.read_state
    stale_head_seen = threading.Event()
    peer_finished = threading.Event()
    blocked = False

    def controlled_read_state(self: AttemptJournal, attempt_id: str) -> Any:
        nonlocal blocked
        state = original_read_state(self, attempt_id)
        if (
            threading.current_thread().name.startswith("stale-finalizer")
            and not blocked
            and state.state == "response_received"
        ):
            blocked = True
            stale_head_seen.set()
            assert peer_finished.wait(timeout=15)
        return state

    monkeypatch.setattr(AttemptJournal, "read_state", controlled_read_state)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-finalizer") as pool:
        stale = pool.submit(_finalize, challenge_dir)
        assert stale_head_seen.wait(timeout=15)
        peer = _finalize(challenge_dir)
        peer_finished.set()
        replay = stale.result(timeout=15)

    assert replay == peer
    assert replay.states[-1] == "succeeded"


def test_live_staging_link_cleanup_converges_with_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "staging-race"
    target.mkdir(mode=0o700)
    output = target / "provider-output-0.png"
    payload = b"exact-provider-output"
    original_link = real_e2e.os.link
    linked = threading.Event()
    cleaned = threading.Event()

    def controlled_link(*args: Any, **kwargs: Any) -> None:
        original_link(*args, **kwargs)
        linked.set()
        assert cleaned.wait(timeout=15)

    monkeypatch.setattr(real_e2e.os, "link", controlled_link)
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher = pool.submit(real_e2e._write_compatible_private_artifact, output, payload)
        assert linked.wait(timeout=15)
        assert real_e2e._compatible_private_artifact(output, payload) is True
        cleaned.set()
        publisher.result(timeout=15)

    assert output.read_bytes() == payload
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_concurrent_replays_converge_when_cleaning_one_linked_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "linked-stage-replay"
    target.mkdir(mode=0o700)
    stage = target / ".openrouter-finalize-crash-window"
    final = target / "result.json"
    payload = b"exact-sanitized-result"
    stage.write_bytes(payload)
    stage.chmod(0o600)
    real_e2e.os.link(stage, final, follow_symlinks=False)
    ready = threading.Barrier(2)
    original_unlink = Path.unlink

    def synchronized_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        if path.name.startswith(".openrouter-finalize-"):
            ready.wait(timeout=15)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", synchronized_unlink)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda _: real_e2e._compatible_private_artifact(final, payload),
                range(2),
            )
        )

    assert outcomes == (True, True)
    assert final.read_bytes() == payload
    assert final.stat().st_nlink == 1
    assert not stage.exists()


def test_finalize_never_reenters_discovery_confirmation_credential_or_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "local-only"
    _stage_durable_response(challenge_dir, monkeypatch)
    calls, unexpected = _unexpected_calls()
    monkeypatch.setattr(real_e2e, "fetch_live_discovery", unexpected("discovery"))
    monkeypatch.setattr(real_e2e, "load_pinned_source", unexpected("source fetch"))
    monkeypatch.setattr(real_e2e, "_confirmation_snapshot", unexpected("confirmation"))
    monkeypatch.setattr(real_e2e, "load_openrouter_keychain_token", unexpected("credential"))
    monkeypatch.setattr(
        real_e2e,
        "direct_openrouter_https_transport",
        unexpected("transport"),
    )
    monkeypatch.setattr(real_e2e, "execute_openrouter_real_e2e", unexpected("execution"))

    result = _finalize(challenge_dir)

    assert result.states[-1] == "succeeded"
    assert result.generation_post_count == 1
    assert calls == []


def test_finalize_rejects_tampered_challenge_with_one_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "challenge-tamper"
    _stage_durable_response(challenge_dir, monkeypatch)
    _rewrite_json(
        challenge_dir / "challenge.json",
        lambda document: document.__setitem__(
            "wire_body_byte_count", document["wire_body_byte_count"] + 1
        ),
    )

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "challenge_artifact_drift")
    assert not (challenge_dir / "result.json").exists()


def test_finalize_rejects_tampered_plan_before_journal_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "plan-tamper"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    _rewrite_json(
        challenge_dir / "plan.json",
        lambda document: document["generation_attempt"].__setitem__(
            "attempt_id", "90000000-0000-4000-8000-000000000009"
        ),
    )

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_invalid")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    assert not (challenge_dir / "result.json").exists()


def test_finalize_rejects_journal_attempt_authority_drift_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "journal-attempt-drift"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    original = AttemptJournal.read_attempt

    def drifted_attempt(self: AttemptJournal, attempt_id: str) -> Any:
        stored = original(self, attempt_id)
        return dataclasses.replace(stored, adapter_revision="evil.adapter.v1")

    monkeypatch.setattr(AttemptJournal, "read_attempt", drifted_attempt)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_invalid")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before


def test_finalize_rederives_confirmation_overlay_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "overlay-rederivation"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    monkeypatch.setattr(real_e2e, "_render_mask_overlay", lambda *_: b"different-overlay")

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_invalid")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before


def test_finalize_rejects_tampered_consumption_proof_before_journal_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "consumption-tamper"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    _rewrite_json(
        challenge_dir / "consumed.json",
        lambda document: document.__setitem__("confirmation_context_id", "f" * 64),
    )

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_invalid")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    assert not (challenge_dir / "result.json").exists()


@pytest.mark.parametrize("artifact_name", ["result.json", "provider-output-0.png"])
def test_finalize_rejects_conflicting_derived_artifact_without_rewriting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    challenge_dir = tmp_path / f"conflicting-{artifact_name.replace('.', '-')}"
    _stage_durable_response(challenge_dir, monkeypatch)
    _finalize(challenge_dir)
    path = challenge_dir / artifact_name
    assert path.exists()
    path.write_bytes(b"caller-owned-conflicting-bytes")
    path.chmod(0o600)
    before = path.read_bytes()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir, finalized_at=_REPLAYED_AT)

    _assert_error_code(raised, "finalization_artifact_conflict")
    assert path.read_bytes() == before


def test_finalize_rejects_planted_output_before_success_journal_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "planted-output-before-success"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    planted = challenge_dir / "provider-output-0.png"
    planted.write_bytes(b"planted-conflicting-output")
    planted.chmod(0o600)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_conflict")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    assert planted.read_bytes() == b"planted-conflicting-output"
    assert not (challenge_dir / "result.json").exists()


def test_finalize_rejects_copied_directory_before_journal_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "bound-original"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    copied = tmp_path / "bound-copy"
    shutil.copytree(challenge_dir, copied)
    copied.chmod(0o700)

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(copied)

    _assert_error_code(raised, "challenge_binding_mismatch")
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    assert not (copied / "result.json").exists()


def test_finalize_result_cross_binds_all_durable_provider_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "result-identities"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    response = journal.read_provider_response(challenge["attempt_id"])

    result = _finalize(challenge_dir)

    success = journal.read_provider_success(challenge["attempt_id"])
    report = _read_json(challenge_dir / "result.json")
    assert report["schema_version"] == "moodboard.openrouter-real-e2e-summary.v1"
    assert result.generation_run_id == report["generation_run_id"] == challenge["generation_run_id"]
    assert result.attempt_id == report["attempt_id"] == challenge["attempt_id"]
    assert (
        result.provider_receipt_id
        == report["provider_receipt_id"]
        == response.receipt.provider_receipt_id
    )
    assert (
        result.output_occurrence_id
        == report["output_occurrence_id"]
        == success.occurrences[0].output_occurrence_id
    )
    assert report["states"] == ["prepared", "submitted", "response_received", "succeeded"]
    assert report["generation_post_count"] == 1
    assert report["semantic_aesthetic_result"] == "not_run"
    assert report["compositor_result"] == "not_run"
    journal.verify_integrity()


def test_finalize_rejects_missing_journal_with_stable_local_error(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "not-executed"
    _prepare(challenge_dir)

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    _assert_error_code(raised, "finalization_artifact_invalid")
    assert not (challenge_dir / "attempts.sqlite3").exists()
    assert not (challenge_dir / "result.json").exists()


def test_finalize_outcome_unknown_is_not_ready_and_never_retries(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "outcome-unknown"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    def ambiguous_transport(**_: Any) -> OpenRouterHttpResponse:
        raise TimeoutError("ambiguous transport after durable claim")

    dispatched = _execute(
        challenge_dir,
        context_path,
        transport=ambiguous_transport,
    )
    assert dispatched.states[-1] == "outcome_unknown"
    before = _artifact_fingerprint(challenge_dir)

    def forbidden_clock() -> str:
        raise AssertionError("outcome_unknown finalization must not sample a clock")

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _REAL_E2E.finalize_openrouter_real_e2e(
            challenge_dir,
            _clock=forbidden_clock,
        )

    _assert_error_code(raised, "finalization_not_ready")
    assert _artifact_fingerprint(challenge_dir) == before


def test_finalize_rejects_non_callable_clock_before_touching_local_artifacts(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "clock"
    _prepare(challenge_dir)
    before = _artifact_fingerprint(challenge_dir)
    finalize = _REAL_E2E.finalize_openrouter_real_e2e

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        finalize(challenge_dir, _clock=None)

    _assert_error_code(raised, "evaluation_callable_invalid")
    assert _artifact_fingerprint(challenge_dir) == before
    assert _TOKEN not in repr(raised.value)


def test_finalize_rejects_canonical_clock_that_regresses_behind_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "regressing-finalization-clock"
    challenge, journal = _stage_durable_response(challenge_dir, monkeypatch)
    before = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir, finalized_at="2026-08-17T03:16:30Z")

    _assert_error_code(raised, "clock_invalid")
    after = tuple(event.attempt_event_id for event in journal.read_events(challenge["attempt_id"]))
    assert after == before
    assert journal.read_state(challenge["attempt_id"]).state == "response_received"
    assert not (challenge_dir / "result.json").exists()


def test_finalize_drops_provider_material_from_public_exception_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge_dir = tmp_path / "sanitized-finalizer-error"
    _stage_durable_response(challenge_dir, monkeypatch)

    def explode_with_provider_material(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError(f"private provider material: {_TOKEN}")

    monkeypatch.setattr(AttemptJournal, "read_provider_response", explode_with_provider_material)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _finalize(challenge_dir)

    assert raised.value.code == "finalization_failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)


def test_finalize_sanitizes_hostile_pathlike_conversion() -> None:
    class HostilePath:
        def __fspath__(self) -> str:
            raise RuntimeError(f"hostile path material: {_TOKEN}")

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _REAL_E2E.finalize_openrouter_real_e2e(HostilePath())

    assert raised.value.code == "finalization_artifact_invalid"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _TOKEN not in _exception_graph_material(raised.value)


def test_a_live_peer_unlink_between_link_count_and_enumeration_converges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A publisher between its final link and its staging cleanup is not a conflict."""
    private_dir = tmp_path / "artifacts"
    private_dir.mkdir()
    private_dir.chmod(0o700)
    final = private_dir / "result.json"
    payload = b'{"ok": true}\n'
    descriptor = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    staging = private_dir / ".openrouter-finalize-peer"
    os.link(final, staging)
    assert final.lstat().st_nlink == 2

    real_iterdir = Path.iterdir

    def racing_iterdir(self: Path) -> Any:
        entries = list(real_iterdir(self))
        if self == private_dir and staging in entries:
            # The peer finishes its cleanup after our link-count sample, before enumeration.
            staging.unlink()
            entries = [entry for entry in entries if entry != staging]
        return iter(entries)

    monkeypatch.setattr(Path, "iterdir", racing_iterdir)
    assert _REAL_E2E._compatible_private_artifact(final, payload) is True
    assert final.lstat().st_nlink == 1
    assert final.read_bytes() == payload


def test_a_staging_io_failure_reports_its_own_code_not_a_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No space is not divergent bytes: the conflict code must stay unambiguous."""
    private_dir = tmp_path / "artifacts"
    private_dir.mkdir()
    private_dir.chmod(0o700)

    def failing_mkstemp(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(real_e2e.tempfile, "mkstemp", failing_mkstemp)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _REAL_E2E._write_compatible_private_artifact(private_dir / "result.json", b"{}\n")
    _assert_error_code(raised, "finalization_artifact_io_failed")
    assert not (private_dir / "result.json").exists()


def test_a_conflict_exit_still_scans_the_private_artifacts(tmp_path: Path) -> None:
    """The scan verdict outranks the conflict diagnostic; a leak is never masked by it."""
    challenge_dir = tmp_path / "planted-divergent-and-leak"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    planted = challenge_dir / "result.json"
    planted.write_bytes(b'{"forged": true}')
    planted.chmod(0o600)
    leak = challenge_dir / "leak.bin"
    leak.write_bytes(_TOKEN.encode("utf-8"))
    leak.chmod(0o600)

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(challenge_dir, context_path)
    _assert_error_code(raised, "credential_material_persisted")


def test_an_unreadable_challenge_dir_never_reports_a_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An enumeration failure is an I/O condition, never a divergent-bytes claim.

    The terminal scan enumerates the same directory, so when enumeration is broken the
    honest outcome is the scan's own failure code — never finalization_artifact_conflict,
    which asserts divergent bytes were found and preserved.
    """
    challenge_dir = tmp_path / "unreadable-enumeration"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    resolved = challenge_dir.resolve()
    posted = {"value": False}

    real_iterdir = Path.iterdir

    def failing_iterdir(self: Path) -> Any:
        if self == resolved and posted["value"]:
            raise PermissionError("challenge directory became unreadable")
        return real_iterdir(self)

    def marking_transport(**kwargs: Any) -> OpenRouterHttpResponse:
        posted["value"] = True
        return _http_response()

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(challenge_dir, context_path, transport=marking_transport)
    _assert_error_code(raised, "artifact_secret_scan_failed")
