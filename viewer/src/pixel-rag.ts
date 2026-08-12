/*
 * Presentation-owned, compile-time demo evidence. This module has no loader and accepts no
 * view-time input: the closed decoder fails the build/tests when its literal contract drifts.
 * A measured Khive run replaces the fixture values before any empirical claim is shown.
 */
export type PixelRagIntentId = "local_replace" | "global_restyle";
export type PixelRagEvidenceStatus = "contract_fixture" | "measured_run";
export type PixelRagGranularity = "confirmed_mask" | "whole_frame";

export interface PixelRagMediaRef {
  readonly kind: "report_candidate" | "report_reference";
  readonly id: string;
}

export interface PixelRagContentIdentity {
  readonly asset_id: string;
  readonly content_sha256: string;
  readonly media: PixelRagMediaRef;
  readonly khive: {
    readonly content_ref: string;
    readonly record_id: string;
  };
}

export interface PixelRagLicense {
  readonly label: string;
  readonly spdx_id: string;
  readonly source_page: string;
}

export interface PixelRagEvidenceHit extends PixelRagContentIdentity {
  readonly rank: number;
  readonly title: string;
  readonly creator: string;
  readonly license: PixelRagLicense;
  readonly score: {
    readonly kind: "cosine_similarity";
    readonly value: number;
    readonly descriptor_id: string;
  };
  readonly rationale: string;
}

export type PixelRagStageId =
  | "retrieval"
  | "region"
  | "conditioning"
  | "generator"
  | "verification"
  | "immutable_output";

export interface PixelRagPipelineStage {
  readonly id: PixelRagStageId;
  readonly label: string;
  readonly executor:
    | "khive_retrieval"
    | "human_confirmed"
    | "deterministic_conditioner"
    | "external_or_precomputed"
    | "recorded_verifier"
    | "khive_blob_store";
  readonly detail: string;
}

export interface PixelRagMetric {
  readonly id: "precision_at_3" | "ndcg_at_5" | "intent_top3_jaccard" | "outside_mask_ssim" | "content_retention";
  readonly label: string;
  readonly value: number;
  readonly display: string;
  readonly target: string;
  readonly passed: boolean;
  readonly source: PixelRagEvidenceStatus;
}

export interface PixelRagIntent {
  readonly id: PixelRagIntentId;
  readonly eyebrow: string;
  readonly title: string;
  readonly prompt: string;
  readonly query: {
    readonly granularity: PixelRagGranularity;
    readonly label: string;
    readonly namespace: string;
    readonly corpus_label: string;
    readonly rationale: string;
    readonly mask_ref: string | null;
  };
  readonly evidence: readonly PixelRagEvidenceHit[];
  readonly pipeline: readonly PixelRagPipelineStage[];
  readonly metrics: readonly PixelRagMetric[];
  readonly output: {
    readonly state: "external_precomputed_fixture" | "recorded_external_output";
    readonly label: string;
    readonly content_ref: string;
    readonly rollback_ref: string;
    readonly caveat: string;
  };
}

export interface PixelRagPreferenceSnapshot {
  readonly label: string;
  readonly model_id: string;
  readonly preferred_asset: string;
  readonly probability: number;
  readonly provenance: {
    readonly bundle_ref: string;
    readonly feature_schema_sha256: string;
    readonly judgments: number;
  };
}

export interface PixelRagArtifact {
  readonly format_version: 1;
  readonly artifact_id: string;
  readonly evidence_status: PixelRagEvidenceStatus;
  readonly status_label: string;
  readonly source: PixelRagContentIdentity & {
    readonly label: string;
    readonly source_page: string;
  };
  readonly intents: readonly PixelRagIntent[];
  readonly preference: {
    readonly status: "governed_snapshot_fixture" | "trained_snapshot";
    readonly explanation: string;
    readonly before: PixelRagPreferenceSnapshot;
    readonly after: PixelRagPreferenceSnapshot;
  };
  readonly provenance: {
    readonly generated_at: string;
    readonly source_manifest_sha256: string;
    readonly khive_revision: string;
    readonly lattice_descriptor: string;
    readonly run_fingerprint: string;
  };
}

const SOURCE_HASH = "2a9e8619086866776d516fae6b9f3d4b245ecfff483937d4996b1706fec162ef";
const FEATURE_SCHEMA_HASH = "11b2f08fd3ff26523d874566aa6d85d63c9ce3301fd8c94718eb56e9b299a34a";

