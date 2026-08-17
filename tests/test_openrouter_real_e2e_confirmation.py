"""RED contracts for the two-phase OpenRouter real-provider confirmation boundary.

Preparation is deliberately non-authorizing: it snapshots exact discovery, source, mask, Studio
authority, compact-summary, and overlay bytes into a private challenge directory.  A separate,
trusted Studio boundary writes the closed confirmation context.  Execution may refresh discovery
and resolve a credential only after it has proved that the challenge, context, path/inode binding,
and expiry still match.  These tests inject every external byte and never access Keychain or the
network.

The challenge and compact-summary records are their first artifact versions even though they form
the v2 evaluation API.  Their selected wire versions are therefore ``*.v1``.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import inspect
import json
import os
import shutil
import signal
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

import eval.openrouter_real_e2e as real_e2e
from moodboard.attempt_journal import AttemptJournal
from moodboard.contracts import compute_document_identity, verify_document_identity
from moodboard.locality import compile_canonical_raster, compile_rectangle_mask
from moodboard.openrouter import OpenRouterHttpResponse
from moodboard.studio_confirmation_ledger import (
    ConfirmationConsumptionResult,
    StudioConfirmationLedger,
    StudioConfirmationLedgerError,
    StudioSessionAuthority,
)
from tests.test_openrouter_discovery import _DISCOVERY_BODY
from tests.test_openrouter_real_e2e import (
    _SOURCE_BYTES,
    _TOKEN,
    _http_response,
    _uuid_supplier,
)

JsonObject = dict[str, Any]
_REAL_E2E: Any = real_e2e

_CHALLENGE_VERSION = "moodboard.openrouter-real-e2e-confirmation-challenge.v1"
_SUMMARY_VERSION = "moodboard.openrouter-real-e2e-compact-summary.v1"
_CONTEXT_VERSION = "moodboard.openrouter-real-e2e-confirmation-context.v1"
_PREPARED_AT = "2026-08-17T03:15:00Z"
_CONFIRMED_AT = "2026-08-17T03:16:00Z"
_EXECUTED_AT = "2026-08-17T03:17:00Z"
_CREATIVE_SESSION_ID = "10000000-0000-4000-8000-000000000001"
_PRINCIPAL_ID = "20000000-0000-4000-8000-000000000002"
_STUDIO_SESSION_ID = "30000000-0000-4000-8000-000000000003"
_AUTHORITY_EPOCH = 1
_SESSION_ACTIVE_FROM = "2026-08-17T03:00:00Z"
_SESSION_EXPIRES_AT = "2026-08-17T05:00:00Z"


def _digest(character: str) -> str:
    assert len(character) == 1 and character in "0123456789abcdef"
    return character * 64


def _authority_bytes() -> bytes:
    """One already-authorized Studio projection; the harness may store but never mint it."""

    document: JsonObject = {
        "schema_version": "moodboard.openrouter-real-e2e-authority.v1",
        "authority_id": "0" * 64,
        "creative_session_id": _CREATIVE_SESSION_ID,
        "board": {
            "board_id": _digest("1"),
            "representation_id": _digest("2"),
            "fit_policy_id": _digest("3"),
        },
        "retrieval_route": {
            "schema_version": "moodboard.intent-route.collection-gate.v1",
            "route_policy_id": _digest("4"),
            "eligible_corpus_sha256": _digest("5"),
            "empty_result_policy": "no_ungated_fallback",
            "evidence_artifact_id": _digest("6"),
        },
        "references": [
            {
                "reference_occurrence_id": "40000000-0000-4000-8000-000000000004",
                "role": "visual_context",
                "asset_id": "50000000-0000-4000-8000-000000000005",
                "content_ref": _digest("7"),
                "source_search_rank": 1,
                "routed_rank": 1,
                "source_similarity": 0.843299582601,
                "route_reason": "declared_collection_match",
                "provider_use": "prompt_context_only",
                "prompt_context": {
                    "compiler_revision": "moodboard.reference-prompt.v1",
                    "text_items": ["Mature lemon canopy", "Natural branching structure"],
                },
            }
        ],
    }
    document["authority_id"] = compute_document_identity(
        document,
        schema_version="moodboard.openrouter-real-e2e-authority.v1",
        identity_field="authority_id",
    )
    return _json_bytes(document)


def _json_bytes(document: JsonObject) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


_AUTHORITY_BYTES = _authority_bytes()


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _prepare(
    challenge_dir: Path,
    *,
    discovery_body: bytes = _DISCOVERY_BODY,
    source_bytes: bytes = _SOURCE_BYTES,
) -> Any:
    prepare = _REAL_E2E.prepare_openrouter_real_e2e
    return prepare(
        challenge_dir,
        _discovery_fetcher=lambda: discovery_body,
        _source_fetcher=lambda: source_bytes,
        _authority_bundle=_AUTHORITY_BYTES,
        _clock=lambda: _PREPARED_AT,
    )


def _artifact_path(challenge_dir: Path, name: str) -> Path:
    challenge = _read_json(challenge_dir / "challenge.json")
    descriptor = challenge["artifacts"][name]
    assert isinstance(descriptor, dict)
    relative = Path(descriptor["relative_path"])
    assert not relative.is_absolute()
    assert ".." not in relative.parts
    return challenge_dir / relative


def _context_document(challenge_dir: Path, **overrides: Any) -> JsonObject:
    challenge = _read_json(challenge_dir / "challenge.json")
    document: JsonObject = {
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
    document["confirmation_context_id"] = compute_document_identity(
        document,
        schema_version=_CONTEXT_VERSION,
        identity_field="confirmation_context_id",
    )
    return document


def _write_context(
    challenge_dir: Path,
    *,
    name: str = "confirmation-context.json",
    register_confirmation: bool = True,
    **overrides: Any,
) -> Path:
    path = challenge_dir / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, _json_bytes(_context_document(challenge_dir, **overrides)))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    if register_confirmation:
        # Registration failures always raise: silently skipping the grant would change what a
        # tamper test exercises. A caller building a context the ledger must reject passes
        # register_confirmation=False explicitly.
        _register_confirmation(challenge_dir, path)
    return path


def _ledger_path(challenge_dir: Path) -> Path:
    state_dir = challenge_dir.parent / ".studio-confirmation-ledgers"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state_dir.chmod(0o700)
    return (state_dir / f"{challenge_dir.name}.sqlite3").resolve()


def _ledger_for(challenge_dir: Path) -> StudioConfirmationLedger:
    return StudioConfirmationLedger(_ledger_path(challenge_dir))


def _register_confirmation(
    challenge_dir: Path,
    context_path: Path,
) -> StudioConfirmationLedger:
    ledger = _ledger_for(challenge_dir)
    authority = StudioSessionAuthority(
        principal_id=_PRINCIPAL_ID,
        studio_session_id=_STUDIO_SESSION_ID,
        creative_session_id=_CREATIVE_SESSION_ID,
        authority_epoch=_AUTHORITY_EPOCH,
        active_from=_SESSION_ACTIVE_FROM,
        expires_at=_SESSION_EXPIRES_AT,
    )
    ledger.record_session_authority(authority)
    ledger.record_explicit_confirmation(
        _read_json(context_path),
        _read_json(challenge_dir / "challenge.json"),
        authority_epoch=_AUTHORITY_EPOCH,
    )
    return ledger


def _rewrite_context(path: Path, mutate: Callable[[JsonObject], None]) -> None:
    document = _read_json(path)
    mutate(document)
    document["confirmation_context_id"] = compute_document_identity(
        document,
        schema_version=_CONTEXT_VERSION,
        identity_field="confirmation_context_id",
    )
    path.write_bytes(_json_bytes(document))
    path.chmod(0o600)


def _execute(
    challenge_dir: Path,
    context_path: Path,
    *,
    discovery_fetcher: Callable[[], bytes] | None = None,
    credential_loader: Callable[[str], str] | None = None,
    transport: Callable[..., OpenRouterHttpResponse] | None = None,
    confirmation_ledger: StudioConfirmationLedger | None = None,
    clock: Callable[[], str] | None = None,
) -> Any:
    execute = _REAL_E2E.execute_openrouter_real_e2e
    return execute(
        challenge_dir,
        context_path,
        _discovery_fetcher=discovery_fetcher or (lambda: _DISCOVERY_BODY),
        _credential_loader=credential_loader or (lambda _: _TOKEN),
        _transport=transport or (lambda **_: _http_response()),
        _confirmation_ledger=confirmation_ledger or _ledger_for(challenge_dir),
        _clock=clock or (lambda: _EXECUTED_AT),
        _uuid4=_uuid_supplier(),
    )


def _assert_error_code(error: pytest.ExceptionInfo[BaseException], code: str) -> None:
    assert isinstance(error.value, real_e2e.OpenRouterRealE2EError)
    assert error.value.code == code
    assert _TOKEN not in str(error.value)
    assert _TOKEN not in repr(error.value)


def _unexpected_calls() -> tuple[list[str], Callable[[str], Callable[..., Any]]]:
    calls: list[str] = []

    def unexpected(name: str) -> Callable[..., Any]:
        def called(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            calls.append(name)
            raise AssertionError(f"{name} crossed a pre-dispatch rejection boundary")

        return called

    return calls, unexpected


def _descriptor(payload: bytes, relative_path: str) -> JsonObject:
    return {
        "relative_path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
    }


def test_prepare_api_has_no_credential_or_transport_and_returns_frozen_challenge(
    tmp_path: Path,
) -> None:
    prepare = _REAL_E2E.prepare_openrouter_real_e2e
    parameters = inspect.signature(prepare).parameters
    assert "_credential_loader" not in parameters
    assert "_transport" not in parameters
    assert "authorize_one_paid_call" not in parameters

    challenge = _prepare(tmp_path / "challenge")

    assert dataclasses.is_dataclass(challenge)
    frozen_challenge: Any = challenge
    stored = _read_json(tmp_path / "challenge/challenge.json")
    assert frozen_challenge.challenge_id == stored["challenge_id"]
    assert frozen_challenge.compact_summary_id == stored["compact_summary_id"]
    with pytest.raises(FrozenInstanceError):
        frozen_challenge.challenge_id = _digest("f")


def test_execute_requires_a_durable_ledger_and_has_no_callable_authority_seam() -> None:
    parameters = inspect.signature(_REAL_E2E.execute_openrouter_real_e2e).parameters

    assert "_confirmation_ledger" in parameters
    assert "_confirmation_consumer" not in parameters


def test_execute_rejects_a_ledger_subclass_before_discovery_or_key(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "ledger-subclass"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls, unexpected = _unexpected_calls()

    class ForgedLedger(StudioConfirmationLedger):
        pass

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=ForgedLedger(_ledger_path(challenge_dir)),
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "confirmation_authority_unavailable")
    assert calls == []


def test_execute_rejects_a_ledger_inside_the_challenge_directory_before_discovery(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "embedded-ledger"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    state_dir = challenge_dir / "studio-state"
    state_dir.mkdir(mode=0o700)
    embedded = StudioConfirmationLedger((state_dir / "confirmations.sqlite3").resolve())
    embedded.record_session_authority(
        StudioSessionAuthority(
            principal_id=_PRINCIPAL_ID,
            studio_session_id=_STUDIO_SESSION_ID,
            creative_session_id=_CREATIVE_SESSION_ID,
            authority_epoch=_AUTHORITY_EPOCH,
            active_from=_SESSION_ACTIVE_FROM,
            expires_at=_SESSION_EXPIRES_AT,
        )
    )
    embedded.record_explicit_confirmation(
        _read_json(context_path),
        _read_json(challenge_dir / "challenge.json"),
        authority_epoch=_AUTHORITY_EPOCH,
    )
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=embedded,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "confirmation_authority_invalid")
    assert calls == []


def test_execute_rejects_a_ledger_nested_in_a_sibling_challenge_root(
    tmp_path: Path,
) -> None:
    sibling = tmp_path / "sibling-challenge"
    _prepare(sibling)
    challenge_dir = tmp_path / "target-challenge"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    state_dir = sibling / "studio-state"
    state_dir.mkdir(mode=0o700)
    embedded = StudioConfirmationLedger((state_dir / "confirmations.sqlite3").resolve())
    embedded.record_session_authority(
        StudioSessionAuthority(
            principal_id=_PRINCIPAL_ID,
            studio_session_id=_STUDIO_SESSION_ID,
            creative_session_id=_CREATIVE_SESSION_ID,
            authority_epoch=_AUTHORITY_EPOCH,
            active_from=_SESSION_ACTIVE_FROM,
            expires_at=_SESSION_EXPIRES_AT,
        )
    )
    embedded.record_explicit_confirmation(
        _read_json(context_path),
        _read_json(challenge_dir / "challenge.json"),
        authority_epoch=_AUTHORITY_EPOCH,
    )
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=embedded,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "confirmation_authority_invalid")
    assert calls == []


def test_prepare_requires_exactly_one_authority_source_before_external_io(tmp_path: Path) -> None:
    calls, unexpected = _unexpected_calls()
    prepare = _REAL_E2E.prepare_openrouter_real_e2e

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as missing:
        prepare(
            tmp_path / "missing-authority",
            _discovery_fetcher=unexpected("discovery"),
            _source_fetcher=unexpected("source"),
        )
    _assert_error_code(missing, "authority_unavailable")

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as ambiguous:
        prepare(
            tmp_path / "ambiguous-authority",
            _discovery_fetcher=unexpected("discovery"),
            _source_fetcher=unexpected("source"),
            _authority_bundle=_AUTHORITY_BYTES,
            _authority_loader=unexpected("authority"),
        )
    _assert_error_code(ambiguous, "authority_ambiguous")
    assert calls == []


def test_injected_credential_seam_enforces_the_production_token_shape(tmp_path: Path) -> None:
    """An empty or non-ASCII token would make the artifact secret scan vacuous."""
    for bad_token in ("", "short", "token with spaces padded to length!!", "秘密" * 16):
        challenge_dir = tmp_path / f"bad-token-{len(bad_token)}"
        _prepare(challenge_dir)
        context_path = _write_context(challenge_dir)
        with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
            _execute(
                challenge_dir,
                context_path,
                credential_loader=lambda _, token=bad_token: token,
            )
        _assert_error_code(raised, "credential_unavailable")


def test_artifact_secret_scan_detects_a_persisted_token(tmp_path: Path) -> None:
    scan_dir = tmp_path / "artifacts"
    scan_dir.mkdir()
    scan_dir.chmod(0o700)
    clean = scan_dir / "result.json"
    clean.write_bytes(b'{"ok": true}')
    clean.chmod(0o600)
    real_e2e._scan_private_artifacts(scan_dir, _TOKEN)

    leaky = scan_dir / "leak.json"
    leaky.write_bytes(json.dumps({"token": _TOKEN}).encode("utf-8"))
    leaky.chmod(0o600)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        real_e2e._scan_private_artifacts(scan_dir, _TOKEN)
    _assert_error_code(raised, "credential_material_persisted")


def test_execution_reports_the_secret_scan_verdict_as_its_own_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one diagnostic built to be unambiguous must survive the generic failure collapse."""
    challenge_dir = tmp_path / "scan-verdict"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    def planted_scan(output_dir: Path, token: str) -> None:
        raise real_e2e.OpenRouterRealE2EError("credential_material_persisted")

    monkeypatch.setattr(real_e2e, "_scan_private_artifacts", planted_scan)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(challenge_dir, context_path)
    _assert_error_code(raised, "credential_material_persisted")


