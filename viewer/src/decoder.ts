import type { ErrorObject, ValidateFunction } from "ajv";
import { compareNumber, isInteger, isLosslessNumber, parse, parseLosslessNumber } from "lossless-json";

import reportV10SchemaText from "../../moodboard/schema/report_v1_0.schema.json?raw";
import reportV11SchemaText from "../../moodboard/schema/report_v1_1.schema.json?raw";
import { validateReportV10, validateReportV11 } from "./generated/report-validators.mjs";
import {
  MAX_REPORT_BYTES,
  MAX_THUMBNAIL_PROBE_CONCURRENCY,
  MAX_TOTAL_THUMBNAILS,
  MAX_TOTAL_THUMBNAIL_DECODED_BYTES,
  thumbnailLimitMessage,
} from "./limits";
import type {
  Asset,
  AxisDefinition,
  DecodeResult,
  ImageIdentity,
  ReferenceEntry,
  ReportDecoder,
  ReportIssue,
  ReportIssueCode,
  ReportModel,
  ReportOrigin,
  ReportProjection,
  SafeThumbnailSource,
  StructuralDecodeResult,
  Thumbnail,
  ThumbnailProbe,
} from "./model";

const SUPPORTED_VERSIONS = new Set(["1.0", "1.1"]);
const SAFE_INTEGER = 9_007_199_254_740_991;
const SAFE_MIMES = new Set(["image/png", "image/jpeg", "image/webp"]);
const textEncoder = new TextEncoder();

const EXPECTED_AXIS_DEFINITIONS: readonly AxisDefinition[] = [
  {
    axis_id: "style",
    label: "Style fit",
    value_kind: "conformal_p_value",
    direction: "higher_is_better_fit",
    aggregation: "full_conformal_category",
    availability: "scored_only",
    uncertainty: "asset_interval",
    method: { name: "full-conformal-p-value", revision: 1 },
  },
  {
    axis_id: "palette",
    label: "Palette distance",
    value_kind: "normalized_distance",
    direction: "lower_is_closer",
    aggregation: "mean_over_exemplars",
    availability: "all_assets",
    uncertainty: "none",
    method: { name: "palette-distance", revision: 1 },
  },
  {
    axis_id: "tone",
    label: "Tone distance",
    value_kind: "normalized_distance",
    direction: "lower_is_closer",
    aggregation: "mean_over_exemplars",
    availability: "all_assets",
    uncertainty: "none",
    method: { name: "tone-distance", revision: 1 },
  },
  {
    axis_id: "composition",
    label: "Composition distance",
    value_kind: "normalized_distance",
    direction: "lower_is_closer",
    aggregation: "mean_over_exemplars",
    availability: "all_assets",
    uncertainty: "none",
    method: { name: "composition-distance", revision: 1 },
  },
] as const;

function issue(
  severity: ReportIssue["severity"],
  code: ReportIssueCode,
  path: string,
  message: string,
): ReportIssue {
  return { severity, code, path, message };
}

function bytesCompare(left: string, right: string): number {
  const a = textEncoder.encode(left);
  const b = textEncoder.encode(right);
  const size = Math.min(a.length, b.length);
  for (let index = 0; index < size; index += 1) {
    const av = a[index] ?? 0;
    const bv = b[index] ?? 0;
    if (av !== bv) return av - bv;
  }
  return a.length - b.length;
}

function normalizeIssues(values: readonly ReportIssue[]): readonly ReportIssue[] {
  const unique = new Map<string, ReportIssue>();
  for (const value of values) {
    const key = `${value.severity}\u0000${value.code}\u0000${value.path}`;
    if (!unique.has(key)) unique.set(key, value);
  }
  return [...unique.values()].toSorted((left, right) => {
    const path = bytesCompare(left.path, right.path);
    if (path !== 0) return path;
    if (left.severity !== right.severity) return left.severity === "fatal" ? -1 : 1;
    return left.code.localeCompare(right.code);
  });
}

function pointerEscape(value: string): string {
  return value.replaceAll("~", "~0").replaceAll("/", "~1");
}

