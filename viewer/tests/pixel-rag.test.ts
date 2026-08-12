import {
  decodePixelRagArtifact,
  pixelRagArtifact,
  resolvePixelRagMediaSource,
} from "../src/pixel-rag";
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

describe("Pixel RAG demo artifact", () => {
  it("is a closed, intent-specific, provenance-complete projection", () => {
    const decoded = decodePixelRagArtifact(pixelRagArtifact);

    expect(decoded.intents).toHaveLength(2);
    expect(decoded.intents.map((intent) => intent.id)).toEqual([
      "local_replace",
      "global_restyle",
    ]);
    expect(decoded.intents.map((intent) => intent.query.granularity)).toEqual([
      "confirmed_mask",
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
        "generator",
        "verification",
        "immutable_output",
      ]);
      expect(intent.pipeline.find((stage) => stage.id === "generator")?.executor).toBe(
        "external_or_precomputed",
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
