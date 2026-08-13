/* Closed, build-time-only projection of one governed Khive preference replay. */
import preferenceReplayBridgeDocument from "./generated/preference-replay-bridge.json";

type JsonRecord = Readonly<Record<string, unknown>>;

function objectAt(value: unknown, path: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as JsonRecord;
}

function closed(value: JsonRecord, allowed: readonly string[], path: string): void {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length > 0) throw new Error(`${path} has unknown key ${unknown[0]}`);
  const missing = allowed.filter((key) => !(key in value));
  if (missing.length > 0) throw new Error(`${path} is missing key ${missing[0]}`);
}

function exact<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new Error(`${path} has unsupported value`);
  }
  return value as T;
}

function stringAt(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${path} must be a non-empty string`);
  }
  return value;
}

function digestAt(value: unknown, path: string): string {
  const measured = stringAt(value, path);
  if (!/^[a-f0-9]{64}$/.test(measured)) {
    throw new Error(`${path} must be a lowercase 64-hex digest`);
  }
  return measured;
}

function uuidAt(value: unknown, path: string): string {
  const measured = stringAt(value, path);
  if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(measured)) {
    throw new Error(`${path} must be a canonical UUID`);
  }
  return measured;
}

function finiteAt(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${path} must be finite`);
  }
  return value;
}

function countAt(value: unknown, path: string): number {
  const measured = finiteAt(value, path);
  if (!Number.isSafeInteger(measured) || measured < 0) {
    throw new Error(`${path} must be a non-negative safe integer`);
  }
  return measured;
}

function probabilityAt(value: unknown, path: string): number {
  const measured = finiteAt(value, path);
  if (measured < 0 || measured > 1) throw new Error(`${path} must be within [0, 1]`);
  return measured;
}