const stages: readonly PixelRagPipelineStage[] = [
  {
    id: "retrieval",
    label: "Retrieval",
    executor: "khive_retrieval",
    detail: "Khive narrows immutable visual evidence inside the active intent namespace.",
  },
  {
    id: "region",
    label: "Region",
    executor: "human_confirmed",
    detail: "The query declares either a confirmed editable mask or the complete source frame.",
  },
  {
    id: "conditioning",
    label: "Conditioning",
    executor: "deterministic_conditioner",
    detail: "Intent, protected pixels, evidence refs, and constraints become a recorded edit plan.",
  },
  {
    id: "generator",
    label: "External generator",
    executor: "external_or_precomputed",
    detail: "External generator boundary: Adobe Firefly or another named provider executes the edit outside Moodboard.",
  },
  {
    id: "verification",
    label: "Verification",
    executor: "recorded_verifier",
    detail: "Intent success, content retention, and protected-pixel checks are recorded separately.",
  },
  {
    id: "immutable_output",
    label: "Immutable Khive output",
    executor: "khive_blob_store",
    detail: "The result is a new content-addressed asset. Rollback selects the prior ref; no bytes are overwritten.",
  },
];

function fixtureHit(
  rank: number,
  referenceId: string,
  contentSha256: string,
  title: string,
  creator: string,
  score: number,
  rationale: string,
): PixelRagEvidenceHit {
  return {
    rank,
    asset_id: referenceId,
    content_sha256: contentSha256,
    media: { kind: "report_reference", id: referenceId },
    khive: {
      content_ref: `sha256:${contentSha256}`,
      record_id: `fixture:${referenceId}`,
    },
    title,
    creator,
    license: {
      label: "Public domain · UI fixture",
      spdx_id: "CC0-1.0",
      source_page: "fixture://showcase-report",
    },
    score: {
      kind: "cosine_similarity",
      value: score,
      descriptor_id: "lattice:qwen3.5-0.8b:mean_visual_tokens:fixture",
    },
    rationale,
  };
}

