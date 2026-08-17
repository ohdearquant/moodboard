"""Byte-derived provider media admission and terminal-success candidates.

This module is pure: it decodes exact receipt payloads, derives raw-generator output
occurrences, and proves the complete ADR-0014 authority chain.  Persistence and CAS
authority remain in :mod:`moodboard.attempt_journal`.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from moodboard.contracts import is_canonical_utc_timestamp
from moodboard.intent_packet import IntentPacket
from moodboard.intent_packet import from_json_dict as packet_from_json
from moodboard.intent_packet import to_json_dict as packet_to_json
from moodboard.locality import (
    DEFAULT_COMPILER_MANIFEST,
    LocalityError,
    _compile_provider_output_media,
)
from moodboard.provider_artifacts import (
    EVENT_VERSION,
    OUTPUT_VERSION,
    GenerationAttempt,
    GenerationAttemptEvent,
    GenerationRun,
    NormalizedProviderRequest,
    OutputOccurrence,
    ProviderArtifact,
    ProviderArtifactError,
    ProviderCapabilitySnapshot,
    ProviderReceipt,
    from_json_dict,
    seal_provider_artifact,
    to_json_dict,
    validate_artifact_bundle,
)

__all__ = [
    "ProviderMediaAdmissionError",
    "ProviderSuccessCandidates",
    "build_provider_success_candidates",
]

_MAX_OUTPUTS = 8
_ArtifactT = TypeVar("_ArtifactT", bound=ProviderArtifact)


class ProviderMediaAdmissionError(ValueError):
    """Exact provider bytes or their authority chain cannot become eligible output."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ProviderSuccessCandidates:
    """Fully validated, persistence-neutral output occurrences and success event."""

    occurrences: tuple[OutputOccurrence, ...]
    event: GenerationAttemptEvent


def _packet(value: IntentPacket | Mapping[str, Any]) -> IntentPacket:
    try:
        if isinstance(value, IntentPacket):
            return packet_from_json(packet_to_json(value))
        if isinstance(value, Mapping):
            return packet_from_json(copy.deepcopy(dict(value)))
    except Exception:
        pass
    raise ProviderMediaAdmissionError(
        "authority_invalid", "intent packet is not valid for provider media admission"
    ) from None


def _artifact(value: _ArtifactT | Mapping[str, Any], expected: type[_ArtifactT]) -> _ArtifactT:
    try:
        if isinstance(value, expected):
            artifact = from_json_dict(to_json_dict(value))
        elif isinstance(value, Mapping):
            artifact = from_json_dict(copy.deepcopy(dict(value)))
        else:
            artifact = None
    except Exception:
        artifact = None
    if not isinstance(artifact, expected):
        raise ProviderMediaAdmissionError(
            "authority_invalid", "provider authority is not valid for media admission"
        ) from None
    return artifact


def _event_sequence(
    values: Iterable[GenerationAttemptEvent | Mapping[str, Any]],
) -> tuple[GenerationAttemptEvent, ...]:
    events: list[GenerationAttemptEvent] = []
    try:
        for index, value in enumerate(values):
            if index >= 5:
                raise ProviderMediaAdmissionError(
                    "authority_invalid", "attempt history exceeds the terminal bound"
                )
            events.append(_artifact(value, GenerationAttemptEvent))
    except ProviderMediaAdmissionError:
        raise
    except Exception:
        raise ProviderMediaAdmissionError(
            "authority_invalid", "attempt history is not valid for media admission"
        ) from None
    if [event.sequence for event in events] != list(range(1, len(events) + 1)):
        raise ProviderMediaAdmissionError(
            "authority_invalid", "attempt history must be supplied in sequence order"
        )
    if any(
        _timestamp_key(current.recorded_at) < _timestamp_key(previous.recorded_at)
        for previous, current in zip(events, events[1:], strict=False)
    ):
        raise ProviderMediaAdmissionError(
            "authority_invalid", "attempt history timestamps must not regress"
        )
    return tuple(events)


def _timestamp_key(value: str) -> tuple[str, int]:
    fraction = value[20:-1] if len(value) > 20 else ""
    return value[:19], int(fraction.ljust(9, "0") or "0")


