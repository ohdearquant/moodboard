import {
  decodePixelRagArtifact,
  pixelRagBridge,
  pixelRagArtifact,
  projectPythonPrevalidatedPixelRagArtifact,
  resolvePixelRagMediaSource,
  verifiedPixelRagArtifact,
} from "../src/pixel-rag";
import type { PythonPrevalidatedProjectedPixelRagBridge } from "../src/pixel-rag";
import type { ReportModel, SafeThumbnailSource } from "../src/model";

const digest = (value: string): string => value.replace(/^sha256:/, "");

function measuredArtifact(): any {
  const artifact = structuredClone(pixelRagArtifact) as any;
  artifact.evidence_status = "measured_run";
  artifact.status_label = "Measured Khive run";
  artifact.source.khive.content_ref = digest(artifact.source.khive.content_ref);
  for (const intent of artifact.intents) {
    for (const hit of intent.evidence) hit.khive.content_ref = digest(hit.khive.content_ref);
    for (const metric of intent.metrics) metric.source = "measured_run";
    intent.output.content_ref = digest(intent.output.content_ref);
    intent.output.rollback_ref = digest(intent.output.rollback_ref);
  }
  artifact.preference.status = "trained_snapshot";
  artifact.preference.before.provenance.bundle_ref = digest(
    artifact.preference.before.provenance.bundle_ref,
  );
  artifact.preference.after.provenance.bundle_ref = digest(
    artifact.preference.after.provenance.bundle_ref,
  );
  return artifact;
}

function engineEvidence(
  rank: number,
  collection: "fruit-lemon" | "style-claude-lorrain",
): any {
  const suffix = collection === "fruit-lemon" ? rank : rank + 3;
  return {
    asset_id: `${collection}-${rank}`,
    artist: `Artist ${suffix}`,
    collection,
    image: { format: "PNG", height: 960, width: 1280 },
    khive: {
      content_ref: String(suffix).repeat(64),
      record_id: `00000000-0000-4000-8000-00000000000${suffix}`,
    },
    license: {
      id: "CC0-1.0",
      public_domain: true,
      short_name: "CC0",
      source_url: null,
      url: "https://creativecommons.org/publicdomain/zero/1.0/",
    },
    rank,
    score: {
      descriptor_fingerprint: "d".repeat(64),
      kind: "cosine_similarity",
      value: 0.9 - rank / 100,
    },
    sha256: String(suffix + 1).repeat(64),
    source_page_url: `https://example.test/${collection}/${rank}`,
    source_search_rank: rank + 2,
    title: `${collection} evidence ${rank}`,
  };
}