export const pixelRagArtifact = {
  format_version: 1,
  artifact_id: "pixel-rag:adobe-demo:contract-v1",
  evidence_status: "contract_fixture",
  status_label: "Deterministic interface fixture · measured Khive run replaces these values",
  source: {
    asset_id: "01_aligned.png",
    label: "Shared source frame",
    content_sha256: SOURCE_HASH,
    source_page: "fixture://showcase-report/01_aligned.png",
    media: { kind: "report_candidate", id: "01_aligned.png" },
    khive: {
      content_ref: `sha256:${SOURCE_HASH}`,
      record_id: "fixture:01_aligned.png",
    },
  },
  intents: [
    {
      id: "local_replace",
      eyebrow: "Local semantic replacement",
      title: "Replace apple tree with lemon tree",
      prompt: "Replace only the selected apple tree with a mature lemon tree. Preserve the ground, sky, people, and camera geometry.",
      query: {
        granularity: "confirmed_mask",
        label: "Confirmed tree mask",
        namespace: "demo:replace:lemon-tree",
        corpus_label: "Lemon trees · silhouette, view, light",
        rationale: "Retrieve region-compatible lemon-tree evidence; whole-frame painting similarity would answer the wrong question.",
        mask_ref: "sha256:760d394bf9b4350301f29fcff17f315412ad8a0ae3de9c9ef1b3a92f915bcc11",
      },
      evidence: [
        fixtureHit(1, "reference_02.png", "a1f3d5526341c49554b1377d705a4ef9663626e17f49cb6d89cacc377961b15c", "Eureka lemon · lateral canopy", "Dataset manifest fixture", 0.882, "Closest canopy silhouette and diffuse light."),
        fixtureHit(2, "reference_03.png", "ed9f89c487cd4cbefb67327e1c253c03617396a199d88fbe6f743ca6cec5b990", "Menton lemon tree · full crown", "Dataset manifest fixture", 0.846, "Supports branch density without changing the horizon."),
        fixtureHit(3, "reference_04.png", "916431096934722cb5fdab9d03f62b00de94385c2f590725a5b61ff16bbe3707", "Lemon tree · fruit detail", "Dataset manifest fixture", 0.819, "Adds fruit and leaf evidence inside the selected region."),
      ],
      pipeline: stages,
      metrics: [
        { id: "precision_at_3", label: "P@3", value: 0.667, display: "2 / 3", target: "≥ 2 / 3", passed: true, source: "contract_fixture" },
        { id: "ndcg_at_5", label: "nDCG@5", value: 0.86, display: "0.860", target: "≥ 0.800", passed: true, source: "contract_fixture" },
        { id: "outside_mask_ssim", label: "Outside-mask SSIM", value: 0.963, display: "0.963", target: "≥ 0.950", passed: true, source: "contract_fixture" },
      ],
      output: {
        state: "external_precomputed_fixture",
        label: "Firefly Fill boundary · output fixture",
        content_ref: "sha256:58581211d6fa59997ee9872ca39e0b7a5385c3941c338df85a44d93814b88701",
        rollback_ref: `sha256:${SOURCE_HASH}`,
        caveat: "Moodboard retrieves, constrains, and verifies. It does not claim to be the image generator.",
      },
    },
    {
      id: "global_restyle",
      eyebrow: "Global style transfer",
      title: "Restyle as Claude Lorrain",
      prompt: "Restyle the complete scene as a luminous Claude Lorrain pastoral painting while preserving the original layout and subject relationships.",
      query: {
        granularity: "whole_frame",
        label: "Whole frame",
        namespace: "demo:style:claude-lorrain",
        corpus_label: "Claude Lorrain · public-domain paintings",
        rationale: "Retrieve global style evidence while a separate content anchor protects layout and subject identity.",
        mask_ref: null,
      },
      evidence: [
        fixtureHit(1, "reference_10.png", "65d29543dffe714b664332b9943b074a5fc3a380dd749834674600beb4f4be7f", "Pastoral landscape · luminous distance", "Claude Lorrain corpus fixture", 0.901, "Closest atmospheric depth and warm horizon."),
        fixtureHit(2, "reference_11.png", "c057e767b2d23ec56d4d2cd26aa7fef5e33b80be2c4707fb59948a0d2c3cd115", "Sunrise · architectural framing", "Claude Lorrain corpus fixture", 0.873, "Supports framing, haze, and tonal progression."),
        fixtureHit(3, "reference_12.png", "6966d49789ba42ab14bbc2694460eb3405b56ee75328e0393436884329e3c9c3", "The Ford · pastoral movement", "Claude Lorrain corpus fixture", 0.851, "Supports pastoral figures without replacing scene geometry."),
      ],
      pipeline: stages,
      metrics: [
        { id: "precision_at_3", label: "P@3", value: 1, display: "3 / 3", target: "≥ 2 / 3", passed: true, source: "contract_fixture" },
        { id: "ndcg_at_5", label: "nDCG@5", value: 0.91, display: "0.910", target: "≥ 0.800", passed: true, source: "contract_fixture" },
        { id: "content_retention", label: "Content retention", value: 0.886, display: "0.886", target: "reported, not gated", passed: true, source: "contract_fixture" },
      ],
      output: {
        state: "external_precomputed_fixture",
        label: "Firefly Style Reference boundary · output fixture",
        content_ref: "sha256:9d4bb2e38ae92482963d51111ee897fc054e42de17bbca163a2aaf15c2c026ef",
        rollback_ref: `sha256:${SOURCE_HASH}`,
        caveat: "The style references condition an external edit; content preservation remains a separate verifier concern.",
      },
    },
  ],
  preference: {
    status: "governed_snapshot_fixture",
    explanation: "Clicks append immutable judgments. A governed retrain publishes a new FANN snapshot; rankings never mutate in place.",
    before: {
      label: "Model A · baseline",
      model_id: "fann:sha256:3182544743645b831843562c401480b02315878ae67571633ea8541d4b15ab20",
      preferred_asset: "claude-pastoral-01",
      probability: 0.51,
      provenance: { bundle_ref: "sha256:3182544743645b831843562c401480b02315878ae67571633ea8541d4b15ab20", feature_schema_sha256: FEATURE_SCHEMA_HASH, judgments: 96 },
    },
    after: {
      label: "Model B · learned snapshot",
      model_id: "fann:sha256:ae0b044fa6ef4cf9ad2bc50d57f82f014890cb71d33cf03c3527f766291beca5",
      preferred_asset: "claude-sunrise-02",
      probability: 0.74,
      provenance: { bundle_ref: "sha256:ae0b044fa6ef4cf9ad2bc50d57f82f014890cb71d33cf03c3527f766291beca5", feature_schema_sha256: FEATURE_SCHEMA_HASH, judgments: 128 },
    },
  },
  provenance: {
    generated_at: "2026-08-12T16:00:00Z",
    source_manifest_sha256: "6b79dd458c0137dc295d96d622e8df6a5f1c453deab3543f33375bdc74e64917",
    khive_revision: "fixture:replace-with-remote-main-run",
    lattice_descriptor: "qwen3.5-0.8b · mean_visual_tokens · fixture identity",
    run_fingerprint: "fixture:pixel-rag:v1",
  },
} as const satisfies PixelRagArtifact;

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
}

