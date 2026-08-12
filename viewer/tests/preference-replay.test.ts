import {
  decodePreferenceReplayBridge,
  measuredPreferenceReplayEvidence,
  preferenceReplayBridge,
  projectPreferenceReplayEvidence,
} from "../src/preference-replay";

function projectedBridge(adaptationDirectionObserved = true): any {
  const before = 0.22;
  const after = adaptationDirectionObserved ? 0.78 : 0.1;
  return {
    evidence: {
      bindings: {
        board_entity_id: "01915124-7abc-7def-8abc-0123456789ab",
        board_id: "1".repeat(64),
        candidate_pool_sha256: "2".repeat(64),
        descriptor_fingerprint: "3".repeat(64),
        feature_producer_id: "f".repeat(64),
        feature_producer_revision: "moodboard.preference-producer.v1",
        feature_schema_id: "4".repeat(64),
        model_key: `moodboard_${"3".repeat(64)}_1024`,
        schema_version: "moodboard.preference-feature-artifact.v2",
        scope_sha256: "5".repeat(64),
        source_report_sha256: "6".repeat(64),
      },
      delta: {
        adaptation_direction_observed: adaptationDirectionObserved,
        mean_delta: after - before,
        mean_probability_for_policy_b_preferred_after: after,
        mean_probability_for_policy_b_preferred_before: before,
        outcome: adaptationDirectionObserved
          ? "improvement_observed"
          : "no_improvement_observed",
        probe_count: 8,
      },
      event_counts: {
        model_a_calibration_decisive: 16,
        model_a_calibration_ties: 16,
        model_a_test_decisive: 16,
        model_a_train_decisive: 64,
        model_b_appended_train_decisive: 96,
        total: 208,
      },
      evidence_class: "policy_simulated",
      model_a: {
        bundle_ref: "7".repeat(64),
        fann_inference_verified: true,
        model_fingerprint: "8".repeat(64),
        network_content_ref: "9".repeat(64),
        preference_model_id: "00000000-0000-4000-8000-00000000000a",
        snapshot_event_count: 112,
      },
      model_b: {
        bundle_ref: "a".repeat(64),
        fann_inference_verified: true,
        model_fingerprint: "b".repeat(64),
        network_content_ref: "c".repeat(64),
        preference_model_id: "00000000-0000-4000-8000-00000000000b",
        snapshot_event_count: 208,
      },
      non_claims: [
        "No human preference evidence: policy-simulated only.",
        "No online learning: immutable snapshots are retrained.",
        "No coherence or conformal claim: preference stays separate.",
      ],
      replay_fingerprint: "d".repeat(64),
      support_refusal: {
        captured: true,
        classification: "below_support_refusal",
        message: "moodboard.train_preference requires at least 64 distinct decisive train unordered-pair groups; observed 0",
      },
      verification: {
        fann_inference_verified: true,
        frozen_probe_count: 8,
        model_a_predictions_unchanged_after_model_b: true,
        model_snapshots_distinct: true,
        restart_exact: true,
      },
    },
    format_version: "moodboard.viewer-preference-replay-bridge.v1",
    generator_revision: "moodboard.preference-replay-viewer-bridge.v1",
    input: {
      byte_size: 1024,
      replay_fingerprint: "d".repeat(64),
      schema_version: "moodboard.preference-demo-replay.v1",
      sha256: "e".repeat(64),
    },
    state: "projected",
  };
}