function engineIntent(id: "local_replace" | "global_restyle"): any {
  const local = id === "local_replace";
  const collection = local ? "fruit-lemon" : "style-claude-lorrain";
  const evidence = [1, 2, 3].map((rank) => engineEvidence(rank, collection));
  return {
    designer_prompt: local ? "Replace the selected tree." : "Restyle the whole frame.",
    id,
    negative_output_evidence: local
      ? [{
          disposition: "rejected",
          evidence_id: "external_precomputed_failed_locality_v1",
          output: {
            blob_store_registration: {
              content_ref: "1".repeat(64),
              record_id: "00000000-0000-4000-8000-000000000010",
              state: "registered",
            },
            generator: {
              execution_mode: "precomputed_external",
              provider: "OpenAI",
              service: "ImageGen through Codex built-in",
            },
            output_content_ref: "1".repeat(64),
            output_sha256: "2".repeat(64),
            rollback: {
              content_ref: "a".repeat(64),
              record_id: "00000000-0000-4000-8000-000000000001",
            },
            state: "precomputed_external_output",
          },
          verification: {
            metrics: [{
              id: "outside_mask_ssim",
              operator: "greater_than_or_equal",
              passed: false,
              source: "measured_run",
              threshold: 0.95,
              value: 0.3849,
            }, {
              id: "intent_alignment",
              method_revision: "moodboard.intent-alignment.v1",
              operator: "greater_than_or_equal",
              passed: true,
              source: "measured_run",
              threshold: 0.7,
              value: 0.8,
            }],
            status: "failed",
          },
        }]
      : [],
    output: local
      ? {
          blob_store_registration: {
            content_ref: "e".repeat(64),
            record_id: "00000000-0000-4000-8000-000000000009",
            state: "registered",
          },
          generator: {
            deterministic_postprocess: {
              method: "source_backed_region_overlay",
              provenance_sha256: "6".repeat(64),
              revision: "ffmpeg-7.1.1-mask-overlay-v1",
            },
            execution_mode: "precomputed_external",
            provider: "openai-imagegen",
            service: "Codex built-in ImageGen",
          },
          output_content_ref: "e".repeat(64),
          output_sha256: "f".repeat(64),
          rollback: {
            content_ref: "a".repeat(64),
            record_id: "00000000-0000-4000-8000-000000000001",
          },
          state: "precomputed_external_output",
        }
      : null,
    plan: {
      stages: [
        ["retrieval", "khive_visual_retrieval"],
        ["region", "human_confirmation"],
        ["conditioning", "deterministic_control_plan"],
        ["external_generation", "external_generator"],
        ["verification", "recorded_verifier"],
        ["immutable_output", "khive_blob_store"],
        ["rollback", "content_ref_pointer"],
      ].map(([stageId, executor]) => ({ detail: `${stageId} detail`, executor, id: stageId })),
    },
    retrieval: {
      metrics: [
        {
          id: "precision_at_3",
          reason: "explicit relevance judgments were not supplied",
          source: "measured_run",
          state: "not_computed",
          value: null,
        },
        {
          gain_scale: [0, 1, 2, 3],
          id: "ndcg_at_5",
          k: 5,
          source: "measured_run",
          state: "computed",
          value: 0.91,
        },
        { id: "mrr", source: "measured_run", state: "computed", value: 1 },
        {
          id: "recall_at_5",
          k: 5,
          relevant_retrieved: 3,
          source: "measured_run",
          state: "computed",
          total_relevant: 3,
          value: 1,
        },
      ],
      metrics_interpretation: "structural_routing_control_not_learned_retrieval_quality",
      raw_diagnostics: {
        exact_score_order: [
          {
            asset_id: "raw-apple",
            content_ref: "8".repeat(64),
            score: 0.9627652950584888,
            source_search_rank: 1,
          },
          {
            asset_id: "raw-lemon",
            content_ref: "7".repeat(64),
            score: 0.910125,
            source_search_rank: 2,
          },
        ],
        gate: "ungated",
        interpretation: "experimental_qwen_visual_embedding_geometry_not_probability",
        metrics: [
          { id: "precision_at_3", judged_relevant: 1, k: 3, source: "measured_run", state: "computed", value: 1 / 3 },
          { gain_scale: [0, 1, 2, 3], id: "ndcg_at_5", k: 5, source: "measured_run", state: "computed", value: 0.51 },
          { id: "mrr", source: "measured_run", state: "computed", value: 1 / 3 },
          { id: "recall_at_5", k: 5, relevant_retrieved: 2, source: "measured_run", state: "computed", total_relevant: 3, value: 2 / 3 },
        ],
        probabilistic_interpretation: false,
      },
      ranked_evidence: evidence,
    },
    route: {
      hard_filter: { field: "collection", operator: "equals", value: collection },
      namespace: `adobe-demo:${id}`,
      query: {
        content_ref: local ? "b".repeat(64) : "a".repeat(64),
        record_id: local
          ? "00000000-0000-4000-8000-000000000002"
          : "00000000-0000-4000-8000-000000000001",
        sha256: local ? "c".repeat(64) : "9".repeat(64),
      },
      query_granularity: local ? "human_confirmed_region" : "whole_frame",
      region: local
        ? {
            confirmation: {
              actor: "operator:demo",
              confirmed_at: "2026-08-12T18:55:00Z",
              method: "human_confirmed",
            },
            height: 0.9,
            kind: "normalized_rectangle",
            label: "tree",
            width: 0.8,
            x: 0.1,
            y: 0.05,
          }
        : null,
    },
    verification: local
      ? {
          metrics: [
            {
              id: "outside_mask_ssim",
              method_revision: "moodboard.outside-mask-ssim.v1",
              operator: "greater_than_or_equal",
              passed: false,
              source: "measured_run",
              threshold: 0.95,
              value: 0.38,
            },
          ],
          status: "failed",
        }
      : { metrics: [], status: "not_run" },
  };
}