def test_prepare_rejects_a_reference_missing_its_occurrence_id(tmp_path: Path) -> None:
    """A wrong-key reference fails at the stable boundary, not as a KeyError in packet build."""
    document = json.loads(_AUTHORITY_BYTES.decode("utf-8"))
    del document["references"][0]["reference_occurrence_id"]
    document["authority_id"] = "0" * 64
    document["authority_id"] = compute_document_identity(
        document,
        schema_version="moodboard.openrouter-real-e2e-authority.v1",
        identity_field="authority_id",
    )
    prepare = _REAL_E2E.prepare_openrouter_real_e2e

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        prepare(
            tmp_path / "wrong-key-reference",
            _discovery_fetcher=lambda: _DISCOVERY_BODY,
            _source_fetcher=lambda: _SOURCE_BYTES,
            _authority_bundle=_json_bytes(document),
            _clock=lambda: _PREPARED_AT,
        )
    _assert_error_code(raised, "authority_invalid")


def test_default_direct_transport_preflight_fails_before_authorization_or_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "unsafe-direct-transport-environment"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls, unexpected = _unexpected_calls()
    monkeypatch.setenv("SSLKEYLOGFILE", "/tmp/credential-bearing-tls-log")

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        real_e2e.execute_openrouter_real_e2e(
            challenge_dir,
            context_path,
            _discovery_fetcher=unexpected("discovery"),
            _credential_loader=unexpected("credential"),
            _transport=real_e2e.direct_openrouter_https_transport,
            _confirmation_ledger=_ledger_for(challenge_dir),
            _clock=lambda: _EXECUTED_AT,
            _uuid4=_uuid_supplier(),
        )

    _assert_error_code(raised, "ambient_tls_configuration_forbidden")
    assert calls == []
    assert not (challenge_dir / "consumed.json").exists()
    assert not (challenge_dir / "attempts.sqlite3").exists()


