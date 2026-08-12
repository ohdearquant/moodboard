import { fireEvent, render, screen, within } from "@testing-library/react";

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
    expect(within(lab).getByText("Confirmed tree mask")).toBeTruthy();
    expect(within(lab).getByText("demo:replace:lemon-tree")).toBeTruthy();
    expect(within(lab).getAllByText(/Public domain|CC0/).length).toBeGreaterThanOrEqual(3);
    expect(within(lab).getByText(/external generator boundary/i)).toBeTruthy();
    expect(within(lab).getAllByText(/immutable Khive output/i).length).toBeGreaterThanOrEqual(1);

    fireEvent.click(within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i }));
    expect(within(lab).getByText("Whole frame")).toBeTruthy();
    expect(within(lab).getByText("demo:style:claude-lorrain")).toBeTruthy();
    expect(within(lab).getByText("nDCG@5")).toBeTruthy();
    expect(within(lab).getByText("Model B · learned snapshot")).toBeTruthy();
    expect(container.querySelectorAll(".pixel-evidence-card")).toHaveLength(3);
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
