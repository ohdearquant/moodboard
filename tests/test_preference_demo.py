from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from moodboard.khive import (
    KhiveJudgmentResult,
    KhivePreferencePrediction,
    KhiveProtocolError,
    KhiveServeOccurrence,
    KhiveServeResult,
    KhiveTrainedPreferenceModel,
)
from moodboard.preference import (
    PreferenceCandidate,
    PreferenceFeatureArtifact,
    write_preference_feature_artifact,
)
from moodboard.preference_demo import (
    POLICY_A,
    POLICY_B,
    PreferenceDemoError,
    pair_split,
    replay_preference_demo,
)


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"moodboard-preference-demo-test:{label}"))


def _feature_row(index: int) -> np.ndarray:
    return np.array(
        [((index * (axis + 3) + axis * axis + 1) % 29) / 28.0 for axis in range(10)],
        dtype=np.float32,
    )


def _artifact() -> PreferenceFeatureArtifact:
    descriptor = hashlib.sha256(b"descriptor").hexdigest()
    return PreferenceFeatureArtifact.build(
        board_entity_id=_stable_uuid("board"),
        board_id=hashlib.sha256(b"board-id").hexdigest(),
        model_key=f"moodboard_{descriptor}_1024",
        descriptor_fingerprint=descriptor,
        source_report_sha256=hashlib.sha256(b"report").hexdigest(),
        candidates=tuple(
            PreferenceCandidate(
                label=f"candidate-{index:02d}.png",
                asset_id=_stable_uuid(f"asset-{index}"),
                content_ref=hashlib.sha256(f"content-{index}".encode()).hexdigest(),
                source_rank=index,
                features=_feature_row(index),
            )
            for index in range(1, 25)
        ),
    )


def _write_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "preference-features.json"
    write_preference_feature_artifact(_artifact(), path)
    return path


@dataclass
class _FakeState:
    serves: dict[str, dict[str, Any]] = field(default_factory=dict)
    served_pairs: set[tuple[str, str]] = field(default_factory=set)
    judgments: list[dict[str, Any]] = field(default_factory=list)
    models: dict[str, int] = field(default_factory=dict)
    model_fingerprints: dict[str, str] = field(default_factory=dict)
    train_attempts: int = 0
    corrupt_serve: bool = False
    corrupt_restart: bool = False


