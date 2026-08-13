/*
 * Presentation-owned, compile-time demo evidence. This module has no loader and accepts no
 * view-time input: the closed decoder fails the build/tests when its literal contract drifts.
 * A measured Khive run replaces the fixture values before any empirical claim is shown.
 */
import type { ReportModel, SafeThumbnailSource } from "./model";
import pixelRagBridgeDocument from "./generated/pixel-rag-bridge.json";

export type PixelRagIntentId = "local_replace" | "global_restyle";
export type PixelRagEvidenceStatus = "contract_fixture" | "measured_run";
export type PixelRagGranularity = "confirmed_region" | "whole_frame";

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

/**
 * Resolve only report-owned, decoder-verified media. Presentation ids are useful hints, but
 * SHA-256 is the byte identity: a stale id must never display unrelated pixels. The digest
 * fallback lets a measured artifact join a separately generated offline report without
 * fabricating a shared filename convention.
 */
export function resolvePixelRagMediaSource(
  identity: Pick<PixelRagContentIdentity, "content_sha256" | "media">,
  model: ReportModel,
): SafeThumbnailSource | undefined {
  const candidate = (id: string): SafeThumbnailSource | undefined => {
    const asset = model.assetsById.get(id);
    return asset?.image?.content_sha256 === identity.content_sha256
      ? model.candidateSources.get(id)
      : undefined;
  };
  const reference = (id: string): SafeThumbnailSource | undefined => {
    const entry = model.referencesById.get(id);
    return entry?.content_sha256 === identity.content_sha256
      ? model.referenceSources.get(id)
      : undefined;
  };
  const hinted = identity.media.kind === "report_candidate"
    ? candidate(identity.media.id)
    : reference(identity.media.id);
  if (hinted) return hinted;

  const candidates = model.report.assets
    .filter((asset) => asset.image?.content_sha256 === identity.content_sha256)
    .map((asset) => candidate(asset.asset_id));
  const references = model.report.references
    .filter((entry) => entry.content_sha256 === identity.content_sha256)
    .map((entry) => reference(entry.reference_id));
  const ordered = identity.media.kind === "report_candidate"
    ? [...candidates, ...references]
    : [...references, ...candidates];
  return ordered.find((source) => source !== undefined);
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
  | "external_generation"
  | "verification"
  | "immutable_output"
  | "rollback";

export interface PixelRagPipelineStage {
  readonly id: PixelRagStageId;
  readonly label: string;
  readonly executor:
    | "content_ref_pointer"
    | "deterministic_control_plan"
    | "external_generator"
    | "human_confirmation"
    | "intent_router"
    | "khive_blob_store"
    | "khive_visual_retrieval"
    | "recorded_verifier";
  readonly detail: string;
}

export interface PixelRagMetric {
  readonly id: "precision_at_3" | "ndcg_at_5" | "mrr" | "recall_at_5" | "intent_top3_jaccard" | "outside_mask_ssim" | "content_retention" | "intent_alignment";
  readonly label: string;
  readonly value: number | null;
  readonly display: string;
  readonly target: string;
  readonly passed: boolean | null;
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
    readonly region_query_ref: string | null;
    readonly rectangle: {
      readonly height: number;
      readonly width: number;
      readonly x: number;
      readonly y: number;
    } | null;
  };
  readonly evidence: readonly PixelRagEvidenceHit[];
  readonly pipeline: readonly PixelRagPipelineStage[];
  readonly metrics: readonly PixelRagMetric[];
  readonly raw_metrics?: readonly PixelRagMetric[];
  readonly verification_status: "not_run" | "passed" | "failed";
  readonly output: {
    readonly state: "external_precomputed_fixture" | "recorded_external_output" | "not_available";
    readonly label: string;
    readonly content_ref: string | null;
    readonly rollback_ref: string;
    readonly caveat: string;
    readonly history?: readonly {
      readonly content_ref: string;
      readonly disposition: "rejected";
      readonly evidence_id: string;
      readonly verification: readonly PixelRagMetric[];
    }[];
    readonly postprocess?: {
      readonly method: "source_backed_region_overlay";
      readonly provenance_sha256: string;
      readonly revision: string;
    } | null;
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
  readonly qwen_diagnostics?: {
    readonly interpretation: string;
    readonly local_lemon_minus_apple_margin: number;
    readonly raw_cosine_is_probability: false;
    readonly restyle_content_retention: number;
    readonly style_margin: number;
    readonly validated_style_probability: false;
  };
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
    executor: "khive_visual_retrieval",
    detail: "Khive narrows immutable visual evidence inside the active intent namespace.",
  },
  {
    id: "region",
    label: "Region",
    executor: "human_confirmation",
    detail: "The query declares either a confirmed editable mask or the complete source frame.",
  },
  {
    id: "conditioning",
    label: "Conditioning",
    executor: "deterministic_control_plan",
    detail: "Intent, protected pixels, evidence refs, and constraints become a recorded edit plan.",
  },
  {
    id: "external_generation",
    label: "External generator",
    executor: "external_generator",
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
  {
    id: "rollback",
    label: "Rollback",
    executor: "content_ref_pointer",
    detail: "Rollback selects the immutable source ContentRef; it never overwrites generated bytes.",
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
        granularity: "confirmed_region",
        label: "Confirmed editable rectangle",
        namespace: "demo:replace:lemon-tree",
        corpus_label: "Lemon trees · silhouette, view, light",
        rationale: "Retrieve region-compatible lemon-tree evidence; whole-frame painting similarity would answer the wrong question.",
        region_query_ref: "sha256:760d394bf9b4350301f29fcff17f315412ad8a0ae3de9c9ef1b3a92f915bcc11",
        rectangle: { height: 0.9, width: 0.72, x: 0.18, y: 0.05 },
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
      verification_status: "passed",
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
        region_query_ref: null,
        rectangle: null,
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
      verification_status: "passed",
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
  if (!/^(?:sha256:)?[a-f0-9]{64}$/.test(resolved)) {
    throw new Error(`${path} must be a 64-hex Khive ContentRef or a legacy fixture SHA-256 ref`);
  }
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

const stageIds = ["retrieval", "region", "conditioning", "external_generation", "verification", "immutable_output", "rollback"] as const;

function stageAt(value: unknown, path: string): PixelRagPipelineStage {
  const record = objectAt(value, path);
  closed(record, ["id", "label", "executor", "detail"], path);
  return {
    id: exact(record.id, stageIds, `${path}.id`),
    label: stringAt(record.label, `${path}.label`),
    executor: exact(record.executor, ["content_ref_pointer", "deterministic_control_plan", "external_generator", "human_confirmation", "intent_router", "khive_blob_store", "khive_visual_retrieval", "recorded_verifier"], `${path}.executor`),
    detail: stringAt(record.detail, `${path}.detail`),
  };
}

function metricAt(value: unknown, path: string): PixelRagMetric {
  const record = objectAt(value, path);
  closed(record, ["id", "label", "value", "display", "target", "passed", "source"], path);
  const measured = record.value === null ? null : finiteAt(record.value, `${path}.value`);
  const passed = record.passed === null ? null : booleanAt(record.passed, `${path}.passed`);
  if (measured === null && passed !== null) {
    throw new Error(`${path}.passed cannot exist without a measured value`);
  }
  return {
    id: exact(record.id, ["precision_at_3", "ndcg_at_5", "mrr", "recall_at_5", "intent_top3_jaccard", "outside_mask_ssim", "content_retention", "intent_alignment"], `${path}.id`),
    label: stringAt(record.label, `${path}.label`),
    value: measured,
    display: stringAt(record.display, `${path}.display`),
    target: stringAt(record.target, `${path}.target`),
    passed,
    source: exact(record.source, ["contract_fixture", "measured_run"], `${path}.source`),
  };
}

function intentAt(value: unknown, path: string): PixelRagIntent {
  const record = objectAt(value, path);
  closed(record, ["id", "eyebrow", "title", "prompt", "query", "evidence", "pipeline", "metrics", "raw_metrics", "verification_status", "output"], path);
  const query = objectAt(record.query, `${path}.query`);
  closed(query, ["granularity", "label", "namespace", "corpus_label", "rationale", "region_query_ref", "rectangle"], `${path}.query`);
  const evidence = arrayAt(record.evidence, `${path}.evidence`).map((hit, index) => hitAt(hit, `${path}.evidence[${index}]`));
  if (evidence.length !== 3 || evidence.some((hit, index) => hit.rank !== index + 1)) {
    throw new Error(`${path}.evidence must have three contiguous ranks`);
  }
  const pipeline = arrayAt(record.pipeline, `${path}.pipeline`).map((stage, index) => stageAt(stage, `${path}.pipeline[${index}]`));
  if (pipeline.length !== stageIds.length || pipeline.some((stage, index) => stage.id !== stageIds[index])) {
    throw new Error(`${path}.pipeline must preserve the declared stage sequence`);
  }
  const metrics = arrayAt(record.metrics, `${path}.metrics`).map((metric, index) => metricAt(metric, `${path}.metrics[${index}]`));
  const rawMetrics = record.raw_metrics === undefined
    ? undefined
    : arrayAt(record.raw_metrics, `${path}.raw_metrics`).map((metric, index) => metricAt(metric, `${path}.raw_metrics[${index}]`));
  const output = objectAt(record.output, `${path}.output`);
  closed(output, ["state", "label", "content_ref", "rollback_ref", "caveat", "history", "postprocess"], `${path}.output`);
  const history = output.history === undefined
    ? undefined
    : arrayAt(output.history, `${path}.output.history`).map((item, index) => {
      const itemPath = `${path}.output.history[${index}]`;
      const entry = objectAt(item, itemPath);
      closed(entry, ["content_ref", "disposition", "evidence_id", "verification"], itemPath);
      const verification = arrayAt(entry.verification, `${itemPath}.verification`)
        .map((metric, metricIndex) => metricAt(
          metric,
          `${itemPath}.verification[${metricIndex}]`,
        ));
      if (!verification.some((metric) => metric.passed === false)) {
        throw new Error(`${itemPath} must retain at least one failed verifier`);
      }
      return {
        content_ref: contentRefAt(entry.content_ref, `${itemPath}.content_ref`),
        disposition: exact(entry.disposition, ["rejected"], `${itemPath}.disposition`),
        evidence_id: stringAt(entry.evidence_id, `${itemPath}.evidence_id`),
        verification,
      };
    });
  const postprocess = output.postprocess === undefined || output.postprocess === null
    ? output.postprocess
    : (() => {
      const entry = objectAt(output.postprocess, `${path}.output.postprocess`);
      closed(entry, ["method", "provenance_sha256", "revision"], `${path}.output.postprocess`);
      return {
        method: exact(entry.method, ["source_backed_region_overlay"], `${path}.output.postprocess.method`),
        provenance_sha256: shaAt(
          entry.provenance_sha256,
          `${path}.output.postprocess.provenance_sha256`,
        ),
        revision: stringAt(entry.revision, `${path}.output.postprocess.revision`),
      };
    })();
  const regionQueryRef = query.region_query_ref === null
    ? null
    : contentRefAt(query.region_query_ref, `${path}.query.region_query_ref`);
  const rectangle = query.rectangle === null
    ? null
    : (() => {
      const entry = objectAt(query.rectangle, `${path}.query.rectangle`);
      closed(entry, ["height", "width", "x", "y"], `${path}.query.rectangle`);
      const parsed = {
        height: finiteAt(entry.height, `${path}.query.rectangle.height`),
        width: finiteAt(entry.width, `${path}.query.rectangle.width`),
        x: finiteAt(entry.x, `${path}.query.rectangle.x`),
        y: finiteAt(entry.y, `${path}.query.rectangle.y`),
      };
      if (
        parsed.height <= 0 || parsed.width <= 0 || parsed.x < 0 || parsed.y < 0
        || parsed.x + parsed.width > 1 || parsed.y + parsed.height > 1
      ) {
        throw new Error(`${path}.query.rectangle must remain inside the normalized frame`);
      }
      return parsed;
    })();
  return {
    id: exact(record.id, ["local_replace", "global_restyle"], `${path}.id`),
    eyebrow: stringAt(record.eyebrow, `${path}.eyebrow`),
    title: stringAt(record.title, `${path}.title`),
    prompt: stringAt(record.prompt, `${path}.prompt`),
    query: {
      granularity: exact(query.granularity, ["confirmed_region", "whole_frame"], `${path}.query.granularity`),
      label: stringAt(query.label, `${path}.query.label`),
      namespace: stringAt(query.namespace, `${path}.query.namespace`),
      corpus_label: stringAt(query.corpus_label, `${path}.query.corpus_label`),
      rationale: stringAt(query.rationale, `${path}.query.rationale`),
      region_query_ref: regionQueryRef,
      rectangle,
    },
    evidence,
    pipeline,
    metrics,
    ...(rawMetrics === undefined ? {} : { raw_metrics: rawMetrics }),
    verification_status: exact(
      record.verification_status,
      ["not_run", "passed", "failed"],
      `${path}.verification_status`,
    ),
    output: {
      state: exact(output.state, ["external_precomputed_fixture", "recorded_external_output", "not_available"], `${path}.output.state`),
      label: stringAt(output.label, `${path}.output.label`),
      content_ref: output.content_ref === null
        ? null
        : contentRefAt(output.content_ref, `${path}.output.content_ref`),
      rollback_ref: contentRefAt(output.rollback_ref, `${path}.output.rollback_ref`),
      caveat: stringAt(output.caveat, `${path}.output.caveat`),
      ...(history === undefined ? {} : { history }),
      ...(postprocess === undefined ? {} : { postprocess }),
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
  closed(record, ["format_version", "artifact_id", "evidence_status", "status_label", "source", "intents", "qwen_diagnostics", "preference", "provenance"], "$pixel_rag");
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
  const evidenceStatus = exact(record.evidence_status, ["contract_fixture", "measured_run"], "$pixel_rag.evidence_status");
  if (intents.some((intent) => intent.metrics.some((metric) => metric.source !== evidenceStatus))) {
    throw new Error("$pixel_rag metrics must match artifact evidence status");
  }
  const preference = objectAt(record.preference, "$pixel_rag.preference");
  closed(preference, ["status", "explanation", "before", "after"], "$pixel_rag.preference");
  const before = snapshotAt(preference.before, "$pixel_rag.preference.before");
  const after = snapshotAt(preference.after, "$pixel_rag.preference.after");
  if (before.model_id === after.model_id) throw new Error("$pixel_rag preference snapshots require distinct immutable model ids");
  if (evidenceStatus === "measured_run") {
    const contentRefs = [
      khiveAt(source.khive, "$pixel_rag.source.khive").content_ref,
      ...intents.flatMap((intent) => [
        ...intent.evidence.map((hit) => hit.khive.content_ref),
        ...(intent.output.history ?? []).map((entry) => entry.content_ref),
        intent.output.content_ref,
        intent.output.rollback_ref,
      ]),
    ].filter((contentRef): contentRef is string => contentRef !== null);
    if (contentRefs.some((contentRef) => !/^[a-f0-9]{64}$/.test(contentRef))) {
      throw new Error("$pixel_rag measured evidence requires bare BLAKE3 ContentRefs from Khive");
    }
  }
  const provenance = objectAt(record.provenance, "$pixel_rag.provenance");
  closed(provenance, ["generated_at", "source_manifest_sha256", "khive_revision", "lattice_descriptor", "run_fingerprint"], "$pixel_rag.provenance");
  const qwen = record.qwen_diagnostics === undefined
    ? undefined
    : (() => {
      const entry = objectAt(record.qwen_diagnostics, "$pixel_rag.qwen_diagnostics");
      closed(entry, ["interpretation", "local_lemon_minus_apple_margin", "raw_cosine_is_probability", "restyle_content_retention", "style_margin", "validated_style_probability"], "$pixel_rag.qwen_diagnostics");
      if (entry.raw_cosine_is_probability !== false || entry.validated_style_probability !== false) {
        throw new Error("$pixel_rag.qwen_diagnostics must remain explicitly non-probabilistic");
      }
      return {
        interpretation: stringAt(entry.interpretation, "$pixel_rag.qwen_diagnostics.interpretation"),
        local_lemon_minus_apple_margin: finiteAt(entry.local_lemon_minus_apple_margin, "$pixel_rag.qwen_diagnostics.local_lemon_minus_apple_margin"),
        raw_cosine_is_probability: false as const,
        restyle_content_retention: finiteAt(entry.restyle_content_retention, "$pixel_rag.qwen_diagnostics.restyle_content_retention"),
        style_margin: finiteAt(entry.style_margin, "$pixel_rag.qwen_diagnostics.style_margin"),
        validated_style_probability: false as const,
      };
    })();
  return {
    format_version: 1,
    artifact_id: stringAt(record.artifact_id, "$pixel_rag.artifact_id"),
    evidence_status: evidenceStatus,
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
    ...(qwen === undefined ? {} : { qwen_diagnostics: qwen }),
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

export type PixelRagBridge =
  | {
      readonly artifact: null;
      readonly format_version: "moodboard.viewer-pixel-rag-bridge.v1";
      readonly generator_revision: "moodboard.pixel-rag-viewer-bridge.v1";
      readonly input: null;
      readonly state: "fallback";
    }
  | {
      readonly artifact: JsonRecord;
      readonly format_version: "moodboard.viewer-pixel-rag-bridge.v1";
      readonly generator_revision: "moodboard.pixel-rag-viewer-bridge.v1";
      readonly input: {
        readonly artifact_id: string;
        readonly byte_size: number;
        readonly canonical_sha256: string;
        readonly schema_version: "moodboard.pixel-rag-artifact.v1";
        readonly sha256: string;
      };
      readonly state: "projected";
    };

export function decodePixelRagBridge(value: unknown): PixelRagBridge {
  const record = objectAt(value, "$pixel_rag_bridge");
  closed(
    record,
    ["artifact", "format_version", "generator_revision", "input", "state"],
    "$pixel_rag_bridge",
  );
  exact(record.format_version, ["moodboard.viewer-pixel-rag-bridge.v1"], "$pixel_rag_bridge.format_version");
  exact(
    record.generator_revision,
    ["moodboard.pixel-rag-viewer-bridge.v1"],
    "$pixel_rag_bridge.generator_revision",
  );
  const state = exact(record.state, ["fallback", "projected"], "$pixel_rag_bridge.state");
  if (state === "fallback") {
    if (record.input !== null || record.artifact !== null) {
      throw new Error("$pixel_rag_bridge fallback cannot carry input or artifact data");
    }
    return {
      artifact: null,
      format_version: "moodboard.viewer-pixel-rag-bridge.v1",
      generator_revision: "moodboard.pixel-rag-viewer-bridge.v1",
      input: null,
      state,
    };
  }
  const input = objectAt(record.input, "$pixel_rag_bridge.input");
  closed(
    input,
    ["artifact_id", "byte_size", "canonical_sha256", "schema_version", "sha256"],
    "$pixel_rag_bridge.input",
  );
  const byteSize = finiteAt(input.byte_size, "$pixel_rag_bridge.input.byte_size");
  if (!Number.isSafeInteger(byteSize) || byteSize < 1) {
    throw new Error("$pixel_rag_bridge.input.byte_size must be a positive safe integer");
  }
  const artifact = objectAt(record.artifact, "$pixel_rag_bridge.artifact");
  const artifactId = shaAt(input.artifact_id, "$pixel_rag_bridge.input.artifact_id");
  if (artifact.artifact_id !== artifactId) {
    throw new Error("$pixel_rag_bridge artifact_id does not match its input pin");
  }
  return {
    artifact,
    format_version: "moodboard.viewer-pixel-rag-bridge.v1",
    generator_revision: "moodboard.pixel-rag-viewer-bridge.v1",
    input: {
      artifact_id: artifactId,
      byte_size: byteSize,
      canonical_sha256: shaAt(
        input.canonical_sha256,
        "$pixel_rag_bridge.input.canonical_sha256",
      ),
      schema_version: exact(
        input.schema_version,
        ["moodboard.pixel-rag-artifact.v1"],
        "$pixel_rag_bridge.input.schema_version",
      ),
      sha256: shaAt(input.sha256, "$pixel_rag_bridge.input.sha256"),
    },
    state,
  };
}

type ProjectedPixelRagBridge = Extract<PixelRagBridge, { readonly state: "projected" }>;

const engineStageLabels: Readonly<Record<PixelRagStageId, string>> = {
  retrieval: "Retrieval",
  region: "Region",
  conditioning: "Conditioning",
  external_generation: "External generator",
  verification: "Verification",
  immutable_output: "Immutable output",
  rollback: "Rollback",
};

function enginePipeline(value: unknown, path: string): readonly PixelRagPipelineStage[] {
  const stages = arrayAt(value, path).map((entry, index) => {
    const stagePath = `${path}[${index}]`;
    const record = objectAt(entry, stagePath);
    const id = exact(record.id, stageIds, `${stagePath}.id`);
    return {
      id,
      label: engineStageLabels[id],
      executor: exact(
        record.executor,
        ["content_ref_pointer", "deterministic_control_plan", "external_generator", "human_confirmation", "intent_router", "khive_blob_store", "khive_visual_retrieval", "recorded_verifier"],
        `${stagePath}.executor`,
      ),
      detail: stringAt(record.detail, `${stagePath}.detail`),
    } satisfies PixelRagPipelineStage;
  });
  if (stages.length !== stageIds.length || stages.some((stage, index) => stage.id !== stageIds[index])) {
    throw new Error(`${path} must preserve the engine's seven-stage sequence`);
  }
  return stages;
}

const metricLabels: Readonly<Record<PixelRagMetric["id"], string>> = {
  precision_at_3: "P@3",
  ndcg_at_5: "nDCG@5",
  mrr: "MRR",
  recall_at_5: "Recall@5",
  intent_top3_jaccard: "Top-3 overlap",
  outside_mask_ssim: "Outside-mask SSIM",
  content_retention: "Content retention",
  intent_alignment: "Intent alignment",
};

function engineRetrievalMetric(
  value: unknown,
  path: string,
  evidenceStatus: PixelRagEvidenceStatus,
): PixelRagMetric {
  const record = objectAt(value, path);
  const id = exact(record.id, ["precision_at_3", "ndcg_at_5", "mrr", "recall_at_5"], `${path}.id`);
  const source = exact(record.source, ["contract_fixture", "measured_run"], `${path}.source`);
  if (source !== evidenceStatus) throw new Error(`${path}.source does not match artifact evidence`);
  const state = exact(record.state, ["computed", "not_computed"], `${path}.state`);
  if (state === "not_computed") {
    if (record.value !== null) throw new Error(`${path}.value must be null when not computed`);
    const reason = stringAt(record.reason, `${path}.reason`);
    return {
      id,
      label: metricLabels[id],
      value: null,
      display: "Not computed",
      target: reason,
      passed: null,
      source,
    };
  }
  const measured = finiteAt(record.value, `${path}.value`);
  if (measured < 0 || measured > 1) throw new Error(`${path}.value must be within [0, 1]`);
  return {
    id,
    label: metricLabels[id],
    value: measured,
    display: measured.toFixed(3),
    target: "reported, not gated",
    passed: null,
    source,
  };
}

function engineVerificationMetric(
  value: unknown,
  path: string,
  evidenceStatus: PixelRagEvidenceStatus,
): PixelRagMetric {
  const record = objectAt(value, path);
  const id = exact(
    record.id,
    ["outside_mask_ssim", "content_retention", "intent_alignment"],
    `${path}.id`,
  );
  const source = exact(record.source, ["contract_fixture", "measured_run"], `${path}.source`);
  if (source !== evidenceStatus) throw new Error(`${path}.source does not match artifact evidence`);
  const measured = finiteAt(record.value, `${path}.value`);
  const threshold = finiteAt(record.threshold, `${path}.threshold`);
  const operator = exact(
    record.operator,
    ["greater_than_or_equal", "less_than_or_equal"],
    `${path}.operator`,
  );
  return {
    id,
    label: metricLabels[id],
    value: measured,
    display: measured.toFixed(3),
    target: `${operator === "greater_than_or_equal" ? "≥" : "≤"} ${threshold.toFixed(3)}`,
    passed: booleanAt(record.passed, `${path}.passed`),
    source,
  };
}

function engineCrossMetric(
  value: unknown,
  path: string,
  evidenceStatus: PixelRagEvidenceStatus,
): PixelRagMetric {
  const record = objectAt(value, path);
  exact(record.id, ["intent_top3_jaccard"], `${path}.id`);
  const source = exact(record.source, ["contract_fixture", "measured_run"], `${path}.source`);
  if (source !== evidenceStatus) throw new Error(`${path}.source does not match artifact evidence`);
  const measured = finiteAt(record.value, `${path}.value`);
  return {
    id: "intent_top3_jaccard",
    label: metricLabels.intent_top3_jaccard,
    value: measured,
    display: measured.toFixed(3),
    target: "routing separation · reported, not gated",
    passed: null,
    source,
  };
}

function engineEvidenceHit(
  value: unknown,
  path: string,
): PixelRagEvidenceHit {
  const record = objectAt(value, path);
  const assetId = stringAt(record.asset_id, `${path}.asset_id`);
  const collection = exact(
    record.collection,
    ["fruit-lemon", "style-claude-lorrain"],
    `${path}.collection`,
  );
  const rank = finiteAt(record.rank, `${path}.rank`);
  const sourceSearchRank = finiteAt(record.source_search_rank, `${path}.source_search_rank`);
  if (!Number.isSafeInteger(rank) || rank < 1 || !Number.isSafeInteger(sourceSearchRank)) {
    throw new Error(`${path} ranks must be positive safe integers`);
  }
  const license = objectAt(record.license, `${path}.license`);
  const score = objectAt(record.score, `${path}.score`);
  return {
    rank,
    asset_id: assetId,
    content_sha256: shaAt(record.sha256, `${path}.sha256`),
    media: { kind: "report_reference", id: assetId },
    khive: khiveAt(record.khive, `${path}.khive`),
    title: stringAt(record.title, `${path}.title`),
    creator: stringAt(record.artist, `${path}.artist`),
    license: {
      label: `${stringAt(license.short_name, `${path}.license.short_name`)} · public domain`,
      spdx_id: exact(license.id, ["CC0-1.0", "PDM-1.0"], `${path}.license.id`),
      source_page: stringAt(record.source_page_url, `${path}.source_page_url`),
    },
    score: {
      kind: exact(score.kind, ["cosine_similarity"], `${path}.score.kind`),
      value: finiteAt(score.value, `${path}.score.value`),
      descriptor_id: shaAt(
        score.descriptor_fingerprint,
        `${path}.score.descriptor_fingerprint`,
      ),
    },
    rationale: `Hard-gated to ${collection}; exact Khive search rank ${sourceSearchRank} retained.`,
  };
}

function engineOutput(
  value: unknown,
  path: string,
  rollbackRef: string,
  history: NonNullable<PixelRagIntent["output"]["history"]>,
): PixelRagIntent["output"] {
  if (value === null) {
    return {
      state: "not_available",
      label: "No external output recorded",
      content_ref: null,
      rollback_ref: rollbackRef,
      caveat: "The governed retrieval plan exists, but this artifact supplies no generator output.",
      history,
      postprocess: null,
    };
  }
  const record = objectAt(value, path);
  exact(record.state, ["precomputed_external_output"], `${path}.state`);
  const generator = objectAt(record.generator, `${path}.generator`);
  const postprocess = generator.deterministic_postprocess === undefined
    ? null
    : (() => {
      const entry = objectAt(generator.deterministic_postprocess, `${path}.generator.deterministic_postprocess`);
      closed(
        entry,
        ["method", "provenance_sha256", "revision"],
        `${path}.generator.deterministic_postprocess`,
      );
      return {
        method: exact(entry.method, ["source_backed_region_overlay"], `${path}.generator.deterministic_postprocess.method`),
        provenance_sha256: shaAt(
          entry.provenance_sha256,
          `${path}.generator.deterministic_postprocess.provenance_sha256`,
        ),
        revision: stringAt(entry.revision, `${path}.generator.deterministic_postprocess.revision`),
      };
    })();
  const registration = objectAt(record.blob_store_registration, `${path}.blob_store_registration`);
  const registrationState = exact(
    registration.state,
    ["registered", "not_registered"],
    `${path}.blob_store_registration.state`,
  );
  const contentRef = contentRefAt(record.output_content_ref, `${path}.output_content_ref`);
  if (
    registrationState === "registered"
    && contentRefAt(registration.content_ref, `${path}.blob_store_registration.content_ref`) !== contentRef
  ) {
    throw new Error(`${path} registered ContentRef does not match output bytes`);
  }
  return {
    state: "recorded_external_output",
    label: `${stringAt(generator.provider, `${path}.generator.provider`)} · ${stringAt(generator.service, `${path}.generator.service`)}`,
    content_ref: contentRef,
    rollback_ref: contentRefAt(
      objectAt(record.rollback, `${path}.rollback`).content_ref,
      `${path}.rollback.content_ref`,
    ),
    caveat: registrationState === "registered"
      ? "External generator bytes are registered under the matching immutable Khive ContentRef."
      : "External generator bytes are hashed locally and are not claimed as a Khive BlobStore record.",
    history,
    postprocess,
  };
}

function engineOutputHistory(
  value: unknown,
  path: string,
  evidenceStatus: PixelRagEvidenceStatus,
): NonNullable<PixelRagIntent["output"]["history"]> {
  return arrayAt(value, path).map((item, index) => {
    const itemPath = `${path}[${index}]`;
    const entry = objectAt(item, itemPath);
    const output = objectAt(entry.output, `${itemPath}.output`);
    const verification = objectAt(entry.verification, `${itemPath}.verification`);
    const metrics = arrayAt(verification.metrics, `${itemPath}.verification.metrics`)
      .map((metric, metricIndex) => engineVerificationMetric(
        metric,
        `${itemPath}.verification.metrics[${metricIndex}]`,
        evidenceStatus,
      ));
    if (!metrics.some((candidate) => candidate.passed === false)) {
      throw new Error(`${itemPath} must preserve at least one failed verifier`);
    }
    return {
      content_ref: contentRefAt(output.output_content_ref, `${itemPath}.output.output_content_ref`),
      disposition: exact(entry.disposition, ["rejected"], `${itemPath}.disposition`),
      evidence_id: stringAt(entry.evidence_id, `${itemPath}.evidence_id`),
      verification: metrics,
    };
  });
}

function engineIntent(
  value: unknown,
  path: string,
  evidenceStatus: PixelRagEvidenceStatus,
  crossMetric: PixelRagMetric,
  rollbackRef: string,
): PixelRagIntent {
  const record = objectAt(value, path);
  const id = exact(record.id, ["local_replace", "global_restyle"], `${path}.id`);
  const local = id === "local_replace";
  const route = objectAt(record.route, `${path}.route`);
  const query = objectAt(route.query, `${path}.route.query`);
  const hardFilter = objectAt(route.hard_filter, `${path}.route.hard_filter`);
  const expectedCollection = local ? "fruit-lemon" : "style-claude-lorrain";
  exact(hardFilter.value, [expectedCollection], `${path}.route.hard_filter.value`);
  const retrieval = objectAt(record.retrieval, `${path}.retrieval`);
  const evidence = arrayAt(retrieval.ranked_evidence, `${path}.retrieval.ranked_evidence`)
    .map((entry, index) => engineEvidenceHit(entry, `${path}.retrieval.ranked_evidence[${index}]`));
  const retrievalMetrics = arrayAt(retrieval.metrics, `${path}.retrieval.metrics`)
    .map((entry, index) => engineRetrievalMetric(
      entry,
      `${path}.retrieval.metrics[${index}]`,
      evidenceStatus,
    ));
  const rawDiagnostics = retrieval.raw_diagnostics === undefined
    ? null
    : objectAt(retrieval.raw_diagnostics, `${path}.retrieval.raw_diagnostics`);
  const rawMetrics = rawDiagnostics === null
    ? undefined
    : arrayAt(rawDiagnostics.metrics, `${path}.retrieval.raw_diagnostics.metrics`)
      .map((entry, index) => engineRetrievalMetric(
        entry,
        `${path}.retrieval.raw_diagnostics.metrics[${index}]`,
        evidenceStatus,
      ));
  const verification = objectAt(record.verification, `${path}.verification`);
  const verificationStatus = exact(
    verification.status,
    ["not_run", "passed", "failed"],
    `${path}.verification.status`,
  );
  const verificationMetrics = arrayAt(verification.metrics, `${path}.verification.metrics`)
    .map((entry, index) => engineVerificationMetric(
      entry,
      `${path}.verification.metrics[${index}]`,
      evidenceStatus,
    ));
  const plan = objectAt(record.plan, `${path}.plan`);
  const queryRef = contentRefAt(query.content_ref, `${path}.route.query.content_ref`);
  const routeRegion = local
    ? objectAt(route.region, `${path}.route.region`)
    : null;
  const rectangle = routeRegion === null
    ? null
    : {
      height: finiteAt(routeRegion.height, `${path}.route.region.height`),
      width: finiteAt(routeRegion.width, `${path}.route.region.width`),
      x: finiteAt(routeRegion.x, `${path}.route.region.x`),
      y: finiteAt(routeRegion.y, `${path}.route.region.y`),
    };
  const history = engineOutputHistory(
    record.negative_output_evidence ?? [],
    `${path}.negative_output_evidence`,
    evidenceStatus,
  );
  return {
    id,
    eyebrow: local ? "Local semantic replacement" : "Global style transfer",
    title: local ? "Replace apple tree with lemon tree" : "Restyle as Claude Lorrain",
    prompt: stringAt(record.designer_prompt, `${path}.designer_prompt`),
    query: {
      granularity: local ? "confirmed_region" : "whole_frame",
      label: local
        ? `Confirmed · ${stringAt(routeRegion?.label, `${path}.route.region.label`)}`
        : "Whole frame",
      namespace: stringAt(route.namespace, `${path}.route.namespace`),
      corpus_label: local
        ? "Lemon trees · explicit collection gate"
        : "Claude Lorrain · explicit collection gate",
      rationale: local
        ? "A confirmed region queries only governed lemon-tree evidence; global painting similarity would answer the wrong task."
        : "The whole source queries governed Claude Lorrain evidence while verification remains a separate content-preservation concern.",
      region_query_ref: local ? queryRef : null,
      rectangle,
    },
    evidence,
    pipeline: enginePipeline(plan.stages, `${path}.plan.stages`),
    metrics: [...retrievalMetrics, ...verificationMetrics, crossMetric],
    ...(rawMetrics === undefined ? {} : { raw_metrics: rawMetrics }),
    verification_status: verificationStatus,
    output: engineOutput(record.output, `${path}.output`, rollbackRef, history),
  };
}

function engineQwenDiagnostics(
  value: unknown,
  path: string,
): NonNullable<PixelRagArtifact["qwen_diagnostics"]> | undefined {
  if (value === null || value === undefined) return undefined;
  const record = objectAt(value, path);
  const contract = objectAt(record.contract, `${path}.contract`);
  if (
    contract.raw_cosine_is_probability !== false
    || contract.validated_csd_or_style_probability !== false
  ) {
    throw new Error(`${path} must remain explicitly non-probabilistic`);
  }
  const local = objectAt(
    record.local_output_region_intent_alignment,
    `${path}.local_output_region_intent_alignment`,
  );
  const retention = objectAt(
    record.restyle_content_retention,
    `${path}.restyle_content_retention`,
  );
  const style = objectAt(record.restyle_style_affinity, `${path}.restyle_style_affinity`);
  return {
    interpretation: stringAt(contract.interpretation, `${path}.contract.interpretation`),
    local_lemon_minus_apple_margin: finiteAt(
      local.mean_lemon_minus_apple_margin,
      `${path}.local_output_region_intent_alignment.mean_lemon_minus_apple_margin`,
    ),
    raw_cosine_is_probability: false,
    restyle_content_retention: finiteAt(
      retention.cosine,
      `${path}.restyle_content_retention.cosine`,
    ),
    style_margin: finiteAt(
      style.claude_minus_vangogh_margin,
      `${path}.restyle_style_affinity.claude_minus_vangogh_margin`,
    ),
    validated_style_probability: false,
  };
}

export function projectEnginePixelRagArtifact(
  bridge: ProjectedPixelRagBridge,
): PixelRagArtifact {
  const engine = bridge.artifact;
  exact(
    engine.schema_version,
    ["moodboard.pixel-rag-artifact.v1"],
    "$pixel_rag_bridge.artifact.schema_version",
  );
  const artifactId = shaAt(engine.artifact_id, "$pixel_rag_bridge.artifact.artifact_id");
  const evidenceStatus = exact(
    engine.evidence_status,
    ["contract_fixture", "measured_run"],
    "$pixel_rag_bridge.artifact.evidence_status",
  );
  const source = objectAt(engine.source, "$pixel_rag_bridge.artifact.source");
  const sourceKhive = khiveAt(source.khive, "$pixel_rag_bridge.artifact.source.khive");
  const crossMetrics = arrayAt(
    engine.cross_intent_metrics,
    "$pixel_rag_bridge.artifact.cross_intent_metrics",
  );
  if (crossMetrics.length !== 1) {
    throw new Error("$pixel_rag_bridge.artifact.cross_intent_metrics must contain one metric");
  }
  const crossMetric = engineCrossMetric(
    crossMetrics[0],
    "$pixel_rag_bridge.artifact.cross_intent_metrics[0]",
    evidenceStatus,
  );
  const intents = arrayAt(engine.intents, "$pixel_rag_bridge.artifact.intents")
    .map((entry, index) => engineIntent(
      entry,
      `$pixel_rag_bridge.artifact.intents[${index}]`,
      evidenceStatus,
      crossMetric,
      sourceKhive.content_ref,
    ));
  const descriptor = objectAt(engine.descriptor, "$pixel_rag_bridge.artifact.descriptor");
  const inference = objectAt(
    descriptor.inference,
    "$pixel_rag_bridge.artifact.descriptor.inference",
  );
  const sourceManifest = objectAt(
    engine.source_manifest,
    "$pixel_rag_bridge.artifact.source_manifest",
  );
  const provenance = objectAt(engine.provenance, "$pixel_rag_bridge.artifact.provenance");
  const qwen = engineQwenDiagnostics(
    engine.experimental_visual_embedding_diagnostics,
    "$pixel_rag_bridge.artifact.experimental_visual_embedding_diagnostics",
  );
  const artifact = {
    format_version: 1,
    artifact_id: artifactId,
    evidence_status: evidenceStatus,
    status_label: evidenceStatus === "measured_run"
      ? `Measured engine artifact · input ${bridge.input.sha256.slice(0, 12)}… · preference panel remains a governed fixture`
      : `Engine contract fixture · input ${bridge.input.sha256.slice(0, 12)}… · not an empirical claim`,
    source: {
      asset_id: stringAt(source.asset_id, "$pixel_rag_bridge.artifact.source.asset_id"),
      label: stringAt(source.title, "$pixel_rag_bridge.artifact.source.title"),
      content_sha256: shaAt(source.sha256, "$pixel_rag_bridge.artifact.source.sha256"),
      source_page: stringAt(
        source.source_page_url,
        "$pixel_rag_bridge.artifact.source.source_page_url",
      ),
      media: {
        kind: "report_candidate" as const,
        id: stringAt(source.asset_id, "$pixel_rag_bridge.artifact.source.asset_id"),
      },
      khive: sourceKhive,
    },
    intents,
    ...(qwen === undefined ? {} : { qwen_diagnostics: qwen }),
    // Preference evidence is governed by its own artifact. The bridge never upgrades this panel.
    preference: pixelRagArtifact.preference,
    provenance: {
      generated_at: stringAt(
        provenance.generated_at,
        "$pixel_rag_bridge.artifact.provenance.generated_at",
      ),
      source_manifest_sha256: shaAt(
        sourceManifest.manifest_sha256,
        "$pixel_rag_bridge.artifact.source_manifest.manifest_sha256",
      ),
      khive_revision: stringAt(
        provenance.khive_revision,
        "$pixel_rag_bridge.artifact.provenance.khive_revision",
      ),
      lattice_descriptor: `${stringAt(descriptor.model_name, "$pixel_rag_bridge.artifact.descriptor.model_name")} · ${stringAt(descriptor.pooling, "$pixel_rag_bridge.artifact.descriptor.pooling")} · ${stringAt(inference.provider, "$pixel_rag_bridge.artifact.descriptor.inference.provider")} ${stringAt(inference.version, "$pixel_rag_bridge.artifact.descriptor.inference.version")} · ${finiteAt(descriptor.dimensions, "$pixel_rag_bridge.artifact.descriptor.dimensions")}D`,
      run_fingerprint: shaAt(
        provenance.run_fingerprint,
        "$pixel_rag_bridge.artifact.provenance.run_fingerprint",
      ),
    },
  };
  return decodePixelRagArtifact(artifact);
}

export const pixelRagBridge = decodePixelRagBridge(pixelRagBridgeDocument);

export const verifiedPixelRagArtifact = pixelRagBridge.state === "fallback"
  ? decodePixelRagArtifact(pixelRagArtifact)
  : projectEnginePixelRagArtifact(pixelRagBridge);
