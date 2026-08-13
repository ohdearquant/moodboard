import { fireEvent, render, screen, within } from "@testing-library/react";

import { __test } from "../src/App";
import { createReportDecoder } from "../src/decoder";
import type { PreferenceReplayEvidence } from "../src/preference-replay";
import { acceptingProbe, encodeReport, fixtureBytes, fixtureObject, toLegacy } from "./helpers";

const origin = { kind: "embedded", label: "showcase fixture" } as const;
const noop = () => undefined;

async function modelFor(bytes = fixtureBytes()) {
  const result = await createReportDecoder(acceptingProbe).decode(bytes, origin);
  if (!result.ok) throw new Error(JSON.stringify(result.issues));
  return result.model;
}

function preferenceEvidence(improved = true): PreferenceReplayEvidence {
  const before = 0.43;
  const after = improved ? 0.67 : 0.31;
  return {
    bindings: {
      board_entity_id: "00000000-0000-4000-8000-000000000001",
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
      adaptation_direction_observed: improved,
      mean_delta: after - before,
      mean_probability_for_policy_b_preferred_after: after,
      mean_probability_for_policy_b_preferred_before: before,
      outcome: improved ? "improvement_observed" : "no_improvement_observed",
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
      "No human preference evidence: policy-simulated labels only.",
      "No online learning: immutable snapshots are retrained.",
      "No coherence or conformal claim: preference remains separate.",
    ],
    replay_fingerprint: "d".repeat(64),
    state: "measured_replay",
    status_label: improved
      ? "Measured policy-simulated improvement on 8 frozen probes"
      : "Measured replay · no improvement observed on 8 frozen probes",
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
  };
}

