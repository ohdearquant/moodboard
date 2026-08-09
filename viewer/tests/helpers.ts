import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { ReportProjection, ThumbnailProbe } from "../src/model";

const fixturePath = resolve(process.cwd(), "tests", "fixtures", "showcase", "report.json");

export function fixtureBytes(): Uint8Array {
  return new Uint8Array(readFileSync(fixturePath));
}

export function fixtureObject(): Record<string, unknown> {
  return JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>;
}

export const acceptingProbe: ThumbnailProbe = {
  async decode() {
    return "decoded";
  },
};

export function encodeReport(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(value));
}

export function toLegacy(value: Record<string, unknown>): ReportProjection {
  const report = structuredClone(value) as Record<string, any>;
  report.schema_version = "1.0";
  delete report.board.representation.axis_definitions;
  for (const field of [
    "k_cap",
    "min_category_size",
    "interval_level",
    "far_outlier_iqr_multiplier",
    "far_outlier_iqr_multiplier_source",
  ]) delete report.board.fit[field];
  for (const asset of report.assets) delete asset.image;
  delete report.provenance.argv;
  delete report.provenance.schema;
  delete report.provenance.engine.source_repository;
  delete report.provenance.engine.source_revision;
  delete report.provenance.engine.source_dirty;
  return report as ReportProjection;
}