describe("preference replay viewer bridge", () => {
  it("accepts only the closed evidence-free fallback sentinel", () => {
    const fallback = {
      evidence: null,
      format_version: "moodboard.viewer-preference-replay-bridge.v1",
      generator_revision: "moodboard.preference-replay-viewer-bridge.v1",
      input: null,
      state: "fallback",
    };
    expect(decodePreferenceReplayBridge(fallback)).toEqual(fallback);
    expect(preferenceReplayBridge).toEqual(fallback);
    expect(measuredPreferenceReplayEvidence).toBeNull();
    expect(() => decodePreferenceReplayBridge({ ...fallback, future: true })).toThrow(
      /unknown key/i,
    );
    expect(() =>
      decodePreferenceReplayBridge({
        ...fallback,
        evidence: projectedBridge().evidence,
      }),
    ).toThrow(/fallback/i);
  });

  it("projects measured policy-simulated A-to-B evidence without human-learning claims", () => {
    const bridge = decodePreferenceReplayBridge(projectedBridge());
    if (bridge.state !== "projected") throw new Error("expected projected bridge");
    const evidence = projectPreferenceReplayEvidence(bridge);

    expect(evidence.state).toBe("measured_replay");
    expect(evidence.evidence_class).toBe("policy_simulated");
    expect(evidence.delta.probe_count).toBe(8);
    expect(evidence.delta.mean_probability_for_policy_b_preferred_before).toBe(0.22);
    expect(evidence.delta.mean_probability_for_policy_b_preferred_after).toBe(0.78);
    expect(evidence.delta.mean_delta).toBeCloseTo(0.56);
    expect(evidence.delta.outcome).toBe("improvement_observed");
    expect(evidence.verification.restart_exact).toBe(true);
    expect(evidence.verification.model_a_predictions_unchanged_after_model_b).toBe(true);
    expect(evidence.model_a.preference_model_id).not.toBe(evidence.model_b.preference_model_id);
    expect(evidence.bindings.source_report_sha256).toBe("6".repeat(64));
    expect(evidence.non_claims.join(" ")).toMatch(/No human preference evidence/i);
    expect(evidence.non_claims.join(" ")).toMatch(/No online learning/i);
    expect(evidence.non_claims.join(" ")).toMatch(/No coherence or conformal claim/i);
  });

  it("preserves a measured no-improvement direction instead of selling adaptation", () => {
    const bridge = decodePreferenceReplayBridge(projectedBridge(false));
    if (bridge.state !== "projected") throw new Error("expected projected bridge");
    const evidence = projectPreferenceReplayEvidence(bridge);

    expect(evidence.delta.adaptation_direction_observed).toBe(false);
    expect(evidence.delta.mean_delta).toBeLessThan(0);
    expect(evidence.delta.outcome).toBe("no_improvement_observed");
    expect(evidence.status_label).toMatch(/no improvement observed/i);
  });

  it("rejects arithmetic, direction, snapshot, event, and binding contradictions", () => {
    const arithmetic = projectedBridge();
    arithmetic.evidence.delta.mean_delta = 0.1;
    expect(() => decodePreferenceReplayBridge(arithmetic)).toThrow(/mean_delta/i);

    const direction = projectedBridge();
    direction.evidence.delta.adaptation_direction_observed = false;
    expect(() => decodePreferenceReplayBridge(direction)).toThrow(/direction/i);

    const snapshots = projectedBridge();
    snapshots.evidence.model_b.preference_model_id =
      snapshots.evidence.model_a.preference_model_id;
    expect(() => decodePreferenceReplayBridge(snapshots)).toThrow(/distinct/i);

    const events = projectedBridge();
    events.evidence.event_counts.total = 207;
    expect(() => decodePreferenceReplayBridge(events)).toThrow(/event count/i);

    const snapshot = projectedBridge();
    snapshot.evidence.model_b.snapshot_event_count = 207;
    expect(() => decodePreferenceReplayBridge(snapshot)).toThrow(/snapshot event count/i);

    const binding = projectedBridge();
    binding.evidence.bindings.model_key = "moodboard_drifted_1024";
    expect(() => decodePreferenceReplayBridge(binding)).toThrow(/model_key/i);

    const producer = projectedBridge();
    producer.evidence.bindings.feature_producer_id = "not-a-digest";
    expect(() => decodePreferenceReplayBridge(producer)).toThrow(/feature_producer_id/i);

    const producerRevision = projectedBridge();
    producerRevision.evidence.bindings.feature_producer_revision = "";
    expect(() => decodePreferenceReplayBridge(producerRevision)).toThrow(
      /feature_producer_revision/i,
    );

    const featureArtifactSchema = projectedBridge();
    featureArtifactSchema.evidence.bindings.schema_version =
      "moodboard.preference-feature-artifact.v1";
    expect(() => decodePreferenceReplayBridge(featureArtifactSchema)).toThrow(/schema_version/i);

    const phase = projectedBridge();
    phase.evidence.event_counts.model_a_train_decisive = 65;
    phase.evidence.event_counts.total = 209;
    phase.evidence.model_a.snapshot_event_count = 113;
    phase.evidence.model_b.snapshot_event_count = 209;
    expect(() => decodePreferenceReplayBridge(phase)).toThrow(/exact demo phase counts/i);

    const support = projectedBridge();
    support.evidence.support_refusal.message = "observed zero";
    expect(() => decodePreferenceReplayBridge(support)).toThrow(/support refusal message/i);
  });
});