class NumericRangeError extends Error {
  constructor(readonly path: string, message: string) {
    super(message);
  }
}

function convertNumbers(value: unknown, path = ""): unknown {
  if (isLosslessNumber(value)) {
    const lexeme = value.toString();
    const numberValue = Number(lexeme);
    if (!Number.isFinite(numberValue)) {
      throw new NumericRangeError(path || "/", `Numeric token ${lexeme} is not a finite binary64 value.`);
    }
    if (isInteger(lexeme) && (!Number.isSafeInteger(numberValue) || Math.abs(numberValue) > SAFE_INTEGER)) {
      throw new NumericRangeError(path || "/", `Integer token ${lexeme} exceeds ECMAScript's exact integer range.`);
    }
    if (compareNumber(lexeme, String(numberValue)) !== 0) {
      throw new NumericRangeError(path || "/", `Numeric token ${lexeme} does not round-trip exactly through binary64.`);
    }
    return numberValue;
  }
  if (Array.isArray(value)) return value.map((item, index) => convertNumbers(item, `${path}/${index}`));
  if (typeof value === "object" && value !== null) {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = convertNumbers(item, `${path}/${pointerEscape(key)}`);
    }
    return result;
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseUntrusted(bytes: Uint8Array):
  | { readonly ok: true; readonly value: unknown; readonly version: "1.0" | "1.1" }
  | { readonly ok: false; readonly issues: readonly ReportIssue[] } {
  if (bytes.byteLength > MAX_REPORT_BYTES) {
    return {
      ok: false,
      issues: [
        issue(
          "fatal",
          "resource-limit",
          "$bytes",
          `Report exceeds the ${MAX_REPORT_BYTES}-byte transport limit.`,
        ),
      ],
    };
  }
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return { ok: false, issues: [issue("fatal", "utf8", "$bytes", "Report bytes are not strict UTF-8.")] };
  }

  let lossless: unknown;
  try {
    lossless = parse(text, null, {
      parseNumber: parseLosslessNumber,
      onDuplicateKey: ({ key }) => {
        throw new SyntaxError(`Duplicate JSON object key ${JSON.stringify(key)}.`);
      },
    });
  } catch {
    return { ok: false, issues: [issue("fatal", "json-syntax", "$bytes", "Report bytes are not one unambiguous JSON document.")] };
  }

  const version = isRecord(lossless) ? lossless.schema_version : undefined;
  if (typeof version !== "string" || !SUPPORTED_VERSIONS.has(version)) {
    const received = typeof version === "string" ? version : "missing or malformed";
    return {
      ok: false,
      issues: [issue("fatal", "version", "/schema_version", `Unsupported schema version ${received}; this viewer supports exactly 1.0 and 1.1.`)],
    };
  }

  try {
    return { ok: true, value: convertNumbers(lossless), version: version as "1.0" | "1.1" };
  } catch (error) {
    if (error instanceof NumericRangeError) {
      return { ok: false, issues: [issue("fatal", "numeric-range", error.path, error.message)] };
    }
    return { ok: false, issues: [issue("fatal", "numeric-range", "/", "A numeric token could not be represented faithfully.")] };
  }
}

const validators: Readonly<Record<"1.0" | "1.1", ValidateFunction>> = {
  "1.0": validateReportV10,
  "1.1": validateReportV11,
};

function schemaIssues(errors: readonly ErrorObject[] | null | undefined): readonly ReportIssue[] {
  return normalizeIssues((errors ?? []).map((error) => {
    const path = error.instancePath || "/";
    return issue("fatal", "schema", path, `Schema rule ${error.keyword} failed at ${path}.`);
  }));
}

function sameStringSet(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && new Set(left).size === left.length && left.every((item) => right.includes(item));
}

function deepEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function quoteShell(value: string): string {
  if (value.length === 0) return "''";
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function shlexJoin(argv: readonly string[]): string {
  return argv.map(quoteShell).join(" ");
}

function validateCrossFields(report: ReportProjection): readonly ReportIssue[] {
  const issues: ReportIssue[] = [];
  const strict = report.schema_version === "1.1";
  const referencePositions = new Map<string, number>();
  let totalThumbnailRasterBytes = 0;
  report.references.forEach((reference, index) => {
    const preflight = thumbnailPreflight(reference.thumbnail);
    totalThumbnailRasterBytes += preflight.rasterBytes;
    if (preflight.resourceMessage) {
      issues.push(
        issue("fatal", "resource-limit", `/references/${index}/thumbnail`, preflight.resourceMessage),
      );
    } else if (preflight.integrityMessage) {
      issues.push(
        issue(
          strict ? "fatal" : "diagnostic",
          "integrity",
          `/references/${index}/thumbnail`,
          preflight.integrityMessage,
        ),
      );
    }
    if (referencePositions.has(reference.reference_id)) {
      issues.push(issue("fatal", "cross-field", `/references/${index}/reference_id`, "Reference identifiers must be unique."));
    } else {
      referencePositions.set(reference.reference_id, index);
    }
  });
  if (report.board.n_references !== report.references.length) {
    issues.push(issue("fatal", "cross-field", "/board/n_references", "Board reference count does not match the reference catalogue."));
  }

  const assetsById = new Map<string, Asset>();
  report.assets.forEach((asset, assetIndex) => {
    const assetPath = `/assets/${assetIndex}`;
    if (assetsById.has(asset.asset_id)) {
      issues.push(issue("fatal", "cross-field", `${assetPath}/asset_id`, "Asset identifiers must be unique."));
    } else {
      assetsById.set(asset.asset_id, asset);
    }
    if (asset.image) {
      const preflight = thumbnailPreflight(asset.image.thumbnail);
      totalThumbnailRasterBytes += preflight.rasterBytes;
      if (preflight.resourceMessage) {
        issues.push(
          issue("fatal", "resource-limit", `${assetPath}/image/thumbnail`, preflight.resourceMessage),
        );
      } else if (preflight.integrityMessage) {
        issues.push(
          issue(
            strict ? "fatal" : "diagnostic",
            "integrity",
            `${assetPath}/image/thumbnail`,
            preflight.integrityMessage,
          ),
        );
      }
    }

    const expectedAxes = ["style", ...report.board.representation.axes];
    if (!sameStringSet(Object.keys(asset.axes), expectedAxes)) {
      issues.push(issue("fatal", "cross-field", `${assetPath}/axes`, "Asset axis keys must exactly match the board vocabulary."));
    }
    if (asset.state === "scored") {
      if (asset.interval.low > asset.interval.high) {
        issues.push(issue("fatal", "cross-field", `${assetPath}/interval`, "Interval low cannot exceed interval high."));
      }
      if (asset.score !== asset.axes.style) {
        issues.push(issue("fatal", "cross-field", `${assetPath}/axes/style`, "The style axis must exactly equal the reported score."));
      }
    } else if (asset.axes.style !== null) {
      issues.push(issue("fatal", "cross-field", `${assetPath}/axes/style`, "An abstained asset must carry a null style axis."));
    }

    const seenExemplars = new Set<string>();
    asset.exemplars.forEach((exemplar, exemplarIndex) => {
      const exemplarPath = `${assetPath}/exemplars/${exemplarIndex}`;
      if (seenExemplars.has(exemplar.reference_id)) {
        issues.push(issue("fatal", "cross-field", `${exemplarPath}/reference_id`, "Duplicate exemplar identifiers are fatal in every supported version."));
      }
      seenExemplars.add(exemplar.reference_id);
      if (!referencePositions.has(exemplar.reference_id)) {
        issues.push(issue(strict ? "fatal" : "diagnostic", "integrity", `${exemplarPath}/reference_id`, "Exemplar identifier does not resolve into the reference catalogue."));
      }
      if (strict && exemplarIndex > 0) {
        const previous = asset.exemplars[exemplarIndex - 1];
        if (previous) {
          const previousPosition = referencePositions.get(previous.reference_id) ?? Number.MAX_SAFE_INTEGER;
          const currentPosition = referencePositions.get(exemplar.reference_id) ?? Number.MAX_SAFE_INTEGER;
          if (exemplar.similarity > previous.similarity || (exemplar.similarity === previous.similarity && currentPosition < previousPosition)) {
            issues.push(issue("fatal", "cross-field", exemplarPath, "Strict exemplars must be ordered by descending similarity, then reference catalogue position."));
          }
        }
      }
    });

    const requiredCount = Math.min(3, report.references.length);
    if (strict && asset.exemplars.length !== requiredCount) {
      issues.push(issue("fatal", "integrity", `${assetPath}/exemplars`, `Report 1.1 requires exactly ${requiredCount} exemplar entries for this board.`));
    } else if (!strict && asset.exemplars.length !== 3) {
      issues.push(issue("diagnostic", "integrity", `${assetPath}/exemplars`, "Legacy report does not supply exactly three reported exemplar slots."));
    }
  });
  const thumbnailCount = report.references.length + report.assets.filter((asset) => asset.image).length;
  if (thumbnailCount > MAX_TOTAL_THUMBNAILS) {
    issues.push(
      issue(
        "fatal",
        "resource-limit",
        "/thumbnails",
        `Report carries ${thumbnailCount} thumbnails and exceeds the ${MAX_TOTAL_THUMBNAILS}-thumbnail limit.`,
      ),
    );
  }
  if (totalThumbnailRasterBytes > MAX_TOTAL_THUMBNAIL_DECODED_BYTES) {
    issues.push(
      issue(
        "fatal",
        "resource-limit",
        "/thumbnails",
        `Thumbnail rasters exceed the ${MAX_TOTAL_THUMBNAIL_DECODED_BYTES}-byte aggregate limit.`,
      ),
    );
  }

  const tiePairs = new Set<string>();
  report.comparisons.ties.forEach(([left, right], index) => {
    const path = `/comparisons/ties/${index}`;
    const leftAsset = assetsById.get(left);
    const rightAsset = assetsById.get(right);
    if (left === right || leftAsset?.state !== "scored" || rightAsset?.state !== "scored") {
      issues.push(issue("fatal", "cross-field", path, "Every tie must name two distinct scored assets."));
    }
    const key = left < right ? `${left}\u0000${right}` : `${right}\u0000${left}`;
    if (tiePairs.has(key)) issues.push(issue("fatal", "cross-field", path, "An unordered tie pair may appear only once."));
    tiePairs.add(key);
  });

  if (strict) {
    const definitions = report.board.representation.axis_definitions;
    const expectedIds = ["style", ...report.board.representation.axes];
    if (!definitions || definitions.map((item) => item.axis_id).join("\u0000") !== expectedIds.join("\u0000")) {
      issues.push(issue("fatal", "cross-field", "/board/representation/axis_definitions", "Axis definitions must follow style plus the declared axis order exactly."));
    }
    if (!definitions || !deepEqual(definitions, EXPECTED_AXIS_DEFINITIONS.filter((item) => expectedIds.includes(item.axis_id)))) {
      issues.push(issue("fatal", "cross-field", "/board/representation/axis_definitions", "Current v1.1 axis definitions do not match the frozen method table."));
    }
    if (report.provenance.argv && report.provenance.command !== shlexJoin(report.provenance.argv)) {
      issues.push(issue("fatal", "cross-field", "/provenance/command", "Command must exactly equal POSIX shlex.join(argv)."));
    }
  }
  return normalizeIssues(issues);
}

function decodeCanonicalBase64(payload: string): Uint8Array | null {
  if (payload.length === 0 || payload.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(payload)) return null;
  try {
    const raw = atob(payload);
    if (btoa(raw) !== payload) return null;
    return Uint8Array.from(raw, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

interface ImageDimensions {
  readonly width: number;
  readonly height: number;
}

function uint24le(bytes: Uint8Array, offset: number): number {
  return (bytes[offset] ?? 0) | ((bytes[offset + 1] ?? 0) << 8) | ((bytes[offset + 2] ?? 0) << 16);
}

function pngDimensions(bytes: Uint8Array): ImageDimensions | null {
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (bytes.length < 24 || !signature.every((value, index) => bytes[index] === value)) return null;
  if (String.fromCharCode(...bytes.slice(12, 16)) !== "IHDR") return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16, false), height: view.getUint32(20, false) };
}

function jpegDimensions(bytes: Uint8Array): ImageDimensions | null {
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
  const startOfFrame = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
  let offset = 2;
  while (offset + 1 < bytes.length) {
    if (bytes[offset] !== 0xff) return null;
    while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset];
    offset += 1;
    if (marker === undefined || marker === 0xd9 || marker === 0xda) return null;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length) return null;
    const length = ((bytes[offset] ?? 0) << 8) | (bytes[offset + 1] ?? 0);
    if (length < 2 || offset + length > bytes.length) return null;
    if (startOfFrame.has(marker)) {
      if (length < 7) return null;
      return {
        height: ((bytes[offset + 3] ?? 0) << 8) | (bytes[offset + 4] ?? 0),
        width: ((bytes[offset + 5] ?? 0) << 8) | (bytes[offset + 6] ?? 0),
      };
    }
    offset += length;
  }
  return null;
}