def test_direct_transport_preflight_rejects_active_timer_and_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(signal, "getitimer", lambda _which: (1.0, 0.0))
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as active_timer:
        real_e2e._preflight_direct_transport_environment()
    _assert_error_code(active_timer, "direct_transport_environment_invalid")

    monkeypatch.setattr(signal, "getitimer", lambda _which: (0.0, 0.0))
    with ThreadPoolExecutor(max_workers=1) as executor:
        error = executor.submit(real_e2e._preflight_direct_transport_environment).exception(
            timeout=10
        )
    assert isinstance(error, real_e2e.OpenRouterRealE2EError)
    assert error.code == "direct_transport_environment_invalid"


def test_prepare_freezes_exact_content_bound_snapshot_summary_and_overlay(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "content-bound"
    _prepare(challenge_dir)
    challenge = _read_json(challenge_dir / "challenge.json")
    summary_path = _artifact_path(challenge_dir, "compact_summary")
    summary = _read_json(summary_path)

    assert challenge["schema_version"] == _CHALLENGE_VERSION
    verify_document_identity(
        challenge,
        schema_version=_CHALLENGE_VERSION,
        identity_field="challenge_id",
    )
    assert summary["schema_version"] == _SUMMARY_VERSION
    verify_document_identity(
        summary,
        schema_version=_SUMMARY_VERSION,
        identity_field="compact_summary_id",
    )
    assert challenge["compact_summary_id"] == summary["compact_summary_id"]

    packet = challenge["packet_projection"]
    request = packet["generation_request"]
    policy = packet["verification_policy"]
    assert summary["operation"] == {
        "kind": packet["operation"]["kind"],
        "schema_version": packet["operation"]["schema_version"],
    }
    assert summary["reference_count"] == len(packet["references"])
    assert summary["operation_inputs"] == request["operation_inputs"]
    assert summary["dispatch_confirmation"] == {
        "requested_provider": request["requested_provider"],
        "requested_model": request["requested_model"],
        "output_count": request["output_count"],
        "destination": request["destination"],
        "adapter_revision": request["adapter_revision"],
        "capability_snapshot_id": request["capability_snapshot_id"],
        "options": request["options"],
        "provider_route_policy": request["provider_route_policy"],
        "actual_model_policy": request["actual_model_policy"],
        "idempotency": request["idempotency"],
        "reconciliation": request["reconciliation"],
        "verification_policy_id": policy["policy_id"],
        "required_verifiers": policy["required_verifiers"],
    }

    expected_exact = {
        "discovery": _DISCOVERY_BODY,
        "source": _SOURCE_BYTES,
        "authority": _AUTHORITY_BYTES,
    }
    source_sha256 = hashlib.sha256(_SOURCE_BYTES).hexdigest()
    raster = compile_canonical_raster(_SOURCE_BYTES, source_content_sha256=source_sha256)
    left, top, right, bottom = real_e2e._mask_bounds(raster)
    mask = compile_rectangle_mask(
        raster,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
    )
    expected_exact["mask"] = mask.mask_bytes

    for name, expected in expected_exact.items():
        path = _artifact_path(challenge_dir, name)
        assert path.read_bytes() == expected
        assert challenge["artifacts"][name] == _descriptor(
            expected, challenge["artifacts"][name]["relative_path"]
        )
        assert summary["artifacts"][name] == challenge["artifacts"][name]

    overlay_path = _artifact_path(challenge_dir, "overlay")
    overlay_bytes = overlay_path.read_bytes()
    assert challenge["artifacts"]["overlay"] == _descriptor(
        overlay_bytes, challenge["artifacts"]["overlay"]["relative_path"]
    )
    assert summary["artifacts"]["overlay"] == challenge["artifacts"]["overlay"]
    with Image.open(BytesIO(overlay_bytes)) as overlay:
        overlay.load()
        assert overlay.format == "PNG"
        assert overlay.size == (raster.width, raster.height)
        assert overlay.convert("RGB").tobytes() != raster.rgb_bytes

    metadata = challenge_dir.stat()
    assert challenge["directory_binding"] == {
        "absolute_path": str(challenge_dir),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }
    assert challenge["expires_at"] > challenge["prepared_at"] == _PREPARED_AT
    assert "confirmation" not in challenge
    assert "confirmed_at" not in challenge
    assert "decision" not in challenge
    assert "principal_id" not in challenge
    assert "studio_session_id" not in challenge


@pytest.mark.parametrize(
    "artifact_name",
    ["discovery", "source", "mask", "authority", "overlay", "compact_summary"],
)
def test_any_snapshot_byte_drift_rejects_before_live_discovery_key_or_transport(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    challenge_dir = tmp_path / artifact_name
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    path = _artifact_path(challenge_dir, artifact_name)
    original = path.read_bytes()
    assert original
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    path.chmod(0o600)
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "challenge_artifact_drift")
    assert calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("challenge_id", _digest("f")),
        lambda value: value.__setitem__("compact_summary_id", _digest("e")),
        lambda value: value.__setitem__("decision", "approve_two_paid_calls"),
        lambda value: value.__setitem__("authorized_generation_post_count", 2),
        lambda value: value.__setitem__(
            "creative_session_id", "90000000-0000-4000-8000-000000000009"
        ),
        lambda value: value.__setitem__("confirmed_at", "2000-01-01T00:00:00Z"),
        lambda value: value.__setitem__("confirmed_at", "2100-01-01T00:00:00Z"),
        lambda value: value.__setitem__("unexpected", True),
    ],
)
def test_missing_or_forged_context_rejects_before_discovery_key_or_transport(
    tmp_path: Path,
    mutate: Callable[[JsonObject], None],
) -> None:
    challenge_dir = tmp_path / "forged"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    _rewrite_context(context_path, mutate)
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "confirmation_context_invalid")
    assert calls == []


