"""RED contract for byte-derived provider media admission and atomic success.

The response store intentionally stops at ``response_received``.  This slice proves that only
exact retained provider bytes can mint eligible ``generator_raw`` occurrences and that every
occurrence plus the terminal event has one crash-recoverable journal commit boundary.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import sqlite3
import struct
import threading
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from queue import Queue
from typing import Any

import pytest
from blake3 import blake3
from PIL import Image

import moodboard.locality as locality_module
import moodboard.provider_media as provider_media_module
from moodboard.attempt_journal import (
    AttemptJournal,
    AttemptJournalError,
    JournalCorruptionError,
    JournalNotFoundError,
    JournalSecurityError,
    ProviderSuccessConflictError,
    ProviderSuccessPublishResult,
    StoredProviderSuccess,
)
from moodboard.contracts import compute_projection_identity
from moodboard.intent_packet import IntentPacket
from moodboard.locality import (
    COMPILER_REVISION,
    LocalityError,
    ProviderMediaCompilation,
    compile_canonical_raster,
    compile_provider_output_media,
    verify_output_structure,
)
from moodboard.openrouter import (
    OpenRouterHttpResponse,
    decode_openrouter_response,
)
from moodboard.provider_artifacts import (
    OUTPUT_VERSION,
    GenerationAttempt,
    GenerationAttemptEvent,
    GenerationRun,
    NormalizedProviderRequest,
    OutputOccurrence,
    ProviderCapabilitySnapshot,
    ProviderReceipt,
    from_json_dict,
    seal_provider_artifact,
    to_json_dict,
    validate_artifact_bundle,
)
from moodboard.provider_media import (
    ProviderMediaAdmissionError,
    ProviderSuccessCandidates,
    build_provider_success_candidates,
)
from tests.test_intent_packet import _refresh_packet_identity, _sync_confirmation
from tests.test_openrouter_adapter import (
    _CLAIMED_AT,
    _DISPATCH_CLAIM_ID,
    _seed_dispatch,
)
from tests.test_provider_artifacts import (
    _event,
    _refresh_document_id,
    _valid_attempt,
    _valid_capability,
    _valid_normalized_request,
    _valid_packet,
    _valid_receipt,
    _valid_run,
)

JsonObject = dict[str, Any]
_SUCCEEDED_AT = "2026-08-16T20:30:06Z"


def _image_bytes(
    mode: str = "RGB",
    *,
    size: tuple[int, int] = (4, 3),
    value: Any | None = None,
    image_format: str = "PNG",
    orientation: int | None = None,
) -> bytes:
    if value is None:
        value = {
            "L": 31,
            "LA": (31, 255),
            "RGBA": (11, 22, 33, 255),
        }.get(mode, (11, 22, 33))
    image = Image.new(mode, size, value)
    options: dict[str, Any] = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    buffer = BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def _insert_png_chunk(payload: bytes, chunk_type: bytes, data: bytes) -> bytes:
    position = payload.index(b"IDAT") - 4
    chunk = (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + (zlib.crc32(chunk_type + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )
    return payload[:position] + chunk + payload[position:]


@dataclass(frozen=True, slots=True)
class _ResponseCase:
    journal: AttemptJournal
    packet: IntentPacket | JsonObject
    run: GenerationRun
    attempt: GenerationAttempt
    capability: ProviderCapabilitySnapshot
    normalized_request: NormalizedProviderRequest
    receipt: ProviderReceipt
    output_bytes: tuple[bytes, ...]
    response_event: GenerationAttemptEvent


def _single_response_case(
    tmp_path: Path,
    payload: bytes,
    *,
    media_type_claim: str | None = None,
    forbidden_secrets: tuple[str, ...] = (),
    mutate_receipt: Callable[[JsonObject], None] | None = None,
) -> _ResponseCase:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    journal, attempt, capability, prepared = _seed_dispatch(
        tmp_path,
        forbidden_secrets=forbidden_secrets,
    )
    state = journal.read_state(attempt.attempt_id)
    claim = journal.claim_non_idempotent_dispatch(
        attempt.attempt_id,
        capability,
        expected_head_event_id=state.head_event_id or "",
        expected_next_sequence=state.next_sequence,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        wire_request_sha256=prepared.wire_body_sha256,
        wire_request_byte_count=prepared.wire_body_byte_count,
    )
    output: JsonObject = {"b64_json": base64.b64encode(payload).decode("ascii")}
    if media_type_claim is not None:
        output["media_type"] = media_type_claim
    raw_response = json.dumps(
        {"created": 1_786_930_000, "data": [output], "usage": {"cost": 0.03125}},
        separators=(",", ":"),
    ).encode()
    decoded = decode_openrouter_response(
        attempt,
        prepared,
        OpenRouterHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=raw_response,
            elapsed_milliseconds=2450,
        ),
        received_at="2026-08-16T20:30:05Z",
    )
    receipt = decoded.receipt
    if mutate_receipt is not None:
        receipt_document = to_json_dict(receipt)
        receipt_document.pop("provider_receipt_id")
        mutate_receipt(receipt_document)
        sealed = seal_provider_artifact(receipt_document)
        assert isinstance(sealed, ProviderReceipt)
        receipt = sealed
    response = journal.publish_provider_response(
        receipt,
        decoded.raw_response_bytes,
        decoded.output_bytes,
        expected_head_event_id=claim.submitted_event.attempt_event_id,
        expected_next_sequence=3,
    )
    return _ResponseCase(
        journal=journal,
        packet=prepared.intent_packet,
        run=journal.read_run(attempt.generation_run_id),
        attempt=attempt,
        capability=capability,
        normalized_request=prepared.normalized_request,
        receipt=receipt,
        output_bytes=decoded.output_bytes,
        response_event=response.event,
    )


def _manual_response_case(
    tmp_path: Path,
    payloads: tuple[bytes, ...],
    *,
    max_width: int = 4096,
    max_height: int = 4096,
) -> _ResponseCase:
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    packet = _valid_packet()
    packet["generation_request"]["output_count"] = len(payloads)
    capability_document = _valid_capability(packet)
    capability_document["outputs"]["max_count"] = max(6, len(payloads))
    capability_document["outputs"]["max_width"] = max_width
    capability_document["outputs"]["max_height"] = max_height
    _refresh_document_id(capability_document)
    packet["generation_request"]["capability_snapshot_id"] = capability_document[
        "capability_snapshot_id"
    ]
    _sync_confirmation(packet)
    _refresh_packet_identity(packet)
    normalized_document = _valid_normalized_request(packet, capability_document)
    run_document = _valid_run(packet)
    attempt_document = _valid_attempt(
        packet,
        run_document,
        capability_document,
        normalized_document,
    )
    receipt_document = _valid_receipt(packet, attempt_document, normalized_document)
    receipt_document["raw_response"] = {
        "state": "not_retained",
        "reason": "retention_policy",
    }
    receipt_document["outputs"] = [
        {
            "output_index": index,
            "role": "generated_image",
            "content_ref": blake3(payload).hexdigest(),
            "content_sha256": hashlib.sha256(payload).hexdigest(),
            "byte_count": len(payload),
            "media_type_claim": "image/png",
        }
        for index, payload in enumerate(payloads)
    ]
    _refresh_document_id(receipt_document)

    journal = AttemptJournal((tmp_path / "attempts.sqlite3").resolve())
    run = journal.register_run(run_document).artifact
    attempt = journal.register_attempt(attempt_document).artifact
    prepared = _event(
        attempt_id=attempt_document["attempt_id"],
        sequence=1,
        state="prepared",
        recorded_at=attempt_document["created_at"],
        detail={"kind": "prepared"},
    )
    journal.append_event(prepared, expected_head_event_id=None, expected_next_sequence=1)
    capability = from_json_dict(capability_document)
    normalized = from_json_dict(normalized_document)
    receipt = from_json_dict(receipt_document)
    assert isinstance(run, GenerationRun)
    assert isinstance(attempt, GenerationAttempt)
    assert isinstance(capability, ProviderCapabilitySnapshot)
    assert isinstance(normalized, NormalizedProviderRequest)
    assert isinstance(receipt, ProviderReceipt)
    claim = journal.claim_non_idempotent_dispatch(
        attempt.attempt_id,
        capability,
        expected_head_event_id=journal.read_state(attempt.attempt_id).head_event_id or "",
        expected_next_sequence=2,
        dispatch_claim_id=_DISPATCH_CLAIM_ID,
        claimed_at=_CLAIMED_AT,
        wire_request_sha256="e" * 64,
        wire_request_byte_count=512,
    )
    response = journal.publish_provider_response(
        receipt,
        None,
        payloads,
        expected_head_event_id=claim.submitted_event.attempt_event_id,
        expected_next_sequence=3,
    )
    return _ResponseCase(
        journal=journal,
        packet=packet,
        run=run,
        attempt=attempt,
        capability=capability,
        normalized_request=normalized,
        receipt=receipt,
        output_bytes=payloads,
        response_event=response.event,
    )


def _candidates(case: _ResponseCase, *, succeeded_at: str = _SUCCEEDED_AT):
    return build_provider_success_candidates(
        intent_packet=case.packet,
        generation_run=case.run,
        attempt=case.attempt,
        capability=case.capability,
        normalized_request=case.normalized_request,
        receipt=case.receipt,
        prior_events=case.journal.read_events(case.attempt.attempt_id),
        output_bytes=case.output_bytes,
        succeeded_at=succeeded_at,
    )


def _publish_success(
    case: _ResponseCase,
    *,
    journal: AttemptJournal | None = None,
    succeeded_at: str = _SUCCEEDED_AT,
) -> ProviderSuccessPublishResult:
    return (journal or case.journal).publish_provider_success(
        case.attempt.attempt_id,
        case.packet,
        case.normalized_request,
        succeeded_at=succeeded_at,
        expected_head_event_id=case.response_event.attempt_event_id,
        expected_next_sequence=case.response_event.sequence + 1,
    )


def _success_table(connection: sqlite3.Connection) -> str:
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if "succeeded_event_id" in columns:
            return str(name)
    raise AssertionError("success evidence table is absent")


def _occurrence_table(connection: sqlite3.Connection) -> str:
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if "output_occurrence_id" in columns:
            return str(name)
    raise AssertionError("output occurrence table is absent")


def test_media_compilation_derives_oriented_facts_from_exact_bytes_and_is_repr_safe(
    tmp_path: Path,
) -> None:
    payload = _image_bytes("LA", size=(2, 3), orientation=6)
    case = _single_response_case(tmp_path, payload, media_type_claim="image/png")

    compiled = compile_provider_output_media(
        case.receipt,
        output_index=0,
        output_bytes=payload,
    )

    assert isinstance(compiled, ProviderMediaCompilation)
    assert dataclasses.is_dataclass(compiled)
    with pytest.raises(dataclasses.FrozenInstanceError):
        compiled.detected_mime = "image/jpeg"  # type: ignore[misc]
    assert compiled.decoder_revision == COMPILER_REVISION
    assert compiled.content_ref == blake3(payload).hexdigest()
    assert compiled.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert compiled.byte_count == len(payload)
    assert compiled.detected_mime == "image/png"
    assert (compiled.oriented_width, compiled.oriented_height) == (3, 2)
    assert compiled.observed_mode == "LA"
    assert compiled.frame_count == 1
    assert compiled.active_content is False
    assert compiled.bounded is True
    assert (compiled.canonical_raster.width, compiled.canonical_raster.height) == (3, 2)
    assert compiled.canonical_raster.mode == "RGB"
    assert repr(compiled.canonical_raster.rgb_bytes) not in repr(compiled)


@pytest.mark.parametrize(
    "payload",
    (
        b"not-an-image",
        b"\x89PNG\r\n\x1a\ntruncated",
        _image_bytes("RGBA", value=(11, 22, 33, 0)),
        _insert_png_chunk(_image_bytes(), b"acTL", struct.pack(">II", 2, 0)),
        _insert_png_chunk(
            _image_bytes(),
            b"zTXt",
            b"Comment\0\0" + zlib.compress(b"expanded text" * 10_000),
        ),
    ),
    ids=("unsupported", "truncated", "nonopaque", "animated", "compressed-text"),
)
def test_invalid_active_or_compressed_metadata_media_never_compiles(
    tmp_path: Path,
    payload: bytes,
) -> None:
    case = _single_response_case(tmp_path, payload)

    with pytest.raises(LocalityError):
        compile_provider_output_media(
            case.receipt,
            output_index=0,
            output_bytes=payload,
        )


def test_exact_bytes_subclasses_and_receipt_mime_drift_fail_closed(tmp_path: Path) -> None:
    payload = _image_bytes()
    case = _single_response_case(tmp_path, payload, media_type_claim="image/jpeg")

    class HostileBytes(bytes):
        def __len__(self) -> int:
            return 1

        def startswith(self, _: object, *args: object) -> bool:
            return False

    with pytest.raises(LocalityError, match="bytes|payload|exact|MIME"):
        compile_provider_output_media(
            case.receipt,
            output_index=0,
            output_bytes=HostileBytes(payload),
        )
    with pytest.raises(LocalityError, match="MIME|claim|payload"):
        compile_provider_output_media(
            case.receipt,
            output_index=0,
            output_bytes=payload,
        )


def test_success_candidates_mint_complete_eligible_occurrences_without_circular_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _image_bytes(size=(4, 3))
    case = _single_response_case(tmp_path, payload, media_type_claim="image/png")
    candidates = _candidates(case)

    assert isinstance(candidates, ProviderSuccessCandidates)
    assert dataclasses.is_dataclass(candidates)
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidates.occurrences = ()  # type: ignore[misc]
    assert len(candidates.occurrences) == 1
    occurrence = candidates.occurrences[0]
    assert isinstance(occurrence, OutputOccurrence)
    expected_id = compute_projection_identity(
        {"attempt_id": case.attempt.attempt_id, "output_index": 0},
        domain_tag=OUTPUT_VERSION,
    )
    assert occurrence.output_occurrence_id == expected_id
    assert occurrence.producer_kind == "generator_raw"
    assert occurrence.original == {
        "content_ref": blake3(payload).hexdigest(),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "mime": "image/png",
        "byte_count": len(payload),
        "width": 4,
        "height": 3,
    }
    assert occurrence.media_validation == {
        "schema_version": "moodboard.media-validation.v1",
        "state": "pass",
        "decoder_revision": COMPILER_REVISION,
        "measured_content_sha256": hashlib.sha256(payload).hexdigest(),
        "measured_content_ref": blake3(payload).hexdigest(),
        "measured_byte_count": len(payload),
        "measured_mime": "image/png",
        "measured_width": 4,
        "measured_height": 3,
        "measured_mode": "RGB",
        "frame_count": 1,
        "active_content": False,
        "bounded": True,
    }
    assert occurrence.admission == {"state": "eligible", "rejection_reasons": ()}
    assert candidates.event.state == "succeeded"
    assert candidates.event.detail == {
        "kind": "succeeded",
        "output_occurrence_ids": (expected_id,),
    }
    validate_artifact_bundle(
        [
            case.run,
            case.capability,
            case.normalized_request,
            case.attempt,
            *case.journal.read_events(case.attempt.attempt_id),
            candidates.event,
            case.receipt,
            *candidates.occurrences,
        ],
        intent_packet=case.packet,
    )

    source = compile_canonical_raster(
        payload,
        source_content_sha256=hashlib.sha256(payload).hexdigest(),
    )
    calls: list[int] = []
    original_compile = locality_module.compile_provider_output_media

    def counted_compile(
        provider_receipt: ProviderReceipt | JsonObject,
        *,
        output_index: int,
        output_bytes: bytes,
        **kwargs: Any,
    ) -> ProviderMediaCompilation:
        calls.append(output_index)
        return original_compile(
            provider_receipt,
            output_index=output_index,
            output_bytes=output_bytes,
            **kwargs,
        )

    monkeypatch.setattr(locality_module, "compile_provider_output_media", counted_compile)
    structural = verify_output_structure(
        source,
        provider_receipt=case.receipt,
        output_index=0,
        output_bytes=payload,
        output_occurrence=occurrence,
    )
    assert structural.judgment.result["state"] == "pass"
    assert calls == [0]


def test_success_candidates_reject_reordered_or_retrograde_prior_history(
    tmp_path: Path,
) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    events = case.journal.read_events(case.attempt.attempt_id)
    authorities = {
        "intent_packet": case.packet,
        "generation_run": case.run,
        "attempt": case.attempt,
        "capability": case.capability,
        "normalized_request": case.normalized_request,
        "receipt": case.receipt,
        "output_bytes": case.output_bytes,
    }

    with pytest.raises(ProviderMediaAdmissionError, match="sequence order"):
        build_provider_success_candidates(
            **authorities,
            prior_events=reversed(events),
            succeeded_at=_SUCCEEDED_AT,
        )
    with pytest.raises(ProviderMediaAdmissionError, match="timestamp regresses"):
        build_provider_success_candidates(
            **authorities,
            prior_events=events,
            succeeded_at="2026-08-16T20:30:04Z",
        )


@pytest.mark.parametrize("failure", ("invalid_media", "provenance", "capability"))
def test_invalid_media_provenance_or_capability_mints_no_success_rows(
    tmp_path: Path,
    failure: str,
) -> None:
    payload = b"not-an-image" if failure == "invalid_media" else _image_bytes(size=(4, 3))
    if failure == "capability":
        case = _manual_response_case(tmp_path, (payload,), max_width=3)
    else:
        mutator: Callable[[JsonObject], None] | None = None
        if failure == "provenance":

            def mutate_provenance(document: JsonObject) -> None:
                document["actual_model"] = {
                    "state": "attested",
                    "model": "other/model",
                    "source_field": "$.model",
                }

            mutator = mutate_provenance
        case = _single_response_case(tmp_path, payload, mutate_receipt=mutator)

    with pytest.raises((ProviderMediaAdmissionError, ProviderSuccessConflictError)):
        _publish_success(case)

    assert case.journal.read_state(case.attempt.attempt_id).state == "response_received"
    with pytest.raises(JournalNotFoundError):
        case.journal.read_provider_success(case.attempt.attempt_id)
    with sqlite3.connect(case.journal.path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM attempt_events WHERE state='succeeded'"
        ).fetchone() == (0,)
        assert connection.execute(
            f'SELECT count(*) FROM "{_success_table(connection)}"'
        ).fetchone() == (0,)
        assert connection.execute(
            f'SELECT count(*) FROM "{_occurrence_table(connection)}"'
        ).fetchone() == (0,)


def test_multioutput_success_is_one_atomic_ordered_commit_and_reopens(tmp_path: Path) -> None:
    payloads = (_image_bytes(value=(11, 22, 33)), _image_bytes(value=(44, 55, 66)))
    case = _manual_response_case(tmp_path, payloads)

    result = _publish_success(case)
    reopened = AttemptJournal(case.journal.path).read_provider_success(case.attempt.attempt_id)

    assert isinstance(result, ProviderSuccessPublishResult)
    assert isinstance(reopened, StoredProviderSuccess)
    assert result.created is True
    assert [item.output_index for item in result.occurrences] == [0, 1]
    assert len({item.output_occurrence_id for item in result.occurrences}) == 2
    assert result.event.detail["output_occurrence_ids"] == tuple(
        item.output_occurrence_id for item in result.occurrences
    )
    assert result.state.state == "succeeded" and result.state.terminal is True
    assert reopened.occurrences == result.occurrences
    assert reopened.event == result.event
    assert all(payload not in repr(reopened).encode() for payload in payloads)
    assert [event.state for event in case.journal.read_events(case.attempt.attempt_id)] == [
        "prepared",
        "submitted",
        "response_received",
        "succeeded",
    ]
    case.journal.verify_integrity()

    with sqlite3.connect(case.journal.path) as connection:
        occurrence_table = _occurrence_table(connection)
        assert connection.execute(f'SELECT count(*) FROM "{occurrence_table}"').fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(f'DELETE FROM "{occurrence_table}" WHERE output_index=0')


def test_last_output_failure_and_commit_failure_roll_back_every_terminal_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _manual_response_case(tmp_path, (_image_bytes(), b"invalid-final-output"))

    with pytest.raises(ProviderMediaAdmissionError):
        _publish_success(case)
    assert case.journal.read_state(case.attempt.attempt_id).state == "response_received"

    valid = _manual_response_case(
        tmp_path / "commit-failure",
        (_image_bytes(value=(1, 2, 3)), _image_bytes(value=(4, 5, 6))),
    )
    original_commit = AttemptJournal._commit

    def reject_commit(_: sqlite3.Connection) -> None:
        raise AttemptJournalError("simulated pre-commit failure")

    monkeypatch.setattr(AttemptJournal, "_commit", staticmethod(reject_commit))
    with pytest.raises(AttemptJournalError, match="pre-commit"):
        _publish_success(valid)
    monkeypatch.setattr(AttemptJournal, "_commit", staticmethod(original_commit))

    assert AttemptJournal(valid.journal.path).read_state(valid.attempt.attempt_id).state == (
        "response_received"
    )
    with pytest.raises(JournalNotFoundError):
        AttemptJournal(valid.journal.path).read_provider_success(valid.attempt.attempt_id)


def test_exact_replay_precedes_stale_cas_ignores_new_timestamp_and_conflicts_on_artifacts(
    tmp_path: Path,
) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    first = _publish_success(case)

    replay = _publish_success(
        case,
        journal=AttemptJournal(case.journal.path),
        succeeded_at="2026-08-16T20:31:00Z",
    )

    assert replay.created is False
    assert replay.occurrences == first.occurrences
    assert replay.event == first.event
    assert replay.event.recorded_at == _SUCCEEDED_AT
    normalized = to_json_dict(case.normalized_request)
    normalized["prompt"]["text"] += " changed"
    with pytest.raises(ProviderSuccessConflictError):
        case.journal.publish_provider_success(
            case.attempt.attempt_id,
            case.packet,
            normalized,
            succeeded_at=_SUCCEEDED_AT,
            expected_head_event_id=case.response_event.attempt_event_id,
            expected_next_sequence=4,
        )


def test_stored_media_rederivation_runs_outside_journal_transactions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    _publish_success(case)
    stored_response = case.journal.read_provider_response(case.attempt.attempt_id)
    original_compile = provider_media_module._compile_provider_output_media
    original_begin_read = AttemptJournal._begin_read
    read_connections: list[sqlite3.Connection] = []
    compile_calls = 0

    def tracked_begin_read(journal: AttemptJournal) -> sqlite3.Connection:
        connection = original_begin_read(journal)
        read_connections.append(connection)
        return connection

    def compile_with_writer_probe(*args: Any, **kwargs: Any) -> ProviderMediaCompilation:
        nonlocal compile_calls
        compile_calls += 1
        assert read_connections
        for read_connection in read_connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                read_connection.execute("SELECT 1")
        read_connections.clear()
        with sqlite3.connect(case.journal.path, timeout=0.05) as contender:
            contender.execute("BEGIN IMMEDIATE")
            contender.rollback()
        return original_compile(*args, **kwargs)

    monkeypatch.setattr(AttemptJournal, "_begin_read", tracked_begin_read)
    monkeypatch.setattr(
        provider_media_module,
        "_compile_provider_output_media",
        compile_with_writer_probe,
    )
    stored = case.journal.read_provider_success(case.attempt.attempt_id)
    assert stored.event.state == "succeeded"
    assert compile_calls == 1

    # Writer paths only perform the shallow coexistence/authority proof. In particular,
    # exact provider-response replay must not invoke Pillow while BEGIN IMMEDIATE is held.
    replay = case.journal.publish_provider_response(
        case.receipt,
        stored_response.raw_response_bytes,
        case.output_bytes,
        expected_head_event_id=case.response_event.attempt_event_id,
        expected_next_sequence=4,
    )
    assert replay.created is False
    assert compile_calls == 1


def test_concurrent_exact_success_publishers_converge_and_lost_ack_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    barrier = threading.Barrier(2)
    outcomes: Queue[bool | Exception] = Queue()

    def worker() -> None:
        try:
            barrier.wait()
            outcomes.put(_publish_success(case, journal=AttemptJournal(case.journal.path)).created)
        except Exception as error:  # noqa: BLE001 - concurrent outcomes are asserted below
            outcomes.put(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    observed = [outcomes.get() for _ in range(2)]
    assert not [item for item in observed if isinstance(item, Exception)]
    assert sorted(item for item in observed if isinstance(item, bool)) == [False, True]

    lost = _single_response_case(tmp_path / "lost-ack", _image_bytes(value=(5, 6, 7)))
    original_commit = AttemptJournal._commit

    def commit_then_lose_ack(connection: sqlite3.Connection) -> None:
        original_commit(connection)
        raise AttemptJournalError("simulated lost success commit acknowledgement")

    monkeypatch.setattr(AttemptJournal, "_commit", staticmethod(commit_then_lose_ack))
    with pytest.raises(AttemptJournalError, match="lost success"):
        _publish_success(lost)
    monkeypatch.setattr(AttemptJournal, "_commit", staticmethod(original_commit))
    recovered = _publish_success(
        lost,
        journal=AttemptJournal(lost.journal.path),
        succeeded_at="2026-08-16T20:32:00Z",
    )
    assert recovered.created is False
    assert recovered.event.recorded_at == _SUCCEEDED_AT


def test_success_and_failed_terminal_cas_cannot_both_commit(tmp_path: Path) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    failed = _event(
        attempt_id=case.attempt.attempt_id,
        sequence=4,
        state="failed",
        recorded_at=_SUCCEEDED_AT,
        detail={
            "kind": "failed",
            "failure_stage": "output_validation",
            "failure_code": "operator_rejected",
        },
    )
    barrier = threading.Barrier(2)
    outcomes: Queue[str | Exception] = Queue()

    def success_worker() -> None:
        try:
            barrier.wait()
            _publish_success(case, journal=AttemptJournal(case.journal.path))
            outcomes.put("succeeded")
        except Exception as error:  # noqa: BLE001
            outcomes.put(error)

    def failed_worker() -> None:
        try:
            barrier.wait()
            AttemptJournal(case.journal.path).append_event(
                failed,
                expected_head_event_id=case.response_event.attempt_event_id,
                expected_next_sequence=4,
            )
            outcomes.put("failed")
        except Exception as error:  # noqa: BLE001
            outcomes.put(error)

    threads = [threading.Thread(target=success_worker), threading.Thread(target=failed_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    observed = [outcomes.get() for _ in range(2)]
    assert len([item for item in observed if isinstance(item, str)]) == 1
    terminal = case.journal.read_state(case.attempt.attempt_id).state
    assert terminal in {"succeeded", "failed"}
    if terminal == "succeeded":
        case.journal.read_provider_success(case.attempt.attempt_id)
    else:
        with pytest.raises(JournalNotFoundError):
            case.journal.read_provider_success(case.attempt.attempt_id)


def test_timestamp_secrets_and_cumulative_decoded_rgb_budget_fail_without_success(
    tmp_path: Path,
) -> None:
    secret = "super-secret-success-value"
    timestamp_case = _single_response_case(
        tmp_path / "timestamp",
        _image_bytes(),
        forbidden_secrets=(secret,),
    )
    with pytest.raises(ProviderSuccessConflictError, match="time|timestamp|regress"):
        _publish_success(timestamp_case, succeeded_at="2026-08-16T20:30:04Z")

    normalized = to_json_dict(timestamp_case.normalized_request)
    normalized["prompt"]["text"] = secret
    with pytest.raises(JournalSecurityError):
        timestamp_case.journal.publish_provider_success(
            timestamp_case.attempt.attempt_id,
            timestamp_case.packet,
            normalized,
            succeeded_at=_SUCCEEDED_AT,
            expected_head_event_id=timestamp_case.response_event.attempt_event_id,
            expected_next_sequence=4,
        )
    with pytest.raises(JournalNotFoundError):
        timestamp_case.journal.read_provider_success(timestamp_case.attempt.attempt_id)

    # Six individually valid 2048-square RGB images exceed the registered 48 MiB work cap
    # while their solid-color PNG encodings remain tiny and inside response-storage limits.
    payloads = tuple(
        _image_bytes(size=(2048, 2048), value=(index, index, index)) for index in range(6)
    )
    budget_case = _manual_response_case(tmp_path / "rgb-budget", payloads)
    with pytest.raises(ProviderMediaAdmissionError, match="cumulative|decoded|RGB|bound"):
        _publish_success(budget_case)
    with pytest.raises(JournalNotFoundError):
        budget_case.journal.read_provider_success(budget_case.attempt.attempt_id)


def test_hostile_mapping_and_scalar_errors_cannot_leak_through_exception_context(
    tmp_path: Path,
) -> None:
    secret = "credential-sentinel-hostile-mapping-123456"

    class ExplodingMapping(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError(secret)

        def __len__(self) -> int:
            return 1

    class EvilStr(str):
        def encode(self, *args: Any, **kwargs: Any) -> bytes:
            del args, kwargs
            raise RuntimeError(secret)

    class ExplodingSecrets:
        def __iter__(self) -> Iterator[str]:
            raise RuntimeError(secret)

    class StatefulMapping(Mapping[str, Any]):
        def __init__(self) -> None:
            self.iterations = 0

        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            self.iterations += 1
            if self.iterations >= 4:
                raise RuntimeError(secret)
            return iter(())

        def __len__(self) -> int:
            return 0

    def assert_secret_free(error: BaseException) -> None:
        current: BaseException | None = error
        while current is not None:
            assert secret not in str(current)
            current = current.__cause__ or current.__context__

    mapping_path = (tmp_path / "hostile-mapping.sqlite3").resolve()
    mapping_journal = AttemptJournal(mapping_path)
    with pytest.raises(JournalSecurityError) as mapping_error:
        mapping_journal.publish_provider_success(
            "00000000-0000-4000-8000-000000000001",
            ExplodingMapping(),
            {},
            succeeded_at=_SUCCEEDED_AT,
            expected_head_event_id="a" * 64,
            expected_next_sequence=4,
        )
    assert_secret_free(mapping_error.value)
    assert not mapping_path.exists()
    assert not Path(f"{mapping_path}-wal").exists()

    scalar_path = (tmp_path / "hostile-scalar.sqlite3").resolve()
    scalar_journal = AttemptJournal(scalar_path)
    with pytest.raises(JournalSecurityError) as scalar_error:
        scalar_journal.publish_provider_success(
            "00000000-0000-4000-8000-000000000001",
            {},
            {},
            succeeded_at=EvilStr(_SUCCEEDED_AT),
            expected_head_event_id="a" * 64,
            expected_next_sequence=4,
        )
    assert_secret_free(scalar_error.value)
    assert not scalar_path.exists()
    assert not Path(f"{scalar_path}-wal").exists()

    iterable_path = (tmp_path / "hostile-secret-iterable.sqlite3").resolve()
    with pytest.raises(JournalSecurityError) as iterable_error:
        AttemptJournal(iterable_path, forbidden_secrets=ExplodingSecrets())
    assert_secret_free(iterable_error.value)
    assert not iterable_path.exists()

    conversion_path = (tmp_path / "stateful-mapping.sqlite3").resolve()
    conversion_journal = AttemptJournal(conversion_path)
    with pytest.raises(ProviderSuccessConflictError) as conversion_error:
        conversion_journal.publish_provider_success(
            "00000000-0000-4000-8000-000000000001",
            _valid_packet(),
            StatefulMapping(),
            succeeded_at=_SUCCEEDED_AT,
            expected_head_event_id="a" * 64,
            expected_next_sequence=4,
        )
    assert_secret_free(conversion_error.value)
    assert not conversion_path.exists()
    assert not Path(f"{conversion_path}-wal").exists()


def _remove_immutable_row(
    path: Path,
    *,
    table: str,
    where: str,
    parameters: tuple[object, ...],
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        trigger = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name=? AND name LIKE '%delete_immutable'",
            (table,),
        ).fetchone()
        assert trigger is not None and trigger[1] is not None
        connection.execute(f'DROP TRIGGER "{trigger[0]}"')
        connection.execute(f'DELETE FROM "{table}" WHERE {where}', parameters)
        connection.execute(str(trigger[1]))


@pytest.mark.parametrize("missing", ("event", "gate", "occurrence"))
def test_split_or_orphan_success_evidence_is_corruption(tmp_path: Path, missing: str) -> None:
    case = _single_response_case(tmp_path, _image_bytes())
    result = _publish_success(case)
    with sqlite3.connect(case.journal.path) as connection:
        success_table = _success_table(connection)
        occurrence_table = _occurrence_table(connection)
    if missing == "event":
        _remove_immutable_row(
            case.journal.path,
            table="attempt_events",
            where="attempt_id=? AND state='succeeded'",
            parameters=(case.attempt.attempt_id,),
        )
    elif missing == "gate":
        _remove_immutable_row(
            case.journal.path,
            table=success_table,
            where="attempt_id=?",
            parameters=(case.attempt.attempt_id,),
        )
    else:
        _remove_immutable_row(
            case.journal.path,
            table=occurrence_table,
            where="output_occurrence_id=?",
            parameters=(result.occurrences[0].output_occurrence_id,),
        )

    with pytest.raises(JournalCorruptionError, match="success|succeeded|occurrence|evidence"):
        AttemptJournal(case.journal.path).verify_integrity()


def test_reordered_occurrence_canonical_bytes_are_corruption(tmp_path: Path) -> None:
    case = _manual_response_case(
        tmp_path,
        (_image_bytes(value=(1, 2, 3)), _image_bytes(value=(4, 5, 6))),
    )
    _publish_success(case)
    with sqlite3.connect(case.journal.path) as connection:
        table = _occurrence_table(connection)
        trigger = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name=? AND name LIKE '%update_immutable'",
            (table,),
        ).fetchone()
        assert trigger is not None and trigger[1] is not None
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
        assert {"canonical_sha256", "document_json"} <= columns
        rows = connection.execute(
            f'SELECT output_index, canonical_sha256, document_json FROM "{table}" '
            "ORDER BY output_index"
        ).fetchall()
        assert len(rows) == 2
        connection.execute(f'DROP TRIGGER "{trigger[0]}"')
        connection.execute(
            f'UPDATE "{table}" SET canonical_sha256=?, document_json=? WHERE output_index=0',
            (rows[1][1], rows[1][2]),
        )
        connection.execute(str(trigger[1]))

    with pytest.raises(JournalCorruptionError, match="occurrence|index|identity|canonical"):
        AttemptJournal(case.journal.path).read_provider_success(case.attempt.attempt_id)
