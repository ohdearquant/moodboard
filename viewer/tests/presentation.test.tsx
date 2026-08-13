import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import { __test } from "../src/App";
import { createReportDecoder } from "../src/decoder";
import { acceptingProbe, encodeReport, fixtureBytes, fixtureObject, toLegacy } from "./helpers";

const origin = { kind: "embedded", label: "showcase fixture" } as const;
const noop = () => undefined;

async function modelFor(bytes = fixtureBytes()) {
  const result = await createReportDecoder(acceptingProbe).decode(bytes, origin);
  if (!result.ok) throw new Error(JSON.stringify(result.issues));
  return result.model;
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
    expect(within(lab).queryByRole("region", { name: "Governed preference replay" })).toBeNull();
    expect(screen.getByRole("region", { name: "Governed preference replay" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Measured Firefly iteration loop" })).toBeTruthy();
    expect(container.querySelectorAll(".pixel-evidence-card")).toHaveLength(3);
  });

  it("keeps compatibility, cohesion, diversity, uncertainty, and abstention distinct", async () => {
    const model = await modelFor();
    const abstained = model.report.assets.find((asset) => asset.state === "abstained");
    render(
      <__test.ReportView
        model={model}
        activeId={abstained?.asset_id ?? null}
        filter="all"
        dispatch={noop}
        onFile={noop}
      />,
    );

    expect(screen.getAllByText("Compatibility", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("Cohesion", { exact: true })).toBeTruthy();
    expect(screen.getByText("Diversity / coverage", { exact: true })).toBeTruthy();
    expect(screen.getByText("Uncertainty", { exact: true })).toBeTruthy();
    expect(screen.getByText("No style score was issued.")).toBeTruthy();
    expect(screen.getAllByText(/conformal p /i).length).toBeGreaterThanOrEqual(1);
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

  it("leads with every governed reference and candidate while mounting one detail card", async () => {
    const model = await modelFor();
    const dispatch = vi.fn();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={dispatch} onFile={noop} />,
    );

    const references = screen.getByRole("region", { name: "Governed reference board" });
    expect(
      within(references).getByRole("heading", {
        name: `Governed reference board · ${model.report.references.length} immutable references`,
      }),
    ).toBeTruthy();
    const referenceTiles = within(references).getAllByTestId("governed-reference-tile");
    expect(referenceTiles).toHaveLength(model.report.references.length);
    expect(referenceTiles.map((tile) => tile.querySelector("strong")?.textContent)).toEqual(
      model.report.references.map((reference) => reference.reference_id),
    );

    const candidates = screen.getByRole("region", {
      name: `${model.report.assets.length} candidate overview`,
    });
    expect(references.nextElementSibling).toBe(candidates);
    const tiles = within(candidates).getAllByRole("button");
    expect(tiles).toHaveLength(model.report.assets.length);
    expect(new Set(tiles.map((tile) => tile.getAttribute("data-asset-id"))).size).toBe(
      model.report.assets.length,
    );
    expect(within(candidates).getByRole("button", { name: /06_far_outlier\.png/i }).textContent).toContain("REFUSED");
    expect(container.querySelectorAll(".asset-card")).toHaveLength(1);
    fireEvent.click(tiles[3]!);
    expect(dispatch).toHaveBeenCalledWith({ type: "inspect", assetId: tiles[3]!.getAttribute("data-asset-id") });

    expect(
      screen.getByText(
        `Verified · ${model.report.references.length} governed references · ${model.report.assets.length} candidate outcomes · 5 scored · 1 abstained · engine rank preserved · no merged preference score`,
      ),
    ).toBeTruthy();
    const audit = screen.getByText("Compatibility audit & comparisons").closest("details");
    expect(audit?.hasAttribute("open")).toBe(false);
  });

  it("renders the real policy-simulated replay as a separate eight-probe mechanism", async () => {
    const model = await modelFor();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const pixel = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(within(pixel).queryByRole("region", { name: "Governed preference replay" })).toBeNull();
    const panel = screen.getByRole("region", { name: "Governed preference replay" });
    const firefly = screen.getByRole("region", { name: "Measured Firefly iteration loop" });
    expect(pixel.nextElementSibling).toBe(firefly);
    expect(panel.previousElementSibling).toBe(firefly);
    expect(
      within(panel).getByText(
        "Independent preference mechanism replay · policy-simulated · not trained on these Firefly outputs",
      ),
    ).toBeTruthy();
    expect(within(panel).getByText("0.000248")).toBeTruthy();
    expect(within(panel).getByText("0.501136")).toBeTruthy();
    expect(within(panel).getByText("+0.500888")).toBeTruthy();
    expect(within(panel).getByText(/\+96 judgments/)).toBeTruthy();
    expect(within(panel).getAllByTestId("preference-probe-row")).toHaveLength(8);
    expect(within(panel).getByText("style_vangogh_cypresses--center-crop-90pct.png")).toBeTruthy();
    expect(within(panel).getByText(/FANN A\+B.*A probe predictions value-exact.*restart predictions exact/)).toBeTruthy();
    const audit = within(panel).getByText("Audit & identity").closest("details");
    expect(audit?.hasAttribute("open")).toBe(false);
    expect(container.querySelectorAll(".preference-measured")).toHaveLength(1);
  });

  it("shows the frozen Firefly loop between Pixel RAG and the independent preference replay", async () => {
    const model = await modelFor();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const pixel = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    const firefly = screen.getByRole("region", { name: "Measured Firefly iteration loop" });
    const preference = screen.getByRole("region", { name: "Governed preference replay" });
    expect(pixel.nextElementSibling).toBe(firefly);
    expect(firefly.nextElementSibling).toBe(preference);
    expect(within(firefly).getByText("Frozen Firefly iteration · measured verification")).toBeTruthy();
    expect(within(firefly).getByText("Adobe Firefly web Edit > Prompt")).toBeTruthy();
    expect(
      within(firefly).getByText("Gemini 2.5 (Nano Banana) · Google partner model via Adobe Firefly"),
    ).toBeTruthy();
    expect(within(firefly).getByText(/Uses 0 credits.*captured display for this run/i)).toBeTruthy();
    expect(within(firefly).getByText(/No native Firefly API was used in this capture/i)).toBeTruthy();
    expect(within(firefly).getByText("Canonical search-result bytes match across process restart.")).toBeTruthy();
    expect(within(firefly).getByText(/lattice-embed 0\.9\.0 \(Qwen visual checkpoint\)/)).toBeTruthy();
    expect(within(firefly).getByText(/0\.174819482254.*0\.95.*FAIL/i)).toBeTruthy();
    expect(within(firefly).getByText(/1\.0.*PASS by construction/i)).toBeTruthy();
    expect(within(firefly).getByText(/Restyle acceptance.*not computed/i)).toBeTruthy();
    expect(within(firefly).getAllByText(/not attached as direct generator image references/i).length).toBeGreaterThanOrEqual(1);
    expect(within(firefly).getByText(/premium Gemini 3\.1 model was not used/i)).toBeTruthy();

    const previews = within(firefly).getAllByRole("img");
    expect(previews).toHaveLength(3);
    for (const preview of previews) {
      expect(preview.getAttribute("width")).toMatch(/^\d+$/u);
      expect(preview.getAttribute("height")).toMatch(/^\d+$/u);
      expect(preview.getAttribute("loading")).toBe("lazy");
      expect(preview.getAttribute("decoding")).toBe("async");
    }
    const audit = within(firefly).getByText("Audit & identity").closest("details");
    expect(audit?.hasAttribute("open")).toBe(false);
    expect(within(audit as HTMLElement).getByText(/Transport binary/)).toBeTruthy();
    expect(container.querySelectorAll(".firefly-measured-loop")).toHaveLength(1);
  });

  it("disables outcome filters that would leave the selected detail empty", () => {
    render(<__test.FilterBar filter="all" dispatch={noop} scored={24} abstained={0} />);
    expect(screen.getByRole("button", { name: "Abstained 0" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Ranked 24" }).hasAttribute("disabled")).toBe(false);
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
    expect(screen.getAllByText("Candidate image was not included in report version 1.0.")).toHaveLength(1);
    expect(screen.getAllByText("Legacy axis values")).toHaveLength(1);
    expect(screen.getByText("Schema hash was not recorded in report version 1.0.")).toBeTruthy();
  });
});
