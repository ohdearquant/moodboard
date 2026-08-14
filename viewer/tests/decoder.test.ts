import { createReportDecoder } from "../src/decoder";
import { MAX_REPORT_BYTES } from "../src/limits";
import { acceptingProbe, encodeReport, fixtureBytes, fixtureObject, toLegacy } from "./helpers";

const origin = { kind: "embedded", label: "test fixture" } as const;

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

describe("versioned report decoder", () => {
  it("accepts the real-engine v1.1 showcase with strict triptychs", async () => {
    const result = await createReportDecoder(acceptingProbe).decode(fixtureBytes(), origin);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.model.report.schema_version).toBe("1.1");
    expect(result.model.documentSha256).toBe(await sha256(fixtureBytes()));
    expect(result.model.report.assets).toHaveLength(6);
    expect(result.model.report.assets.every((asset) => asset.exemplars.length === 3)).toBe(true);
    expect(result.model.referenceSources.size).toBeGreaterThanOrEqual(3);
    expect(result.model.candidateSources.size).toBe(6);
    expect(result.model.diagnostics).toEqual([]);
  });

  it("reports real validation phases and decoded-image progress in order", async () => {
    const progress: Array<{ phase: string; completed?: number; total?: number }> = [];
    const result = await createReportDecoder(acceptingProbe).decode(
      fixtureBytes(),
      origin,
      (event) => progress.push(event),
    );

    expect(result.ok).toBe(true);
    expect(progress.slice(0, 2).map((event) => event.phase)).toEqual(["schema", "hash"]);
    expect(progress.at(-1)?.phase).toBe("bindings");
    const imageProgress = progress.filter((event) => event.phase === "images");
    const total = imageProgress[0]?.total;
    expect(total).toBeGreaterThan(0);
    expect(imageProgress[0]).toEqual({ phase: "images", completed: 0, total });
    expect(imageProgress.at(-1)).toEqual({ phase: "images", completed: total, total });
    expect(imageProgress.map((event) => event.completed)).toEqual(
      Array.from({ length: (total ?? 0) + 1 }, (_, index) => index),
    );
  });

  it("binds the model to exact source bytes, not merely the decoded projection", async () => {
    const canonical = fixtureBytes();
    const alternate = new TextEncoder().encode(`${new TextDecoder().decode(canonical)}\n`);
    const first = await createReportDecoder(acceptingProbe).decode(canonical, origin);
    const second = await createReportDecoder(acceptingProbe).decode(alternate, origin);
    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    if (!first.ok || !second.ok) return;
    expect(first.model.report).toEqual(second.model.report);
    expect(first.model.documentSha256).not.toBe(second.model.documentSha256);
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

  it("checks encoded header dimensions before native decode in every version", () => {
    const header = new Uint8Array(24);
    header.set([137, 80, 78, 71, 13, 10, 26, 10], 0);
    header.set([73, 72, 68, 82], 12);
    new DataView(header.buffer).setUint32(16, 32_768, false);
    new DataView(header.buffer).setUint32(20, 32_768, false);
    const payload = Buffer.from(header).toString("base64");

    for (const version of ["1.1", "1.0"] as const) {
      const source = fixtureObject();
      const report: any = version === "1.1" ? source : toLegacy(source);
      report.references[0].thumbnail.width = 1;
      report.references[0].thumbnail.height = 1;
      report.references[0].thumbnail.data_base64 = payload;
      const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.issues).toContainEqual(
          expect.objectContaining({ code: "resource-limit", path: "/references/0/thumbnail" }),
        );
      }
    }
  });

  it("preflights governed JPEG and WebP headers without native decoding", () => {
    const fixtures = [
      {
        mime: "image/jpeg",
        payload: "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAMDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDyOiiiuw5D/9k=",
      },
      {
        mime: "image/webp",
        payload: "UklGRjAAAABXRUJQVlA4ICQAAABQAQCdASoDAAIAAUAmJQBOgCgAAP76id+R2EN2HLri5shvAAA=",
      },
    ];
    for (const fixture of fixtures) {
      const report: any = fixtureObject();
      report.references[0].thumbnail = {
        mime: fixture.mime,
        width: 3,
        height: 2,
        data_base64: fixture.payload,
      };
      const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
      expect(result.ok).toBe(true);
    }
  });

  it("bounds the aggregate declared thumbnail raster budget", () => {
    const report: any = toLegacy(fixtureObject());
    for (const reference of report.references) {
      reference.thumbnail.width = 4_096;
      reference.thumbnail.height = 4_096;
    }
    const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues).toContainEqual(
        expect.objectContaining({ code: "resource-limit", path: "/thumbnails" }),
      );
    }
  });

  it("bounds the total native thumbnail target count", () => {
    const report: any = fixtureObject();
    const template = report.assets[0];
    report.assets = Array.from({ length: 513 }, (_, index) => ({
      ...structuredClone(template),
      asset_id: `candidate-${index}`,
      rank: index + 1,
    }));
    report.comparisons.ties = [];
    const result = createReportDecoder(acceptingProbe).validateStructure(encodeReport(report));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.issues).toContainEqual(
        expect.objectContaining({ code: "resource-limit", path: "/thumbnails" }),
      );
    }
  });

  it("bounds concurrent native thumbnail probes", async () => {
    let active = 0;
    let peak = 0;
    let calls = 0;
    const observingProbe = {
      async decode() {
        active += 1;
        peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        active -= 1;
        calls += 1;
        return "decoded" as const;
      },
    };

    const result = await createReportDecoder(observingProbe).decode(fixtureBytes(), origin);
    expect(result.ok).toBe(true);
    expect(calls).toBeGreaterThan(4);
    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThanOrEqual(4);
  });
});