function webpDimensions(bytes: Uint8Array): ImageDimensions | null {
  if (
    bytes.length < 20 ||
    String.fromCharCode(...bytes.slice(0, 4)) !== "RIFF" ||
    String.fromCharCode(...bytes.slice(8, 12)) !== "WEBP"
  ) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 12;
  while (offset + 8 <= bytes.length) {
    const kind = String.fromCharCode(...bytes.slice(offset, offset + 4));
    const size = view.getUint32(offset + 4, true);
    const data = offset + 8;
    if (data + size > bytes.length) return null;
    if (kind === "VP8X" && size >= 10) {
      return { width: uint24le(bytes, data + 4) + 1, height: uint24le(bytes, data + 7) + 1 };
    }
    if (
      kind === "VP8 " && size >= 10 &&
      bytes[data + 3] === 0x9d && bytes[data + 4] === 0x01 && bytes[data + 5] === 0x2a
    ) {
      return {
        width: (((bytes[data + 7] ?? 0) << 8) | (bytes[data + 6] ?? 0)) & 0x3fff,
        height: (((bytes[data + 9] ?? 0) << 8) | (bytes[data + 8] ?? 0)) & 0x3fff,
      };
    }
    if (kind === "VP8L" && size >= 5 && bytes[data] === 0x2f) {
      const b1 = bytes[data + 1] ?? 0;
      const b2 = bytes[data + 2] ?? 0;
      const b3 = bytes[data + 3] ?? 0;
      const b4 = bytes[data + 4] ?? 0;
      return {
        width: 1 + b1 + ((b2 & 0x3f) << 8),
        height: 1 + (b2 >> 6) + (b3 << 2) + ((b4 & 0x0f) << 10),
      };
    }
    offset = data + size + (size % 2);
  }
  return null;
}

