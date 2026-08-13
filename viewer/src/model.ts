export type SchemaVersion = "1.0" | "1.1";

export type ReportOrigin =
  | { readonly kind: "embedded"; readonly label: string }
  | { readonly kind: "local-file"; readonly label: string };

export type ReportIssueCode =
  | "source-read"
  | "utf8"
  | "json-syntax"
  | "version"
  | "schema"
  | "cross-field"
  | "numeric-range"
  | "resource-limit"
  | "thumbnail-probe"
  | "integrity";

export interface ReportIssue {
  readonly severity: "fatal" | "diagnostic";
  readonly code: ReportIssueCode;
  readonly path: string;
  readonly message: string;
}

export interface Thumbnail {
  readonly mime: string;
  readonly width: number;
  readonly height: number;
  readonly data_base64: string;
}

export interface ImageIdentity {
  readonly content_sha256: string;
  readonly mime: string;
  readonly width: number;
  readonly height: number;
  readonly thumbnail: Thumbnail;
}

export interface ReferenceEntry extends ImageIdentity {
  readonly reference_id: string;
}

export interface Exemplar {
  readonly reference_id: string;
  readonly similarity: number;
}

export interface Interval {
  readonly low: number;
  readonly high: number;
  readonly level: number;
  readonly method: "loo-jackknife-plus";
}

export interface AxisMethod {
  readonly name: string;
  readonly revision: number;
}

export interface AxisDefinition {
  readonly axis_id: string;
  readonly label: string;
  readonly value_kind: "conformal_p_value" | "normalized_distance";
  readonly direction: "higher_is_better_fit" | "lower_is_closer";
  readonly aggregation: "full_conformal_category" | "mean_over_exemplars";
  readonly availability: "scored_only" | "all_assets";
  readonly uncertainty: "asset_interval" | "none";
  readonly method: AxisMethod;
}

export interface StyleModelInfo {
  readonly model: string;
  readonly revision: string;
  readonly dim: number;
}

export interface Representation {
  readonly style: StyleModelInfo;
  readonly axes: readonly string[];
  readonly axis_definitions?: readonly AxisDefinition[];
}

export interface FitInterval {
  readonly method: "loo-jackknife-plus";
  readonly replicates: null;
  readonly seed: number;
}

export interface BoardFit {
  readonly metric: "cosine";
  readonly k: number;
  readonly cluster_cut: number;
  readonly dup_cut: number;
  readonly interval: FitInterval;
  readonly [key: string]: unknown;
}

export interface Category {
  readonly category_id: string;
  readonly n_local: number;
  readonly member_ids: readonly string[];
}

export interface Board {
  readonly id: string;
  readonly name: string;
  readonly n_references: number;
  readonly n_eff: number;
  readonly requested_alpha: number;
  readonly supported_alpha: number;
  readonly built_at: string;
  readonly representation: Representation;
  readonly fit: BoardFit;
  readonly categories: readonly Category[];
}

export interface Tightness {
  readonly loo_mean: number;
  readonly loo_sd: number;
  readonly loo_quantiles: Readonly<Record<"p10" | "p50" | "p90", number>>;
}

export interface Leverage {
  readonly reference_id: string;
  readonly delta_tightness: number;
  readonly rank: number;
}

export interface BoardStats {
  readonly tightness: Tightness;
  readonly leverage: readonly Leverage[];
  readonly flags: readonly string[];
}

interface AssetBase {
  readonly asset_id: string;
  readonly source: string;
  readonly category_id: string;
  readonly axes: Readonly<Record<string, number | null>>;
  readonly exemplars: readonly Exemplar[];
  readonly flags: readonly string[];
  readonly image?: ImageIdentity;
}

export interface ScoredAsset extends AssetBase {
  readonly state: "scored";
  readonly n_local: number;
  readonly score: number;
  readonly interval: Interval;
  readonly rank: number;
}

export interface AbstainedAsset extends AssetBase {
  readonly state: "abstained";
  readonly reason: "resolution" | "multi_modality" | "far_outlier";
  readonly explanation: string;
  readonly measurement: Readonly<Record<string, unknown>>;
}

export type Asset = ScoredAsset | AbstainedAsset;

export interface Comparisons {
  readonly ties: readonly (readonly [string, string])[];
  readonly note: string;
}

export interface EngineProvenance {
  readonly name: string;
  readonly version: string;
  readonly source_repository?: string;
  readonly source_revision?: string;
  readonly source_dirty?: boolean;
}

export interface ModelProvenance {
  readonly repo: string;
  readonly revision: string;
  readonly sha256: string;
}

export interface SchemaProvenance {
  readonly id: string;
  readonly sha256: string;
}

export interface Provenance {
  readonly engine: EngineProvenance;
  readonly model: ModelProvenance;
  readonly command: string;
  readonly argv?: readonly string[];
  readonly seed: number;
  readonly created_at: string;
  readonly schema?: SchemaProvenance;
}

export interface ReportProjection {
  readonly schema_version: SchemaVersion;
  readonly board: Board;
  readonly board_stats: BoardStats;
  readonly references: readonly ReferenceEntry[];
  readonly assets: readonly Asset[];
  readonly comparisons: Comparisons;
  readonly provenance: Provenance;
}

declare const safeThumbnailSource: unique symbol;
export type SafeThumbnailSource = string & { readonly [safeThumbnailSource]: true };

export interface ReportModel {
  readonly report: ReportProjection;
  readonly documentSha256: string;
  readonly origin: ReportOrigin;
  readonly diagnostics: readonly ReportIssue[];
  readonly referencesById: ReadonlyMap<string, ReferenceEntry>;
  readonly assetsById: ReadonlyMap<string, Asset>;
  readonly referenceSources: ReadonlyMap<string, SafeThumbnailSource>;
  readonly candidateSources: ReadonlyMap<string, SafeThumbnailSource>;
}

export type StructuralDecodeResult =
  | {
      readonly ok: true;
      readonly projection: ReportProjection;
      readonly diagnostics: readonly ReportIssue[];
    }
  | { readonly ok: false; readonly issues: readonly ReportIssue[] };

export type DecodeResult =
  | { readonly ok: true; readonly model: ReportModel }
  | { readonly ok: false; readonly issues: readonly ReportIssue[] };

export interface ThumbnailProbe {
  decode(
    source: SafeThumbnailSource,
    expectedWidth: number,
    expectedHeight: number,
  ): Promise<"decoded" | "undecodable">;
}

export interface ReportDecoder {
  validateStructure(bytes: Uint8Array): StructuralDecodeResult;
  decode(bytes: Uint8Array, origin: ReportOrigin): Promise<DecodeResult>;
}
