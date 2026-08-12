"""Freeze one real governed preference replay into the offline viewer build.

The replay remains the evidence source of truth.  This bridge exposes only a closed aggregate
projection needed by the presentation and deliberately preserves ``policy_simulated`` plus the
producer's non-claims.  It never upgrades synthetic policy labels into human feedback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

BRIDGE_FORMAT = "moodboard.viewer-preference-replay-bridge.v1"
GENERATOR_REVISION = "moodboard.preference-replay-viewer-bridge.v1"
REPLAY_SCHEMA = "moodboard.preference-demo-replay.v1"
_MAX_BYTES = 16 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")
_BRIDGE_KEYS = frozenset({"evidence", "format_version", "generator_revision", "input", "state"})
_INPUT_KEYS = frozenset({"byte_size", "replay_fingerprint", "schema_version", "sha256"})
_EVIDENCE_KEYS = frozenset(
    {
        "bindings",
        "delta",
        "event_counts",
        "evidence_class",
        "model_a",
        "model_b",
        "non_claims",
        "replay_fingerprint",
        "support_refusal",
        "verification",
    }
)
_BINDING_KEYS = frozenset(
    {
        "board_entity_id",
        "board_id",
        "candidate_pool_sha256",
        "descriptor_fingerprint",
        "feature_producer_id",
        "feature_producer_revision",
        "feature_schema_id",
        "model_key",
        "schema_version",
        "scope_sha256",
        "source_report_sha256",
    }
)
_DELTA_KEYS = frozenset(
    {
        "adaptation_direction_observed",
        "mean_delta",
        "mean_probability_for_policy_b_preferred_after",
        "mean_probability_for_policy_b_preferred_before",
        "outcome",
        "probe_count",
    }
)
_COUNT_KEYS = frozenset(
    {
        "model_a_calibration_decisive",
        "model_a_calibration_ties",
        "model_a_test_decisive",
        "model_a_train_decisive",
        "model_b_appended_train_decisive",
        "total",
    }
)
_MODEL_KEYS = frozenset(
    {
        "bundle_ref",
        "fann_inference_verified",
        "model_fingerprint",
        "network_content_ref",
        "preference_model_id",
        "snapshot_event_count",
    }
)
_SUPPORT_KEYS = frozenset({"captured", "classification", "message"})
_VERIFICATION_KEYS = frozenset(
    {
        "fann_inference_verified",
        "frozen_probe_count",
        "model_a_predictions_unchanged_after_model_b",
        "model_snapshots_distinct",
        "restart_exact",
    }
)
_REPLAY_KEYS = frozenset(
    {
        "actor",
        "artifact",
        "delta",
        "events",
        "evidence_class",
        "frozen_conflict_probes",
        "immutability",
        "model_a",
        "model_b",
        "namespace",
        "non_claims",
        "phase_counts",
        "policies",
        "replay_fingerprint",
        "restart_verification",
        "schema_version",
        "selection_revision",
        "split_revision",
        "support_refusal",
    }
)
_REPLAY_ARTIFACT_KEYS = frozenset(
    {
        "board_entity_id",
        "board_id",
        "candidate_pool_sha256",
        "descriptor_fingerprint",
        "feature_producer_id",
        "feature_producer_revision",
        "feature_schema_id",
        "model_key",
        "schema_version",
        "scope_sha256",
        "source_report_sha256",
    }
)
_REPLAY_MODEL_KEYS = frozenset(
    {
        "calibration",
        "content_ref",
        "fann_inference_verified",
        "model_fingerprint",
        "network_content_ref",
        "network_sha256",
        "preference_model_id",
        "test_metrics",
        "training",
    }
)
_MODEL_A_COUNT_KEYS = frozenset(
    {"calibration_decisive", "calibration_ties", "test_decisive", "train_decisive"}
)
_MODEL_B_COUNT_KEYS = frozenset({"train_decisive"})
_PREFERENCE_FEATURE_ARTIFACT_SCHEMA = "moodboard.preference-feature-artifact.v2"
_SUPPORT_REFUSAL_MESSAGE = (
    "moodboard.train_preference requires at least 64 distinct decisive train "
    "unordered-pair groups; observed 0"
)
_EXPECTED_EVENT_COUNTS = {
    "model_a_calibration_decisive": 16,
    "model_a_calibration_ties": 16,
    "model_a_test_decisive": 16,
    "model_a_train_decisive": 64,
    "model_b_appended_train_decisive": 96,
    "total": 208,
}

__all__ = [
    "BRIDGE_FORMAT",
    "GENERATOR_REVISION",
    "PreferenceReplayViewerBridgeError",
    "compile_viewer_preference_replay_bridge",
    "fallback_viewer_preference_replay_bridge",
    "read_viewer_preference_replay_bridge",
    "validate_viewer_preference_replay_bridge",
    "write_viewer_preference_replay_bridge",
]


class PreferenceReplayViewerBridgeError(ValueError):
    """A preference replay cannot be projected without retaining its evidence contract."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _core_bytes(value: Mapping[str, Any]) -> bytes:
    core = {key: item for key, item in value.items() if key != "replay_fingerprint"}
    return json.dumps(
        core,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token!r}")


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceReplayViewerBridgeError(f"{label} must be an object")
    return value