function headerDimensions(bytes: Uint8Array, mime: string): ImageDimensions | null {
  if (mime === "image/png") return pngDimensions(bytes);
  if (mime === "image/jpeg") return jpegDimensions(bytes);
  if (mime === "image/webp") return webpDimensions(bytes);
  return null;
}

interface ThumbnailPreflight {
  readonly source: SafeThumbnailSource | null;
  readonly resourceMessage: string | null;
  readonly integrityMessage: string | null;
  readonly rasterBytes: number;
}

function thumbnailPreflight(thumbnail: Thumbnail): ThumbnailPreflight {
  const declaredMessage = thumbnailLimitMessage(thumbnail);
  const declaredRasterBytes = thumbnail.width * thumbnail.height * 4;
  if (declaredMessage) {
    return { source: null, resourceMessage: declaredMessage, integrityMessage: null, rasterBytes: declaredRasterBytes };
  }
  if (!SAFE_MIMES.has(thumbnail.mime)) {
    return { source: null, resourceMessage: null, integrityMessage: "Thumbnail MIME is not safe to render.", rasterBytes: declaredRasterBytes };
  }
  const bytes = decodeCanonicalBase64(thumbnail.data_base64);
  if (!bytes) {
    return { source: null, resourceMessage: null, integrityMessage: "Thumbnail base64 is not canonical.", rasterBytes: declaredRasterBytes };
  }
  const dimensions = headerDimensions(bytes, thumbnail.mime);
  if (!dimensions) {
    return { source: null, resourceMessage: null, integrityMessage: "Thumbnail header does not match its declared MIME.", rasterBytes: declaredRasterBytes };
  }
  const actualMessage = thumbnailLimitMessage({ ...thumbnail, ...dimensions });
  const rasterBytes = Math.max(declaredRasterBytes, dimensions.width * dimensions.height * 4);
  if (actualMessage) {
    return { source: null, resourceMessage: actualMessage, integrityMessage: null, rasterBytes };
  }
  if (dimensions.width !== thumbnail.width || dimensions.height !== thumbnail.height) {
    return { source: null, resourceMessage: null, integrityMessage: "Thumbnail dimensions do not match its encoded header.", rasterBytes };
  }
  return {
    source: `data:${thumbnail.mime};base64,${thumbnail.data_base64}` as SafeThumbnailSource,
    resourceMessage: null,
    integrityMessage: null,
    rasterBytes,
  };
}

