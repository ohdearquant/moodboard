import { createReportDecoder } from "../src/decoder";
import { MAX_REPORT_BYTES } from "../src/limits";
import { acceptingProbe, encodeReport, fixtureBytes, fixtureObject, toLegacy } from "./helpers";

const origin = { kind: "embedded", label: "test fixture" } as const;

describe("versioned report decoder", () => {
  it("accepts the real-engine v1.1 showcase with strict triptychs", async () => {
    const result = await createReportDecoder(acceptingProbe).decode(fixtureBytes(), origin);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.model.report.schema_version).toBe("1.1");
    expect(result.model.report.assets).toHaveLength(6);
    expect(result.model.report.assets.every((asset) => asset.exemplars.length === 3)).toBe(true);
    expect(result.model.referenceSources.size).toBeGreaterThanOrEqual(3);
    expect(result.model.candidateSources.size).toBe(6);
    expect(result.model.diagnostics).toEqual([]);
  });

  it("refuses an unsupported version before interpreting poisoned content", () => {
    const report = fixtureObject() as any;
    report.schema_version = "1.2";
    report.assets[0].score = "not-a-number";
    const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.issues).toEqual([
      expect.objectContaining({ code: "version", path: "/schema_version" }),
    ]);
  });

  it("rejects a duplicate exemplar in every supported version", () => {
    for (const version of ["1.1", "1.0"] as const) {
      const source = fixtureObject();
      const report: any = version === "1.1" ? source : toLegacy(source);
      report.assets[0].exemplars[1] = structuredClone(report.assets[0].exemplars[0]);
      const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.issues).toContainEqual(
          expect.objectContaining({ code: "cross-field", path: "/assets/0/exemplars/1/reference_id" }),
        );
      }
    }
  });

  it("keeps v1.0 as an explicit legacy model without candidate imagery", async () => {
    const legacy = toLegacy(fixtureObject());
    const result = await createReportDecoder(acceptingProbe).decode(encodeReport(legacy), origin);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.model.report.schema_version).toBe("1.0");
    expect(result.model.candidateSources.size).toBe(0);
    expect(result.model.report.assets.every((asset) => asset.image === undefined)).toBe(true);
  });

  it("rejects a decimal that would silently change through binary64", () => {
    const text = new TextDecoder().decode(fixtureBytes());
    const mutated = text.replace('"score": 1.0', '"score": 0.100000000000000005');
    expect(mutated).not.toBe(text);
    const result = createReportDecoder(acceptingProbe).validateStructure(new TextEncoder().encode(mutated));
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.issues[0]).toEqual(expect.objectContaining({ code: "numeric-range" }));
  });

  it("treats thumbnail probe rejection as fatal for v1.1", async () => {
    const rejectingProbe = { async decode() { return "undecodable" as const; } };
    const result = await createReportDecoder(rejectingProbe).decode(fixtureBytes(), origin);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.issues.every((entry) => entry.severity === "fatal")).toBe(true);
    expect(result.issues.some((entry) => entry.path.includes("thumbnail"))).toBe(true);
  });

  it("refuses transport and thumbnail decode work beyond the safety envelope", () => {
    const decoder = createReportDecoder(acceptingProbe);
    const oversizedBytes = { byteLength: MAX_REPORT_BYTES + 1 } as Uint8Array;
    const transport = decoder.validateStructure(oversizedBytes);
    expect(transport.ok).toBe(false);
    if (!transport.ok) {
      expect(transport.issues).toContainEqual(
        expect.objectContaining({ code: "resource-limit", path: "$bytes" }),
      );
    }

    for (const version of ["1.1", "1.0"] as const) {
      const source = fixtureObject();
      const report: any = version === "1.1" ? source : toLegacy(source);
      report.references[0].thumbnail.width = 4_097;
      report.references[0].thumbnail.height = 4_097;
      const result = decoder.validateStructure(encodeReport(report));
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.issues).toContainEqual(
          expect.objectContaining({ code: "resource-limit", path: "/references/0/thumbnail" }),
        );
      }
    }
  });
});