def _read_bounded_regular_file(path: Path, label: str) -> bytes:
    """Stat a regular input and enforce its byte ceiling before allocating its contents."""

    try:
        metadata = path.stat()
    except OSError as error:
        raise PreferenceReplayViewerBridgeError(f"{label} cannot be statted: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise PreferenceReplayViewerBridgeError(f"{label} must be a regular file")
    if metadata.st_size > _MAX_BYTES:
        raise PreferenceReplayViewerBridgeError(f"{label} exceeds the byte ceiling")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PreferenceReplayViewerBridgeError(f"{label} cannot be read: {error}") from error
    if len(raw) > _MAX_BYTES:
        raise PreferenceReplayViewerBridgeError(f"{label} grew beyond the byte ceiling")
    return raw


def _closed(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        detail = f"unknown keys {unknown}" if unknown else f"missing keys {missing}"
        raise PreferenceReplayViewerBridgeError(f"{label} is not closed: {detail}")


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise PreferenceReplayViewerBridgeError(f"{label} must be a lowercase 64-hex digest")
    return value


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PreferenceReplayViewerBridgeError(f"{label} must be a UUID")
    import uuid

    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise PreferenceReplayViewerBridgeError(f"{label} must be a UUID") from error
    if str(parsed) != value:
        raise PreferenceReplayViewerBridgeError(f"{label} must be canonical lowercase UUID")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PreferenceReplayViewerBridgeError(f"{label} must be a non-negative integer")
    return value


def _probability(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PreferenceReplayViewerBridgeError(f"{label} must be a probability")
    measured = float(value)
    if not math.isfinite(measured) or not 0.0 <= measured <= 1.0:
        raise PreferenceReplayViewerBridgeError(f"{label} must be a probability")
    return measured


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise PreferenceReplayViewerBridgeError(f"{label} must be boolean")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PreferenceReplayViewerBridgeError(f"{label} must be a non-empty string")
    return value


def _snapshot_event_count(model: Mapping[str, Any], label: str) -> int:
    training = _mapping(model.get("training"), f"{label}.training")
    return _integer(training.get("snapshot_event_count"), f"{label}.training.snapshot_event_count")


def fallback_viewer_preference_replay_bridge() -> dict[str, Any]:
    """Return the sole sentinel permitted when no real replay has been frozen."""

    return {
        "evidence": None,
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": None,
        "state": "fallback",
    }


def _validate_replay(document: Mapping[str, Any]) -> None:
    _closed(document, _REPLAY_KEYS, "preference replay")
    if document.get("schema_version") != REPLAY_SCHEMA:
        raise PreferenceReplayViewerBridgeError("preference replay schema_version drifted")
    if document.get("evidence_class") != "policy_simulated":
        raise PreferenceReplayViewerBridgeError("preference replay must remain policy_simulated")
    fingerprint = _digest(document.get("replay_fingerprint"), "replay_fingerprint")
    measured = hashlib.sha256(
        b"moodboard-preference-demo-replay-v1\0" + _core_bytes(document)
    ).hexdigest()
    if fingerprint != measured:
        raise PreferenceReplayViewerBridgeError("preference replay fingerprint drifted")

    artifact = _mapping(document.get("artifact"), "preference replay artifact")
    _closed(artifact, _REPLAY_ARTIFACT_KEYS, "preference replay artifact")
    for field in (
        "board_id",
        "candidate_pool_sha256",
        "descriptor_fingerprint",
        "feature_producer_id",
        "feature_schema_id",
        "scope_sha256",
        "source_report_sha256",
    ):
        _digest(artifact.get(field), f"artifact.{field}")
    _nonempty_string(
        artifact.get("feature_producer_revision"), "artifact.feature_producer_revision"
    )
    if artifact.get("schema_version") != _PREFERENCE_FEATURE_ARTIFACT_SCHEMA:
        raise PreferenceReplayViewerBridgeError("artifact.schema_version drifted")
    _uuid(artifact.get("board_entity_id"), "artifact.board_entity_id")
    model_key = artifact.get("model_key")
    descriptor = artifact["descriptor_fingerprint"]
    if (
        not isinstance(model_key, str)
        or not model_key.startswith(f"moodboard_{descriptor}_")
        or not model_key.removeprefix(f"moodboard_{descriptor}_").isdigit()
    ):
        raise PreferenceReplayViewerBridgeError("artifact.model_key is not descriptor-bound")

    probes = document.get("frozen_conflict_probes")
    if not isinstance(probes, list) or len(probes) != 8:
        raise PreferenceReplayViewerBridgeError("preference replay must contain 8 frozen probes")
    before_values: list[float] = []
    after_values: list[float] = []
    pair_ids: set[str] = set()
    for index, probe_value in enumerate(probes):
        probe = _mapping(probe_value, f"frozen probe {index}")
        pair_id = _digest(probe.get("pair_id"), f"frozen probe {index}.pair_id")
        if pair_id in pair_ids:
            raise PreferenceReplayViewerBridgeError("frozen probe pair IDs must be unique")
        pair_ids.add(pair_id)
        before_values.append(
            _probability(
                probe.get("probability_for_policy_b_preferred_before"),
                f"frozen probe {index} before probability",
            )
        )
        after_values.append(
            _probability(
                probe.get("probability_for_policy_b_preferred_after"),
                f"frozen probe {index} after probability",
            )
        )

    delta = _mapping(document.get("delta"), "preference replay delta")
    if delta.get("measurement") != "frozen_policy_conflict_probes":
        raise PreferenceReplayViewerBridgeError("preference replay delta measurement drifted")
    before = math.fsum(before_values) / len(before_values)
    after = math.fsum(after_values) / len(after_values)
    measured_delta = after - before
    declared_before = _probability(
        delta.get("mean_probability_for_policy_b_preferred_before"), "delta before"
    )
    declared_after = _probability(
        delta.get("mean_probability_for_policy_b_preferred_after"), "delta after"
    )
    declared_delta = delta.get("mean_delta")
    if isinstance(declared_delta, bool) or not isinstance(declared_delta, int | float):
        raise PreferenceReplayViewerBridgeError("delta mean_delta must be finite")
    if not all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1.0e-12)
        for left, right in (
            (before, declared_before),
            (after, declared_after),
            (measured_delta, float(declared_delta)),
        )
    ):
        raise PreferenceReplayViewerBridgeError("preference replay delta aggregate drifted")
    direction = _boolean(delta.get("adaptation_direction_observed"), "delta direction")
    if direction != (measured_delta > 0.0):
        raise PreferenceReplayViewerBridgeError("preference replay delta direction drifted")

    events = document.get("events")
    if not isinstance(events, list):
        raise PreferenceReplayViewerBridgeError("preference replay events must be an array")
    measured_event_counts = {key: 0 for key in _EXPECTED_EVENT_COUNTS if key != "total"}
    for index, event_value in enumerate(events):
        event = _mapping(event_value, f"preference replay event {index}")
        phase = event.get("phase")
        split = event.get("split")
        if phase == "model_a_decisive" and split in {"train", "calibration", "test"}:
            measured_event_counts[f"model_a_{split}_decisive"] += 1
        elif phase == "model_a_tie" and split == "calibration":
            measured_event_counts["model_a_calibration_ties"] += 1
        elif phase == "model_b_append" and split == "train":
            measured_event_counts["model_b_appended_train_decisive"] += 1
        else:
            raise PreferenceReplayViewerBridgeError(
                f"preference replay event {index} has an unsupported phase/split"
            )
    measured_event_counts["total"] = len(events)
    if measured_event_counts != _EXPECTED_EVENT_COUNTS:
        raise PreferenceReplayViewerBridgeError("preference replay exact demo phase counts drifted")
    phase_counts = _mapping(document.get("phase_counts"), "preference replay phase_counts")
    model_a_counts = _mapping(phase_counts.get("model_a"), "phase_counts.model_a")
    model_b_counts = _mapping(phase_counts.get("model_b_append"), "phase_counts.model_b_append")
    _closed(model_a_counts, _MODEL_A_COUNT_KEYS, "phase_counts.model_a")
    _closed(model_b_counts, _MODEL_B_COUNT_KEYS, "phase_counts.model_b_append")
    source_event_counts = {
        "model_a_calibration_decisive": _integer(
            model_a_counts.get("calibration_decisive"), "phase count calibration_decisive"
        ),
        "model_a_calibration_ties": _integer(
            model_a_counts.get("calibration_ties"), "phase count calibration_ties"
        ),
        "model_a_test_decisive": _integer(
            model_a_counts.get("test_decisive"), "phase count test_decisive"
        ),
        "model_a_train_decisive": _integer(
            model_a_counts.get("train_decisive"), "phase count train_decisive"
        ),
        "model_b_appended_train_decisive": _integer(
            model_b_counts.get("train_decisive"), "phase count model B train_decisive"
        ),
        "total": len(events),
    }
    if source_event_counts != _EXPECTED_EVENT_COUNTS:
        raise PreferenceReplayViewerBridgeError("preference replay exact demo phase counts drifted")
    if source_event_counts != measured_event_counts:
        raise PreferenceReplayViewerBridgeError("preference replay event count drifted")

    model_a = _mapping(document.get("model_a"), "model_a")
    model_b = _mapping(document.get("model_b"), "model_b")
    identities: list[tuple[str, ...]] = []
    snapshot_counts: list[int] = []
    for label, model in (("model_a", model_a), ("model_b", model_b)):
        _closed(model, _REPLAY_MODEL_KEYS, label)
        identities.append(
            (
                _uuid(model.get("preference_model_id"), f"{label}.preference_model_id"),
                _digest(model.get("model_fingerprint"), f"{label}.model_fingerprint"),
                _digest(model.get("content_ref"), f"{label}.content_ref"),
                _digest(model.get("network_content_ref"), f"{label}.network_content_ref"),
                _digest(model.get("network_sha256"), f"{label}.network_sha256"),
            )
        )
        if model.get("fann_inference_verified") is not True:
            raise PreferenceReplayViewerBridgeError(f"{label} FANN inference was not verified")
        snapshot_counts.append(_snapshot_event_count(model, label))
    if any(left == right for left, right in zip(identities[0], identities[1], strict=True)):
        raise PreferenceReplayViewerBridgeError("preference model snapshots are not distinct")
    expected_snapshots = [sum(model_a_counts.values()), len(events)]
    if snapshot_counts != expected_snapshots:
        raise PreferenceReplayViewerBridgeError("preference model snapshot event count drifted")
    if snapshot_counts[1] - snapshot_counts[0] != model_b_counts["train_decisive"]:
        raise PreferenceReplayViewerBridgeError("preference model appended event count drifted")

    immutability = _mapping(document.get("immutability"), "preference replay immutability")
    restart = _mapping(document.get("restart_verification"), "restart_verification")
    if immutability.get("model_a_predictions_unchanged_after_model_b") is not True:
        raise PreferenceReplayViewerBridgeError("model A immutable prediction check failed")
    if immutability.get("model_snapshots_distinct") is not True:
        raise PreferenceReplayViewerBridgeError("model snapshot distinction check failed")
    if (
        not all(
            restart.get(field) is True
            for field in (
                "exact_prediction_equality",
                "model_fingerprint_equal",
                "preference_model_id_equal",
            )
        )
        or restart.get("probe_count") != 8
    ):
        raise PreferenceReplayViewerBridgeError("restart exact verification failed")
    support = _mapping(document.get("support_refusal"), "support_refusal")
    if (
        support.get("captured") is not True
        or support.get("classification") != "below_support_refusal"
    ):
        raise PreferenceReplayViewerBridgeError("below-support refusal was not captured")
    if support.get("message") != _SUPPORT_REFUSAL_MESSAGE:
        raise PreferenceReplayViewerBridgeError("preference replay support refusal message drifted")
    non_claims = document.get("non_claims")
    if not isinstance(non_claims, list) or not all(
        isinstance(claim, str) and claim for claim in non_claims
    ):
        raise PreferenceReplayViewerBridgeError("preference replay non_claims are invalid")
    joined = " ".join(non_claims)
    for required in ("No human preference evidence", "No online learning", "No coherence"):
        if required not in joined:
            raise PreferenceReplayViewerBridgeError(
                f"preference replay omits non-claim: {required}"
            )


def _model_projection(model: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bundle_ref": model["content_ref"],
        "fann_inference_verified": model["fann_inference_verified"],
        "model_fingerprint": model["model_fingerprint"],
        "network_content_ref": model["network_content_ref"],
        "preference_model_id": model["preference_model_id"],
        "snapshot_event_count": _snapshot_event_count(model, "model"),
    }


def _project(document: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(document["artifact"], "artifact")
    delta = _mapping(document["delta"], "delta")
    phase_counts = _mapping(document["phase_counts"], "phase_counts")
    a_counts = _mapping(phase_counts["model_a"], "phase_counts.model_a")
    b_counts = _mapping(phase_counts["model_b_append"], "phase_counts.model_b_append")
    model_a = _mapping(document["model_a"], "model_a")
    model_b = _mapping(document["model_b"], "model_b")
    immutability = _mapping(document["immutability"], "immutability")
    probes = document["frozen_conflict_probes"]
    return {
        "bindings": {key: artifact[key] for key in sorted(_BINDING_KEYS)},
        "delta": {
            "adaptation_direction_observed": delta["adaptation_direction_observed"],
            "mean_delta": delta["mean_delta"],
            "mean_probability_for_policy_b_preferred_after": delta[
                "mean_probability_for_policy_b_preferred_after"
            ],
            "mean_probability_for_policy_b_preferred_before": delta[
                "mean_probability_for_policy_b_preferred_before"
            ],
            "outcome": (
                "improvement_observed"
                if delta["adaptation_direction_observed"]
                else "no_improvement_observed"
            ),
            "probe_count": len(probes),
        },
        "event_counts": {
            "model_a_calibration_decisive": a_counts["calibration_decisive"],
            "model_a_calibration_ties": a_counts["calibration_ties"],
            "model_a_test_decisive": a_counts["test_decisive"],
            "model_a_train_decisive": a_counts["train_decisive"],
            "model_b_appended_train_decisive": b_counts["train_decisive"],
            "total": len(document["events"]),
        },
        "evidence_class": "policy_simulated",
        "model_a": _model_projection(model_a),
        "model_b": _model_projection(model_b),
        "non_claims": list(document["non_claims"]),
        "replay_fingerprint": document["replay_fingerprint"],
        "support_refusal": dict(document["support_refusal"]),
        "verification": {
            "fann_inference_verified": (
                model_a["fann_inference_verified"] and model_b["fann_inference_verified"]
            ),
            "frozen_probe_count": len(probes),
            "model_a_predictions_unchanged_after_model_b": immutability[
                "model_a_predictions_unchanged_after_model_b"
            ],
            "model_snapshots_distinct": immutability["model_snapshots_distinct"],
            "restart_exact": True,
        },
    }


def compile_viewer_preference_replay_bridge(source: Path) -> dict[str, Any]:
    """Validate one canonical replay and return its closed aggregate viewer projection."""

    path = Path(source)
    raw = _read_bounded_regular_file(path, "preference replay")
    try:
        parsed = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PreferenceReplayViewerBridgeError(
            f"preference replay is invalid JSON: {error}"
        ) from error
    document = _mapping(parsed, "preference replay")
    if raw != _canonical_bytes(document):
        raise PreferenceReplayViewerBridgeError("preference replay is not canonical JSON")
    _validate_replay(document)
    fingerprint = document["replay_fingerprint"]
    bridge = {
        "evidence": _project(document),
        "format_version": BRIDGE_FORMAT,
        "generator_revision": GENERATOR_REVISION,
        "input": {
            "byte_size": len(raw),
            "replay_fingerprint": fingerprint,
            "schema_version": REPLAY_SCHEMA,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "state": "projected",
    }
    validate_viewer_preference_replay_bridge(bridge)
    return bridge


def validate_viewer_preference_replay_bridge(value: Mapping[str, Any]) -> None:
    """Fail closed on any bridge projection, arithmetic, identity, or status drift."""

    bridge = _mapping(value, "preference replay viewer bridge")
    _closed(bridge, _BRIDGE_KEYS, "preference replay viewer bridge")
    if (
        bridge["format_version"] != BRIDGE_FORMAT
        or bridge["generator_revision"] != GENERATOR_REVISION
    ):
        raise PreferenceReplayViewerBridgeError("preference replay bridge identity drifted")
    state = bridge["state"]
    if state == "fallback":
        if bridge["input"] is not None or bridge["evidence"] is not None:
            raise PreferenceReplayViewerBridgeError("fallback bridge cannot carry evidence")
        return
    if state != "projected":
        raise PreferenceReplayViewerBridgeError("preference replay bridge state is unsupported")
    identity = _mapping(bridge["input"], "bridge input")
    _closed(identity, _INPUT_KEYS, "bridge input")
    _integer(identity["byte_size"], "input.byte_size")
    if identity["byte_size"] < 1 or identity["schema_version"] != REPLAY_SCHEMA:
        raise PreferenceReplayViewerBridgeError("preference replay input identity drifted")
    replay_fingerprint = _digest(identity["replay_fingerprint"], "input.replay_fingerprint")
    _digest(identity["sha256"], "input.sha256")
    evidence = _mapping(bridge["evidence"], "bridge evidence")
    _closed(evidence, _EVIDENCE_KEYS, "bridge evidence")
    if evidence["evidence_class"] != "policy_simulated":
        raise PreferenceReplayViewerBridgeError("bridge evidence must remain policy_simulated")
    if _digest(evidence["replay_fingerprint"], "evidence.replay_fingerprint") != replay_fingerprint:
        raise PreferenceReplayViewerBridgeError("bridge replay fingerprint drifted")
    bindings = _mapping(evidence["bindings"], "evidence.bindings")
    _closed(bindings, _BINDING_KEYS, "evidence.bindings")
    for field in _BINDING_KEYS - {
        "board_entity_id",
        "feature_producer_revision",
        "model_key",
        "schema_version",
    }:
        _digest(bindings[field], f"bindings.{field}")
    _nonempty_string(bindings["feature_producer_revision"], "bindings.feature_producer_revision")
    if bindings["schema_version"] != _PREFERENCE_FEATURE_ARTIFACT_SCHEMA:
        raise PreferenceReplayViewerBridgeError("bindings.schema_version drifted")
    _uuid(bindings["board_entity_id"], "bindings.board_entity_id")
    if not str(bindings["model_key"]).startswith(
        f"moodboard_{bindings['descriptor_fingerprint']}_"
    ):
        raise PreferenceReplayViewerBridgeError("bindings.model_key is not descriptor-bound")
    delta = _mapping(evidence["delta"], "evidence.delta")
    _closed(delta, _DELTA_KEYS, "evidence.delta")
    before = _probability(delta["mean_probability_for_policy_b_preferred_before"], "delta.before")
    after = _probability(delta["mean_probability_for_policy_b_preferred_after"], "delta.after")
    mean_delta = delta["mean_delta"]
    if (
        isinstance(mean_delta, bool)
        or not isinstance(mean_delta, int | float)
        or not math.isfinite(float(mean_delta))
    ):
        raise PreferenceReplayViewerBridgeError("delta.mean_delta must be finite")
    if not math.isclose(float(mean_delta), after - before, rel_tol=0.0, abs_tol=1.0e-12):
        raise PreferenceReplayViewerBridgeError("delta.mean_delta drifted from before and after")
    direction = _boolean(delta["adaptation_direction_observed"], "delta direction")
    if direction != (mean_delta > 0.0):
        raise PreferenceReplayViewerBridgeError("delta direction contradicts mean_delta")
    expected_outcome = "improvement_observed" if direction else "no_improvement_observed"
    if delta["outcome"] != expected_outcome or delta["probe_count"] != 8:
        raise PreferenceReplayViewerBridgeError("delta outcome or frozen probe count drifted")
    counts = _mapping(evidence["event_counts"], "evidence.event_counts")
    _closed(counts, _COUNT_KEYS, "evidence.event_counts")
    components = [_integer(counts[key], f"event_counts.{key}") for key in _COUNT_KEYS - {"total"}]
    if _integer(counts["total"], "event_counts.total") != sum(components):
        raise PreferenceReplayViewerBridgeError("event count total drifted")
    measured_counts = {key: _integer(counts[key], f"event_counts.{key}") for key in _COUNT_KEYS}
    if measured_counts != _EXPECTED_EVENT_COUNTS:
        raise PreferenceReplayViewerBridgeError("preference replay exact demo phase counts drifted")
    models = []
    snapshot_counts = []
    for label in ("model_a", "model_b"):
        model = _mapping(evidence[label], label)
        _closed(model, _MODEL_KEYS, label)
        if model["fann_inference_verified"] is not True:
            raise PreferenceReplayViewerBridgeError(f"{label} FANN inference is unverified")
        models.append(
            (
                _uuid(model["preference_model_id"], f"{label}.preference_model_id"),
                _digest(model["model_fingerprint"], f"{label}.model_fingerprint"),
                _digest(model["bundle_ref"], f"{label}.bundle_ref"),
                _digest(model["network_content_ref"], f"{label}.network_content_ref"),
            )
        )
        snapshot_counts.append(
            _integer(model["snapshot_event_count"], f"{label}.snapshot_event_count")
        )
    if any(left == right for left, right in zip(models[0], models[1], strict=True)):
        raise PreferenceReplayViewerBridgeError("preference snapshots must remain distinct")
    expected_snapshots = [
        counts["total"] - counts["model_b_appended_train_decisive"],
        counts["total"],
    ]
    if snapshot_counts != expected_snapshots:
        raise PreferenceReplayViewerBridgeError("preference snapshot event count drifted")
    support = _mapping(evidence["support_refusal"], "support_refusal")
    _closed(support, _SUPPORT_KEYS, "support_refusal")
    if support["captured"] is not True or support["classification"] != "below_support_refusal":
        raise PreferenceReplayViewerBridgeError("support refusal evidence drifted")
    if support["message"] != _SUPPORT_REFUSAL_MESSAGE:
        raise PreferenceReplayViewerBridgeError("preference replay support refusal message drifted")
    verification = _mapping(evidence["verification"], "verification")
    _closed(verification, _VERIFICATION_KEYS, "verification")
    if not all(value is True for key, value in verification.items() if key != "frozen_probe_count"):
        raise PreferenceReplayViewerBridgeError("replay verification gate is not exact")
    if verification["frozen_probe_count"] != 8:
        raise PreferenceReplayViewerBridgeError("verification frozen probe count drifted")
    non_claims = evidence["non_claims"]
    if not isinstance(non_claims, list) or not all(
        isinstance(claim, str) and claim for claim in non_claims
    ):
        raise PreferenceReplayViewerBridgeError("bridge non_claims are invalid")
    joined = " ".join(non_claims)
    if any(
        required not in joined
        for required in ("No human preference evidence", "No online learning", "No coherence")
    ):
        raise PreferenceReplayViewerBridgeError("bridge required non_claims drifted")


def write_viewer_preference_replay_bridge(value: Mapping[str, Any], destination: Path) -> None:
    """Atomically replace the generated bridge with canonical JSON."""

    validate_viewer_preference_replay_bridge(value)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def read_viewer_preference_replay_bridge(path: Path) -> dict[str, Any]:
    """Read a canonical generated bridge and repeat all projection checks."""

    source = Path(path)
    raw = _read_bounded_regular_file(source, "preference replay bridge")
    try:
        parsed = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PreferenceReplayViewerBridgeError(
            f"preference replay bridge is invalid JSON: {error}"
        ) from error
    bridge = dict(_mapping(parsed, "preference replay bridge"))
    validate_viewer_preference_replay_bridge(bridge)
    if raw != _canonical_bytes(bridge):
        raise PreferenceReplayViewerBridgeError("preference replay bridge is not canonical JSON")
    return bridge


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moodboard.preference_replay_viewer",
        description="Freeze a governed preference replay into the offline viewer.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--input", type=Path)
    action.add_argument("--check", type=Path)
    parser.add_argument("--write", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.check is not None:
            if arguments.write is not None:
                raise PreferenceReplayViewerBridgeError("--write cannot be combined with --check")
            bridge = read_viewer_preference_replay_bridge(arguments.check)
            path = arguments.check
        else:
            if arguments.write is None:
                raise PreferenceReplayViewerBridgeError("--input requires --write")
            bridge = compile_viewer_preference_replay_bridge(arguments.input)
            write_viewer_preference_replay_bridge(bridge, arguments.write)
            path = arguments.write
    except (OSError, PreferenceReplayViewerBridgeError, ValueError) as error:
        raise SystemExit(f"BLOCKED: {error}") from error
    print(
        json.dumps(
            {
                "bridge": str(path.resolve()),
                "replay_fingerprint": (
                    bridge["input"]["replay_fingerprint"] if bridge["input"] else None
                ),
                "state": bridge["state"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