def test_missing_context_rejects_before_discovery_key_or_transport(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "missing-context"
    _prepare(challenge_dir)
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            challenge_dir / "does-not-exist.json",
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )

    _assert_error_code(raised, "confirmation_context_unavailable")
    assert calls == []


def test_production_default_rejects_self_minted_context_before_discovery_or_key(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "untrusted-context"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir, register_confirmation=False)
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _REAL_E2E.execute_openrouter_real_e2e(
            challenge_dir,
            context_path,
            _discovery_fetcher=unexpected("discovery"),
            _credential_loader=unexpected("credential"),
            _transport=unexpected("transport"),
            _clock=lambda: _EXECUTED_AT,
            _uuid4=_uuid_supplier(),
        )

    _assert_error_code(raised, "confirmation_authority_unavailable")
    assert calls == []


def test_expired_challenge_context_rejects_before_discovery_key_or_transport(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "expired"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
            clock=lambda: "2100-01-01T00:00:00Z",
        )

    _assert_error_code(raised, "challenge_expired")
    assert calls == []


def test_fresh_discovery_must_equal_snapshot_bytes_before_key_or_claim(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "discovery-drift"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    credential_calls = 0
    sends: list[bytes] = []

    def credential(_: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert bearer_token == _TOKEN
        sends.append(body)
        return _http_response()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=lambda: _DISCOVERY_BODY + b"\n",
            credential_loader=credential,
            transport=transport,
        )

    _assert_error_code(raised, "challenge_discovery_drift")
    assert credential_calls == 0
    assert sends == []

    result = _execute(
        challenge_dir,
        context_path,
        credential_loader=credential,
        transport=transport,
    )
    assert result.generation_post_count == 1
    assert credential_calls == 1
    assert len(sends) == 1


def test_directory_swap_during_discovery_rejects_before_key_or_claim(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "directory-swap"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    moved_dir = tmp_path / "directory-swap-moved"
    credential_calls = 0

    def discovery() -> bytes:
        challenge_dir.rename(moved_dir)
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=discovery,
            credential_loader=credential,
            transport=lambda **_: _http_response(),
        )

    _assert_error_code(raised, "challenge_binding_mismatch")
    assert credential_calls == 0
    assert not (moved_dir / "consumed.json").exists()


def test_challenge_expiring_during_discovery_rejects_before_key_or_claim(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "expires-during-discovery"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    expired = False
    credential_calls = 0

    def clock() -> str:
        return "2100-01-01T00:00:00Z" if expired else _EXECUTED_AT

    def discovery() -> bytes:
        nonlocal expired
        expired = True
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=discovery,
            credential_loader=credential,
            transport=lambda **_: _http_response(),
            clock=clock,
        )

    _assert_error_code(raised, "challenge_expired")
    assert credential_calls == 0
    assert not (challenge_dir / "consumed.json").exists()


def test_rebuilt_wire_identity_drift_rejects_before_key_or_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "wire-drift"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    original = real_e2e._prepare_request
    credential_calls = 0

    def drift(*args: Any, **kwargs: Any) -> Any:
        prepared = original(*args, **kwargs)
        wire_body = prepared.wire_body + b" "
        return dataclasses.replace(
            prepared,
            wire_body=wire_body,
            wire_body_sha256=hashlib.sha256(wire_body).hexdigest(),
            wire_body_byte_count=len(wire_body),
        )

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    monkeypatch.setattr(real_e2e, "_prepare_request", drift)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            credential_loader=credential,
            transport=lambda **_: _http_response(),
        )

    _assert_error_code(raised, "challenge_artifact_drift")
    assert credential_calls == 0
    assert not (challenge_dir / "consumed.json").exists()


