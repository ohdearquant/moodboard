import {
  fireflyBridge,
  verifiedFireflyEvidence,
} from "../src/firefly";

describe("measured Firefly loop bridge", () => {
  it("preserves the authenticated web, zero-credit, non-API boundary", () => {
    expect(fireflyBridge.state).toBe("projected");
    expect(verifiedFireflyEvidence.capture).toEqual({
      authenticated_session: true,
      cost_display: "Uses 0 credits",
      model: "Gemini 2.5 (Nano Banana)",
      native_firefly_api: false,
      provider_boundary: "Google partner model served through Adobe Firefly",
      surface: "Adobe Firefly web Edit > Prompt",
    });
  });

  it("keeps raw failure separate from compositor preservation", () => {
    const timeline = verifiedFireflyEvidence.replacement.timeline;
    expect(timeline.at(-2)).toMatchObject({
      decision: "fail",
      outside_mask_ssim: 0.174819482254,
      output_sha256: "8e33e4e6485ab776d5794cfa8ebba2f20687dfc391de1cd97d587bb4e3632f27",
    });
    expect(timeline.at(-1)).toMatchObject({
      decision: "pass",
      outside_mask_ssim: 1,
      output_sha256: "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d",
      pass_semantics: "deterministic_preservation_constraint_not_intrinsic_generator_locality",
    });
    expect(timeline.at(-2)?.preview?.source.startsWith("data:image/jpeg;base64,")).toBe(true);
    expect(timeline.at(-1)?.preview?.source.startsWith("data:image/jpeg;base64,")).toBe(true);
  });

  it("keeps restyle acceptance open and binds real Khive/Lattice restart evidence", () => {
    expect(verifiedFireflyEvidence.restyle).toMatchObject({
      acceptance_decision: "not_computed",
      output_sha256: "930dd8ddfb4fafcf724027fbeee652f19fe56233994926dc0f7a44186510b45a",
    });
    expect(verifiedFireflyEvidence.khive.descriptor.inference).toEqual({
      provider: "lattice-embed",
      version: "0.9.0",
    });
    expect(verifiedFireflyEvidence.khive.restart).toMatchObject({
      canonical_search_byte_exact: true,
      first_search_sha256: "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
      restart_search_sha256: "18f9f1b4cd289834ee6aaa50d4f5076c1bd048edea3b0e3d94ff0c99fedd1b48",
    });
  });
});