def build_provider_success_candidates(
    *,
    intent_packet: IntentPacket | Mapping[str, Any],
    generation_run: GenerationRun | Mapping[str, Any],
    attempt: GenerationAttempt | Mapping[str, Any],
    capability: ProviderCapabilitySnapshot | Mapping[str, Any],
    normalized_request: NormalizedProviderRequest | Mapping[str, Any],
    receipt: ProviderReceipt | Mapping[str, Any],
    prior_events: Iterable[GenerationAttemptEvent | Mapping[str, Any]],
    output_bytes: tuple[bytes, ...],
    succeeded_at: str,
) -> ProviderSuccessCandidates:
    """Derive and prove one complete success package from exact provider bytes.

    Media compilation is sequential and no decoded raster is retained in the returned
    value.  The cumulative RGB-work ceiling is the registered single-output compiler
    ceiling, so a wider route cannot amplify decoder memory through this v1 gate.
    """

    packet = _packet(intent_packet)
    run = _artifact(generation_run, GenerationRun)
    attempt_artifact = _artifact(attempt, GenerationAttempt)
    capability_artifact = _artifact(capability, ProviderCapabilitySnapshot)
    normalized = _artifact(normalized_request, NormalizedProviderRequest)
    receipt_artifact = _artifact(receipt, ProviderReceipt)
    events = _event_sequence(prior_events)
    if not is_canonical_utc_timestamp(succeeded_at) or (
        events and _timestamp_key(succeeded_at) < _timestamp_key(events[-1].recorded_at)
    ):
        raise ProviderMediaAdmissionError(
            "authority_not_eligible",
            "provider success timestamp regresses behind its attempt history",
        )
    if type(output_bytes) is not tuple or not 1 <= len(output_bytes) <= _MAX_OUTPUTS:
        raise ProviderMediaAdmissionError(
            "provider_payload_mismatch", "provider output count is outside the admission bound"
        )
    if len(receipt_artifact.outputs) != len(output_bytes):
        raise ProviderMediaAdmissionError(
            "provider_payload_mismatch", "provider output count does not match its receipt"
        )

    packet_document = packet_to_json(packet)
    cumulative_rgb_bytes = 0
    occurrences: list[OutputOccurrence] = []
    for index, payload in enumerate(output_bytes):
        if type(payload) is not bytes:
            raise ProviderMediaAdmissionError(
                "provider_payload_mismatch", "provider output must be exact built-in bytes"
            )
        try:
            media = _compile_provider_output_media(
                receipt_artifact,
                output_index=index,
                output_bytes=payload,
                max_decoded_rgb_bytes=(
                    DEFAULT_COMPILER_MANIFEST.max_rgb_bytes - cumulative_rgb_bytes
                ),
            )
        except LocalityError as error:
            message = (
                "provider outputs exceed the cumulative decoded RGB work bound"
                if error.code == "decoded_rgb_budget_exceeded"
                else "provider output failed the registered media admission profile"
            )
            raise ProviderMediaAdmissionError(error.code, message) from None
        cumulative_rgb_bytes += media.canonical_raster.byte_count
        if cumulative_rgb_bytes > DEFAULT_COMPILER_MANIFEST.max_rgb_bytes:
            raise ProviderMediaAdmissionError(
                "decoded_rgb_budget_exceeded",
                "provider outputs exceed the cumulative decoded RGB work bound",
            )
        receipt_output = receipt_artifact.outputs[index]
        if not isinstance(receipt_output, Mapping):
            raise ProviderMediaAdmissionError(
                "provider_payload_mismatch", "provider receipt output is not an object"
            )
        try:
            sealed = seal_provider_artifact(
                {
                    "schema_version": OUTPUT_VERSION,
                    "producer_kind": "generator_raw",
                    "attempt_id": attempt_artifact.attempt_id,
                    "output_index": index,
                    "role": "generated_image",
                    "generation_run_id": run.generation_run_id,
                    "intent_packet_id": packet.intent_packet_id,
                    "normalized_request_id": normalized.normalized_request_id,
                    "provider_receipt_id": receipt_artifact.provider_receipt_id,
                    "original": {
                        "content_ref": receipt_output["content_ref"],
                        "content_sha256": receipt_output["content_sha256"],
                        "mime": media.detected_mime,
                        "byte_count": receipt_output["byte_count"],
                        "width": media.oriented_width,
                        "height": media.oriented_height,
                    },
                    "media_validation": {
                        "schema_version": "moodboard.media-validation.v1",
                        "state": "pass",
                        "decoder_revision": media.decoder_revision,
                        "measured_content_sha256": receipt_output["content_sha256"],
                        "measured_content_ref": receipt_output["content_ref"],
                        "measured_byte_count": receipt_output["byte_count"],
                        "measured_mime": media.detected_mime,
                        "measured_width": media.oriented_width,
                        "measured_height": media.oriented_height,
                        "measured_mode": media.observed_mode,
                        "frame_count": media.frame_count,
                        "active_content": media.active_content,
                        "bounded": media.bounded,
                    },
                    "admission": {"state": "eligible", "rejection_reasons": []},
                    "lineage": {
                        "source_asset_id": packet_document["source"]["asset_id"],
                        "source_content_sha256": packet_document["source"]["content_sha256"],
                        "reference_occurrence_ids": [
                            item["reference_occurrence_id"]
                            for item in packet_document["references"]
                        ],
                    },
                }
            )
        except (KeyError, ProviderArtifactError, TypeError):
            raise ProviderMediaAdmissionError(
                "authority_invalid", "provider output occurrence could not be derived"
            ) from None
        if not isinstance(sealed, OutputOccurrence):
            raise ProviderMediaAdmissionError(
                "authority_invalid", "provider output occurrence has the wrong artifact branch"
            )
        occurrences.append(sealed)

    try:
        event = seal_provider_artifact(
            {
                "schema_version": EVENT_VERSION,
                "attempt_id": attempt_artifact.attempt_id,
                "sequence": len(events) + 1,
                "state": "succeeded",
                "recorded_at": succeeded_at,
                "detail": {
                    "kind": "succeeded",
                    "output_occurrence_ids": [
                        occurrence.output_occurrence_id for occurrence in occurrences
                    ],
                },
            }
        )
        if not isinstance(event, GenerationAttemptEvent):
            raise TypeError("wrong event branch")
        validate_artifact_bundle(
            [
                run,
                capability_artifact,
                normalized,
                attempt_artifact,
                *events,
                event,
                receipt_artifact,
                *occurrences,
            ],
            intent_packet=packet,
        )
    except (ProviderArtifactError, TypeError, ValueError):
        raise ProviderMediaAdmissionError(
            "authority_not_eligible",
            "provider authority chain cannot produce eligible terminal output",
        ) from None
    return ProviderSuccessCandidates(tuple(occurrences), event)