function engineArtifact(): any {
  return {
    artifact_id: "8".repeat(64),
    cross_intent_metrics: [
      {
        id: "intent_top3_jaccard",
        intersection_count: 0,
        source: "measured_run",
        union_count: 6,
        value: 0,
      },
    ],
    descriptor: {
      dimensions: 1024,
      fingerprint: "d".repeat(64),
      inference: { provider: "lattice-embed", version: "0.9.0" },
      model_name: "qwen3.5-vlm-pooled-visual",
      model_revision: "Qwen/Qwen3.5-0.8B",
      pooling: "mean_visual_tokens",
    },
    evidence_status: "measured_run",
    experimental_visual_embedding_diagnostics: {
      contract: {
        interpretation: "descriptive diagnostic only; no calibrated semantic or style claim",
        kind: "experimental_qwen_visual_embedding_cosine",
        raw_cosine_is_probability: false,
        validated_csd_or_style_probability: false,
      },
      local_output_region_intent_alignment: {
        mean_apple_cosine: 0.8773,
        mean_lemon_cosine: 0.8791,
        mean_lemon_minus_apple_margin: 0.0018,
        output_role: "local_output_region_diagnostic",
      },
      restyle_content_retention: { cosine: 0.8287, output_role: "external_precomputed_global_restyle" },
      restyle_style_affinity: {
        claude_centroid_cosine: 0.847032,
        claude_minus_vangogh_margin: 0.000006,
        claude_reference_count: 4,
        output_role: "external_precomputed_global_restyle",
        vangogh_centroid_cosine: 0.847026,
        vangogh_reference_count: 4,
      },
    },
    intents: [engineIntent("local_replace"), engineIntent("global_restyle")],
    provenance: {
      generated_at: "2026-08-12T19:00:00Z",
      khive_revision: "5e7e73c0e7d8868c6a7aabbde3124ecb42289acc",
      run_fingerprint: "7".repeat(64),
    },
    schema_version: "moodboard.pixel-rag-artifact.v1",
    source: {
      asset_id: "fruit_apple_garden",
      khive: {
        content_ref: "a".repeat(64),
        record_id: "00000000-0000-4000-8000-000000000001",
      },
      sha256: "9".repeat(64),
      source_page_url: "https://example.test/apple-garden",
      title: "Apple garden",
    },
    source_manifest: { manifest_sha256: "6".repeat(64) },
  };
}

