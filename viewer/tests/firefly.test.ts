import {
  __test,
  fireflyBridge,
  verifiedFireflyEvidence,
} from "../src/firefly";
import fireflyBridgeDocument from "../src/generated/firefly-bridge.json";

describe("measured Firefly loop bridge", () => {
  it("preserves the authenticated web, zero-credit, non-API boundary", () => {
    expect(fireflyBridge.state).toBe("projected");
    expect(fireflyBridge.format_version).toBe("moodboard.viewer-firefly-measured-loop-bridge.v2");
    expect(verifiedFireflyEvidence.capture).toEqual({
      authenticated_session: true,
      cost_display: "Uses 0 credits",
      model: "Gemini 2.5 (Nano Banana)",
      native_firefly_api: false,
      provider_boundary: "Google partner model served through Adobe Firefly",
      surface: "Adobe Firefly web Edit > Prompt",
    });
  });

  it("binds the exact immutable apple source used by replacement and restyle", () => {
    expect(verifiedFireflyEvidence.source).toEqual({
      asset_id: "fruit_apple_garden",
      byte_size: 645201,
      content_ref: "d9c1a0e3e6a5a72a9da252a0ea9fb4616c9099dd20cdc65ea00ffc29d14f23a8",
      height: 960,
      mime: "image/jpeg",
      sha256: "3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23",
      width: 1280,
    });
  });

  it.each([
    ["sha256", "f".repeat(64)],
    ["content_ref", "f".repeat(64)],
    ["width", 1279],
  ] as const)("rejects frozen source %s drift", (field, driftedValue) => {
    const drifted = JSON.parse(JSON.stringify(fireflyBridgeDocument)) as {
      evidence: { source: Record<string, unknown> };
    };
    drifted.evidence.source[field] = driftedValue;

    expect(() => __test.decodePythonPrevalidatedFireflyBridge(drifted)).toThrow(
      /source identity/u,
    );
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

  it("binds an exact outside-mask compositor invariant to source, mask, and selected bytes", () => {
    expect(verifiedFireflyEvidence.replacement.compositor_exact_outside_mask).toEqual({
      changed_pixel_count: 0,
      comparison: "decoded_rgb_u8_outside_mask",
      mask: {
        bounds_half_open_source_pixels: {
          x0_inclusive: 230,
          x1_exclusive: 1152,
          y0_inclusive: 48,
          y1_exclusive: 912,
        },
        encoding: "u8_row_major_1_inside_0_outside",
        height: 960,
        inside_pixel_count: 796608,
        outside_pixel_count: 432192,
        sha256: "09f9072f646ef8d99af30736210a57f2de448e8ca90fbff07a07edd7bd5eef4b",
        width: 1280,
      },
      max_abs_channel_error: 0,
      result: "pass",
      selected_output_sha256: "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d",
      semantics: "deterministic_compositor_invariant_not_generator_locality",
      source_sha256: "3bda38b4304152f813f6bea37dc236f95670fbea5da4731903d9ce8cfaa8ae23",
    });
  });

  it.each([
    ["changed_pixel_count", 1],
    ["max_abs_channel_error", 1],
    ["source_sha256", "f".repeat(64)],
  ] as const)("rejects exact outside-mask %s drift", (field, driftedValue) => {
    const drifted = JSON.parse(JSON.stringify(fireflyBridgeDocument)) as {
      evidence: { replacement: { compositor_exact_outside_mask: Record<string, unknown> } };
    };
    drifted.evidence.replacement.compositor_exact_outside_mask[field] = driftedValue;

    expect(() => __test.decodePythonPrevalidatedFireflyBridge(drifted)).toThrow(
      /exact outside-mask/u,
    );
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