def test_context_values_are_projected_exactly_and_never_generated(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "exact-confirmation"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    context = _read_json(context_path)

    result = _execute(challenge_dir, context_path)

    plan = _read_json(challenge_dir / "plan.json")
    confirmation = plan["intent_packet"]["confirmation"]
    assert confirmation["mode"] == "explicit"
    assert confirmation["compact_summary_id"] == context["compact_summary_id"]
    assert confirmation["confirmed_at"] == context["confirmed_at"]
    assert confirmation["principal_id"] == context["principal_id"]
    assert confirmation["studio_session_id"] == context["studio_session_id"]
    assert plan["intent_packet"]["creative_session_id"] == context["creative_session_id"]
    assert result.generation_post_count == context["authorized_generation_post_count"] == 1


def test_durable_ledger_cas_precedes_local_evidence_key_journal_and_post(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "durable-order"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    ledger = _ledger_for(challenge_dir)
    calls: list[str] = []
    original_inspect = StudioConfirmationLedger.inspect_confirmation
    original_consume = StudioConfirmationLedger.consume_confirmation
    original_claim = real_e2e._claim_challenge_consumption
    original_journal_init = AttemptJournal.__init__

    def inspect_confirmation(*args: Any, **kwargs: Any) -> Any:
        calls.append("ledger_inspect")
        assert "discovery" not in calls
        return original_inspect(*args, **kwargs)

    def consume_confirmation(*args: Any, **kwargs: Any) -> Any:
        calls.append("ledger_consume")
        assert "discovery" in calls
        assert not (challenge_dir / "consumed.json").exists()
        assert not (challenge_dir / "plan.json").exists()
        assert not (challenge_dir / "attempts.sqlite3").exists()
        result = original_consume(*args, **kwargs)
        assert result.created is True
        assert result.generation_post_authorized is True
        return result

    def claim(*args: Any, **kwargs: Any) -> None:
        calls.append("local_consumed")
        assert "ledger_consume" in calls
        original_claim(*args, **kwargs)

    def journal_init(self: AttemptJournal, *args: Any, **kwargs: Any) -> None:
        calls.append("journal")
        assert "credential" in calls
        original_journal_init(self, *args, **kwargs)

    def discovery() -> bytes:
        calls.append("discovery")
        assert "ledger_inspect" in calls
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        calls.append("credential")
        assert (challenge_dir / "consumed.json").is_file()
        assert (challenge_dir / "plan.json").is_file()
        assert not (challenge_dir / "attempts.sqlite3").exists()
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert body and bearer_token == _TOKEN
        calls.append("post")
        assert (challenge_dir / "attempts.sqlite3").is_file()
        return _http_response()

    monkeypatch.setattr(
        StudioConfirmationLedger,
        "inspect_confirmation",
        inspect_confirmation,
    )
    monkeypatch.setattr(
        StudioConfirmationLedger,
        "consume_confirmation",
        consume_confirmation,
    )
    monkeypatch.setattr(real_e2e, "_claim_challenge_consumption", claim)
    monkeypatch.setattr(AttemptJournal, "__init__", journal_init)

    result = _execute(
        challenge_dir,
        context_path,
        confirmation_ledger=ledger,
        discovery_fetcher=discovery,
        credential_loader=credential,
        transport=transport,
    )

    assert result.generation_post_count == 1
    assert calls.index("ledger_inspect") < calls.index("discovery")
    assert calls.index("discovery") < calls.index("ledger_consume")
    assert calls.index("ledger_consume") < calls.index("local_consumed")
    assert calls.index("local_consumed") < calls.index("credential")
    assert calls.index("credential") < calls.index("journal") < calls.index("post")


def test_clock_regression_after_inspection_stops_before_ledger_consume_or_key(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "clock-regression"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    ledger = _ledger_for(challenge_dir)
    samples = iter((_EXECUTED_AT, "2026-08-17T03:16:30Z"))
    calls, unexpected = _unexpected_calls()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=ledger,
            discovery_fetcher=lambda: calls.append("discovery") or _DISCOVERY_BODY,
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
            clock=lambda: next(samples),
        )

    _assert_error_code(raised, "clock_invalid")
    assert calls == ["discovery"]
    assert (
        ledger.inspect_confirmation(
            _read_json(context_path),
            _read_json(challenge_dir / "challenge.json"),
            inspected_at=_EXECUTED_AT,
        ).state
        == "available"
    )
    assert not (challenge_dir / "consumed.json").exists()
    assert not (challenge_dir / "attempts.sqlite3").exists()


@pytest.mark.parametrize("failure_kind", ["lost_race", "ambiguous_commit"])
def test_non_authorizing_consume_outcome_never_reaches_local_key_journal_or_post(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
) -> None:
    challenge_dir = tmp_path / failure_kind
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    ledger = _ledger_for(challenge_dir)
    original_consume = StudioConfirmationLedger.consume_confirmation
    calls: list[str] = []

    def consume(
        self: StudioConfirmationLedger,
        context: JsonObject,
        challenge: JsonObject,
        *,
        consumed_at: str,
    ) -> ConfirmationConsumptionResult:
        calls.append("consume")
        if failure_kind == "lost_race":
            original_consume(
                StudioConfirmationLedger(self.path),
                context,
                challenge,
                consumed_at=consumed_at,
            )
            return original_consume(
                self,
                context,
                challenge,
                consumed_at=consumed_at,
            )
        result = original_consume(
            self,
            context,
            challenge,
            consumed_at=consumed_at,
        )
        assert result.generation_post_authorized is True
        raise StudioConfirmationLedgerError("confirmation_persistence_ambiguous")

    def discovery() -> bytes:
        calls.append("discovery")
        return _DISCOVERY_BODY

    monkeypatch.setattr(StudioConfirmationLedger, "consume_confirmation", consume)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=ledger,
            discovery_fetcher=discovery,
            credential_loader=lambda _profile: calls.append("credential") or _TOKEN,
            transport=lambda **_kwargs: calls.append("post") or _http_response(),
        )

    _assert_error_code(raised, "confirmation_authority_invalid")
    assert calls == ["discovery", "consume"]
    assert not (challenge_dir / "consumed.json").exists()
    assert not (challenge_dir / "plan.json").exists()
    assert not (challenge_dir / "attempts.sqlite3").exists()

    monkeypatch.setattr(StudioConfirmationLedger, "consume_confirmation", original_consume)
    replay_calls, unexpected = _unexpected_calls()
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as replay:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=StudioConfirmationLedger(ledger.path),
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )
    _assert_error_code(replay, "confirmation_authority_invalid")
    assert replay_calls == []


