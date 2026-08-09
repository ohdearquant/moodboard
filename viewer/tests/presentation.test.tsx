import { render, screen, within } from "@testing-library/react";

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