class _StatefulPreferenceClient:
    actor = "lambda:adobe-demo-policy-simulated"
    namespace = "adobe-demo-preference-replay-test"

    def __init__(self, state: _FakeState | None = None, *, restarted: bool = False) -> None:
        self.state = state or _FakeState()
        self.restarted = restarted

    def serve(self, **arguments: Any) -> KhiveServeResult:
        candidates = arguments["candidates"]
        assert len(candidates) == 2
        pair = tuple(sorted((candidates[0]["content_ref"], candidates[1]["content_ref"])))
        if pair in self.state.served_pairs:
            raise AssertionError(f"unordered pair was reused: {pair}")
        self.state.served_pairs.add(pair)
        serve_index = len(self.state.serves) + 1
        swap = serve_index % 2 == 0
        displayed = (candidates[1], candidates[0]) if swap else (candidates[0], candidates[1])
        serve_id = _stable_uuid(f"serve-{serve_index}-{pair[0]}-{pair[1]}")
        left_id = _stable_uuid(f"occurrence-left-{serve_id}")
        right_id = _stable_uuid(f"occurrence-right-{serve_id}")
        left = dict(displayed[0])
        right = dict(displayed[1])
        if self.state.corrupt_serve and serve_index == 1:
            left["asset_id"] = _stable_uuid("corrupt-asset")
        self.state.serves[serve_id] = {
            "arguments": arguments,
            "left": left,
            "right": right,
            "left_occurrence_id": left_id,
            "right_occurrence_id": right_id,
            "swap": swap,
        }
        return KhiveServeResult(
            serve_id=serve_id,
            feature_schema_id=arguments["candidates"][0].get(
                "feature_schema_id",
                "f691fc73bf9a50d72157e21601fa579caa707bf2c448df546c63e915b4e42175",
            ),
            left=KhiveServeOccurrence(
                result_occurrence_id=left_id,
                asset_id=left["asset_id"],
                content_ref=left["content_ref"],
                source_rank=left["source_rank"],
            ),
            right=KhiveServeOccurrence(
                result_occurrence_id=right_id,
                asset_id=right["asset_id"],
                content_ref=right["content_ref"],
                source_rank=right["source_rank"],
            ),
            swap_applied=swap,
        )

    def judge(self, **arguments: Any) -> KhiveJudgmentResult:
        served = self.state.serves[arguments["serve_id"]]
        assert arguments["left_result_occurrence_id"] == served["left_occurrence_id"]
        assert arguments["right_result_occurrence_id"] == served["right_occurrence_id"]
        event = {**served, "choice": arguments["choice"]}
        self.state.judgments.append(event)
        return KhiveJudgmentResult(
            judgment_id=_stable_uuid(f"judgment-{len(self.state.judgments)}"),
            serve_id=arguments["serve_id"],
            choice=arguments["choice"],
            reason_code=arguments.get("reason_code"),
            created=True,
        )

    def _split_counts(self) -> dict[str, dict[str, int]]:
        counts = {
            split: {
                "decisive_groups": 0,
                "decisive_judgments": 0,
                "left_labels": 0,
                "right_labels": 0,
                "tie_groups": 0,
                "tie_judgments": 0,
                "abstain_groups": 0,
                "abstain_judgments": 0,
            }
            for split in ("train", "calibration", "test")
        }
        for event in self.state.judgments:
            arguments = event["arguments"]
            candidate_refs = [row["content_ref"] for row in arguments["candidates"]]
            split = pair_split(
                board_id=arguments["board_id"],
                descriptor_fingerprint=arguments["descriptor_fingerprint"],
                lower_content_ref=min(candidate_refs),
                upper_content_ref=max(candidate_refs),
            )
            choice = event["choice"]
            if choice in {"left", "right"}:
                counts[split]["decisive_groups"] += 1
                counts[split]["decisive_judgments"] += 1
                counts[split][f"{choice}_labels"] += 1
            elif choice == "tie":
                counts[split]["tie_groups"] += 1
                counts[split]["tie_judgments"] += 1
        return counts

    def train_preference(self, **arguments: Any) -> KhiveTrainedPreferenceModel:
        self.state.train_attempts += 1
        counts = self._split_counts()
        required = {"train": 64, "calibration": 16, "test": 16}
        for split, minimum in required.items():
            observed = counts[split]["decisive_groups"]
            if observed < minimum:
                raise KhiveProtocolError(
                    "kkernel exec returned exit status 1: "
                    f"moodboard.train_preference requires at least {minimum} distinct decisive "
                    f"{split} unordered-pair groups; observed {observed}"
                )
            if counts[split]["left_labels"] == 0 or counts[split]["right_labels"] == 0:
                raise KhiveProtocolError(
                    f"moodboard.train_preference {split} split must contain both randomized "
                    "displayed-side labels"
                )
        if counts["calibration"]["tie_groups"] < 16:
            raise KhiveProtocolError(
                "moodboard.train_preference requires at least 16 distinct calibration tie groups"
            )

        snapshot = hashlib.sha256(
            json.dumps(
                [
                    {
                        "choice": row["choice"],
                        "left": row["left"]["content_ref"],
                        "right": row["right"]["content_ref"],
                    }
                    for row in self.state.judgments
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        generation = len(self.state.models) + 1
        model_id = _stable_uuid(f"model-{generation}-{snapshot}")
        self.state.models[model_id] = generation
        fingerprint = hashlib.sha256(
            f"model-fingerprint-{generation}-{snapshot}".encode()
        ).hexdigest()
        self.state.model_fingerprints[model_id] = fingerprint
        return KhiveTrainedPreferenceModel(
            preference_model_id=model_id,
            model_fingerprint=fingerprint,
            content_ref=hashlib.sha256(f"bundle-{generation}-{snapshot}".encode()).hexdigest(),
            network_sha256=hashlib.sha256(
                f"network-sha-{generation}-{snapshot}".encode()
            ).hexdigest(),
            network_content_ref=hashlib.sha256(
                f"network-ref-{generation}-{snapshot}".encode()
            ).hexdigest(),
            fann_inference_verified=True,
            training={
                "snapshot_sha256": snapshot,
                "snapshot_event_count": len(self.state.judgments),
                "split_revision": "moodboard-pair-split-v1",
                "split_counts": counts,
            },
            calibration={"temperature": 1.0, "tie_band_half_width": 0.08},
            test_metrics={"accuracy": 0.75, "brier": 0.2, "log_loss": 0.5},
        )

    def preference(self, **arguments: Any) -> KhivePreferencePrediction:
        generation = self.state.models[arguments["preference_model_id"]]
        left_features = np.asarray(arguments["left"]["features"], dtype=np.float64)
        right_features = np.asarray(arguments["right"]["features"], dtype=np.float64)
        policy = POLICY_A if generation == 1 else POLICY_B
        left_preferred = policy.score(left_features) > policy.score(right_features)
        probability_left = 0.78 if left_preferred else 0.22
        if self.restarted and self.state.corrupt_restart:
            probability_left += 0.01
        return KhivePreferencePrediction(
            preference_model_id=arguments["preference_model_id"],
            model_fingerprint=self.state.model_fingerprints[arguments["preference_model_id"]],
            probability_left_given_decisive=probability_left,
            probability_right_given_decisive=1.0 - probability_left,
            raw_fann_logit=1.0 if left_preferred else -1.0,
            calibrated_temperature=1.0,
            indifference_state="outside_calibrated_band",
            conformal_state="not_computed_by_this_verb",
        )


def _run(tmp_path: Path, *, state: _FakeState | None = None):
    shared = state or _FakeState()
    client = _StatefulPreferenceClient(shared)
    output = tmp_path / "preference-replay.json"
    replay = replay_preference_demo(
        client=client,
        artifact_path=_write_artifact(tmp_path),
        restart_client_factory=lambda: _StatefulPreferenceClient(shared, restarted=True),
        output_path=output,
    )
    return replay, shared, output


def test_replay_meets_support_gates_and_never_reuses_an_unordered_pair(tmp_path: Path) -> None:
    replay, state, output = _run(tmp_path)
    document = replay.document

    assert document["evidence_class"] == "policy_simulated"
    assert document["support_refusal"] == {
        "captured": True,
        "classification": "below_support_refusal",
        "message": (
            "moodboard.train_preference requires at least 64 distinct decisive train "
            "unordered-pair groups; observed 0"
        ),
    }
    assert document["phase_counts"] == {
        "model_a": {
            "calibration_decisive": 16,
            "calibration_ties": 16,
            "test_decisive": 16,
            "train_decisive": 64,
        },
        "model_b_append": {"train_decisive": 96},
    }
    events = document["events"]
    pair_ids = [event["pair_id"] for event in events]
    assert len(events) == 208
    assert len(pair_ids) == len(set(pair_ids)) == len(state.served_pairs)
    probe_ids = {probe["pair_id"] for probe in document["frozen_conflict_probes"]}
    assert probe_ids.isdisjoint(pair_ids)
    assert all(event["split"] == "train" for event in events if event["phase"] == "model_b_append")
    assert {event["randomized_occurrence_provenance"]["swap_applied"] for event in events} == {
        False,
        True,
    }
    assert state.train_attempts == 3
    assert output.read_bytes() == replay.canonical_json

    a_decisive = [event for event in events if event["phase"] == "model_a_decisive"]
    for split in ("train", "calibration", "test"):
        labels = {event["choice"] for event in a_decisive if event["split"] == split}
        assert labels == {"left", "right"}
    assert all(
        set(event["randomized_occurrence_provenance"])
        == {
            "left_result_occurrence_id",
            "right_result_occurrence_id",
            "swap_applied",
        }
        for event in events
    )


def test_replay_is_deterministic_and_measures_a_to_b_on_frozen_conflict_probes(
    tmp_path: Path,
) -> None:
    first, _, _ = _run(tmp_path / "first")
    second, _, _ = _run(tmp_path / "second")

    assert first.canonical_json == second.canonical_json
    assert first.replay_fingerprint == second.replay_fingerprint
    document = first.document
    assert document["model_a"]["preference_model_id"] != document["model_b"]["preference_model_id"]
    assert (
        document["delta"]["mean_probability_for_policy_b_preferred_after"]
        > document["delta"]["mean_probability_for_policy_b_preferred_before"]
    )
    assert document["delta"]["mean_delta"] > 0.0
    assert document["delta"]["adaptation_direction_observed"] is True
    assert len(document["frozen_conflict_probes"]) == 8
    assert all(
        probe["policy_a_preferred_asset_id"] != probe["policy_b_preferred_asset_id"]
        for probe in document["frozen_conflict_probes"]
    )
    assert document["immutability"]["model_a_predictions_unchanged_after_model_b"] is True


def test_restart_reloads_model_b_with_exact_prediction_identity(tmp_path: Path) -> None:
    replay, _, _ = _run(tmp_path)
    restart = replay.document["restart_verification"]
    assert restart == {
        "exact_prediction_equality": True,
        "model_fingerprint_equal": True,
        "preference_model_id_equal": True,
        "probe_count": 8,
    }


def test_corrupt_serve_occurrence_identity_fails_closed(tmp_path: Path) -> None:
    state = _FakeState(corrupt_serve=True)
    with pytest.raises(PreferenceDemoError, match="occurrence identities"):
        _run(tmp_path, state=state)
    assert not state.judgments


def test_corrupt_restart_prediction_fails_closed(tmp_path: Path) -> None:
    state = _FakeState(corrupt_restart=True)
    with pytest.raises(PreferenceDemoError, match="restart prediction drift"):
        _run(tmp_path, state=state)


def test_corrupt_governed_artifact_fails_before_any_khive_side_effect(tmp_path: Path) -> None:
    artifact_path = _write_artifact(tmp_path)
    document = json.loads(artifact_path.read_text(encoding="utf-8"))
    document["candidates"][0]["features"][0] = 0.999
    artifact_path.write_text(json.dumps(document), encoding="utf-8")
    state = _FakeState()
    client = _StatefulPreferenceClient(state)

    with pytest.raises(ValueError):
        replay_preference_demo(
            client=client,
            artifact_path=artifact_path,
            restart_client_factory=lambda: _StatefulPreferenceClient(state, restarted=True),
        )

    assert state.train_attempts == 0
    assert not state.served_pairs


def test_output_cannot_replace_governed_artifact_before_side_effect(tmp_path: Path) -> None:
    artifact_path = _write_artifact(tmp_path)
    state = _FakeState()
    client = _StatefulPreferenceClient(state)

    with pytest.raises(PreferenceDemoError, match="must differ"):
        replay_preference_demo(
            client=client,
            artifact_path=artifact_path,
            restart_client_factory=lambda: _StatefulPreferenceClient(state, restarted=True),
            output_path=artifact_path,
        )

    assert state.train_attempts == 0
    assert not state.served_pairs


def test_output_is_no_clobber_before_any_khive_side_effect(tmp_path: Path) -> None:
    artifact_path = _write_artifact(tmp_path)
    output_path = tmp_path / "existing-replay.json"
    output_path.write_bytes(b"immutable prior replay\n")
    state = _FakeState()
    client = _StatefulPreferenceClient(state)

    with pytest.raises(PreferenceDemoError, match="must not already exist"):
        replay_preference_demo(
            client=client,
            artifact_path=artifact_path,
            restart_client_factory=lambda: _StatefulPreferenceClient(state, restarted=True),
            output_path=output_path,
        )

    assert output_path.read_bytes() == b"immutable prior replay\n"
    assert state.train_attempts == 0
    assert not state.served_pairs