def test_expiry_crossed_inside_trusted_ledger_stops_before_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "consumer-crosses-expiry"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    measured_now = _EXECUTED_AT
    credential_calls = 0

    def clock() -> str:
        return measured_now

    ledger = _ledger_for(challenge_dir)
    original_consume = StudioConfirmationLedger.consume_confirmation

    def consume(
        self: StudioConfirmationLedger,
        context: JsonObject,
        challenge: JsonObject,
        *,
        consumed_at: str,
    ) -> ConfirmationConsumptionResult:
        nonlocal measured_now
        result = original_consume(
            self,
            context,
            challenge,
            consumed_at=consumed_at,
        )
        measured_now = "2026-08-17T04:00:00Z"
        return result

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    monkeypatch.setattr(StudioConfirmationLedger, "consume_confirmation", consume)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            confirmation_ledger=ledger,
            credential_loader=credential,
            clock=clock,
        )

    _assert_error_code(raised, "challenge_expired")
    assert credential_calls == 0
    assert not (challenge_dir / "consumed.json").exists()


def test_expiry_crossed_after_local_consume_still_stops_before_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "local-consume-crosses-expiry"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    measured_now = _EXECUTED_AT
    credential_calls = 0
    original_claim = real_e2e._claim_challenge_consumption

    def clock() -> str:
        return measured_now

    def claim(*args: Any, **kwargs: Any) -> None:
        nonlocal measured_now
        original_claim(*args, **kwargs)
        measured_now = "2026-08-17T04:00:00Z"

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    monkeypatch.setattr(real_e2e, "_claim_challenge_consumption", claim)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
        _execute(
            challenge_dir,
            context_path,
            credential_loader=credential,
            clock=clock,
        )

    _assert_error_code(raised, "challenge_expired")
    assert credential_calls == 0
    assert (challenge_dir / "consumed.json").is_file()