function safeSource(thumbnail: Thumbnail): SafeThumbnailSource | null {
  return thumbnailPreflight(thumbnail).source;
}

class BrowserThumbnailProbe implements ThumbnailProbe {
  async decode(source: SafeThumbnailSource, expectedWidth: number, expectedHeight: number): Promise<"decoded" | "undecodable"> {
    try {
      const image = new Image();
      image.src = source;
      await image.decode();
      return image.naturalWidth === expectedWidth && image.naturalHeight === expectedHeight ? "decoded" : "undecodable";
    } catch {
      return "undecodable";
    }
  }
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", textEncoder.encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(value).buffer);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

interface ProbeTarget {
  readonly key: string;
  readonly path: string;
  readonly identity: ImageIdentity | ReferenceEntry;
  readonly kind: "reference" | "candidate";
  readonly strict: boolean;
}

async function probeThumbnails(
  report: ReportProjection,
  probe: ThumbnailProbe,
): Promise<{
  readonly issues: readonly ReportIssue[];
  readonly referenceSources: ReadonlyMap<string, SafeThumbnailSource>;
  readonly candidateSources: ReadonlyMap<string, SafeThumbnailSource>;
}> {
  const issues: ReportIssue[] = [];
  const referenceSources = new Map<string, SafeThumbnailSource>();
  const candidateSources = new Map<string, SafeThumbnailSource>();
  const targets: ProbeTarget[] = [];
  const strict = report.schema_version === "1.1";
  const referencedIds = new Set(report.assets.flatMap((asset) => asset.exemplars.slice(0, 3).map((item) => item.reference_id)));

  report.references.forEach((reference, index) => {
    if (referencedIds.has(reference.reference_id)) targets.push({ key: reference.reference_id, path: `/references/${index}/thumbnail`, identity: reference, kind: "reference", strict });
  });
  if (strict) {
    report.assets.forEach((asset, index) => {
      if (asset.image) targets.push({ key: asset.asset_id, path: `/assets/${index}/image/thumbnail`, identity: asset.image, kind: "candidate", strict: true });
    });
  }

  let nextTarget = 0;
  const worker = async () => {
    while (nextTarget < targets.length) {
      const target = targets[nextTarget];
      nextTarget += 1;
      if (!target) continue;
    const source = safeSource(target.identity.thumbnail);
    if (!source) {
      issues.push(issue(target.strict ? "fatal" : "diagnostic", "integrity", target.path, "Thumbnail MIME or base64 payload is not safe to render."));
      continue;
    }
    let result: "decoded" | "undecodable";
    try {
      result = await probe.decode(source, target.identity.thumbnail.width, target.identity.thumbnail.height);
    } catch {
      issues.push(issue("fatal", "thumbnail-probe", target.path, "The browser thumbnail probe could not execute."));
      continue;
    }
    if (result !== "decoded") {
      issues.push(issue(target.strict ? "fatal" : "diagnostic", "integrity", target.path, "Thumbnail bytes did not decode to their declared dimensions and MIME."));
      continue;
    }
    if (target.kind === "reference") referenceSources.set(target.key, source);
    else candidateSources.set(target.key, source);
    }
  };
  await Promise.all(
    Array.from(
      { length: Math.min(MAX_THUMBNAIL_PROBE_CONCURRENCY, targets.length) },
      () => worker(),
    ),
  );

  return { issues: normalizeIssues(issues), referenceSources, candidateSources };
}

function makeModel(
  report: ReportProjection,
  documentSha256: string,
  origin: ReportOrigin,
  diagnostics: readonly ReportIssue[],
  referenceSources: ReadonlyMap<string, SafeThumbnailSource>,
  candidateSources: ReadonlyMap<string, SafeThumbnailSource>,
): ReportModel {
  return {
    report,
    documentSha256,
    origin,
    diagnostics,
    referencesById: new Map(report.references.map((reference) => [reference.reference_id, reference])),
    assetsById: new Map(report.assets.map((asset) => [asset.asset_id, asset])),
    referenceSources,
    candidateSources,
  };
}

export function createReportDecoder(probe: ThumbnailProbe = new BrowserThumbnailProbe()): ReportDecoder {
  const validateStructure = (bytes: Uint8Array): StructuralDecodeResult => {
    const parsed = parseUntrusted(bytes);
    if (!parsed.ok) return parsed;
    const validator = validators[parsed.version];
    if (!validator(parsed.value)) return { ok: false, issues: schemaIssues(validator.errors) };
    const projection = parsed.value as ReportProjection;
    const cross = validateCrossFields(projection);
    const fatal = cross.filter((item) => item.severity === "fatal");
    if (fatal.length > 0) return { ok: false, issues: fatal };
    return { ok: true, projection, diagnostics: cross.filter((item) => item.severity === "diagnostic") };
  };

  const decode = async (bytes: Uint8Array, origin: ReportOrigin): Promise<DecodeResult> => {
    const structural = validateStructure(bytes);
    if (!structural.ok) return structural;
    const report = structural.projection;
    const integrity: ReportIssue[] = [];
    if (report.schema_version === "1.1") {
      try {
        const expected = await sha256Hex(reportV11SchemaText);
        if (report.provenance.schema?.sha256 !== expected) {
          integrity.push(issue("fatal", "integrity", "/provenance/schema/sha256", "Report schema hash does not match the exact packaged v1.1 schema bytes."));
        }
      } catch {
        integrity.push(issue("fatal", "integrity", "/provenance/schema/sha256", "The packaged schema identity could not be verified."));
      }
    }
    const probed = await probeThumbnails(report, probe);
    integrity.push(...probed.issues);
    const fatal = normalizeIssues(integrity.filter((item) => item.severity === "fatal"));
    if (fatal.length > 0) return { ok: false, issues: fatal };
    const diagnostics = normalizeIssues([
      ...structural.diagnostics,
      ...integrity.filter((item) => item.severity === "diagnostic"),
    ]);
    let documentSha256: string;
    try {
      documentSha256 = await sha256Bytes(bytes);
    } catch {
      return {
        ok: false,
        issues: [issue("fatal", "integrity", "/", "The report byte identity could not be verified.")],
      };
    }
    return { ok: true, model: makeModel(report, documentSha256, origin, diagnostics, probed.referenceSources, probed.candidateSources) };
  };

  return { validateStructure, decode };
}

export const __test = { quoteShell, shlexJoin, validateCrossFields, decodeCanonicalBase64 };
