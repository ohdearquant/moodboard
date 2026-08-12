import { decodePixelRagArtifact, pixelRagArtifact } from "../src/pixel-rag";

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
});