function stringAt(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${path} must be a non-empty string`);
  return value;
}

function finiteAt(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${path} must be finite`);
  return value;
}

function booleanAt(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${path} must be boolean`);
  return value;
}

function arrayAt(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
  return value;
}

function exact<T extends string | number>(value: unknown, expected: readonly T[], path: string): T {
  if (!expected.includes(value as T)) throw new Error(`${path} has unsupported value`);
  return value as T;
}

function shaAt(value: unknown, path: string): string {
  const resolved = stringAt(value, path);
  if (!/^[a-f0-9]{64}$/.test(resolved)) throw new Error(`${path} must be a lowercase SHA-256`);
  return resolved;
}

function contentRefAt(value: unknown, path: string): string {
  const resolved = stringAt(value, path);
  if (!/^sha256:[a-f0-9]{64}$/.test(resolved)) throw new Error(`${path} must be a SHA-256 content ref`);
  return resolved;
}

function mediaAt(value: unknown, path: string): PixelRagMediaRef {
  const record = objectAt(value, path);
  closed(record, ["kind", "id"], path);
  return {
    kind: exact(record.kind, ["report_candidate", "report_reference"], `${path}.kind`),
    id: stringAt(record.id, `${path}.id`),
  };
}

function khiveAt(value: unknown, path: string): PixelRagContentIdentity["khive"] {
  const record = objectAt(value, path);
  closed(record, ["content_ref", "record_id"], path);
  return {
    content_ref: contentRefAt(record.content_ref, `${path}.content_ref`),
    record_id: stringAt(record.record_id, `${path}.record_id`),
  };
}

function hitAt(value: unknown, path: string): PixelRagEvidenceHit {
  const record = objectAt(value, path);
  closed(record, ["rank", "asset_id", "content_sha256", "media", "khive", "title", "creator", "license", "score", "rationale"], path);
  const license = objectAt(record.license, `${path}.license`);
  closed(license, ["label", "spdx_id", "source_page"], `${path}.license`);
  const score = objectAt(record.score, `${path}.score`);
  closed(score, ["kind", "value", "descriptor_id"], `${path}.score`);
  const rank = finiteAt(record.rank, `${path}.rank`);
  if (!Number.isSafeInteger(rank) || rank < 1) throw new Error(`${path}.rank must be a positive safe integer`);
  const scoreValue = finiteAt(score.value, `${path}.score.value`);
  if (scoreValue < -1 || scoreValue > 1) throw new Error(`${path}.score.value must be within cosine range`);
  return {
    rank,
    asset_id: stringAt(record.asset_id, `${path}.asset_id`),
    content_sha256: shaAt(record.content_sha256, `${path}.content_sha256`),
    media: mediaAt(record.media, `${path}.media`),
    khive: khiveAt(record.khive, `${path}.khive`),
    title: stringAt(record.title, `${path}.title`),
    creator: stringAt(record.creator, `${path}.creator`),
    license: {
      label: stringAt(license.label, `${path}.license.label`),
      spdx_id: stringAt(license.spdx_id, `${path}.license.spdx_id`),
      source_page: stringAt(license.source_page, `${path}.license.source_page`),
    },
    score: {
      kind: exact(score.kind, ["cosine_similarity"], `${path}.score.kind`),
      value: scoreValue,
      descriptor_id: stringAt(score.descriptor_id, `${path}.score.descriptor_id`),
    },
    rationale: stringAt(record.rationale, `${path}.rationale`),
  };
}

const stageIds = ["retrieval", "region", "conditioning", "generator", "verification", "immutable_output"] as const;

function stageAt(value: unknown, path: string): PixelRagPipelineStage {
  const record = objectAt(value, path);
  closed(record, ["id", "label", "executor", "detail"], path);
  return {
    id: exact(record.id, stageIds, `${path}.id`),
    label: stringAt(record.label, `${path}.label`),
    executor: exact(record.executor, ["khive_retrieval", "human_confirmed", "deterministic_conditioner", "external_or_precomputed", "recorded_verifier", "khive_blob_store"], `${path}.executor`),
    detail: stringAt(record.detail, `${path}.detail`),
  };
}

function metricAt(value: unknown, path: string): PixelRagMetric {
  const record = objectAt(value, path);
  closed(record, ["id", "label", "value", "display", "target", "passed", "source"], path);
  return {
    id: exact(record.id, ["precision_at_3", "ndcg_at_5", "intent_top3_jaccard", "outside_mask_ssim", "content_retention"], `${path}.id`),
    label: stringAt(record.label, `${path}.label`),
    value: finiteAt(record.value, `${path}.value`),
    display: stringAt(record.display, `${path}.display`),
    target: stringAt(record.target, `${path}.target`),
    passed: booleanAt(record.passed, `${path}.passed`),
    source: exact(record.source, ["contract_fixture", "measured_run"], `${path}.source`),
  };
}

function intentAt(value: unknown, path: string): PixelRagIntent {
  const record = objectAt(value, path);
  closed(record, ["id", "eyebrow", "title", "prompt", "query", "evidence", "pipeline", "metrics", "output"], path);
  const query = objectAt(record.query, `${path}.query`);
  closed(query, ["granularity", "label", "namespace", "corpus_label", "rationale", "mask_ref"], `${path}.query`);
  const evidence = arrayAt(record.evidence, `${path}.evidence`).map((hit, index) => hitAt(hit, `${path}.evidence[${index}]`));
  if (evidence.length !== 3 || evidence.some((hit, index) => hit.rank !== index + 1)) {
    throw new Error(`${path}.evidence must have three contiguous ranks`);
  }
  const pipeline = arrayAt(record.pipeline, `${path}.pipeline`).map((stage, index) => stageAt(stage, `${path}.pipeline[${index}]`));
  if (pipeline.length !== stageIds.length || pipeline.some((stage, index) => stage.id !== stageIds[index])) {
    throw new Error(`${path}.pipeline must preserve the declared stage sequence`);
  }
  const metrics = arrayAt(record.metrics, `${path}.metrics`).map((metric, index) => metricAt(metric, `${path}.metrics[${index}]`));
  const output = objectAt(record.output, `${path}.output`);
  closed(output, ["state", "label", "content_ref", "rollback_ref", "caveat"], `${path}.output`);
  const maskRef = query.mask_ref === null ? null : contentRefAt(query.mask_ref, `${path}.query.mask_ref`);
  return {
    id: exact(record.id, ["local_replace", "global_restyle"], `${path}.id`),
    eyebrow: stringAt(record.eyebrow, `${path}.eyebrow`),
    title: stringAt(record.title, `${path}.title`),
    prompt: stringAt(record.prompt, `${path}.prompt`),
    query: {
      granularity: exact(query.granularity, ["confirmed_mask", "whole_frame"], `${path}.query.granularity`),
      label: stringAt(query.label, `${path}.query.label`),
      namespace: stringAt(query.namespace, `${path}.query.namespace`),
      corpus_label: stringAt(query.corpus_label, `${path}.query.corpus_label`),
      rationale: stringAt(query.rationale, `${path}.query.rationale`),
      mask_ref: maskRef,
    },
    evidence,
    pipeline,
    metrics,
    output: {
      state: exact(output.state, ["external_precomputed_fixture", "recorded_external_output"], `${path}.output.state`),
      label: stringAt(output.label, `${path}.output.label`),
      content_ref: contentRefAt(output.content_ref, `${path}.output.content_ref`),
      rollback_ref: contentRefAt(output.rollback_ref, `${path}.output.rollback_ref`),
      caveat: stringAt(output.caveat, `${path}.output.caveat`),
    },
  };
}

function snapshotAt(value: unknown, path: string): PixelRagPreferenceSnapshot {
  const record = objectAt(value, path);
  closed(record, ["label", "model_id", "preferred_asset", "probability", "provenance"], path);
  const provenance = objectAt(record.provenance, `${path}.provenance`);
  closed(provenance, ["bundle_ref", "feature_schema_sha256", "judgments"], `${path}.provenance`);
  const probability = finiteAt(record.probability, `${path}.probability`);
  if (probability < 0 || probability > 1) throw new Error(`${path}.probability must be within [0, 1]`);
  const judgments = finiteAt(provenance.judgments, `${path}.provenance.judgments`);
  if (!Number.isSafeInteger(judgments) || judgments < 0) throw new Error(`${path}.provenance.judgments must be a non-negative safe integer`);
  return {
    label: stringAt(record.label, `${path}.label`),
    model_id: stringAt(record.model_id, `${path}.model_id`),
    preferred_asset: stringAt(record.preferred_asset, `${path}.preferred_asset`),
    probability,
    provenance: {
      bundle_ref: contentRefAt(provenance.bundle_ref, `${path}.provenance.bundle_ref`),
      feature_schema_sha256: shaAt(provenance.feature_schema_sha256, `${path}.provenance.feature_schema_sha256`),
      judgments,
    },
  };
}

export function decodePixelRagArtifact(value: unknown): PixelRagArtifact {
  const record = objectAt(value, "$pixel_rag");
  closed(record, ["format_version", "artifact_id", "evidence_status", "status_label", "source", "intents", "preference", "provenance"], "$pixel_rag");
  exact(record.format_version, [1], "$pixel_rag.format_version");
  const source = objectAt(record.source, "$pixel_rag.source");
  closed(source, ["asset_id", "label", "content_sha256", "source_page", "media", "khive"], "$pixel_rag.source");
  const intents = arrayAt(record.intents, "$pixel_rag.intents").map((intent, index) => intentAt(intent, `$pixel_rag.intents[${index}]`));
  if (intents.length !== 2 || intents[0]?.id !== "local_replace" || intents[1]?.id !== "global_restyle") {
    throw new Error("$pixel_rag.intents must contain the two declared intents in stable order");
  }
  if (new Set(intents.map((intent) => intent.query.namespace)).size !== intents.length) {
    throw new Error("$pixel_rag.intents namespaces must be distinct");
  }
  if (intents.some((intent) => intent.metrics.some((metric) => metric.source !== record.evidence_status))) {
    throw new Error("$pixel_rag metrics must match artifact evidence status");
  }
  const preference = objectAt(record.preference, "$pixel_rag.preference");
  closed(preference, ["status", "explanation", "before", "after"], "$pixel_rag.preference");
  const before = snapshotAt(preference.before, "$pixel_rag.preference.before");
  const after = snapshotAt(preference.after, "$pixel_rag.preference.after");
  if (before.model_id === after.model_id) throw new Error("$pixel_rag preference snapshots require distinct immutable model ids");
  const provenance = objectAt(record.provenance, "$pixel_rag.provenance");
  closed(provenance, ["generated_at", "source_manifest_sha256", "khive_revision", "lattice_descriptor", "run_fingerprint"], "$pixel_rag.provenance");
  return {
    format_version: 1,
    artifact_id: stringAt(record.artifact_id, "$pixel_rag.artifact_id"),
    evidence_status: exact(record.evidence_status, ["contract_fixture", "measured_run"], "$pixel_rag.evidence_status"),
    status_label: stringAt(record.status_label, "$pixel_rag.status_label"),
    source: {
      asset_id: stringAt(source.asset_id, "$pixel_rag.source.asset_id"),
      label: stringAt(source.label, "$pixel_rag.source.label"),
      content_sha256: shaAt(source.content_sha256, "$pixel_rag.source.content_sha256"),
      source_page: stringAt(source.source_page, "$pixel_rag.source.source_page"),
      media: mediaAt(source.media, "$pixel_rag.source.media"),
      khive: khiveAt(source.khive, "$pixel_rag.source.khive"),
    },
    intents,
    preference: {
      status: exact(preference.status, ["governed_snapshot_fixture", "trained_snapshot"], "$pixel_rag.preference.status"),
      explanation: stringAt(preference.explanation, "$pixel_rag.preference.explanation"),
      before,
      after,
    },
    provenance: {
      generated_at: stringAt(provenance.generated_at, "$pixel_rag.provenance.generated_at"),
      source_manifest_sha256: shaAt(provenance.source_manifest_sha256, "$pixel_rag.provenance.source_manifest_sha256"),
      khive_revision: stringAt(provenance.khive_revision, "$pixel_rag.provenance.khive_revision"),
      lattice_descriptor: stringAt(provenance.lattice_descriptor, "$pixel_rag.provenance.lattice_descriptor"),
      run_fingerprint: stringAt(provenance.run_fingerprint, "$pixel_rag.provenance.run_fingerprint"),
    },
  };
}

export const verifiedPixelRagArtifact = decodePixelRagArtifact(pixelRagArtifact);