describe("Pixel RAG demo artifact", () => {
  it("consumes the Python-validated, byte-bound measured bridge shell", () => {
    expect(pixelRagBridge).toMatchObject({
      artifact: {
        artifact_id: "896fceb0adf0be1e472db9e353bddaf9be316108506f89357d6c16add0048237",
        evidence_status: "measured_run",
      },
      format_version: "moodboard.viewer-pixel-rag-bridge.v1",
      generator_revision: "moodboard.pixel-rag-viewer-bridge.v1",
      input: {
        artifact_id: "896fceb0adf0be1e472db9e353bddaf9be316108506f89357d6c16add0048237",
        canonical_sha256: "854c9e06e3138bb8e27b2f82b23926e0ce3164cddf2a14230787ea00f4983626",
        sha256: "854c9e06e3138bb8e27b2f82b23926e0ce3164cddf2a14230787ea00f4983626",
      },
      state: "projected",
    });
    expect(verifiedPixelRagArtifact.artifact_id).toBe(
      "896fceb0adf0be1e472db9e353bddaf9be316108506f89357d6c16add0048237",
    );
    expect(verifiedPixelRagArtifact.evidence_status).toBe("measured_run");
  });

  it("projects measured engine evidence without upgrading the independent preference fixture", () => {
    const artifact = engineArtifact();
    // The synthetic fixture deliberately bypasses the build-only Python trust boundary. Runtime
    // production code can project only the checked-in, Python-prevalidated bridge export.
    const bridge = {
      artifact,
      format_version: "moodboard.viewer-pixel-rag-bridge.v1",
      generator_revision: "moodboard.pixel-rag-viewer-bridge.v1",
      input: {
        artifact_id: artifact.artifact_id,
        byte_size: 1234,
        canonical_sha256: "5".repeat(64),
        schema_version: artifact.schema_version,
        sha256: "4".repeat(64),
      },
      state: "projected",
    } as unknown as PythonPrevalidatedProjectedPixelRagBridge;
    const projected = projectPythonPrevalidatedPixelRagArtifact(bridge);

    expect(projected.artifact_id).toBe(artifact.artifact_id);
    expect(projected.evidence_status).toBe("measured_run");
    expect(projected.source.khive.content_ref).toBe("a".repeat(64));
    expect(projected.intents[0]?.evidence[0]?.score.value).toBe(0.89);
    expect(projected.intents[0]?.pipeline.map((stage) => stage.id)).toEqual([
      "retrieval",
      "region",
      "conditioning",
      "external_generation",
      "verification",
      "immutable_output",
      "rollback",
    ]);
    expect(projected.intents[0]?.metrics.find((metric) => metric.id === "precision_at_3"))
      .toMatchObject({ display: "Not computed", passed: null, value: null });
    expect(projected.intents[0]?.metrics.find((metric) => metric.id === "outside_mask_ssim"))
      .toMatchObject({ display: "0.380", passed: false, target: "≥ 0.950" });
    expect(projected.intents[0]?.metrics.find((metric) => metric.id === "mrr"))
      .toMatchObject({ display: "1.000", passed: null });
    expect(projected.intents[0]?.raw_metrics?.map((metric) => metric.id)).toEqual([
      "precision_at_3",
      "ndcg_at_5",
      "mrr",
      "recall_at_5",
    ]);
    expect(projected.intents[0]?.raw_score_order).toEqual([
      {
        asset_id: "raw-apple",
        content_ref: "8".repeat(64),
        score: 0.9627652950584888,
        source_search_rank: 1,
      },
      {
        asset_id: "raw-lemon",
        content_ref: "7".repeat(64),
        score: 0.910125,
        source_search_rank: 2,
      },
    ]);
    expect(projected.intents[0]?.output.history).toEqual([
      expect.objectContaining({
        content_ref: "1".repeat(64),
        disposition: "rejected",
        verification: expect.arrayContaining([
          expect.objectContaining({ display: "0.385", passed: false }),
          expect.objectContaining({ display: "0.800", passed: true }),
        ]),
      }),
    ]);
    expect(projected.intents[0]?.output.postprocess).toMatchObject({
      method: "source_backed_region_overlay",
      provenance_sha256: "6".repeat(64),
      revision: "ffmpeg-7.1.1-mask-overlay-v1",
    });
    expect(projected.intents.map((intent) => intent.verification_status)).toEqual([
      "failed",
      "not_run",
    ]);
    expect(projected.qwen_diagnostics).toMatchObject({
      raw_cosine_is_probability: false,
      local_lemon_minus_apple_margin: 0.0018,
      style_margin: 0.000006,
      validated_style_probability: false,
    });
    expect(projected.intents[0]?.output).toMatchObject({
      content_ref: "e".repeat(64),
      state: "recorded_external_output",
    });
    expect(projected.intents[1]?.output).toMatchObject({
      content_ref: null,
      state: "not_available",
    });
    expect(projected.preference.status).toBe("governed_snapshot_fixture");
    expect(projected.status_label).toMatch(/measured engine artifact/i);
  });

  it("does not accept arbitrary engine JSON as Python-prevalidated evidence", () => {
    const artifact = engineArtifact();
    const structurallyValidButUnvalidated = {
      artifact,
      format_version: "moodboard.viewer-pixel-rag-bridge.v1",
      generator_revision: "moodboard.pixel-rag-viewer-bridge.v1",
      input: {
        artifact_id: artifact.artifact_id,
        byte_size: 1234,
        canonical_sha256: "5".repeat(64),
        schema_version: "moodboard.pixel-rag-artifact.v1",
        sha256: "4".repeat(64),
      },
      state: "projected",
    } as const;

    // @ts-expect-error arbitrary engine JSON is not Python-prevalidated
    projectPythonPrevalidatedPixelRagArtifact(structurallyValidButUnvalidated);
  });

  it("is a closed, intent-specific, provenance-complete projection", () => {
    const decoded = decodePixelRagArtifact(pixelRagArtifact);

    expect(decoded.intents).toHaveLength(2);
    expect(decoded.intents.map((intent) => intent.id)).toEqual([
      "local_replace",
      "global_restyle",
    ]);
    expect(decoded.intents.map((intent) => intent.query.granularity)).toEqual([
      "confirmed_region",
      "whole_frame",
    ]);
    expect(decoded.evidence_status).toBe("contract_fixture");
    expect(new Set(decoded.intents.map((intent) => intent.query.namespace)).size).toBe(2);

    for (const intent of decoded.intents) {
      expect(intent.evidence).toHaveLength(3);
      expect(intent.evidence.map((hit) => hit.rank)).toEqual([1, 2, 3]);
      expect(intent.evidence.every((hit) => hit.license.spdx_id.length > 0)).toBe(true);
      expect(intent.evidence.every((hit) => /^[a-f0-9]{64}$/.test(hit.content_sha256))).toBe(true);
      expect(intent.evidence.every((hit) => hit.khive.content_ref.startsWith("sha256:"))).toBe(true);
      expect(intent.evidence.every((hit) => hit.score.kind === "cosine_similarity")).toBe(true);
      expect(intent.pipeline.map((stage) => stage.id)).toEqual([
        "retrieval",
        "region",
        "conditioning",
        "external_generation",
        "verification",
        "immutable_output",
        "rollback",
      ]);
      expect(intent.pipeline.find((stage) => stage.id === "external_generation")?.executor).toBe(
        "external_generator",
      );
      expect(intent.metrics.every((metric) => metric.source === "contract_fixture")).toBe(true);
    }

    expect(decoded.preference.before.model_id).not.toBe(decoded.preference.after.model_id);
    expect(decoded.preference.after.provenance.bundle_ref.startsWith("sha256:")).toBe(true);
  });

  it("rejects unknown fields instead of silently presenting invented evidence", () => {
    const artifact = structuredClone(pixelRagArtifact) as Record<string, unknown>;
    artifact.unknown = "future field";
    expect(() => decodePixelRagArtifact(artifact)).toThrow(/unknown key/i);
  });

  it("rejects reordered or non-finite retrieval evidence", () => {
    const artifact = structuredClone(pixelRagArtifact) as any;
    artifact.intents[0].evidence[1].rank = 9;
    expect(() => decodePixelRagArtifact(artifact)).toThrow(/contiguous ranks/i);

    const nonFinite = structuredClone(pixelRagArtifact) as any;
    nonFinite.intents[1].evidence[0].score.value = Number.NaN;
    expect(() => decodePixelRagArtifact(nonFinite)).toThrow(/finite/i);
  });

  it("requires bare BLAKE3-shaped Khive ContentRefs for measured evidence", () => {
    const decoded = decodePixelRagArtifact(measuredArtifact());
    expect(decoded.source.khive.content_ref).toMatch(/^[a-f0-9]{64}$/);
    expect(decoded.intents[0]?.output.content_ref).toMatch(/^[a-f0-9]{64}$/);

    const prefixed = measuredArtifact();
    prefixed.intents[0].evidence[0].khive.content_ref =
      `sha256:${prefixed.intents[0].evidence[0].khive.content_ref}`;
    expect(() => decodePixelRagArtifact(prefixed)).toThrow(/measured.*BLAKE3 ContentRefs/i);
  });

  it("resolves report media by verified SHA-256 when presentation ids drift", () => {
    const source = "data:image/png;base64,verified" as SafeThumbnailSource;
    const asset = {
      asset_id: "real-output-v3.png",
      image: { content_sha256: "a".repeat(64) },
    };
    const model = {
      report: {
        assets: [asset],
        references: [],
      },
      assetsById: new Map([[asset.asset_id, asset]]),
      referencesById: new Map(),
      candidateSources: new Map([["real-output-v3.png", source]]),
      referenceSources: new Map(),
    } as unknown as ReportModel;

    expect(
      resolvePixelRagMediaSource(
        {
          content_sha256: "a".repeat(64),
          media: { kind: "report_candidate", id: "stale-fixture-id.png" },
        },
        model,
      ),
    ).toBe(source);
    expect(
      resolvePixelRagMediaSource(
        {
          content_sha256: "b".repeat(64),
          media: { kind: "report_candidate", id: "real-output-v3.png" },
        },
        model,
      ),
    ).toBeUndefined();
  });
});