function booleanAt(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${path} must be boolean`);
  return value;
}

function stringsAt(value: unknown, path: string): readonly string[] {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
  return value.map((entry, index) => stringAt(entry, `${path}[${index}]`));
}

export interface PreferenceReplayBindings {
  readonly board_entity_id: string;
  readonly board_id: string;
  readonly candidate_pool_sha256: string;
  readonly descriptor_fingerprint: string;
  readonly feature_producer_id: string;
  readonly feature_producer_revision: string;
  readonly feature_schema_id: string;
  readonly model_key: string;
  readonly schema_version: "moodboard.preference-feature-artifact.v2";
  readonly scope_sha256: string;
  readonly source_report_sha256: string;
}

export interface PreferenceReplayModelSnapshot {
  readonly bundle_ref: string;
  readonly fann_inference_verified: true;
  readonly model_fingerprint: string;
  readonly network_content_ref: string;
  readonly preference_model_id: string;
  readonly snapshot_event_count: number;
}

export interface PreferenceReplayProbeAsset {
  readonly asset_id: string;
  readonly content_ref: string;
}

export interface PreferenceReplayProbe {
  readonly delta: number;
  readonly left: PreferenceReplayProbeAsset;
  readonly pair_id: string;
  readonly policy_b_preferred: PreferenceReplayProbeAsset & { readonly label: string };
  readonly probability_after: number;
  readonly probability_before: number;
  readonly right: PreferenceReplayProbeAsset;
}

export interface PreferenceFeatureInputIdentity {
  readonly board_entity_id: string;
  readonly board_id: string;
  readonly byte_size: number;
  readonly candidate_pool_sha256: string;
  readonly descriptor_fingerprint: string;
  readonly feature_schema_id: string;
  readonly model_key: string;
  readonly producer_id: string;
  readonly producer_revision: string;
  readonly schema_version: "moodboard.preference-feature-artifact.v2";
  readonly scope_sha256: string;
  readonly sha256: string;
  readonly source_report_sha256: string;
}

export interface PreferenceReplayEvidence {
  readonly state: "measured_replay";
  readonly status_label: string;
  readonly evidence_class: "policy_simulated";
  readonly bindings: PreferenceReplayBindings;
  readonly delta: {
    readonly adaptation_direction_observed: boolean;
    readonly mean_delta: number;
    readonly mean_probability_for_policy_b_preferred_after: number;
    readonly mean_probability_for_policy_b_preferred_before: number;
    readonly outcome: "improvement_observed" | "no_improvement_observed";
    readonly probe_count: 8;
  };
  readonly event_counts: {
    readonly model_a_calibration_decisive: number;
    readonly model_a_calibration_ties: number;
    readonly model_a_test_decisive: number;
    readonly model_a_train_decisive: number;
    readonly model_b_appended_train_decisive: number;
    readonly total: number;
  };
  readonly model_a: PreferenceReplayModelSnapshot;
  readonly model_b: PreferenceReplayModelSnapshot;
  readonly non_claims: readonly string[];
  readonly probes: readonly PreferenceReplayProbe[];
  readonly replay_fingerprint: string;
  readonly support_refusal: {
    readonly captured: true;
    readonly classification: "below_support_refusal";
    readonly message: string;
  };
  readonly verification: {
    readonly fann_inference_verified: true;
    readonly frozen_probe_count: 8;
    readonly model_a_predictions_unchanged_after_model_b: true;
    readonly model_snapshots_distinct: true;
    readonly restart_exact: true;
  };
}

export type PreferenceReplayBridge =
  | {
      readonly evidence: null;
      readonly format_version: "moodboard.viewer-preference-replay-bridge.v1";
      readonly generator_revision: "moodboard.preference-replay-viewer-bridge.v1";
      readonly input: null;
      readonly state: "fallback";
    }
  | {
      readonly evidence: Omit<PreferenceReplayEvidence, "state" | "status_label">;
      readonly format_version: "moodboard.viewer-preference-replay-bridge.v1";
      readonly generator_revision: "moodboard.preference-replay-viewer-bridge.v1";
      readonly input: {
        readonly features: PreferenceFeatureInputIdentity;
        readonly replay: {
          readonly byte_size: number;
          readonly replay_fingerprint: string;
          readonly schema_version: "moodboard.preference-demo-replay.v1";
          readonly sha256: string;
        };
      };
      readonly state: "projected";
    };

const bindingKeys = [
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
] as const;

function bindingsAt(value: unknown, path: string): PreferenceReplayBindings {
  const record = objectAt(value, path);
  closed(record, bindingKeys, path);
  const descriptor = digestAt(record.descriptor_fingerprint, `${path}.descriptor_fingerprint`);
  const modelKey = stringAt(record.model_key, `${path}.model_key`);
  if (!modelKey.startsWith(`moodboard_${descriptor}_`)) {
    throw new Error(`${path}.model_key must bind descriptor_fingerprint`);
  }
  return {
    board_entity_id: uuidAt(record.board_entity_id, `${path}.board_entity_id`),
    board_id: digestAt(record.board_id, `${path}.board_id`),
    candidate_pool_sha256: digestAt(
      record.candidate_pool_sha256,
      `${path}.candidate_pool_sha256`,
    ),
    descriptor_fingerprint: descriptor,
    feature_producer_id: digestAt(
      record.feature_producer_id,
      `${path}.feature_producer_id`,
    ),
    feature_producer_revision: stringAt(
      record.feature_producer_revision,
      `${path}.feature_producer_revision`,
    ),
    feature_schema_id: digestAt(record.feature_schema_id, `${path}.feature_schema_id`),
    model_key: modelKey,
    schema_version: exact(
      record.schema_version,
      ["moodboard.preference-feature-artifact.v2"],
      `${path}.schema_version`,
    ),
    scope_sha256: digestAt(record.scope_sha256, `${path}.scope_sha256`),
    source_report_sha256: digestAt(
      record.source_report_sha256,
      `${path}.source_report_sha256`,
    ),
  };
}

function featureInputAt(value: unknown, path: string): PreferenceFeatureInputIdentity {
  const record = objectAt(value, path);
  closed(
    record,
    [
      "board_entity_id",
      "board_id",
      "byte_size",
      "candidate_pool_sha256",
      "descriptor_fingerprint",
      "feature_schema_id",
      "model_key",
      "producer_id",
      "producer_revision",
      "schema_version",
      "scope_sha256",
      "sha256",
      "source_report_sha256",
    ],
    path,
  );
  const descriptor = digestAt(record.descriptor_fingerprint, `${path}.descriptor_fingerprint`);
  const modelKey = stringAt(record.model_key, `${path}.model_key`);
  if (!modelKey.startsWith(`moodboard_${descriptor}_`)) {
    throw new Error(`${path}.model_key must bind descriptor_fingerprint`);
  }
  const byteSize = countAt(record.byte_size, `${path}.byte_size`);
  if (byteSize < 1) throw new Error(`${path}.byte_size must be positive`);
  return {
    board_entity_id: uuidAt(record.board_entity_id, `${path}.board_entity_id`),
    board_id: digestAt(record.board_id, `${path}.board_id`),
    byte_size: byteSize,
    candidate_pool_sha256: digestAt(
      record.candidate_pool_sha256,
      `${path}.candidate_pool_sha256`,
    ),
    descriptor_fingerprint: descriptor,
    feature_schema_id: digestAt(record.feature_schema_id, `${path}.feature_schema_id`),
    model_key: modelKey,
    producer_id: digestAt(record.producer_id, `${path}.producer_id`),
    producer_revision: stringAt(record.producer_revision, `${path}.producer_revision`),
    schema_version: exact(
      record.schema_version,
      ["moodboard.preference-feature-artifact.v2"],
      `${path}.schema_version`,
    ),
    scope_sha256: digestAt(record.scope_sha256, `${path}.scope_sha256`),
    sha256: digestAt(record.sha256, `${path}.sha256`),
    source_report_sha256: digestAt(
      record.source_report_sha256,
      `${path}.source_report_sha256`,
    ),
  };
}

function probeAssetAt(value: unknown, path: string): PreferenceReplayProbeAsset {
  const record = objectAt(value, path);
  closed(record, ["asset_id", "content_ref"], path);
  return {
    asset_id: uuidAt(record.asset_id, `${path}.asset_id`),
    content_ref: digestAt(record.content_ref, `${path}.content_ref`),
  };
}

function modelAt(value: unknown, path: string): PreferenceReplayModelSnapshot {
  const record = objectAt(value, path);
  closed(
    record,
    [
      "bundle_ref",
      "fann_inference_verified",
      "model_fingerprint",
      "network_content_ref",
      "preference_model_id",
      "snapshot_event_count",
    ],
    path,
  );
  if (record.fann_inference_verified !== true) {
    throw new Error(`${path}.fann_inference_verified must be true`);
  }
  return {
    bundle_ref: digestAt(record.bundle_ref, `${path}.bundle_ref`),
    fann_inference_verified: true,
    model_fingerprint: digestAt(record.model_fingerprint, `${path}.model_fingerprint`),
    network_content_ref: digestAt(record.network_content_ref, `${path}.network_content_ref`),
    preference_model_id: uuidAt(record.preference_model_id, `${path}.preference_model_id`),
    snapshot_event_count: countAt(record.snapshot_event_count, `${path}.snapshot_event_count`),
  };
}

function probesAt(
  value: unknown,
  path: string,
  aggregateBefore: number,
  aggregateAfter: number,
): readonly PreferenceReplayProbe[] {
  if (!Array.isArray(value) || value.length !== 8) {
    throw new Error(`${path} must contain exactly 8 probes`);
  }
  const pairIds = new Set<string>();
  const preferredLabels = new Map<string, string>();
  const probes = value.map((entry, index): PreferenceReplayProbe => {
    const probePath = `${path}[${index}]`;
    const record = objectAt(entry, probePath);
    closed(
      record,
      [
        "delta",
        "left",
        "pair_id",
        "policy_b_preferred",
        "probability_after",
        "probability_before",
        "right",
      ],
      probePath,
    );
    const pairId = digestAt(record.pair_id, `${probePath}.pair_id`);
    if (pairIds.has(pairId)) throw new Error(`${path} pair IDs must be unique`);
    pairIds.add(pairId);
    const left = probeAssetAt(record.left, `${probePath}.left`);
    const right = probeAssetAt(record.right, `${probePath}.right`);
    if (left.asset_id === right.asset_id) throw new Error(`${probePath} sides must be distinct`);
    const preferredRecord = objectAt(record.policy_b_preferred, `${probePath}.policy_b_preferred`);
    closed(
      preferredRecord,
      ["asset_id", "content_ref", "label"],
      `${probePath}.policy_b_preferred`,
    );
    const preferred = {
      ...probeAssetAt(
        { asset_id: preferredRecord.asset_id, content_ref: preferredRecord.content_ref },
        `${probePath}.policy_b_preferred.identity`,
      ),
      label: stringAt(preferredRecord.label, `${probePath}.policy_b_preferred.label`),
    };
    if (
      ![left, right].some(
        (candidate) => candidate.asset_id === preferred.asset_id
          && candidate.content_ref === preferred.content_ref,
      )
    ) {
      throw new Error(`${probePath} policy B preferred identity must be present on one side`);
    }
    const preferredIdentity = `${preferred.content_ref}\0${preferred.label}`;
    const priorPreferred = preferredLabels.get(preferred.asset_id);
    if (priorPreferred !== undefined && priorPreferred !== preferredIdentity) {
      throw new Error(`${path} sidecar identity/label mapping is inconsistent`);
    }
    preferredLabels.set(preferred.asset_id, preferredIdentity);
    const probabilityBefore = probabilityAt(
      record.probability_before,
      `${probePath}.probability_before`,
    );
    const probabilityAfter = probabilityAt(
      record.probability_after,
      `${probePath}.probability_after`,
    );
    const delta = finiteAt(record.delta, `${probePath}.delta`);
    if (Math.abs(delta - (probabilityAfter - probabilityBefore)) > 1e-12) {
      throw new Error(`${probePath} probe delta contradicts before and after`);
    }
    return {
      delta,
      left,
      pair_id: pairId,
      policy_b_preferred: preferred,
      probability_after: probabilityAfter,
      probability_before: probabilityBefore,
      right,
    };
  });
  const meanBefore = probes.reduce((sum, probe) => sum + probe.probability_before, 0) / 8;
  const meanAfter = probes.reduce((sum, probe) => sum + probe.probability_after, 0) / 8;
  if (
    Math.abs(meanBefore - aggregateBefore) > 1e-12
    || Math.abs(meanAfter - aggregateAfter) > 1e-12
  ) {
    throw new Error(`${path} aggregate arithmetic drifted`);
  }
  return probes;
}

function evidenceAt(value: unknown, path: string): Omit<PreferenceReplayEvidence, "state" | "status_label"> {
  const record = objectAt(value, path);
  closed(
    record,
    [
      "bindings",
      "delta",
      "event_counts",
      "evidence_class",
      "model_a",
      "model_b",
      "non_claims",
      "probes",
      "replay_fingerprint",
      "support_refusal",
      "verification",
    ],
    path,
  );
  exact(record.evidence_class, ["policy_simulated"], `${path}.evidence_class`);
  const delta = objectAt(record.delta, `${path}.delta`);
  closed(
    delta,
    [
      "adaptation_direction_observed",
      "mean_delta",
      "mean_probability_for_policy_b_preferred_after",
      "mean_probability_for_policy_b_preferred_before",
      "outcome",
      "probe_count",
    ],
    `${path}.delta`,
  );
  const before = probabilityAt(
    delta.mean_probability_for_policy_b_preferred_before,
    `${path}.delta.mean_probability_for_policy_b_preferred_before`,
  );
  const after = probabilityAt(
    delta.mean_probability_for_policy_b_preferred_after,
    `${path}.delta.mean_probability_for_policy_b_preferred_after`,
  );
  const meanDelta = finiteAt(delta.mean_delta, `${path}.delta.mean_delta`);
  if (Math.abs(meanDelta - (after - before)) > 1e-12) {
    throw new Error(`${path}.delta.mean_delta contradicts before and after`);
  }
  const direction = booleanAt(
    delta.adaptation_direction_observed,
    `${path}.delta.adaptation_direction_observed`,
  );
  if (direction !== (meanDelta > 0)) throw new Error(`${path}.delta direction contradicts mean_delta`);
  const outcome = exact(
    delta.outcome,
    ["improvement_observed", "no_improvement_observed"],
    `${path}.delta.outcome`,
  );
  if (outcome !== (direction ? "improvement_observed" : "no_improvement_observed")) {
    throw new Error(`${path}.delta outcome contradicts direction`);
  }
  if (countAt(delta.probe_count, `${path}.delta.probe_count`) !== 8) {
    throw new Error(`${path}.delta.probe_count must preserve 8 frozen probes`);
  }

  const counts = objectAt(record.event_counts, `${path}.event_counts`);
  const countKeys = [
    "model_a_calibration_decisive",
    "model_a_calibration_ties",
    "model_a_test_decisive",
    "model_a_train_decisive",
    "model_b_appended_train_decisive",
    "total",
  ] as const;
  closed(counts, countKeys, `${path}.event_counts`);
  const eventCounts = {
    model_a_calibration_decisive: countAt(
      counts.model_a_calibration_decisive,
      `${path}.event_counts.model_a_calibration_decisive`,
    ),
    model_a_calibration_ties: countAt(
      counts.model_a_calibration_ties,
      `${path}.event_counts.model_a_calibration_ties`,
    ),
    model_a_test_decisive: countAt(
      counts.model_a_test_decisive,
      `${path}.event_counts.model_a_test_decisive`,
    ),
    model_a_train_decisive: countAt(
      counts.model_a_train_decisive,
      `${path}.event_counts.model_a_train_decisive`,
    ),
    model_b_appended_train_decisive: countAt(
      counts.model_b_appended_train_decisive,
      `${path}.event_counts.model_b_appended_train_decisive`,
    ),
    total: countAt(counts.total, `${path}.event_counts.total`),
  };
  const measuredTotal = eventCounts.model_a_calibration_decisive
    + eventCounts.model_a_calibration_ties
    + eventCounts.model_a_test_decisive
    + eventCounts.model_a_train_decisive
    + eventCounts.model_b_appended_train_decisive;
  if (eventCounts.total !== measuredTotal) throw new Error(`${path}.event count total drifted`);
  if (
    eventCounts.model_a_train_decisive !== 64
    || eventCounts.model_a_calibration_decisive !== 16
    || eventCounts.model_a_calibration_ties !== 16
    || eventCounts.model_a_test_decisive !== 16
    || eventCounts.model_b_appended_train_decisive !== 96
    || eventCounts.total !== 208
  ) {
    throw new Error(`${path} exact demo phase counts drifted`);
  }

  const modelA = modelAt(record.model_a, `${path}.model_a`);
  const modelB = modelAt(record.model_b, `${path}.model_b`);
  if (
    modelA.preference_model_id === modelB.preference_model_id
    || modelA.model_fingerprint === modelB.model_fingerprint
    || modelA.bundle_ref === modelB.bundle_ref
    || modelA.network_content_ref === modelB.network_content_ref
  ) {
    throw new Error(`${path} model snapshots must be distinct`);
  }
  if (
    modelA.snapshot_event_count !== eventCounts.total - eventCounts.model_b_appended_train_decisive
    || modelB.snapshot_event_count !== eventCounts.total
  ) {
    throw new Error(`${path} snapshot event count drifted`);
  }

  const support = objectAt(record.support_refusal, `${path}.support_refusal`);
  closed(support, ["captured", "classification", "message"], `${path}.support_refusal`);
  if (support.captured !== true) throw new Error(`${path}.support_refusal.captured must be true`);
  exact(
    support.classification,
    ["below_support_refusal"],
    `${path}.support_refusal.classification`,
  );
  const supportMessage = stringAt(support.message, `${path}.support_refusal.message`);
  if (
    supportMessage
    !== "moodboard.train_preference requires at least 64 distinct decisive train unordered-pair groups; observed 0"
  ) {
    throw new Error(`${path}.support_refusal support refusal message drifted`);
  }

  const verification = objectAt(record.verification, `${path}.verification`);
  closed(
    verification,
    [
      "fann_inference_verified",
      "frozen_probe_count",
      "model_a_predictions_unchanged_after_model_b",
      "model_snapshots_distinct",
      "restart_exact",
    ],
    `${path}.verification`,
  );
  for (const key of [
    "fann_inference_verified",
    "model_a_predictions_unchanged_after_model_b",
    "model_snapshots_distinct",
    "restart_exact",
  ] as const) {
    if (verification[key] !== true) throw new Error(`${path}.verification.${key} must be true`);
  }
  if (countAt(verification.frozen_probe_count, `${path}.verification.frozen_probe_count`) !== 8) {
    throw new Error(`${path}.verification.frozen_probe_count must be 8`);
  }

  const nonClaims = stringsAt(record.non_claims, `${path}.non_claims`);
  const joined = nonClaims.join(" ");
  for (const required of ["No human preference evidence", "No online learning", "No coherence"] as const) {
    if (!joined.includes(required)) throw new Error(`${path}.non_claims omits ${required}`);
  }

  return {
    bindings: bindingsAt(record.bindings, `${path}.bindings`),
    delta: {
      adaptation_direction_observed: direction,
      mean_delta: meanDelta,
      mean_probability_for_policy_b_preferred_after: after,
      mean_probability_for_policy_b_preferred_before: before,
      outcome,
      probe_count: 8,
    },
    event_counts: eventCounts,
    evidence_class: "policy_simulated",
    model_a: modelA,
    model_b: modelB,
    non_claims: nonClaims,
    probes: probesAt(record.probes, `${path}.probes`, before, after),
    replay_fingerprint: digestAt(record.replay_fingerprint, `${path}.replay_fingerprint`),
    support_refusal: {
      captured: true,
      classification: "below_support_refusal",
      message: supportMessage,
    },
    verification: {
      fann_inference_verified: true,
      frozen_probe_count: 8,
      model_a_predictions_unchanged_after_model_b: true,
      model_snapshots_distinct: true,
      restart_exact: true,
    },
  };
}

export function decodePreferenceReplayBridge(value: unknown): PreferenceReplayBridge {
  const record = objectAt(value, "$preference_replay_bridge");
  closed(
    record,
    ["evidence", "format_version", "generator_revision", "input", "state"],
    "$preference_replay_bridge",
  );
  exact(
    record.format_version,
    ["moodboard.viewer-preference-replay-bridge.v1"],
    "$preference_replay_bridge.format_version",
  );
  exact(
    record.generator_revision,
    ["moodboard.preference-replay-viewer-bridge.v1"],
    "$preference_replay_bridge.generator_revision",
  );
  const state = exact(record.state, ["fallback", "projected"], "$preference_replay_bridge.state");
  if (state === "fallback") {
    if (record.evidence !== null || record.input !== null) {
      throw new Error("$preference_replay_bridge fallback cannot carry evidence or input");
    }
    return {
      evidence: null,
      format_version: "moodboard.viewer-preference-replay-bridge.v1",
      generator_revision: "moodboard.preference-replay-viewer-bridge.v1",
      input: null,
      state,
    };
  }
  const input = objectAt(record.input, "$preference_replay_bridge.input");
  closed(input, ["features", "replay"], "$preference_replay_bridge.input");
  const features = featureInputAt(
    input.features,
    "$preference_replay_bridge.input.features",
  );
  const replay = objectAt(input.replay, "$preference_replay_bridge.input.replay");
  closed(
    replay,
    ["byte_size", "replay_fingerprint", "schema_version", "sha256"],
    "$preference_replay_bridge.input.replay",
  );
  const byteSize = countAt(
    replay.byte_size,
    "$preference_replay_bridge.input.replay.byte_size",
  );
  if (byteSize < 1) {
    throw new Error("$preference_replay_bridge.input.replay.byte_size must be positive");
  }
  const replayFingerprint = digestAt(
    replay.replay_fingerprint,
    "$preference_replay_bridge.input.replay.replay_fingerprint",
  );
  const evidence = evidenceAt(record.evidence, "$preference_replay_bridge.evidence");
  if (evidence.replay_fingerprint !== replayFingerprint) {
    throw new Error("$preference_replay_bridge replay fingerprint drifted");
  }
  const featureBindings = {
    board_entity_id: features.board_entity_id,
    board_id: features.board_id,
    candidate_pool_sha256: features.candidate_pool_sha256,
    descriptor_fingerprint: features.descriptor_fingerprint,
    feature_producer_id: features.producer_id,
    feature_producer_revision: features.producer_revision,
    feature_schema_id: features.feature_schema_id,
    model_key: features.model_key,
    schema_version: features.schema_version,
    scope_sha256: features.scope_sha256,
    source_report_sha256: features.source_report_sha256,
  };
  if (
    (Object.keys(featureBindings) as (keyof PreferenceReplayBindings)[]).some(
      (key) => featureBindings[key] !== evidence.bindings[key],
    )
  ) {
    throw new Error("$preference_replay_bridge feature input identity contradicts bindings");
  }
  return {
    evidence,
    format_version: "moodboard.viewer-preference-replay-bridge.v1",
    generator_revision: "moodboard.preference-replay-viewer-bridge.v1",
    input: {
      features,
      replay: {
        byte_size: byteSize,
        replay_fingerprint: replayFingerprint,
        schema_version: exact(
          replay.schema_version,
          ["moodboard.preference-demo-replay.v1"],
          "$preference_replay_bridge.input.replay.schema_version",
        ),
        sha256: digestAt(
          replay.sha256,
          "$preference_replay_bridge.input.replay.sha256",
        ),
      },
    },
    state,
  };
}

type ProjectedPreferenceReplayBridge = Extract<PreferenceReplayBridge, { readonly state: "projected" }>;

export function projectPreferenceReplayEvidence(
  bridge: ProjectedPreferenceReplayBridge,
): PreferenceReplayEvidence {
  return {
    ...bridge.evidence,
    state: "measured_replay",
    status_label: bridge.evidence.delta.adaptation_direction_observed
      ? "Measured policy-simulated improvement on 8 frozen probes"
      : "Measured replay · no improvement observed on 8 frozen probes",
  };
}

export const preferenceReplayBridge = decodePreferenceReplayBridge(preferenceReplayBridgeDocument);

export const measuredPreferenceReplayEvidence = preferenceReplayBridge.state === "projected"
  ? projectPreferenceReplayEvidence(preferenceReplayBridge)
  : null;