def test_challenge_directory_and_confirmation_path_are_inode_safe(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "bound"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls, unexpected = _unexpected_calls()

    moved_dir = tmp_path / "moved"
    challenge_dir.rename(moved_dir)
    moved_context = moved_dir / context_path.name
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as moved:
        _execute(
            moved_dir,
            moved_context,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )
    _assert_error_code(moved, "challenge_binding_mismatch")
    assert calls == []

    second_dir = tmp_path / "context-symlink"
    _prepare(second_dir)
    real_context = _write_context(second_dir, name="trusted-context.json")
    linked_context = second_dir / "confirmation-context.json"
    linked_context.symlink_to(real_context.name)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as linked:
        _execute(
            second_dir,
            linked_context,
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )
    _assert_error_code(linked, "confirmation_context_invalid")
    assert calls == []

    copied_dir = tmp_path / "copied"
    shutil.copytree(second_dir, copied_dir, symlinks=True)
    with pytest.raises(real_e2e.OpenRouterRealE2EError) as copied:
        _execute(
            copied_dir,
            copied_dir / "trusted-context.json",
            discovery_fetcher=unexpected("discovery"),
            credential_loader=unexpected("credential"),
            transport=unexpected("transport"),
        )
    _assert_error_code(copied, "challenge_binding_mismatch")
    assert calls == []


def test_consumed_challenge_replay_never_refreshes_key_or_sends(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "one-use"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls: list[str] = []

    def discovery() -> bytes:
        calls.append("discovery")
        return _DISCOVERY_BODY

    def credential(_: str) -> str:
        calls.append("credential")
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert body and bearer_token == _TOKEN
        calls.append("transport")
        return _http_response()

    first = _execute(
        challenge_dir,
        context_path,
        discovery_fetcher=discovery,
        credential_loader=credential,
        transport=transport,
    )
    assert first.generation_post_count == 1
    assert calls == ["discovery", "credential", "transport"]

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as replay:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=discovery,
            credential_loader=credential,
            transport=transport,
        )

    _assert_error_code(replay, "challenge_consumed")
    assert calls == ["discovery", "credential", "transport"]


def test_trusted_confirmation_ledger_blocks_replay_after_local_rollback(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "rollback"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    sends = 0
    credential_calls = 0
    discovery_calls = 0

    def discovery() -> bytes:
        nonlocal discovery_calls
        discovery_calls += 1
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        credential_calls += 1
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        nonlocal sends
        assert body and bearer_token == _TOKEN
        sends += 1
        return _http_response()

    first = _execute(
        challenge_dir,
        context_path,
        discovery_fetcher=discovery,
        credential_loader=credential,
        transport=transport,
    )
    assert first.states[-1] == "succeeded"
    for path in tuple(challenge_dir.iterdir()):
        if path.name not in {
            "challenge.json",
            "compact-summary.json",
            "discovery.json",
            "source.png",
            "source.jpg",
            "authority.json",
            "mask.u8",
            "overlay.png",
            context_path.name,
        }:
            path.unlink()

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as replay:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=discovery,
            credential_loader=credential,
            transport=transport,
        )

    _assert_error_code(replay, "confirmation_authority_invalid")
    assert discovery_calls == 1
    assert credential_calls == sends == 1


def test_ambient_decimal_context_cannot_round_0051_quote_under_cap(tmp_path: Path) -> None:
    document = json.loads(_DISCOVERY_BODY)
    pricing = document["endpoints"][0]["pricing"]
    pricing[0]["cost_usd"] = 0.001
    pricing[1]["cost_usd"] = 0.05
    over_cap = json.dumps(document, separators=(",", ":")).encode("utf-8")
    source_calls = 0

    def source() -> bytes:
        nonlocal source_calls
        source_calls += 1
        return _SOURCE_BYTES

    prepare = _REAL_E2E.prepare_openrouter_real_e2e
    with localcontext():
        getcontext().prec = 1
        getcontext().rounding = ROUND_DOWN
        with pytest.raises(real_e2e.OpenRouterRealE2EError) as raised:
            prepare(
                tmp_path / "decimal",
                _discovery_fetcher=lambda: over_cap,
                _source_fetcher=source,
                _authority_bundle=_AUTHORITY_BYTES,
                _clock=lambda: _PREPARED_AT,
            )

    _assert_error_code(raised, "quote_exceeds_cap")
    assert source_calls == 0
    assert not (tmp_path / "decimal").exists()
    assert Decimal("0.051") > real_e2e.MAX_COST_USD


def test_missing_reported_cost_remains_terminal_success_after_paid_response(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "cost-not-reported"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    sends: list[bytes] = []

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert bearer_token == _TOKEN
        sends.append(body)
        return _http_response(cost=None)

    result = _execute(challenge_dir, context_path, transport=transport)

    assert len(sends) == 1
    assert result.generation_post_count == 1
    assert result.reported_cost_usd is None
    assert result.states == ("prepared", "submitted", "response_received", "succeeded")
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    assert journal.read_state(result.attempt_id).state == "succeeded"
    journal.verify_integrity()


def test_invalid_provider_media_is_retained_as_failed_structural_evidence(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "invalid-provider-media"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    invalid_payload = b"not-a-supported-raster"
    body = json.dumps(
        {
            "created": 1_786_930_000,
            "data": [
                {
                    "b64_json": base64.b64encode(invalid_payload).decode("ascii"),
                    "media_type": "image/png",
                }
            ],
            "usage": {"cost": 0.033},
        },
        separators=(",", ":"),
    ).encode("utf-8")

    result = _execute(
        challenge_dir,
        context_path,
        transport=lambda **_: OpenRouterHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=body,
            elapsed_milliseconds=3210,
        ),
    )

    assert result.states == ("prepared", "submitted", "response_received")
    assert result.output_occurrence_id is None
    assert result.provider_media_admission_result == "unsupported_format"
    assert result.raw_structural_result == "fail"
    assert result.raw_structural_reason == "unsupported_format"
    assert result.raw_locality_result == "not_run"
    report = _read_json(challenge_dir / "result.json")
    assert report["provider_lifecycle_state"] == "response_received"
    assert report["provider_media_admission_result"] == "unsupported_format"
    assert report["raw_structural_judgment"]["result"]["state"] == "fail"
    assert report["raw_locality_judgment"]["result"]["state"] == "not_run"
    assert report["localized_edit_gate_status"] == "not_eligible"
    assert report["workflow_acceptance"] == "not_recorded"
    assert report["private_payloads"]["provider_response"] == "retained_private_local_evidence"
    assert report["private_payloads"]["outputs"] == "retained_private_local_evidence"
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    journal.verify_integrity()


def test_post_paid_cost_above_quote_limit_is_reported_but_does_not_strand_success(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "cost-above-quote-limit"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    result = _execute(
        challenge_dir,
        context_path,
        transport=lambda **_: _http_response(cost=Decimal("0.051")),
    )

    assert result.reported_cost_usd == Decimal("0.051")
    assert result.states[-1] == "succeeded"
    report = _read_json(challenge_dir / "result.json")
    assert report["spend_limit_kind"] == "quote_only_not_provider_enforced"
    assert report["cost_telemetry_status"] == "reported_above_quote_admission_limit"
    assert report["semantic_aesthetic_result"] == "not_run"
    assert report["compositor_result"] == "not_run"


def test_non_usd_cost_is_never_mislabeled_as_reported_usd(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "non-usd-cost"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    result = _execute(
        challenge_dir,
        context_path,
        transport=lambda **_: _http_response(cost=Decimal("0.033"), currency="EUR"),
    )

    assert result.states[-1] == "succeeded"
    assert result.reported_cost_usd is None
    assert result.cost_telemetry_status == "reported_non_usd"
    report = _read_json(challenge_dir / "result.json")
    assert report["reported_cost_usd"] is None
    assert report["cost_telemetry_status"] == "reported_non_usd"
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    stored = journal.read_provider_response(result.attempt_id)
    assert real_e2e.provider_to_json(stored.receipt)["cost"]["currency"] == "EUR"


def test_uncertifiable_cost_reaches_the_summary_as_its_own_status(tmp_path: Path) -> None:
    """A reported cost the adapter cannot certify must not read as absent telemetry."""
    challenge_dir = tmp_path / "uncertifiable-cost"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)

    result = _execute(
        challenge_dir,
        context_path,
        transport=lambda **_: _http_response(cost=Decimal("12.50"), currency="usd"),
    )

    assert result.states[-1] == "succeeded"
    assert result.reported_cost_usd is None
    assert result.cost_telemetry_status == "reported_uncertifiable"
    report = _read_json(challenge_dir / "result.json")
    assert report["reported_cost_usd"] is None
    assert report["cost_telemetry_status"] == "reported_uncertifiable"
    journal = AttemptJournal((challenge_dir / "attempts.sqlite3").resolve())
    stored = journal.read_provider_response(result.attempt_id)
    assert real_e2e.provider_to_json(stored.receipt)["cost"] == {
        "state": "unavailable",
        "amount": None,
        "currency": None,
        "provenance": "reported_uncertifiable",
    }


def test_ambiguous_transport_consumes_challenge_and_can_never_retry(tmp_path: Path) -> None:
    challenge_dir = tmp_path / "ambiguous"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    calls: list[str] = []

    def discovery() -> bytes:
        calls.append("discovery")
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        calls.append("credential")
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        assert body and bearer_token == _TOKEN
        calls.append("transport")
        raise TimeoutError("ambiguous provider outcome")

    first = _execute(
        challenge_dir,
        context_path,
        discovery_fetcher=discovery,
        credential_loader=credential,
        transport=transport,
    )
    assert first.states == ("prepared", "submitted", "outcome_unknown")
    assert first.generation_post_count == 1
    assert calls == ["discovery", "credential", "transport"]
    report = _read_json(challenge_dir / "result.json")
    assert report["actual_model"] == "not_reported"
    assert report["private_payloads"]["provider_response"] == "absent"
    assert report["private_payloads"]["outputs"] == "absent"
    assert report["workflow_acceptance"] == "not_recorded"

    with pytest.raises(real_e2e.OpenRouterRealE2EError) as replay:
        _execute(
            challenge_dir,
            context_path,
            discovery_fetcher=discovery,
            credential_loader=credential,
            transport=transport,
        )

    _assert_error_code(replay, "challenge_consumed")
    assert calls == ["discovery", "credential", "transport"]


def test_concurrent_executors_atomically_admit_only_one_credential_and_send(
    tmp_path: Path,
) -> None:
    challenge_dir = tmp_path / "concurrent"
    _prepare(challenge_dir)
    context_path = _write_context(challenge_dir)
    discovery_barrier = threading.Barrier(2)
    lock = threading.Lock()
    credential_calls = 0
    sends = 0

    def discovery() -> bytes:
        discovery_barrier.wait(timeout=10)
        return _DISCOVERY_BODY

    def credential(_profile: str) -> str:
        nonlocal credential_calls
        with lock:
            credential_calls += 1
        return _TOKEN

    def transport(*, body: bytes, bearer_token: str) -> OpenRouterHttpResponse:
        nonlocal sends
        assert body and bearer_token == _TOKEN
        with lock:
            sends += 1
        return _http_response()

    def invoke() -> tuple[str, Any]:
        try:
            return (
                "ok",
                _execute(
                    challenge_dir,
                    context_path,
                    discovery_fetcher=discovery,
                    credential_loader=credential,
                    transport=transport,
                ),
            )
        except real_e2e.OpenRouterRealE2EError as error:
            return "error", error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: invoke(), range(2)))

    assert sorted(kind for kind, _value in results) == ["error", "ok"]
    assert [value for kind, value in results if kind == "error"] == [
        "confirmation_authority_invalid"
    ]
    successful = [value for kind, value in results if kind == "ok"]
    assert len(successful) == 1
    assert successful[0].states[-1] == "succeeded"
    assert credential_calls == sends == 1
