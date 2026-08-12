from __future__ import annotations

import hashlib
import itertools
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
    CALIBRATION_TIE_POLICY,
    POLICY_A,
    POLICY_B,
    PreferenceDemoError,
    pair_split,
    replay_preference_demo,
)
from moodboard.preference_replay_viewer import (
    PreferenceReplayViewerBridgeError,
    compile_viewer_preference_replay_bridge,
    fallback_viewer_preference_replay_bridge,
    read_viewer_preference_replay_bridge,
    validate_viewer_preference_replay_bridge,
    write_viewer_preference_replay_bridge,
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
    hidden_calibration_duplicate: bool = False
    hidden_probability_shown_event: bool = False
    batch_calls: dict[str, int] = field(
        default_factory=lambda: {"judge": 0, "preference": 0, "serve": 0}
    )
    batch_sizes: dict[str, list[int]] = field(
        default_factory=lambda: {"judge": [], "preference": [], "serve": []}
    )


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

    def batch_serve(self, **arguments: Any) -> tuple[KhiveServeResult, ...]:
        self.state.batch_calls["serve"] += 1
        requests = arguments.pop("requests")
        self.state.batch_sizes["serve"].append(len(requests))
        return tuple(
            self.serve(
                **arguments,
                candidates=request.candidates,
                candidate_pool_sha256=request.candidate_pool_sha256,
                policy_revision=request.policy_revision,
                pair_propensity=request.pair_propensity,
            )
            for request in requests
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

    def batch_judge(self, **arguments: Any) -> tuple[KhiveJudgmentResult, ...]:
        self.state.batch_calls["judge"] += 1
        self.state.batch_sizes["judge"].append(len(arguments["requests"]))
        return tuple(
            self.judge(
                serve_id=request.serve_id,
                left_result_occurrence_id=request.left_result_occurrence_id,
                right_result_occurrence_id=request.right_result_occurrence_id,
                choice=request.choice,
                reason_code=request.reason_code,
                response_ms=request.response_ms,
            )
            for request in arguments["requests"]
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
        if self.state.hidden_calibration_duplicate:
            counts["calibration"]["decisive_judgments"] += 1
            counts["calibration"]["left_labels"] += 1
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
                "snapshot_event_count": (
                    len(self.state.judgments)
                    + int(self.state.hidden_calibration_duplicate)
                    + int(self.state.hidden_probability_shown_event)
                ),
                "excluded_probability_shown": int(self.state.hidden_probability_shown_event),
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

    def batch_preference(self, **arguments: Any) -> tuple[KhivePreferencePrediction, ...]:
        self.state.batch_calls["preference"] += 1
        requests = arguments.pop("requests")
        self.state.batch_sizes["preference"].append(len(requests))
        return tuple(
            self.preference(**arguments, left=request.left, right=request.right)
            for request in requests
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


def _write_refingerprinted_replay(document: dict[str, Any], destination: Path) -> None:
    core = {key: value for key, value in document.items() if key != "replay_fingerprint"}
    canonical_core = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    document["replay_fingerprint"] = hashlib.sha256(
        b"moodboard-preference-demo-replay-v1\0" + canonical_core
    ).hexdigest()
    destination.write_bytes(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )


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
    assert state.batch_calls == {"judge": 2, "preference": 4, "serve": 2}
    assert state.batch_sizes == {
        "judge": [112, 96],
        "preference": [8, 8, 8, 8],
        "serve": [112, 96],
    }
    assert state.train_attempts + sum(state.batch_calls.values()) == 11
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


def test_fake_replay_is_deterministic_and_checks_delta_arithmetic_on_frozen_probes(
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
    assert any("test doubles" in claim for claim in document["non_claims"])


def test_calibration_ties_use_disclosed_lowest_margin_policy(tmp_path: Path) -> None:
    artifact = _artifact()
    expected: list[tuple[float, str]] = []
    ordered = sorted(artifact.candidates, key=lambda candidate: candidate.content_ref)
    for lower, upper in itertools.combinations(ordered, 2):
        if (
            pair_split(
                board_id=artifact.board_id,
                descriptor_fingerprint=artifact.descriptor_fingerprint,
                lower_content_ref=lower.content_ref,
                upper_content_ref=upper.content_ref,
            )
            != "calibration"
        ):
            continue
        pair_id = hashlib.sha256(
            b"moodboard-preference-demo-pair-v1\0"
            + bytes.fromhex(lower.content_ref)
            + bytes.fromhex(upper.content_ref)
        ).hexdigest()
        margin = abs(POLICY_A.score(lower.features.values) - POLICY_A.score(upper.features.values))
        expected.append((margin, pair_id))
    expected.sort(key=lambda row: (row[0], row[1]))

    replay, _, _ = _run(tmp_path)
    ties = [event for event in replay.document["events"] if event["phase"] == "model_a_tie"]
    assert [event["pair_id"] for event in ties] == [pair_id for _, pair_id in expected[:16]]
    assert all(event["policy_id"] == CALIBRATION_TIE_POLICY.policy_id for event in ties)
    assert all(event["policy_revision"] == CALIBRATION_TIE_POLICY.revision for event in ties)
    assert all(event["reason_code"] == "other" for event in ties)
    margins = [event["tie_policy"]["absolute_policy_a_margin"] for event in ties]
    threshold = replay.document["policies"]["calibration_ties"][
        "selection_threshold_absolute_policy_a_margin"
    ]
    assert margins == [margin for margin, _ in expected[:16]]
    assert threshold == max(margins)
    assert all(
        event["tie_policy"]["selection_rule"] == CALIBRATION_TIE_POLICY.selection_rule
        for event in ties
    )


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


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (
            _FakeState(hidden_calibration_duplicate=True),
            "calibration decisive judgment support drifted",
        ),
        (
            _FakeState(hidden_probability_shown_event=True),
            "probability-shown contamination",
        ),
    ],
)
def test_preexisting_scoped_judgments_cannot_hide_behind_group_counts(
    tmp_path: Path, state: _FakeState, message: str
) -> None:
    with pytest.raises(PreferenceDemoError, match=message):
        _run(tmp_path, state=state)
    assert not (tmp_path / "preference-replay.json").exists()


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


def test_viewer_bridge_projects_closed_measured_replay_summary(tmp_path: Path) -> None:
    replay, _, source = _run(tmp_path / "run")

    bridge = compile_viewer_preference_replay_bridge(source)
    assert bridge["state"] == "projected"
    assert bridge["input"] == {
        "byte_size": len(replay.canonical_json),
        "replay_fingerprint": replay.replay_fingerprint,
        "schema_version": "moodboard.preference-demo-replay.v1",
        "sha256": hashlib.sha256(replay.canonical_json).hexdigest(),
    }
    evidence = bridge["evidence"]
    assert evidence["evidence_class"] == "policy_simulated"
    assert evidence["delta"] == {
        "adaptation_direction_observed": True,
        "mean_delta": pytest.approx(0.56),
        "mean_probability_for_policy_b_preferred_after": pytest.approx(0.78),
        "mean_probability_for_policy_b_preferred_before": pytest.approx(0.22),
        "outcome": "improvement_observed",
        "probe_count": 8,
    }
    assert evidence["event_counts"] == {
        "model_a_calibration_decisive": 16,
        "model_a_calibration_ties": 16,
        "model_a_test_decisive": 16,
        "model_a_train_decisive": 64,
        "model_b_appended_train_decisive": 96,
        "total": 208,
    }
    assert evidence["model_a"]["preference_model_id"] != evidence["model_b"]["preference_model_id"]
    assert evidence["model_a"]["bundle_ref"] == replay.document["model_a"]["content_ref"]
    assert evidence["model_b"]["bundle_ref"] == replay.document["model_b"]["content_ref"]
    assert evidence["verification"] == {
        "fann_inference_verified": True,
        "frozen_probe_count": 8,
        "model_a_predictions_unchanged_after_model_b": True,
        "model_snapshots_distinct": True,
        "restart_exact": True,
    }
    assert evidence["bindings"]["feature_schema_id"] == _artifact().feature_schema_id
    assert evidence["bindings"]["feature_producer_id"] == _artifact().producer_id
    assert evidence["bindings"]["feature_producer_revision"] == _artifact().producer_revision
    assert evidence["bindings"]["schema_version"] == _artifact().schema_version
    assert evidence["bindings"]["source_report_sha256"] == _artifact().source_report_sha256
    assert any("No human preference evidence" in claim for claim in evidence["non_claims"])
    assert any("No online learning" in claim for claim in evidence["non_claims"])
    assert any("No coherence or conformal claim" in claim for claim in evidence["non_claims"])

    destination = tmp_path / "viewer-preference-replay-bridge.json"
    write_viewer_preference_replay_bridge(bridge, destination)
    assert read_viewer_preference_replay_bridge(destination) == bridge


def test_viewer_bridge_fallback_is_the_only_evidence_free_sentinel() -> None:
    bridge = fallback_viewer_preference_replay_bridge()
    validate_viewer_preference_replay_bridge(bridge)
    assert bridge == {
        "evidence": None,
        "format_version": "moodboard.viewer-preference-replay-bridge.v1",
        "generator_revision": "moodboard.preference-replay-viewer-bridge.v1",
        "input": None,
        "state": "fallback",
    }

    contaminated = {**bridge, "evidence": {"evidence_class": "policy_simulated"}}
    with pytest.raises(PreferenceReplayViewerBridgeError, match="fallback"):
        validate_viewer_preference_replay_bridge(contaminated)


def test_viewer_bridge_preserves_honest_no_improvement_outcome(tmp_path: Path) -> None:
    replay, _, _ = _run(tmp_path / "run")
    document = json.loads(replay.canonical_json)
    before = document["delta"]["mean_probability_for_policy_b_preferred_before"]
    after = 0.1
    for probe in document["frozen_conflict_probes"]:
        preferred_left = probe["policy_b_preferred_asset_id"] == probe["left"]["asset_id"]
        prediction = probe["model_b_prediction"]
        prediction["probability_left_given_decisive"] = after if preferred_left else 1.0 - after
        prediction["probability_right_given_decisive"] = 1.0 - after if preferred_left else after
        probe["probability_for_policy_b_preferred_after"] = after
    document["delta"] = {
        "adaptation_direction_observed": False,
        "measurement": "frozen_policy_conflict_probes",
        "mean_delta": after - before,
        "mean_probability_for_policy_b_preferred_after": after,
        "mean_probability_for_policy_b_preferred_before": before,
    }
    source = tmp_path / "no-improvement-replay.json"
    _write_refingerprinted_replay(document, source)

    evidence = compile_viewer_preference_replay_bridge(source)["evidence"]
    assert evidence["delta"]["adaptation_direction_observed"] is False
    assert evidence["delta"]["mean_delta"] < 0.0
    assert evidence["delta"]["outcome"] == "no_improvement_observed"


def test_viewer_bridge_rejects_replay_fingerprint_or_aggregate_drift(tmp_path: Path) -> None:
    replay, _, source = _run(tmp_path / "run")
    tampered = json.loads(replay.canonical_json)
    tampered["delta"]["mean_delta"] = 0.0
    source.write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    with pytest.raises(PreferenceReplayViewerBridgeError, match="fingerprint"):
        compile_viewer_preference_replay_bridge(source)


def test_viewer_bridge_rejects_model_snapshot_event_count_drift(tmp_path: Path) -> None:
    _, _, source = _run(tmp_path / "run")
    bridge = compile_viewer_preference_replay_bridge(source)
    bridge["evidence"]["model_b"]["snapshot_event_count"] -= 1

    with pytest.raises(PreferenceReplayViewerBridgeError, match="snapshot event count"):
        validate_viewer_preference_replay_bridge(bridge)


@pytest.mark.parametrize("reader", ["compile", "read"])
def test_viewer_bridge_rejects_oversized_regular_file_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    source = tmp_path / "oversized.json"
    with source.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    original = Path.read_bytes
    reads: list[Path] = []

    def forbidden_read(path: Path) -> bytes:
        reads.append(path)
        if path == source:
            raise AssertionError("oversized source was read before the byte ceiling")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    operation = (
        compile_viewer_preference_replay_bridge
        if reader == "compile"
        else read_viewer_preference_replay_bridge
    )
    with pytest.raises(PreferenceReplayViewerBridgeError, match="byte ceiling"):
        operation(source)
    assert reads == []


def test_viewer_bridge_rejects_non_regular_file_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "directory.json"
    source.mkdir()
    reads: list[Path] = []

    def forbidden_read(path: Path) -> bytes:
        reads.append(path)
        raise AssertionError("non-regular source was read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(PreferenceReplayViewerBridgeError, match="regular file"):
        compile_viewer_preference_replay_bridge(source)
    assert reads == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("feature_producer_id", "not-a-digest", "feature_producer_id"),
        ("feature_producer_revision", "", "feature_producer_revision"),
        (
            "schema_version",
            "moodboard.preference-feature-artifact.v1",
            "schema_version",
        ),
    ],
)
def test_viewer_bridge_rejects_producer_binding_drift(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    _, _, source = _run(tmp_path / "run")
    bridge = compile_viewer_preference_replay_bridge(source)
    bridge["evidence"]["bindings"][field] = value

    with pytest.raises(PreferenceReplayViewerBridgeError, match=message):
        validate_viewer_preference_replay_bridge(bridge)


@pytest.mark.parametrize(
    ("count", "value"),
    [
        ("model_a_train_decisive", 65),
        ("model_a_calibration_decisive", 15),
        ("model_a_calibration_ties", 17),
        ("model_a_test_decisive", 15),
        ("model_b_appended_train_decisive", 95),
    ],
)
def test_viewer_bridge_rejects_non_demo_phase_counts(
    tmp_path: Path, count: str, value: int
) -> None:
    _, _, source = _run(tmp_path / "run")
    bridge = compile_viewer_preference_replay_bridge(source)
    previous = bridge["evidence"]["event_counts"][count]
    bridge["evidence"]["event_counts"][count] = value
    bridge["evidence"]["event_counts"]["total"] += value - previous
    bridge["evidence"]["model_b"]["snapshot_event_count"] += value - previous
    if count != "model_b_appended_train_decisive":
        bridge["evidence"]["model_a"]["snapshot_event_count"] += value - previous

    with pytest.raises(PreferenceReplayViewerBridgeError, match="exact demo phase counts"):
        validate_viewer_preference_replay_bridge(bridge)


def test_viewer_bridge_rejects_support_refusal_message_drift(tmp_path: Path) -> None:
    _, _, source = _run(tmp_path / "run")
    bridge = compile_viewer_preference_replay_bridge(source)
    bridge["evidence"]["support_refusal"]["message"] = (
        "moodboard.train_preference requires at least 32 distinct decisive train "
        "unordered-pair groups; observed 0"
    )

    with pytest.raises(PreferenceReplayViewerBridgeError, match="support refusal message"):
        validate_viewer_preference_replay_bridge(bridge)


def test_viewer_bridge_compiler_rejects_refingerprinted_phase_count_drift(
    tmp_path: Path,
) -> None:
    replay, _, _ = _run(tmp_path / "run")
    document = json.loads(replay.canonical_json)
    document["phase_counts"]["model_a"]["train_decisive"] = 65
    source = tmp_path / "phase-drift.json"
    _write_refingerprinted_replay(document, source)

    with pytest.raises(PreferenceReplayViewerBridgeError, match="exact demo phase counts"):
        compile_viewer_preference_replay_bridge(source)


def test_viewer_bridge_compiler_rejects_refingerprinted_support_message_drift(
    tmp_path: Path,
) -> None:
    replay, _, _ = _run(tmp_path / "run")
    document = json.loads(replay.canonical_json)
    document["support_refusal"]["message"] = (
        "moodboard.train_preference requires at least 32 distinct decisive train "
        "unordered-pair groups; observed 0"
    )
    source = tmp_path / "support-drift.json"
    _write_refingerprinted_replay(document, source)

    with pytest.raises(PreferenceReplayViewerBridgeError, match="support refusal message"):
        compile_viewer_preference_replay_bridge(source)
