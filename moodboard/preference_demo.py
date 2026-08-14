"""Deterministic, governed preference-learning replay.

This module deliberately produces evidence about a *simulated feature policy*, not
human taste.  It drives Khive's real randomized serve/judge event path, immutable FANN
training snapshots, and inference path while retaining enough occurrence provenance to
audit every displayed-side label.

The replay is intentionally not wired into the general CLI.  A caller must provide a
fresh actor/board-scoped :class:`~moodboard.khive.KhiveClient` whose actor explicitly
contains ``policy-simulated``.  Reusing an actor with pre-existing judgments fails at the
initial below-support gate instead of silently contaminating the demonstration.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import numpy as np

from moodboard.khive import (
    KhiveJudgmentRequest,
    KhiveJudgmentResult,
    KhivePreferencePrediction,
    KhivePreferenceRequest,
    KhiveProtocolError,
    KhiveServeRequest,
    KhiveServeResult,
    KhiveTrainedPreferenceModel,
)
from moodboard.preference import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_ID,
    PreferenceCandidate,
    PreferenceFeatureArtifact,
    read_preference_feature_artifact,
)

__all__ = [
    "CALIBRATION_TIE_POLICY",
    "POLICY_A",
    "POLICY_B",
    "CalibrationTiePolicy",
    "FeaturePolicy",
    "PreferenceDemoError",
    "PreferenceDemoReplay",
    "pair_split",
    "replay_preference_demo",
]

_SCHEMA_VERSION: Final = "moodboard.preference-demo-replay.v1"
_SELECTION_REVISION: Final = "moodboard.preference-demo-selection.v1"
_SPLIT_REVISION: Final = "moodboard-pair-split-v1"
_TRAIN_MINIMUM: Final = 64
_CALIBRATION_MINIMUM: Final = 16
_TEST_MINIMUM: Final = 16
_CALIBRATION_TIES_MINIMUM: Final = 16
_PROBE_COUNT: Final = 8
_B_APPEND_COUNT: Final = 96
_MAX_OUTPUT_BYTES: Final = 16 * 1024 * 1024
_HEX: Final = frozenset("0123456789abcdef")
_SUPPORT_REFUSAL = re.compile(
    r"moodboard\.train_preference requires at least "
    r"(?P<minimum>\d+) distinct decisive (?P<split>train|calibration|test) "
    r"unordered-pair groups; observed (?P<observed>\d+)"
)


class PreferenceDemoError(RuntimeError):
    """The replay could not preserve one of its governance or evidence invariants."""


@dataclass(frozen=True, slots=True)
class FeaturePolicy:
    """A named, immutable linear policy over the governed feature order."""

    revision: str
    label: str
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.revision or not self.label:
            raise ValueError("feature policy revision and label must be non-empty")
        if len(self.weights) != len(FEATURE_NAMES):
            raise ValueError("feature policy must bind the governed 10D feature order")
        values = np.asarray(self.weights, dtype=np.float64)
        if not np.isfinite(values).all() or not np.any(values):
            raise ValueError("feature policy weights must be finite and non-zero")

    @property
    def policy_id(self) -> str:
        payload = _canonical_bytes(
            {
                "evidence_class": "policy_simulated",
                "feature_names": list(FEATURE_NAMES),
                "feature_schema_id": FEATURE_SCHEMA_ID,
                "label": self.label,
                "revision": self.revision,
                "weights": list(self.weights),
            }
        )
        return hashlib.sha256(b"moodboard-feature-policy-v1\0" + payload).hexdigest()

    def score(self, features: Sequence[float] | np.ndarray) -> float:
        """Score one exact governed row without fitting or hidden state."""

        values = np.asarray(features, dtype=np.float64)
        if values.shape != (len(FEATURE_NAMES),) or not np.isfinite(values).all():
            raise ValueError("feature policy input must be one finite governed 10D row")
        return float(np.dot(np.asarray(self.weights, dtype=np.float64), values))

    def to_json_dict(self) -> dict[str, object]:
        return {
            "evidence_class": "policy_simulated",
            "feature_names": list(FEATURE_NAMES),
            "feature_schema_id": FEATURE_SCHEMA_ID,
            "label": self.label,
            "policy_id": self.policy_id,
            "revision": self.revision,
            "weights": list(self.weights),
        }


@dataclass(frozen=True, slots=True)
class CalibrationTiePolicy:
    """Disclosed feature-margin rule used only for synthetic tie calibration."""

    revision: str
    label: str
    selection_rule: str
    base_policy: FeaturePolicy

    @property
    def policy_id(self) -> str:
        payload = _canonical_bytes(
            {
                "base_policy_id": self.base_policy.policy_id,
                "evidence_class": "policy_simulated",
                "feature_schema_id": FEATURE_SCHEMA_ID,
                "label": self.label,
                "revision": self.revision,
                "selection_rule": self.selection_rule,
            }
        )
        return hashlib.sha256(b"moodboard-calibration-tie-policy-v1\0" + payload).hexdigest()

    def to_json_dict(self, *, selection_threshold: float) -> dict[str, object]:
        if not math.isfinite(selection_threshold) or selection_threshold < 0.0:
            raise ValueError("tie-policy selection threshold must be finite and non-negative")
        return {
            "base_policy_id": self.base_policy.policy_id,
            "evidence_class": "policy_simulated",
            "feature_schema_id": FEATURE_SCHEMA_ID,
            "label": self.label,
            "policy_id": self.policy_id,
            "revision": self.revision,
            "selection_rule": self.selection_rule,
            "selection_threshold_absolute_policy_a_margin": selection_threshold,
        }


_POLICY_A_WEIGHTS: Final = (
    0.24,
    0.18,
    0.08,
    0.14,
    -0.04,
    0.08,
    0.05,
    0.10,
    0.06,
    0.07,
)

POLICY_A: Final = FeaturePolicy(
    revision="showcase-policy-a.v1",
    label="cohesive_style_policy",
    weights=_POLICY_A_WEIGHTS,
)
POLICY_B: Final = FeaturePolicy(
    revision="showcase-policy-b.v1",
    label="counter_style_exploration_policy",
    weights=tuple(-weight for weight in _POLICY_A_WEIGHTS),
)
CALIBRATION_TIE_POLICY: Final = CalibrationTiePolicy(
    revision="showcase-calibration-tie-policy.v1",
    label="lowest_policy_a_margin_calibration_ties",
    selection_rule="lowest_absolute_policy_a_margin_among_unused_calibration_pairs",
    base_policy=POLICY_A,
)


class _PreferenceClient(Protocol):
    actor: str
    namespace: str

    def batch_serve(self, **arguments: Any) -> tuple[KhiveServeResult, ...]: ...

    def batch_judge(self, **arguments: Any) -> tuple[KhiveJudgmentResult, ...]: ...

    def train_preference(self, **arguments: Any) -> KhiveTrainedPreferenceModel: ...

    def batch_preference(self, **arguments: Any) -> tuple[KhivePreferencePrediction, ...]: ...


@dataclass(frozen=True, slots=True)
class PreferenceDemoReplay:
    """Canonical replay document plus the digest of its pre-fingerprint core."""

    document: Mapping[str, Any]
    canonical_json: bytes
    replay_fingerprint: str


@dataclass(frozen=True, slots=True)
class _Pair:
    lower: PreferenceCandidate
    upper: PreferenceCandidate
    split: Literal["train", "calibration", "test"]
    pair_id: str


@dataclass(frozen=True, slots=True)
class _Selection:
    a_train: tuple[_Pair, ...]
    a_calibration: tuple[_Pair, ...]
    a_test: tuple[_Pair, ...]
    a_calibration_ties: tuple[_Pair, ...]
    probes: tuple[_Pair, ...]
    b_train: tuple[_Pair, ...]
    extras: tuple[_Pair, ...]
    calibration_tie_threshold: float


@dataclass(frozen=True, slots=True)
class _EventSpec:
    pair: _Pair
    phase: Literal["model_a_decisive", "model_a_tie", "model_b_append"]
    policy: FeaturePolicy | CalibrationTiePolicy
    tie: bool


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _require_hex_64(value: str, *, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")


def pair_split(
    *,
    board_id: str,
    descriptor_fingerprint: str,
    lower_content_ref: str,
    upper_content_ref: str,
) -> Literal["train", "calibration", "test"]:
    """Return Khive ADR-149's exact unordered-pair SHA split."""

    for label, value in (
        ("board_id", board_id),
        ("descriptor_fingerprint", descriptor_fingerprint),
        ("lower_content_ref", lower_content_ref),
        ("upper_content_ref", upper_content_ref),
    ):
        _require_hex_64(value, label=label)
    if lower_content_ref >= upper_content_ref:
        raise ValueError("pair split content refs must be supplied in strict ascending order")
    digest = hashlib.sha256(
        b"moodboard-pair-split-v1\0"
        + board_id.encode("ascii")
        + b"\0"
        + descriptor_fingerprint.encode("ascii")
        + b"\0"
        + FEATURE_SCHEMA_ID.encode("ascii")
        + b"\0"
        + lower_content_ref.encode("ascii")
        + b"\0"
        + upper_content_ref.encode("ascii")
        + b"\0"
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") % 20
    if bucket <= 13:
        return "train"
    if bucket <= 16:
        return "calibration"
    return "test"


def _pair_id(lower_content_ref: str, upper_content_ref: str) -> str:
    return hashlib.sha256(
        b"moodboard-preference-demo-pair-v1\0"
        + bytes.fromhex(lower_content_ref)
        + bytes.fromhex(upper_content_ref)
    ).hexdigest()


def _policy_preferred(pair: _Pair, policy: FeaturePolicy) -> PreferenceCandidate | None:
    lower_score = policy.score(pair.lower.features.values)
    upper_score = policy.score(pair.upper.features.values)
    if lower_score == upper_score:
        return None
    return pair.lower if lower_score > upper_score else pair.upper


def _absolute_policy_margin(pair: _Pair, policy: FeaturePolicy) -> float:
    return abs(policy.score(pair.lower.features.values) - policy.score(pair.upper.features.values))


def _all_pairs(artifact: PreferenceFeatureArtifact) -> tuple[_Pair, ...]:
    ordered_candidates = sorted(artifact.candidates, key=lambda candidate: candidate.content_ref)
    pairs: list[_Pair] = []
    for lower, upper in itertools.combinations(ordered_candidates, 2):
        pair_id = _pair_id(lower.content_ref, upper.content_ref)
        pairs.append(
            _Pair(
                lower=lower,
                upper=upper,
                split=pair_split(
                    board_id=artifact.board_id,
                    descriptor_fingerprint=artifact.descriptor_fingerprint,
                    lower_content_ref=lower.content_ref,
                    upper_content_ref=upper.content_ref,
                ),
                pair_id=pair_id,
            )
        )
    return tuple(
        sorted(
            pairs,
            key=lambda pair: hashlib.sha256(
                b"moodboard-preference-demo-selection-v1\0"
                + bytes.fromhex(artifact.scope_sha256)
                + bytes.fromhex(pair.pair_id)
            ).digest(),
        )
    )


def _select_pairs(artifact: PreferenceFeatureArtifact) -> _Selection:
    all_pairs = _all_pairs(artifact)
    used: set[str] = set()

    def take(
        count: int,
        *,
        label: str,
        split: Literal["train", "calibration", "test"] | None = None,
        policy: FeaturePolicy | None = None,
        require_conflict: bool = False,
        margin_policy: FeaturePolicy | None = None,
    ) -> tuple[_Pair, ...]:
        chosen: list[_Pair] = []
        ordered = all_pairs
        if margin_policy is not None:
            ordered = tuple(
                sorted(
                    all_pairs,
                    key=lambda pair: (
                        -abs(
                            margin_policy.score(pair.lower.features.values)
                            - margin_policy.score(pair.upper.features.values)
                        ),
                        pair.pair_id,
                    ),
                )
            )
        for pair in ordered:
            if pair.pair_id in used or (split is not None and pair.split != split):
                continue
            a_preferred = _policy_preferred(pair, POLICY_A)
            if policy is not None and _policy_preferred(pair, policy) is None:
                continue
            if require_conflict:
                b_preferred = _policy_preferred(pair, POLICY_B)
                if a_preferred is None or b_preferred is None or a_preferred == b_preferred:
                    continue
            chosen.append(pair)
            used.add(pair.pair_id)
            if len(chosen) == count:
                return tuple(chosen)
        raise PreferenceDemoError(
            f"governed candidate pool cannot allocate {count} distinct pairs for {label}; "
            f"found {len(chosen)}"
        )

    a_train = take(_TRAIN_MINIMUM, label="model A train", split="train", policy=POLICY_A)
    available_calibration = [
        pair for pair in all_pairs if pair.pair_id not in used and pair.split == "calibration"
    ]
    available_calibration.sort(
        key=lambda pair: (_absolute_policy_margin(pair, POLICY_A), pair.pair_id)
    )
    if len(available_calibration) < _CALIBRATION_TIES_MINIMUM:
        raise PreferenceDemoError(
            "governed candidate pool cannot allocate 16 distinct lowest-margin calibration "
            f"ties; found {len(available_calibration)}"
        )
    a_calibration_ties = tuple(available_calibration[:_CALIBRATION_TIES_MINIMUM])
    used.update(pair.pair_id for pair in a_calibration_ties)
    calibration_tie_threshold = max(
        _absolute_policy_margin(pair, POLICY_A) for pair in a_calibration_ties
    )
    a_calibration = take(
        _CALIBRATION_MINIMUM,
        label="model A calibration",
        split="calibration",
        policy=POLICY_A,
    )
    a_test = take(_TEST_MINIMUM, label="model A test", split="test", policy=POLICY_A)
    probes = take(_PROBE_COUNT, label="frozen A/B conflict probes", require_conflict=True)
    b_train = take(
        _B_APPEND_COUNT,
        label="model B appended train",
        split="train",
        policy=POLICY_B,
        require_conflict=True,
        margin_policy=POLICY_B,
    )
    extras = tuple(pair for pair in all_pairs if pair.pair_id not in used)
    return _Selection(
        a_train=a_train,
        a_calibration=a_calibration,
        a_test=a_test,
        a_calibration_ties=a_calibration_ties,
        probes=probes,
        b_train=b_train,
        extras=extras,
        calibration_tie_threshold=calibration_tie_threshold,
    )


def _candidate_wire(candidate: PreferenceCandidate) -> dict[str, object]:
    return {
        "state": "scored",
        "asset_id": candidate.asset_id,
        "content_ref": candidate.content_ref,
        "source_rank": candidate.source_rank,
        "features": candidate.features.as_wire(),
    }


def _candidate_identity(candidate: PreferenceCandidate) -> dict[str, object]:
    return {
        "asset_id": candidate.asset_id,
        "content_ref": candidate.content_ref,
        "source_rank": candidate.source_rank,
    }


def _occurrence_identity(result: KhiveServeResult, side: Literal["left", "right"]):
    occurrence = result.left if side == "left" else result.right
    return (occurrence.asset_id, occurrence.content_ref, occurrence.source_rank)


def _validate_serve_result(
    result: KhiveServeResult,
    *,
    pair: _Pair,
    artifact: PreferenceFeatureArtifact,
) -> None:
    if result.feature_schema_id != artifact.feature_schema_id:
        raise PreferenceDemoError("Khive serve result changed the governed feature schema")
    submitted = (pair.lower, pair.upper)
    expected = (submitted[1], submitted[0]) if result.swap_applied else submitted
    measured = (_occurrence_identity(result, "left"), _occurrence_identity(result, "right"))
    expected_identities = tuple(
        (candidate.asset_id, candidate.content_ref, candidate.source_rank) for candidate in expected
    )
    if measured != expected_identities:
        raise PreferenceDemoError(
            "Khive serve occurrence identities do not match the submitted pair and swap flag"
        )
    if result.left.result_occurrence_id == result.right.result_occurrence_id:
        raise PreferenceDemoError("Khive serve occurrence identities are not distinct")


def _train_arguments(artifact: PreferenceFeatureArtifact) -> dict[str, str]:
    return {
        "board_entity_id": artifact.board_entity_id,
        "board_id": artifact.board_id,
        "model_key": artifact.model_key,
        "descriptor_fingerprint": artifact.descriptor_fingerprint,
    }


def _capture_initial_support_refusal(
    client: _PreferenceClient, artifact: PreferenceFeatureArtifact
) -> dict[str, object]:
    try:
        client.train_preference(**_train_arguments(artifact))
    except KhiveProtocolError as error:
        match = _SUPPORT_REFUSAL.search(str(error))
        if (
            match is None
            or match.group("split") != "train"
            or int(match.group("minimum")) != _TRAIN_MINIMUM
            or int(match.group("observed")) != 0
        ):
            raise PreferenceDemoError(
                "fresh replay did not return the exact below-support train refusal"
            ) from error
        return {
            "captured": True,
            "classification": "below_support_refusal",
            "message": match.group(0),
        }
    raise PreferenceDemoError(
        "initial training unexpectedly succeeded; use a fresh actor/board-scoped Khive state"
    )


def _record_events(
    *,
    client: _PreferenceClient,
    artifact: PreferenceFeatureArtifact,
    specs: Sequence[_EventSpec],
    event_index_start: int,
    calibration_tie_threshold: float,
) -> tuple[dict[str, object], ...]:
    if not specs:
        return ()
    serve_requests = tuple(
        KhiveServeRequest(
            candidates=(_candidate_wire(spec.pair.lower), _candidate_wire(spec.pair.upper)),
            candidate_pool_sha256=artifact.candidate_pool_sha256,
            policy_revision=(f"policy-simulated/{spec.policy.revision}/{spec.phase}"),
        )
        for spec in specs
    )
    serves = client.batch_serve(
        **_train_arguments(artifact),
        source_report_sha256=artifact.source_report_sha256,
        requests=serve_requests,
    )
    if len(serves) != len(specs):
        raise PreferenceDemoError("Khive batch serve result count drifted from submitted order")
    for serve, spec in zip(serves, specs, strict=True):
        _validate_serve_result(serve, pair=spec.pair, artifact=artifact)

    labels: list[tuple[PreferenceCandidate | None, Literal["left", "right", "tie"], str]] = []
    judgment_requests: list[KhiveJudgmentRequest] = []
    for serve, spec in zip(serves, specs, strict=True):
        if spec.tie:
            if not isinstance(spec.policy, CalibrationTiePolicy):
                raise PreferenceDemoError("calibration tie event lacks its disclosed tie policy")
            margin = _absolute_policy_margin(spec.pair, spec.policy.base_policy)
            if margin > calibration_tie_threshold:
                raise PreferenceDemoError(
                    "calibration tie exceeds its disclosed selection threshold"
                )
            preferred = None
            choice: Literal["left", "right", "tie"] = "tie"
            reason_code = "other"
        else:
            if not isinstance(spec.policy, FeaturePolicy):
                raise PreferenceDemoError("decisive event lacks a disclosed feature policy")
            preferred = _policy_preferred(spec.pair, spec.policy)
            if preferred is None:
                raise PreferenceDemoError(
                    "a decisive simulated-policy event has equal feature scores"
                )
            choice = "left" if serve.left.asset_id == preferred.asset_id else "right"
            reason_code = "other"
        labels.append((preferred, choice, reason_code))
        judgment_requests.append(
            KhiveJudgmentRequest(
                serve_id=serve.serve_id,
                left_result_occurrence_id=serve.left.result_occurrence_id,
                right_result_occurrence_id=serve.right.result_occurrence_id,
                choice=choice,
                reason_code=reason_code,
            )
        )
    judgments = client.batch_judge(requests=tuple(judgment_requests))
    if len(judgments) != len(specs):
        raise PreferenceDemoError("Khive batch judgment result count drifted from submitted order")

    documents: list[dict[str, object]] = []
    for offset, (spec, serve, judgment, label) in enumerate(
        zip(specs, serves, judgments, labels, strict=True)
    ):
        preferred, choice, reason_code = label
        if not judgment.created:
            raise PreferenceDemoError("Khive returned an existing judgment during a fresh replay")
        if judgment.serve_id != serve.serve_id or judgment.choice != choice:
            raise PreferenceDemoError("Khive judgment result drifted from the submitted judgment")

        pair = spec.pair
        displayed_by_ref = {pair.lower.content_ref: pair.lower, pair.upper.content_ref: pair.upper}
        displayed_left = displayed_by_ref[serve.left.content_ref]
        displayed_right = displayed_by_ref[serve.right.content_ref]
        document: dict[str, object] = {
            "choice": choice,
            "displayed_candidates": {
                "left": _candidate_identity(displayed_left),
                "right": _candidate_identity(displayed_right),
            },
            "event_index": event_index_start + offset,
            "judgment_id": judgment.judgment_id,
            "pair_id": pair.pair_id,
            "phase": spec.phase,
            "policy_id": spec.policy.policy_id,
            "policy_revision": spec.policy.revision,
            "randomized_occurrence_provenance": {
                "left_result_occurrence_id": serve.left.result_occurrence_id,
                "right_result_occurrence_id": serve.right.result_occurrence_id,
                "swap_applied": serve.swap_applied,
            },
            "reason_code": reason_code,
            "semantic_preferred_asset_id": None if preferred is None else preferred.asset_id,
            "serve_id": serve.serve_id,
            "split": pair.split,
            "submitted_candidates": [
                _candidate_identity(pair.lower),
                _candidate_identity(pair.upper),
            ],
        }
        if spec.tie:
            document["tie_policy"] = {
                "absolute_policy_a_margin": _absolute_policy_margin(pair, POLICY_A),
                "selection_rule": CALIBRATION_TIE_POLICY.selection_rule,
                "selection_threshold_absolute_policy_a_margin": calibration_tie_threshold,
            }
        documents.append(document)
    return tuple(documents)


def _assert_side_label_support(
    *,
    client: _PreferenceClient,
    artifact: PreferenceFeatureArtifact,
    events: list[dict[str, object]],
    extras: Sequence[_Pair],
    used: set[str],
    calibration_tie_threshold: float,
) -> None:
    """Use reserved pairs only if real randomized display lacks one label in a split."""

    for split in ("train", "calibration", "test"):
        labels = {
            event["choice"]
            for event in events
            if event["phase"] == "model_a_decisive" and event["split"] == split
        }
        if labels == {"left", "right"}:
            continue
        for pair in extras:
            if pair.pair_id in used or pair.split != split:
                continue
            if _policy_preferred(pair, POLICY_A) is None:
                continue
            used.add(pair.pair_id)
            events.extend(
                _record_events(
                    client=client,
                    artifact=artifact,
                    specs=(
                        _EventSpec(
                            pair=pair,
                            phase="model_a_decisive",
                            policy=POLICY_A,
                            tie=False,
                        ),
                    ),
                    event_index_start=len(events) + 1,
                    calibration_tie_threshold=calibration_tie_threshold,
                )
            )
            labels.add(events[-1]["choice"])
            if labels == {"left", "right"}:
                break
        if labels != {"left", "right"}:
            raise PreferenceDemoError(
                f"randomized display did not produce both side labels for the {split} split"
            )


def _event_counts(events: Sequence[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    a = {"train_decisive": 0, "calibration_decisive": 0, "test_decisive": 0}
    calibration_ties = 0
    b_train = 0
    for event in events:
        phase = event["phase"]
        split = event["split"]
        if phase == "model_a_decisive":
            a[f"{split}_decisive"] += 1
        elif phase == "model_a_tie":
            calibration_ties += 1
        elif phase == "model_b_append":
            b_train += 1
    a["calibration_ties"] = calibration_ties
    return {"model_a": a, "model_b_append": {"train_decisive": b_train}}


def _validate_model(
    model: KhiveTrainedPreferenceModel,
    *,
    phase_counts: Mapping[str, int],
    cumulative_train_decisive: int,
    expected_snapshot_event_count: int,
) -> None:
    if not model.fann_inference_verified:
        raise PreferenceDemoError("Khive did not verify FANN inference for the trained model")
    training = model.training
    if training.get("split_revision") != _SPLIT_REVISION:
        raise PreferenceDemoError("Khive trained model changed the unordered-pair split revision")
    snapshot_sha256 = training.get("snapshot_sha256")
    if not isinstance(snapshot_sha256, str):
        raise PreferenceDemoError("Khive trained model omitted its snapshot digest")
    try:
        _require_hex_64(snapshot_sha256, label="training.snapshot_sha256")
    except ValueError as error:
        raise PreferenceDemoError(
            "Khive trained model returned an invalid snapshot digest"
        ) from error
    excluded_probability_shown = training.get("excluded_probability_shown")
    if isinstance(excluded_probability_shown, bool) or excluded_probability_shown != 0:
        raise PreferenceDemoError("Khive trained model contains probability-shown contamination")
    raw_counts = training.get("split_counts")
    if not isinstance(raw_counts, Mapping):
        raise PreferenceDemoError("Khive trained model omitted split-count provenance")
    if set(raw_counts) != {"train", "calibration", "test"}:
        raise PreferenceDemoError("Khive trained model changed the closed split-count set")
    expected_decisive = {
        "train": cumulative_train_decisive,
        "calibration": phase_counts["calibration_decisive"],
        "test": phase_counts["test_decisive"],
    }
    count_fields = {
        "decisive_groups",
        "decisive_judgments",
        "left_labels",
        "right_labels",
        "tie_groups",
        "tie_judgments",
        "abstain_groups",
        "abstain_judgments",
    }
    for split, expected in expected_decisive.items():
        split_counts = raw_counts.get(split)
        if not isinstance(split_counts, Mapping):
            raise PreferenceDemoError(f"Khive trained model omitted {split} split counts")
        if set(split_counts) != count_fields:
            raise PreferenceDemoError(
                f"Khive trained model {split} split changed its closed count fields"
            )
        for field in count_fields:
            value = split_counts.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PreferenceDemoError(
                    f"Khive trained model {split} {field} is not a non-negative integer"
                )
        if split_counts["decisive_groups"] != expected:
            raise PreferenceDemoError(f"Khive trained model {split} decisive support drifted")
        if split_counts["decisive_judgments"] != expected:
            raise PreferenceDemoError(
                f"Khive trained model {split} decisive judgment support drifted"
            )
        if not split_counts["left_labels"] or not split_counts["right_labels"]:
            raise PreferenceDemoError(f"Khive trained model {split} lacks both side labels")
        if split_counts["left_labels"] + split_counts["right_labels"] != expected:
            raise PreferenceDemoError(
                f"Khive trained model {split} randomized-side label support drifted"
            )
        expected_ties = phase_counts["calibration_ties"] if split == "calibration" else 0
        if (
            split_counts["tie_groups"] != expected_ties
            or split_counts["tie_judgments"] != expected_ties
        ):
            raise PreferenceDemoError(f"Khive trained model {split} tie support drifted")
        if split_counts["abstain_groups"] != 0 or split_counts["abstain_judgments"] != 0:
            raise PreferenceDemoError(f"Khive trained model {split} abstain support drifted")
    snapshot_event_count = training.get("snapshot_event_count")
    if (
        isinstance(snapshot_event_count, bool)
        or snapshot_event_count != expected_snapshot_event_count
    ):
        raise PreferenceDemoError("Khive trained model snapshot event count drifted")


def _model_document(model: KhiveTrainedPreferenceModel) -> dict[str, object]:
    return {
        "calibration": dict(model.calibration),
        "content_ref": model.content_ref,
        "fann_inference_verified": model.fann_inference_verified,
        "model_fingerprint": model.model_fingerprint,
        "network_content_ref": model.network_content_ref,
        "network_sha256": model.network_sha256,
        "preference_model_id": model.preference_model_id,
        "test_metrics": dict(model.test_metrics),
        "training": dict(model.training),
    }


def _validate_prediction(
    prediction: KhivePreferencePrediction, model: KhiveTrainedPreferenceModel
) -> None:
    if prediction.preference_model_id != model.preference_model_id:
        raise PreferenceDemoError("Khive preference result changed the immutable model ID")
    if prediction.model_fingerprint != model.model_fingerprint:
        raise PreferenceDemoError("Khive preference result changed the model fingerprint")
    probabilities = (
        prediction.probability_left_given_decisive,
        prediction.probability_right_given_decisive,
    )
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in probabilities):
        raise PreferenceDemoError("Khive preference result returned invalid probabilities")
    if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise PreferenceDemoError("Khive preference result probabilities do not sum to one")
    if prediction.conformal_state != "not_computed_by_this_verb":
        raise PreferenceDemoError("Khive preference result crossed the conformal boundary")


def _prediction_document(prediction: KhivePreferencePrediction) -> dict[str, object]:
    return asdict(prediction)


def _probability_for(
    prediction: KhivePreferencePrediction,
    *,
    preferred: PreferenceCandidate,
    pair: _Pair,
) -> float:
    if preferred == pair.lower:
        return prediction.probability_left_given_decisive
    if preferred == pair.upper:
        return prediction.probability_right_given_decisive
    raise PreferenceDemoError("probe preference does not belong to the frozen pair")


def _predict_probes(
    *,
    client: _PreferenceClient,
    artifact: PreferenceFeatureArtifact,
    model: KhiveTrainedPreferenceModel,
    probes: Sequence[_Pair],
) -> tuple[KhivePreferencePrediction, ...]:
    common = _train_arguments(artifact)
    predictions = client.batch_preference(
        **common,
        preference_model_id=model.preference_model_id,
        source_report_sha256=artifact.source_report_sha256,
        requests=tuple(
            KhivePreferenceRequest(
                left=_candidate_wire(pair.lower),
                right=_candidate_wire(pair.upper),
            )
            for pair in probes
        ),
    )
    if len(predictions) != len(probes):
        raise PreferenceDemoError("Khive batch preference result count drifted from probe order")
    for prediction in predictions:
        _validate_prediction(prediction, model)
    return predictions


def _assert_distinct_models(
    model_a: KhiveTrainedPreferenceModel, model_b: KhiveTrainedPreferenceModel
) -> None:
    fields = (
        "preference_model_id",
        "model_fingerprint",
        "content_ref",
        "network_sha256",
        "network_content_ref",
    )
    unchanged = [field for field in fields if getattr(model_a, field) == getattr(model_b, field)]
    if unchanged:
        raise PreferenceDemoError(
            "model B did not publish a distinct immutable snapshot for: " + ", ".join(unchanged)
        )


def _atomic_write(path: Path, payload: bytes) -> None:
    if len(payload) > _MAX_OUTPUT_BYTES:
        raise PreferenceDemoError("preference demo replay exceeds the 16 MiB output ceiling")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise PreferenceDemoError(
                "preference replay output appeared during publication; refusing to replace it"
            ) from error
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def replay_preference_demo(
    *,
    client: _PreferenceClient,
    artifact_path: Path,
    restart_client_factory: Callable[[], _PreferenceClient],
    output_path: Path | None = None,
) -> PreferenceDemoReplay:
    """Run one fresh deterministic policy-simulated preference replay.

    Pair choice and semantic labels are deterministic.  Khive still randomizes displayed
    side and binds every judgment to occurrence IDs; that realized randomization is retained
    in the output rather than normalized away.
    """

    if output_path is not None:
        if output_path.resolve(strict=False) == artifact_path.resolve(strict=False):
            raise PreferenceDemoError(
                "preference replay output must differ from its governed input"
            )
        if os.path.lexists(output_path):
            raise PreferenceDemoError("preference replay output must not already exist")
    artifact = read_preference_feature_artifact(artifact_path)
    selection = _select_pairs(artifact)  # Capacity fails before any Khive mutation.
    actor = getattr(client, "actor", None)
    namespace = getattr(client, "namespace", None)
    if not isinstance(actor, str) or "policy-simulated" not in actor:
        raise PreferenceDemoError(
            "policy-simulated replay requires an actor containing 'policy-simulated'"
        )
    if not isinstance(namespace, str) or not namespace:
        raise PreferenceDemoError("policy-simulated replay requires a named Khive namespace")

    support_refusal = _capture_initial_support_refusal(client, artifact)
    events: list[dict[str, object]] = []
    used: set[str] = set()

    def record_specs(specs: Sequence[_EventSpec]) -> None:
        for spec in specs:
            if spec.pair.pair_id in used:
                raise PreferenceDemoError("selection attempted to reuse an unordered pair")
            used.add(spec.pair.pair_id)
        events.extend(
            _record_events(
                client=client,
                artifact=artifact,
                specs=specs,
                event_index_start=len(events) + 1,
                calibration_tie_threshold=selection.calibration_tie_threshold,
            )
        )

    initial_specs = tuple(
        _EventSpec(pair, "model_a_decisive", POLICY_A, False)
        for pair in (
            *selection.a_train,
            *selection.a_calibration,
            *selection.a_test,
        )
    ) + tuple(
        _EventSpec(pair, "model_a_tie", CALIBRATION_TIE_POLICY, True)
        for pair in selection.a_calibration_ties
    )
    record_specs(initial_specs)
    _assert_side_label_support(
        client=client,
        artifact=artifact,
        events=events,
        extras=selection.extras,
        used=used,
        calibration_tie_threshold=selection.calibration_tie_threshold,
    )

    phase_counts = _event_counts(events)
    model_a_counts = phase_counts["model_a"]
    model_a = client.train_preference(**_train_arguments(artifact))
    _validate_model(
        model_a,
        phase_counts=model_a_counts,
        cumulative_train_decisive=model_a_counts["train_decisive"],
        expected_snapshot_event_count=len(events),
    )
    before = _predict_probes(
        client=client,
        artifact=artifact,
        model=model_a,
        probes=selection.probes,
    )

    record_specs(
        tuple(_EventSpec(pair, "model_b_append", POLICY_B, False) for pair in selection.b_train)
    )
    phase_counts = _event_counts(events)
    model_b = client.train_preference(**_train_arguments(artifact))
    _validate_model(
        model_b,
        phase_counts=phase_counts["model_a"],
        cumulative_train_decisive=(
            phase_counts["model_a"]["train_decisive"]
            + phase_counts["model_b_append"]["train_decisive"]
        ),
        expected_snapshot_event_count=len(events),
    )
    _assert_distinct_models(model_a, model_b)
    after = _predict_probes(
        client=client,
        artifact=artifact,
        model=model_b,
        probes=selection.probes,
    )
    model_a_after_b = _predict_probes(
        client=client,
        artifact=artifact,
        model=model_a,
        probes=selection.probes,
    )
    if model_a_after_b != before:
        raise PreferenceDemoError("model A predictions changed after immutable model B training")

    restarted = restart_client_factory()
    if restarted.actor != actor or restarted.namespace != namespace:
        raise PreferenceDemoError("restart client changed the actor or Khive namespace")
    restarted_after = _predict_probes(
        client=restarted,
        artifact=artifact,
        model=model_b,
        probes=selection.probes,
    )
    if restarted_after != after:
        raise PreferenceDemoError("restart prediction drift for immutable model B")

    probe_documents: list[dict[str, object]] = []
    probabilities_before: list[float] = []
    probabilities_after: list[float] = []
    for pair, before_prediction, after_prediction in zip(
        selection.probes, before, after, strict=True
    ):
        a_preferred = _policy_preferred(pair, POLICY_A)
        b_preferred = _policy_preferred(pair, POLICY_B)
        if a_preferred is None or b_preferred is None or a_preferred == b_preferred:
            raise PreferenceDemoError("frozen probe is not an A/B policy conflict")
        before_probability = _probability_for(
            before_prediction,
            preferred=b_preferred,
            pair=pair,
        )
        after_probability = _probability_for(
            after_prediction,
            preferred=b_preferred,
            pair=pair,
        )
        probabilities_before.append(before_probability)
        probabilities_after.append(after_probability)
        probe_documents.append(
            {
                "left": _candidate_identity(pair.lower),
                "model_a_prediction": _prediction_document(before_prediction),
                "model_b_prediction": _prediction_document(after_prediction),
                "pair_id": pair.pair_id,
                "policy_a_preferred_asset_id": a_preferred.asset_id,
                "policy_b_preferred_asset_id": b_preferred.asset_id,
                "probability_for_policy_b_preferred_after": after_probability,
                "probability_for_policy_b_preferred_before": before_probability,
                "right": _candidate_identity(pair.upper),
                "split": pair.split,
            }
        )

    mean_before = math.fsum(probabilities_before) / len(probabilities_before)
    mean_after = math.fsum(probabilities_after) / len(probabilities_after)
    mean_delta = mean_after - mean_before
    core: dict[str, object] = {
        "actor": actor,
        "artifact": {
            "board_entity_id": artifact.board_entity_id,
            "board_id": artifact.board_id,
            "candidate_pool_sha256": artifact.candidate_pool_sha256,
            "descriptor_fingerprint": artifact.descriptor_fingerprint,
            "feature_producer_id": artifact.producer_id,
            "feature_producer_revision": artifact.producer_revision,
            "feature_schema_id": artifact.feature_schema_id,
            "model_key": artifact.model_key,
            "schema_version": artifact.schema_version,
            "scope_sha256": artifact.scope_sha256,
            "source_report_sha256": artifact.source_report_sha256,
        },
        "delta": {
            "adaptation_direction_observed": mean_delta > 0.0,
            "measurement": "frozen_policy_conflict_probes",
            "mean_delta": mean_delta,
            "mean_probability_for_policy_b_preferred_after": mean_after,
            "mean_probability_for_policy_b_preferred_before": mean_before,
        },
        "events": events,
        "evidence_class": "policy_simulated",
        "frozen_conflict_probes": probe_documents,
        "immutability": {
            "model_a_predictions_unchanged_after_model_b": True,
            "model_snapshots_distinct": True,
        },
        "model_a": _model_document(model_a),
        "model_b": _model_document(model_b),
        "namespace": namespace,
        "non_claims": [
            "No human preference evidence: decisive and tie labels come from separately "
            "disclosed feature-only policies.",
            "No online learning: Khive retrains and publishes immutable model snapshots.",
            "No coherence or conformal claim: preference inference remains a separate signal.",
            "No causal personalization or user-study claim.",
            "No generalization claim beyond this governed demo artifact and frozen probe set.",
            "A-to-B delta measures only policy-B probability on frozen A/B conflict probes.",
            "Unit-test doubles are orchestration checks, not adaptation evidence; demo "
            "readiness requires a real Khive replay with the measured direction gate satisfied.",
        ],
        "phase_counts": phase_counts,
        "policies": {
            "calibration_ties": CALIBRATION_TIE_POLICY.to_json_dict(
                selection_threshold=selection.calibration_tie_threshold
            ),
            "model_a": POLICY_A.to_json_dict(),
            "model_b": POLICY_B.to_json_dict(),
        },
        "restart_verification": {
            "exact_prediction_equality": True,
            "model_fingerprint_equal": True,
            "preference_model_id_equal": True,
            "probe_count": len(selection.probes),
        },
        "schema_version": _SCHEMA_VERSION,
        "selection_revision": _SELECTION_REVISION,
        "split_revision": _SPLIT_REVISION,
        "support_refusal": support_refusal,
    }
    replay_fingerprint = hashlib.sha256(
        b"moodboard-preference-demo-replay-v1\0" + _canonical_bytes(core)
    ).hexdigest()
    document = {**core, "replay_fingerprint": replay_fingerprint}
    canonical_json = _canonical_bytes(document) + b"\n"
    if output_path is not None:
        _atomic_write(output_path, canonical_json)
    return PreferenceDemoReplay(
        document=document,
        canonical_json=canonical_json,
        replay_fingerprint=replay_fingerprint,
    )