describe("editorial report presentation", () => {
  it("routes the same source through two honest, intent-specific Pixel RAG views", async () => {
    const model = await modelFor();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const lab = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(within(lab).getByRole("heading", { name: "Same pixels. Different evidence." })).toBeTruthy();
    expect(
      within(lab).getByRole("button", { name: /replace apple tree with lemon tree/i }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(within(lab).getByText("Confirmed · primary apple tree canopy and trunk")).toBeTruthy();
    expect(within(lab).getByText("adobe-demo-replace-v1")).toBeTruthy();
    expect(within(lab).getAllByText(/Public domain|CC0/).length).toBeGreaterThanOrEqual(3);
    expect(within(lab).getByText(/Execute outside Moodboard\/Khive/i)).toBeTruthy();
    expect(within(lab).getByText("Immutable output")).toBeTruthy();
    expect(within(lab).getByText("Raw Qwen geometry · ungated")).toBeTruthy();
    expect(within(lab).getByText("Intent-routed control")).toBeTruthy();
    expect(within(lab).getByText(/deterministic filter integrity/i)).toBeTruthy();
    expect(within(lab).getByText("Complete ungated Qwen score order")).toBeTruthy();
    expect(within(lab).getByText("fruit_apple_meadow")).toBeTruthy();
    expect(within(lab).getByText("0.9627652950584888")).toBeTruthy();
    expect(within(lab).getByText("Rejected predecessor")).toBeTruthy();
    expect(within(lab).getByText("Source-backed deterministic composite")).toBeTruthy();
    expect(within(lab).getByText(/no exact-RGB or aesthetic claim/i)).toBeTruthy();
    expect(within(lab).getByLabelText("Experimental Qwen diagnostics")).toBeTruthy();
    expect(within(lab).getByText(/geometry—not probability or validated style/i)).toBeTruthy();

    fireEvent.click(within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i }));
    expect(within(lab).getByText("Whole frame")).toBeTruthy();
    expect(within(lab).getByText("adobe-demo-restyle-v1")).toBeTruthy();
    expect(within(lab).getByText("nDCG@5")).toBeTruthy();
    expect(within(lab).getByText(/layout constraint declared; verifier not run/i)).toBeTruthy();
    expect(within(lab).getByText("Not run")).toBeTruthy();
    expect(within(lab).getByText(/real Khive replay not frozen/i)).toBeTruthy();
    expect(within(lab).getByText(/will not substitute fixture probabilities/i)).toBeTruthy();
    expect(within(lab).queryByText("Model B · learned snapshot")).toBeNull();
    expect(container.querySelectorAll(".pixel-evidence-card")).toHaveLength(3);
  });

  it("presents a measured policy-simulated replay without human or online-learning claims", () => {
    const evidence = preferenceEvidence();

    render(<__test.PreferenceReplayPanel evidence={evidence} />);
    const panel = screen.getByRole("region", { name: "Governed preference replay" });
    expect(within(panel).getAllByText(/policy-simulated/i).length).toBeGreaterThanOrEqual(1);
    expect(within(panel).getByText("0.430")).toBeTruthy();
    expect(within(panel).getByText("0.670")).toBeTruthy();
    expect(within(panel).getByText("+0.240")).toBeTruthy();
    expect(within(panel).getByText(/8 frozen probes/i)).toBeTruthy();
    expect(within(panel).getByText(/A unchanged/i)).toBeTruthy();
    expect(within(panel).getByText(/restart exact/i)).toBeTruthy();
    expect(within(panel).getByText(/below-support refusal captured/i)).toBeTruthy();
    expect(within(panel).getByText(/No human preference evidence/i)).toBeTruthy();
    expect(within(panel).getByText(/No online learning/i)).toBeTruthy();
    expect(within(panel).getByText(/No coherence or conformal claim/i)).toBeTruthy();
    expect(within(panel).queryByText(/human feedback learned/i)).toBeNull();
  });

  it("labels a measured negative direction as no improvement", () => {
    const evidence = preferenceEvidence(false);

    render(<__test.PreferenceReplayPanel evidence={evidence} />);
    expect(screen.getAllByText(/no improvement observed/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/^improvement observed$/i)).toBeNull();
  });

  it("keeps compatibility, cohesion, diversity, uncertainty, and abstention distinct", async () => {
    const model = await modelFor();
    render(<__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />);

    expect(screen.getAllByText("Compatibility", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("Cohesion", { exact: true })).toBeTruthy();
    expect(screen.getByText("Diversity / coverage", { exact: true })).toBeTruthy();
    expect(screen.getByText("Uncertainty", { exact: true })).toBeTruthy();
    expect(screen.getByText("No style score was issued.")).toBeTruthy();
    expect(screen.getAllByText(/not approval probability/i)).toHaveLength(5);
    expect(screen.queryByText(/\d+% on.brand|approval probability:\s*\d|confidence score/i)).toBeNull();
    expect(screen.getByText(/does not carry a Khive BlobStore content_ref/i)).toBeTruthy();
    expect(screen.getByText(/FANN preference probability is not part of this report/i)).toBeTruthy();
  });

  it("keeps overview measurements compact enough for the four-column desktop strip", async () => {
    const model = await modelFor();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const strip = container.querySelector(".story-strip");
    expect(strip).not.toBeNull();
    expect(within(strip as HTMLElement).getByText("0.5417", { exact: true })).toBeTruthy();
    expect(within(strip as HTMLElement).queryByText("0.5416666666666666", { exact: true })).toBeNull();
    for (const measurement of strip?.querySelectorAll("strong") ?? []) {
      expect((measurement.textContent ?? "").length).toBeLessThanOrEqual(15);
    }
  });

  it("shows the candidate beside three simultaneous references", async () => {
    const model = await modelFor();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );
    const firstCard = container.querySelector(".asset-card");
    expect(firstCard).not.toBeNull();
    const scoped = within(firstCard as HTMLElement);
    expect(scoped.getByText("Candidate", { exact: true })).toBeTruthy();
    expect(scoped.getByText("Closest references, together")).toBeTruthy();
    expect(firstCard?.querySelectorAll(".reference-cell")).toHaveLength(3);
    expect(firstCard?.querySelectorAll(".reference-cell img")).toHaveLength(3);
    expect(firstCard?.querySelector(".candidate-frame img")).not.toBeNull();
  });

  it("renders free text literally and never turns a source into a link or executable node", async () => {
    const report: any = fixtureObject();
    const payload = '</script><script data-unsafe="true">globalThis.pwned=true</script>';
    report.board.name = payload;
    report.assets[0].source = payload;
    const model = await modelFor(encodeReport(report));
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );
    expect((container.textContent ?? "").split(payload).length - 1).toBeGreaterThanOrEqual(2);
    expect(container.querySelector("script[data-unsafe]")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });

  it("preserves the permanent v1.0 diagnostic presentation", async () => {
    const model = await modelFor(encodeReport(toLegacy(fixtureObject())));
    render(<__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />);
    expect(screen.getAllByText("Candidate image was not included in report version 1.0.")).toHaveLength(6);
    expect(screen.getAllByText("Legacy axis values")).toHaveLength(6);
    expect(screen.getByText("Schema hash was not recorded in report version 1.0.")).toBeTruthy();
  });
});
