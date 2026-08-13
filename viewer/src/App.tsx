import {
  type CSSProperties,
  type ChangeEvent,
  type Dispatch,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import { createReportDecoder } from "./decoder";
import {
  axisDefinitionFallback,
  flattenMeasurement,
  formatCompactNumber,
  formatNumber,
  humanizeToken,
  shortDigest,
} from "./format";
import type {
  AbstainedAsset,
  Asset,
  AxisDefinition,
  ImageIdentity,
  Interval,
  ReferenceEntry,
  ReportIssue,
  ReportModel,
  SafeThumbnailSource,
  ScoredAsset,
} from "./model";
import {
  resolvePixelRagMediaSource,
  verifiedPixelRagArtifact,
} from "./pixel-rag";
import {
  measuredPreferenceReplayEvidence,
  type PreferenceReplayEvidence,
} from "./preference-replay";
import { fireflyBridge, verifiedFireflyEvidence } from "./firefly";
import {
  abstainedAssets,
  activeAssetId,
  initialViewerState,
  rankedAssets,
  type OutcomeFilter,
  type ViewerAction,
  viewerReducer,
} from "./state";
import { EmbeddedSource, hasEmbeddedPayload, LocalFileSource, type ReportSource } from "./sources";

const decoder = createReportDecoder();

function Wordmark(): ReactNode {
  return (
    <div className="wordmark" aria-label="Moodboard">
      <span className="wordmark-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>moodboard</span>
    </div>
  );
}

function FileControl({ onFile }: { readonly onFile: (file: File) => void }): ReactNode {
  const input = useRef<HTMLInputElement>(null);
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.item(0);
    if (file) onFile(file);
    event.target.value = "";
  };
  return (
    <>
      <input
        ref={input}
        className="visually-hidden"
        type="file"
        accept="application/json,.json"
        onChange={onChange}
      />
      <button className="button button-primary" type="button" onClick={() => input.current?.click()}>
        Open a report
      </button>
    </>
  );
}

function AwaitingFileView({ onFile }: { readonly onFile: (file: File) => void }): ReactNode {
  return (
    <main className="start-page">
      <header className="start-header">
        <Wordmark />
        <span className="offline-pill">Offline viewer</span>
      </header>
      <section className="start-hero" aria-labelledby="start-title">
        <p className="eyebrow">Governed visual review</p>
        <h1 id="start-title">Does the work belong—<em>and what evidence says so?</em></h1>
        <p className="start-copy">
          Open a Moodboard report to inspect compatibility evidence, board cohesion, intentional
          diversity, and uncertainty without sending imagery or provenance anywhere.
        </p>
        <div className="start-actions">
          <FileControl onFile={onFile} />
          <span>JSON · processed on this device · no network</span>
        </div>
      </section>
      <section className="start-principles" aria-label="Measurement principles">
        <article>
          <span>01</span>
          <h2>Compatibility is evidence</h2>
          <p>A conformal inlier p-value is not an approval probability or taste score.</p>
        </article>
        <article>
          <span>02</span>
          <h2>Uncertainty stays visible</h2>
          <p>Intervals, ties, and refusals remain primary outcomes—not footnotes.</p>
        </article>
        <article>
          <span>03</span>
          <h2>Every asset is traceable</h2>
          <p>Images stay inline; hashes, model identity, and invocation remain inspectable.</p>
        </article>
      </section>
    </main>
  );
}

function LoadingView({ label }: { readonly label: string }): ReactNode {
  return (
    <main className="status-page" aria-live="polite" aria-busy="true">
      <Wordmark />
      <div className="loading-mark" aria-hidden="true"><i /><i /><i /></div>
      <p className="eyebrow">{label}</p>
      <h1>Validating report and inline images</h1>
      <p>No report values are shown until the complete contract passes.</p>
    </main>
  );
}

function IssueList({ issues }: { readonly issues: readonly ReportIssue[] }): ReactNode {
  return (
    <ol className="issue-list">
      {issues.map((issue, index) => (
        <li key={`${issue.severity}-${issue.code}-${issue.path}-${index}`}>
          <strong>{issue.code}</strong>
          <code>{issue.path}</code>
          <span>{issue.message}</span>
        </li>
      ))}
    </ol>
  );
}

function LoadErrorView({
  label,
  issues,
  allowFile,
  onFile,
}: {
  readonly label: string;
  readonly issues: readonly ReportIssue[];
  readonly allowFile: boolean;
  readonly onFile: (file: File) => void;
}): ReactNode {
  return (
    <main className="status-page status-error">
      <Wordmark />
      <p className="eyebrow">Report refused · {label}</p>
      <h1>Nothing was partially rendered.</h1>
      <p>The report did not satisfy the supported 1.0 or 1.1 contract.</p>
      <IssueList issues={issues} />
      {allowFile ? <FileControl onFile={onFile} /> : null}
    </main>
  );
}

function FlagList({ flags }: { readonly flags: readonly string[] }): ReactNode {
  if (flags.length === 0) return null;
  return (
    <ul className="flag-list" aria-label="Diagnostics">
      {flags.map((flag) => <li key={flag}>{humanizeToken(flag)}</li>)}
    </ul>
  );
}

interface IntervalStyle extends CSSProperties {
  "--interval-low": string;
  "--interval-high": string;
  "--interval-score": string;
}

function IntervalMark({ score, interval, compact = false }: {
  readonly score: number;
  readonly interval: Interval;
  readonly compact?: boolean;
}): ReactNode {
  const style: IntervalStyle = {
    "--interval-low": `${interval.low * 100}%`,
    "--interval-high": `${interval.high * 100}%`,
    "--interval-score": `${score * 100}%`,
  };
  const zeroWidth = interval.low === interval.high;
  const accessible = `Reported inlier p-value ${formatNumber(score)}. Stated level ${formatNumber(interval.level)} interval ${formatNumber(interval.low)} to ${formatNumber(interval.high)}. Method ${interval.method}.${zeroWidth ? " Zero-width interval." : ""}`;
  return (
    <div className={`interval ${compact ? "interval-compact" : ""}`} aria-label={accessible}>
      <div className="interval-scale" style={style} aria-hidden="true">
        <span className={`interval-band ${zeroWidth ? "interval-band-zero" : ""}`} />
        <span className="interval-point" />
      </div>
      <div className="interval-labels">
        <span><b>{formatNumber(interval.low)}</b> low</span>
        <span><b>{formatNumber(score)}</b> p-value</span>
        <span><b>{formatNumber(interval.high)}</b> high</span>
      </div>
      {compact ? null : (
        <p className="interval-method">
          Level {formatNumber(interval.level)} · {interval.method}
          {zeroWidth ? " · zero-width interval" : ""}
        </p>
      )}
    </div>
  );
}

function SafeImage({
  source,
  alt,
  fallback,
  width,
  height,
  loading = "lazy",
}: {
  readonly source: SafeThumbnailSource | undefined;
  readonly alt: string;
  readonly fallback: string;
  readonly width?: number | undefined;
  readonly height?: number | undefined;
  readonly loading?: "eager" | "lazy";
}): ReactNode {
  const [failed, setFailed] = useState(false);
  if (!source || failed) return <div className="image-fallback" role="img" aria-label={fallback}>{fallback}</div>;
  return (
    <img
      src={source}
      alt={alt}
      width={width}
      height={height}
      loading={loading}
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

function CandidatePreview({ asset, model }: { readonly asset: Asset; readonly model: ReportModel }): ReactNode {
  const image = asset.image;
  const source = model.candidateSources.get(asset.asset_id);
  return (
    <section className="candidate-preview" aria-label={`Candidate ${asset.asset_id}`}>
      <div className="preview-kicker"><span>Candidate</span><span>{asset.state}</span></div>
      <div className="candidate-frame">
        {image ? (
          <SafeImage
            source={source}
            alt={`Inline preview for candidate ${asset.asset_id}`}
            fallback="Candidate preview could not be rendered."
          />
        ) : (
          <div className="image-fallback legacy-preview" role="img" aria-label="Candidate image unavailable">
            Candidate image was not included in report version 1.0.
          </div>
        )}
      </div>
      <div className="identity-lines">
        <strong>{asset.asset_id}</strong>
        <span title={asset.source}>Recorded source: {asset.source}</span>
        {image ? (
          <>
            <span title={image.content_sha256}>Content SHA-256 · {shortDigest(image.content_sha256)}</span>
            <span>{image.mime} · {image.width} × {image.height}</span>
          </>
        ) : <span>Candidate content identity unavailable in v1.0</span>}
      </div>
    </section>
  );
}

const ORDINALS = ["First", "Second", "Third"] as const;

function ReferenceCell({
  exemplar,
  reference,
  source,
  index,
  strict,
}: {
  readonly exemplar: Asset["exemplars"][number] | undefined;
  readonly reference: ReferenceEntry | undefined;
  readonly source: SafeThumbnailSource | undefined;
  readonly index: number;
  readonly strict: boolean;
}): ReactNode {
  const position = ORDINALS[index] ?? `Position ${index + 1}`;
  const relation = strict ? "closest reference" : "reported exemplar";
  return (
    <article className="reference-cell">
      <p className="reference-position">{position} {relation}</p>
      <div className="reference-frame">
        {exemplar && reference ? (
          <SafeImage
            source={source}
            alt={`${position} ${relation}: ${reference.reference_id}`}
            fallback="Reference preview is not safely renderable from this report."
          />
        ) : (
          <div className="image-fallback" role="img" aria-label={`Missing ${relation}`}>
            {exemplar ? "Reference identifier did not resolve." : `Report did not supply this ${relation}.`}
          </div>
        )}
      </div>
      <div className="reference-caption">
        <strong>{reference?.reference_id ?? exemplar?.reference_id ?? "Not supplied"}</strong>
        <span>Similarity {exemplar ? formatNumber(exemplar.similarity) : "unavailable"}</span>
        {reference ? (
          <>
            <span title={reference.content_sha256}>SHA-256 · {shortDigest(reference.content_sha256)}</span>
            <span>{reference.mime} · {reference.width} × {reference.height}</span>
            <span>Preview {reference.thumbnail.mime} · {reference.thumbnail.width} × {reference.thumbnail.height}</span>
          </>
        ) : null}
      </div>
    </article>
  );
}

function ReferenceTriptych({ asset, model }: { readonly asset: Asset; readonly model: ReportModel }): ReactNode {
  const strict = model.report.schema_version === "1.1";
  const count = strict ? Math.min(3, model.report.references.length) : 3;
  const exemplars = asset.exemplars.slice(0, count);
  return (
    <section className="reference-evidence" aria-label={`Reference comparison for ${asset.asset_id}`}>
      <div className="section-title-row">
        <div>
          <p className="eyebrow">Visual retrieval evidence</p>
          <h4>{strict ? "Closest references, together" : "Legacy reported exemplars"}</h4>
        </div>
        <span>{exemplars.length}/{count} supplied</span>
      </div>
      <p className="evidence-caveat">
        Nearest references are selected board-wide; compatibility is calibrated within the reported category.
      </p>
      <div className="reference-triptych">
        {Array.from({ length: count }, (_, index) => {
          const exemplar = exemplars[index];
          const reference = exemplar ? model.referencesById.get(exemplar.reference_id) : undefined;
          return (
            <ReferenceCell
              key={`${asset.asset_id}-${index}-${exemplar?.reference_id ?? "missing"}`}
              exemplar={exemplar}
              reference={reference}
              source={reference ? model.referenceSources.get(reference.reference_id) : undefined}
              index={index}
              strict={strict}
            />
          );
        })}
      </div>
      {!strict ? <p className="legacy-note">Report 1.0 does not guarantee three closest, decodable references.</p> : null}
    </section>
  );
}

function AxisTable({ asset, model }: { readonly asset: Asset; readonly model: ReportModel }): ReactNode {
  const ids = ["style", ...model.report.board.representation.axes];
  const recorded = model.report.board.representation.axis_definitions;
  const definitions = ids.map((axisId) => recorded?.find((item) => item.axis_id === axisId) ?? axisDefinitionFallback(axisId));
  return (
    <section className="axis-section" aria-labelledby={`axes-${asset.asset_id}`}>
      <div className="section-title-row">
        <div>
          <p className="eyebrow">Unblended diagnostics</p>
          <h4 id={`axes-${asset.asset_id}`}>{recorded ? "Axis details" : "Legacy axis values"}</h4>
        </div>
        <span>{definitions.length} separate measures</span>
      </div>
      {!recorded ? <p className="legacy-note">Axis method revision was not recorded in report version 1.0.</p> : null}
      <div className="axis-table" role="table" aria-label={`Axis values for ${asset.asset_id}`}>
        {definitions.map((definition) => (
          <AxisRow key={definition.axis_id} definition={definition} value={asset.axes[definition.axis_id]} legacy={!recorded} />
        ))}
      </div>
    </section>
  );
}

function AxisRow({ definition, value, legacy }: {
  readonly definition: AxisDefinition;
  readonly value: number | null | undefined;
  readonly legacy: boolean;
}): ReactNode {
  return (
    <div className="axis-row" role="row">
      <div role="cell">
        <strong>{definition.label}</strong>
        <span>{definition.value_kind === "conformal_p_value" ? "Compatibility evidence" : "Classical visual distance"}</span>
      </div>
      <div className="axis-value" role="cell">{value == null ? "Unavailable" : formatNumber(value)}</div>
      <div role="cell">
        <span>{humanizeToken(definition.direction)}</span>
        <span>{definition.uncertainty === "asset_interval" ? "Interval reported above" : legacy ? "Interval not reported in version 1.0" : "No interval reported"}</span>
      </div>
      <div role="cell">
        <span>{humanizeToken(definition.aggregation)}</span>
        <span>{legacy ? "Method revision not recorded" : `${definition.method.name} · r${definition.method.revision}`}</span>
      </div>
    </div>
  );
}

function conformalFitTierLabel(asset: Asset, model: ReportModel): string {
  if (asset.state === "abstained") {
    return `No conformal fit tier · abstained: ${humanizeToken(asset.reason)}`;
  }
  const ranks = model.report.assets
    .filter((candidate): candidate is ScoredAsset => candidate.state === "scored")
    .map((candidate) => candidate.rank)
    .filter((rank, index, values) => values.indexOf(rank) === index)
    .toSorted((left, right) => left - right);
  const tier = ranks.indexOf(asset.rank) + 1;
  const tieCount = model.report.assets.filter(
    (candidate) => candidate.state === "scored" && candidate.rank === asset.rank,
  ).length;
  const tie = tieCount === 1 ? "no exact-score tie" : `${tieCount}-way tie`;
  return `Fit tier ${tier} of ${ranks.length} · ${tie} · board-fit p ${formatNumber(asset.score)}`;
}

function reportedCompetitionRank(asset: Asset): string {
  return asset.state === "scored"
    ? `reported competition rank ${asset.rank}`
    : "outside the ranking";
}

function ScoredOutcome({ asset, model }: { readonly asset: ScoredAsset; readonly model: ReportModel }): ReactNode {
  return (
    <section className="outcome outcome-scored" aria-labelledby={`outcome-${asset.asset_id}`}>
      <div className="outcome-heading">
        <div>
          <p className="eyebrow">Compatibility evidence</p>
          <h4 id={`outcome-${asset.asset_id}`}>{conformalFitTierLabel(asset, model)}</h4>
        </div>
        <span className="category-pill">{asset.category_id} · n={asset.n_local}</span>
      </div>
      <p className="honesty-note">This is evidence of fit to the board—not approval probability, taste, or confidence in a human decision.</p>
      <IntervalMark score={asset.score} interval={asset.interval} />
    </section>
  );
}

function AbstainedOutcome({ asset }: { readonly asset: AbstainedAsset }): ReactNode {
  const leaves = flattenMeasurement(asset.measurement);
  return (
    <section className="outcome outcome-abstained" aria-labelledby={`outcome-${asset.asset_id}`}>
      <div className="outcome-heading">
        <div>
          <p className="eyebrow">Uncertainty / refusal</p>
          <h4 id={`outcome-${asset.asset_id}`}>No style score was issued.</h4>
        </div>
        <span className="abstention-pill">{humanizeToken(asset.reason)}</span>
      </div>
      <p className="abstention-copy">{asset.explanation}</p>
      <dl className="measurement-tree">
        {leaves.map((leaf) => (
          <div key={leaf.path}>
            <dt>{leaf.path}</dt>
            <dd>{leaf.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function AssetCard({ asset, model, active, dispatch }: {
  readonly asset: Asset;
  readonly model: ReportModel;
  readonly active: boolean;
  readonly dispatch: Dispatch<ViewerAction>;
}): ReactNode {
  const selectOnKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      dispatch({ type: "select", assetId: asset.asset_id });
    }
  };
  const tierLabel = conformalFitTierLabel(asset, model);
  return (
    <article
      className={`asset-card asset-${asset.state} ${active ? "asset-active" : ""}`}
      tabIndex={0}
      aria-label={`${asset.asset_id}, ${asset.state}`}
      onMouseEnter={() => dispatch({ type: "hover", assetId: asset.asset_id })}
      onMouseLeave={() => dispatch({ type: "hover", assetId: null })}
      onFocus={() => dispatch({ type: "focus", assetId: asset.asset_id })}
      onBlur={() => dispatch({ type: "focus", assetId: null })}
      onClick={() => dispatch({ type: "select", assetId: asset.asset_id })}
      onKeyDown={selectOnKey}
    >
      <header className="asset-header">
        <span className="asset-index">{asset.state === "scored" ? tierLabel.match(/^Fit tier (\d+)/u)?.[1]?.padStart(2, "0") ?? "—" : "—"}</span>
        <div>
          <p className="eyebrow">{asset.state === "scored" ? "Conformal fit tier" : "Unranked candidate"}</p>
          <h3>{asset.asset_id}</h3>
        </div>
        <FlagList flags={asset.flags} />
      </header>
      <div className="evidence-layout">
        <CandidatePreview asset={asset} model={model} />
        <ReferenceTriptych asset={asset} model={model} />
      </div>
      {asset.state === "scored" ? <ScoredOutcome asset={asset} model={model} /> : <AbstainedOutcome asset={asset} />}
      <AxisTable asset={asset} model={model} />
    </article>
  );
}

function StoryStrip({ model }: { readonly model: ReportModel }): ReactNode {
  const report = model.report;
  const scored = report.assets.filter((asset) => asset.state === "scored").length;
  const abstained = report.assets.length - scored;
  const families = groupDeterministicVariants(model);
  const completeVariantCorpus = families.length * 3 === report.assets.length;
  return (
    <section className="story-strip" aria-label="Board measurement overview">
      <article className="story-compatibility">
        <p className="eyebrow">Compatibility</p>
        <strong>{scored} evaluated</strong>
        <span>
          {abstained} abstained · {completeVariantCorpus
            ? `${families.length} source artworks × 3 deterministic views`
            : `${report.assets.length} candidate records`}
        </span>
      </article>
      <article>
        <p className="eyebrow">Cohesion</p>
        <strong title={formatNumber(report.board_stats.tightness.loo_quantiles.p50)}>
          {formatCompactNumber(report.board_stats.tightness.loo_quantiles.p50)}
        </strong>
        <span>
          LOO median · p10 {formatCompactNumber(report.board_stats.tightness.loo_quantiles.p10)} · p90{" "}
          {formatCompactNumber(report.board_stats.tightness.loo_quantiles.p90)}
        </span>
      </article>
      <article>
        <p className="eyebrow">Diversity / coverage</p>
        <strong title={formatNumber(report.board.n_eff)}>{formatCompactNumber(report.board.n_eff)} / {report.board.n_references}</strong>
        <span>effective support · {report.board.categories.length} declared look{report.board.categories.length === 1 ? "" : "s"}</span>
      </article>
      <article>
        <p className="eyebrow">Uncertainty</p>
        <strong title={formatNumber(report.board.supported_alpha)}>α {formatCompactNumber(report.board.supported_alpha)}</strong>
        <span>finest supported distinction · requested α {formatCompactNumber(report.board.requested_alpha)}</span>
      </article>
    </section>
  );
}

function MechanismGuide(): ReactNode {
  return (
    <section className="mechanism-guide" aria-label="Three independent evidence mechanisms">
      <article>
        <span>01 · Board fit</span>
        <strong>Does this variant resemble the reference board?</strong>
        <p>Visual embeddings become k-nearest cosine nonconformity, then one board-relative conformal p-value. Higher p means stronger compatibility; equal p shares a fit tier. No reranker.</p>
      </article>
      <article>
        <span>02 · Firefly iteration</span>
        <strong>Did an edit preserve the protected pixels?</strong>
        <p>Generation and locality verification form a separate loop. Its outputs do not change board fit.</p>
      </article>
      <article>
        <span>03 · Pairwise preference</span>
        <strong>Did a newly retrained immutable snapshot respond?</strong>
        <p>A separate FANN replay measures eight untouched pairs. It publishes a new snapshot—no online update—and does not rerank the 24 images.</p>
      </article>
    </section>
  );
}

function GovernedReferenceBoard({ model }: { readonly model: ReportModel }): ReactNode {
  return (
    <section className="reference-board" aria-label="Governed reference board">
      <div className="section-heading reference-board-heading">
        <div>
          <p className="eyebrow">Governed source set</p>
          <h2>Scoring baseline · {model.report.references.length} original artworks</h2>
        </div>
        <p>These immutable originals define the board; every candidate fit tier is calibrated against this exact set.</p>
      </div>
      <div className="reference-board-strip">
        {model.report.references.map((reference, index) => (
          <article key={reference.reference_id} data-testid="governed-reference-tile">
            <div className="reference-board-frame">
              <SafeImage
                source={model.referenceSources.get(reference.reference_id)}
                alt={`Governed reference ${reference.reference_id}`}
                fallback="Governed reference preview could not be rendered."
                width={reference.thumbnail.width}
                height={reference.thumbnail.height}
                loading={index < 6 ? "eager" : "lazy"}
              />
              <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
            </div>
            <strong title={reference.reference_id}>{reference.reference_id}</strong>
            <small title={reference.content_sha256}>SHA-256 · {shortDigest(reference.content_sha256)}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

type DeterministicVariantKind = "original" | "center-crop" | "horizontal-mirror";

interface DeterministicVariantFamily {
  readonly sourceId: string;
  readonly original: Asset;
  readonly transforms: readonly [Asset, Asset];
  readonly reference: ReferenceEntry | null;
  readonly originalReusesReferenceBytes: boolean;
}

interface PendingVariantFamily {
  readonly sourceId: string;
  original?: Asset;
  centerCrop?: Asset;
  horizontalMirror?: Asset;
}

const DETERMINISTIC_VARIANT_SUFFIXES: ReadonlyArray<{
  readonly suffix: string;
  readonly kind: DeterministicVariantKind;
}> = [
  { suffix: "--original.jpg", kind: "original" },
  { suffix: "--center-crop-90pct.png", kind: "center-crop" },
  { suffix: "--horizontal-mirror.png", kind: "horizontal-mirror" },
];

function deterministicVariantIdentity(assetId: string): {
  readonly sourceId: string;
  readonly kind: DeterministicVariantKind;
} | null {
  const matched = DETERMINISTIC_VARIANT_SUFFIXES.find(({ suffix }) => assetId.endsWith(suffix));
  if (!matched) return null;
  const sourceId = assetId.slice(0, -matched.suffix.length);
  return sourceId.length > 0 ? { sourceId, kind: matched.kind } : null;
}

function groupDeterministicVariants(model: ReportModel): readonly DeterministicVariantFamily[] {
  const pending = new Map<string, PendingVariantFamily>();
  for (const asset of model.report.assets) {
    const identity = deterministicVariantIdentity(asset.asset_id);
    if (!identity) continue;
    const family = pending.get(identity.sourceId) ?? { sourceId: identity.sourceId };
    if (identity.kind === "original") family.original = asset;
    if (identity.kind === "center-crop") family.centerCrop = asset;
    if (identity.kind === "horizontal-mirror") family.horizontalMirror = asset;
    pending.set(identity.sourceId, family);
  }

  return Array.from(pending.values()).flatMap((family) => {
    if (!family.original || !family.centerCrop || !family.horizontalMirror) return [];
    const originalHash = family.original.image?.content_sha256;
    const reference = originalHash
      ? model.report.references.find((candidate) => candidate.content_sha256 === originalHash) ?? null
      : null;
    return [{
      sourceId: family.sourceId,
      original: family.original,
      transforms: [family.centerCrop, family.horizontalMirror] as const,
      reference,
      originalReusesReferenceBytes: reference !== null,
    }];
  });
}

function variantLabel(asset: Asset): string {
  const identity = deterministicVariantIdentity(asset.asset_id);
  if (identity?.kind === "original") return "Original";
  if (identity?.kind === "center-crop") return "90% center crop";
  if (identity?.kind === "horizontal-mirror") return "Horizontal mirror";
  return asset.asset_id;
}

function CandidateTile({
  asset,
  model,
  active,
  loading,
  dispatch,
}: {
  readonly asset: Asset;
  readonly model: ReportModel;
  readonly active: boolean;
  readonly loading: "eager" | "lazy";
  readonly dispatch: Dispatch<ViewerAction>;
}): ReactNode {
  const image = asset.image;
  const tierLabel = conformalFitTierLabel(asset, model);
  return (
    <button
      type="button"
      className={`candidate-hero-tile candidate-hero-${asset.state} ${active ? "candidate-hero-active" : ""}`}
      data-asset-id={asset.asset_id}
      aria-label={`Inspect candidate ${asset.asset_id}`}
      aria-pressed={active}
      onClick={() => dispatch({ type: "inspect", assetId: asset.asset_id })}
    >
      <span className="candidate-hero-image">
        <SafeImage
          source={model.candidateSources.get(asset.asset_id)}
          alt={`Candidate ${asset.asset_id}`}
          fallback={image ? "Candidate preview could not be rendered." : "Candidate preview unavailable in report 1.0."}
          width={image?.thumbnail.width}
          height={image?.thumbnail.height}
          loading={loading}
        />
        <i>{asset.state === "scored" ? tierLabel.split(" · ")[0]?.toUpperCase() : "REFUSED"}</i>
      </span>
      <strong title={asset.asset_id}>{asset.asset_id}</strong>
      <small>{tierLabel}</small>
    </button>
  );
}

function VariantFamilyCard({
  family,
  index,
  model,
  selectedId,
  dispatch,
}: {
  readonly family: DeterministicVariantFamily;
  readonly index: number;
  readonly model: ReportModel;
  readonly selectedId: string | null;
  readonly dispatch: Dispatch<ViewerAction>;
}): ReactNode {
  const variants = [family.original, ...family.transforms];
  return (
    <article className="variant-family-card" data-testid="variant-source-family">
      <header>
        <span>{String(index + 1).padStart(2, "0")} · source family</span>
        <h3 title={family.sourceId}>{family.sourceId}</h3>
      </header>
      <div className="variant-family-gallery">
        {variants.map((asset, variantIndex) => (
          <button
            key={asset.asset_id}
            type="button"
            className={`variant-family-tile ${variantIndex === 0 ? "variant-family-original" : ""}`}
            data-asset-id={asset.asset_id}
            aria-label={`Inspect candidate ${asset.asset_id}`}
            aria-pressed={asset.asset_id === selectedId}
            onClick={() => dispatch({ type: "inspect", assetId: asset.asset_id })}
          >
            <span className="variant-family-image">
              <SafeImage
                source={model.candidateSources.get(asset.asset_id)}
                alt={`${family.sourceId}, ${variantLabel(asset)}`}
                fallback={asset.image ? "Candidate preview could not be rendered." : "Candidate preview unavailable in report 1.0."}
                width={asset.image?.thumbnail.width}
                height={asset.image?.thumbnail.height}
                loading={index < 4 ? "eager" : "lazy"}
              />
            </span>
            <strong>{variantLabel(asset)}</strong>
            {variantIndex === 0 && family.originalReusesReferenceBytes ? (
              <mark title={family.reference?.content_sha256}>Same bytes as reference</mark>
            ) : null}
            <small>{conformalFitTierLabel(asset, model)}</small>
          </button>
        ))}
      </div>
    </article>
  );
}

function FullCandidateAudit({ ordered, model }: {
  readonly ordered: readonly Asset[];
  readonly model: ReportModel;
}): ReactNode {
  return (
    <details className="full-candidate-audit">
      <summary>
        <span>Full {ordered.length}-candidate record</span>
        <small>Reported order retained for deterministic audit</small>
      </summary>
      <ol>
        {ordered.map((asset, index) => (
          <li key={asset.asset_id}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{asset.asset_id}</strong>
            <small>{conformalFitTierLabel(asset, model)} · {reportedCompetitionRank(asset)}</small>
          </li>
        ))}
      </ol>
    </details>
  );
}

function CandidateHeroGrid({
  model,
  activeId,
  dispatch,
}: {
  readonly model: ReportModel;
  readonly activeId: string | null;
  readonly dispatch: Dispatch<ViewerAction>;
}): ReactNode {
  const ordered = [...rankedAssets(model), ...abstainedAssets(model)];
  const selectedId = activeId ?? ordered[0]?.asset_id ?? null;
  const families = groupDeterministicVariants(model);
  const groupedAssetCount = families.reduce((count) => count + 3, 0);
  const showsFamilies = families.length > 0 && groupedAssetCount === ordered.length;
  if (ordered.length === 0) return null;

  const declaredModel = model.report.board.representation.style.model;
  const methodNote = /qwen|lattice/i.test(declaredModel)
    ? "Qwen/Lattice embeddings feed k-nearest cosine nonconformity, then full-conformal board-fit. Higher p means stronger board compatibility; order is p high to low, and equal p-values share a fit tier. No reranker changes or breaks ties."
    : `This report declares ${declaredModel} for k-nearest cosine nonconformity and conformal board-fit. Higher p means stronger compatibility; equal p-values share a tier, with no reranker.`;

  return (
    <section className="candidate-hero" aria-label={showsFamilies ? "Deterministic variant stress test" : `${ordered.length} candidate overview`}>
      <div className="section-heading candidate-hero-heading">
        <div>
          <p className="eyebrow">{showsFamilies ? "Deterministic source families" : "Complete candidate field"}</p>
          <h2>{showsFamilies ? `${families.length} originals · three measured views each.` : `${ordered.length} measured outputs, one scan.`}</h2>
        </div>
        <p>Higher p means stronger board compatibility. Equal p-values share a fit tier; report order only keeps equal values deterministic.</p>
      </div>
      {showsFamilies ? (
        <p className="variant-corpus-note">
          This is a deterministic stress test, not 24 generated images: each of the 8 references reappears unchanged once, beside a 90% crop and a mirror, to verify byte identity and transform sensitivity.
        </p>
      ) : null}
      <p className="ranking-method-note">{methodNote}</p>
      {showsFamilies ? (
        <>
          <div className="variant-family-grid">
            {families.map((family, index) => (
              <VariantFamilyCard
                key={family.sourceId}
                family={family}
                index={index}
                model={model}
                selectedId={selectedId}
                dispatch={dispatch}
              />
            ))}
          </div>
          <FullCandidateAudit ordered={ordered} model={model} />
        </>
      ) : (
        <div className="candidate-hero-grid">
          {ordered.map((asset, index) => (
            <CandidateTile
              key={asset.asset_id}
              asset={asset}
              model={model}
              active={asset.asset_id === selectedId}
              loading={index < 12 ? "eager" : "lazy"}
              dispatch={dispatch}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function CoreVerificationLine({ model }: { readonly model: ReportModel }): ReactNode {
  const scored = model.report.assets.filter((asset) => asset.state === "scored").length;
  const abstained = model.report.assets.length - scored;
  return (
    <p className="core-verification-line" aria-label="Core report verification">
      Verified · {model.report.references.length} governed references · {model.report.assets.length} candidate outcomes · {scored} scored · {abstained} abstained · reported fit tiers preserved · no merged preference score
    </p>
  );
}

function displayAssetLabel(assetId: string): string {
  const tokens = assetId
    .replace(/^style_/, "")
    .replace(/--(?:original\.jpg|center-crop-90pct\.png|horizontal-mirror\.png)$/u, "")
    .split("_");
  const [rawArtist = "Unknown", ...rawTitle] = tokens;
  const artist = rawArtist === "vangogh"
    ? "Van Gogh"
    : rawArtist.charAt(0).toUpperCase() + rawArtist.slice(1);
  const title = rawTitle
    .map((token, index) => index === 0 ? token.charAt(0).toUpperCase() + token.slice(1) : token)
    .join(" ");
  return title ? `${artist} · ${title}` : artist;
}

function preferenceAssetSource(
  model: ReportModel,
  label: string,
): SafeThumbnailSource | undefined {
  return model.candidateSources.get(label);
}

function preferenceFitTierLabel(model: ReportModel, label: string): string {
  const asset = model.assetsById.get(label);
  return asset ? conformalFitTierLabel(asset, model) : "Fit tier unavailable · asset/report identity mismatch";
}

function percentage(value: number): string {
  return `${(value * 100).toFixed(3)}%`;
}

function PreferenceReplayPanel({
  evidence,
  model,
}: {
  readonly evidence: PreferenceReplayEvidence | null;
  readonly model: ReportModel;
}): ReactNode {
  if (evidence === null) return null;
  if (model.documentSha256 !== evidence.bindings.source_report_sha256) {
    return (
      <aside className="preference-source-mismatch" role="status">
        <strong>Preference replay not shown.</strong>
        <span>The frozen pairwise evidence source report does not match this loaded report.</span>
      </aside>
    );
  }
  const delta = evidence.delta;
  const probes = evidence.probes.toSorted((left, right) => right.delta - left.delta);
  const concrete = evidence.probes[0];
  const featureCount = evidence.policies.model_a.feature_names.length;
  const snapshots = [
    {
      label: `Snapshot A · ${humanizeToken(evidence.policies.model_a.label)}`,
      model: evidence.model_a,
      probability: delta.mean_probability_for_policy_b_preferred_before,
    },
    {
      label: `Snapshot B · after ${humanizeToken(evidence.policies.model_b.label)}`,
      model: evidence.model_b,
      probability: delta.mean_probability_for_policy_b_preferred_after,
    },
  ] as const;

  return (
    <section className="preference-measured" aria-label="Governed preference replay">
      <div className="section-heading preference-heading">
        <div>
          <p className="eyebrow">
            Independent preference mechanism replay · policy_simulated · not trained on these Firefly outputs
          </p>
          <h2>Can retraining publish a different immutable pairwise model?</h2>
        </div>
        <p>Same 24 image corpus · separate pairwise evidence head · never merged into fit tiers</p>
      </div>
      <p className="preference-deck">
        <strong>24 image records · {featureCount} features each · {evidence.event_counts.total} pair events · 8 untouched probes.</strong>
        {" "}The labels come from two disclosed simulated rules—not people. Snapshot A freezes at
        {" "}{evidence.model_a.snapshot_event_count} events. Then {evidence.event_counts.model_b_appended_train_decisive}
        {" "}new train judgments from a deliberately conflicting rule retrain and publish Snapshot B as a separate immutable model. No online update occurs.
      </p>
      <div className="preference-mechanism" aria-label="Preference replay mechanism">
        <article>
          <span>01 · corpus</span>
          <strong>24 image records</strong>
          <small>byte-bound to the exact source report above</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>02 · representation</span>
          <strong>{featureCount} measured features</strong>
          <small>pair transform: left minus right</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>03 · replay</span>
          <strong>112 + 96 events</strong>
          <small>two immutable FANN snapshots</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>04 · evaluation</span>
          <strong>8 untouched pairs</strong>
          <small>never judged during the replay</small>
        </article>
      </div>
      <div className="preference-snapshots">
        {snapshots.map((snapshot, index) => (
          <article key={snapshot.model.preference_model_id}>
            <span>{snapshot.label}</span>
            <strong title={String(snapshot.probability)}>{percentage(snapshot.probability)}</strong>
            <small>mean probability for counter-style side · {snapshot.model.snapshot_event_count} frozen events</small>
            {index === 0 ? <i aria-hidden="true">→</i> : null}
          </article>
        ))}
      </div>
      <div className={`preference-delta preference-delta-${delta.outcome}`}>
        <span>Measured directional response · +{evidence.event_counts.model_b_appended_train_decisive} judgments</span>
        <strong title={String(delta.mean_delta)}>{delta.adaptation_direction_observed ? "Signal moved" : "No movement"}</strong>
        <small>
          {delta.adaptation_direction_observed
            ? "The model moved toward the counter-style side—from almost never to roughly neutral. This is not a quality improvement claim."
            : "The model did not move toward the counter-style side; no improvement is claimed."}
        </small>
      </div>
      {concrete ? (
        <section className="preference-concrete-pair" aria-label="Concrete frozen preference pair">
          <header>
            <div>
              <span>One untouched disagreement pair</span>
              <h3>Here is what “A versus B” actually means.</h3>
            </div>
            <p>
              Counter-style probability {percentage(concrete.probability_before)} → {percentage(concrete.probability_after)}
            </p>
          </header>
          <div className="preference-pair-images">
            {[concrete.left, concrete.right].map((asset) => {
              const pickedA = asset.asset_id === concrete.policy_a_preferred.asset_id;
              const pickedB = asset.asset_id === concrete.policy_b_preferred.asset_id;
              return (
                <article key={asset.asset_id}>
                  <figure>
                    <SafeImage
                      source={preferenceAssetSource(model, asset.label)}
                      alt={displayAssetLabel(asset.label)}
                      fallback="The exact pair image is unavailable for this report fixture."
                      loading="lazy"
                    />
                  </figure>
                  <strong>{displayAssetLabel(asset.label)}</strong>
                  <small>{preferenceFitTierLabel(model, asset.label)}</small>
                  <div>
                    {pickedA ? <mark>{humanizeToken(evidence.policies.model_a.label)} picked this</mark> : null}
                    {pickedB ? <mark>{humanizeToken(evidence.policies.model_b.label)} picked this</mark> : null}
                  </div>
                </article>
              );
            })}
          </div>
          <p className="preference-pair-footnote">
            The two policies deliberately disagree. Snapshot B does not “prefer better art”; it becomes less certain that the cohesive-style side should always win.
          </p>
        </section>
      ) : null}
      <details className="preference-probes" aria-label="Frozen preference probes">
        <summary>All 8 untouched probe pairs <small>probability for counter-style side</small></summary>
        <div className="preference-probe-heading">
          <span>Counter-style selection</span>
          <span>Snapshot A → B</span>
          <span>Δ</span>
        </div>
        <ol>
          {probes.map((probe, index) => (
            <li key={probe.pair_id} data-testid="preference-probe-row">
              <span className="preference-probe-rank">{String(index + 1).padStart(2, "0")}</span>
              <strong title={probe.policy_b_preferred.asset_id}>{displayAssetLabel(probe.policy_b_preferred.label)}</strong>
              <span title={`${probe.probability_before} → ${probe.probability_after}`}>
                {percentage(probe.probability_before)} → {percentage(probe.probability_after)}
              </span>
              <b>{probe.delta >= 0 ? "+" : ""}{percentage(probe.delta)}</b>
            </li>
          ))}
        </ol>
      </details>
      <p className="preference-verification-line" title={evidence.support_refusal.message}>
        Verified · FANN A+B · A probe predictions value-exact after B · distinct snapshots · restart predictions exact · support refusal captured (0 &lt; 64 groups)
      </p>
      <details className="preference-audit">
        <summary>Audit &amp; identity</summary>
        <div className="preference-provenance">
          <span>Feature schema · {evidence.bindings.feature_schema_id}</span>
          <span>Source report · {evidence.bindings.source_report_sha256}</span>
          <span>Replay · {evidence.replay_fingerprint}</span>
          <span>Model A · {evidence.model_a.preference_model_id} · fingerprint {evidence.model_a.model_fingerprint} · bundle {evidence.model_a.bundle_ref}</span>
          <span>Model B · {evidence.model_b.preference_model_id} · fingerprint {evidence.model_b.model_fingerprint} · bundle {evidence.model_b.bundle_ref}</span>
        </div>
        <ul className="preference-nonclaims" aria-label="Preference replay non-claims">
          {evidence.non_claims.map((claim) => <li key={claim}>{claim}</li>)}
        </ul>
      </details>
    </section>
  );
}

function FireflyMeasuredLoop(): ReactNode {
  const evidence = verifiedFireflyEvidence;
  const [structural, raw, selected] = evidence.replacement.timeline;
  if (!structural || !raw || !selected || !raw.preview || !selected.preview) return null;

  return (
    <section className="firefly-measured-loop" aria-label="Measured Firefly iteration loop">
      <div className="section-heading firefly-heading">
        <div>
          <p className="eyebrow">Frozen Firefly iteration · measured verification</p>
          <h2>Generate in Firefly. Govern and verify in Moodboard.</h2>
        </div>
        <div className="firefly-capture">
          <strong>Gemini 2.5 (Nano Banana) · Google partner model via Adobe Firefly</strong>
          <span>{evidence.capture.cost_display} · captured display for this run, not a pricing promise</span>
          <small>{evidence.capture.surface}</small>
        </div>
      </div>

      <p className="firefly-deck">
        Authenticated web session. No native Firefly API was used in this capture. The retrieved
        references guided prompt wording and were not attached as direct generator image references.
      </p>

      <ol className="firefly-timeline" aria-label="Frozen Firefly iteration timeline">
        <li className="firefly-step firefly-step-structural">
          <span>01</span>
          <div>
            <small>{structural.label}</small>
            <strong>FAIL · structural aspect ratio</strong>
            <p>The square output was rejected before locality comparison against the governed 4:3 source.</p>
          </div>
        </li>
        <li className="firefly-step firefly-step-fail">
          <span>02</span>
          <figure>
            <img src={raw.preview.source} alt="Raw Firefly replacement output that failed locality" width={raw.preview.width} height={raw.preview.height} loading="lazy" decoding="async" />
          </figure>
          <div>
            <small>{raw.label}</small>
            <strong>Raw generator output</strong>
            <p>{String(raw.outside_mask_ssim)} outside-mask SSIM · threshold {String(evidence.replacement.threshold)} · FAIL</p>
          </div>
        </li>
        <li className="firefly-step firefly-step-pass">
          <span>03</span>
          <figure>
            <img src={selected.preview.source} alt="Selected Firefly cutout composited into the governed source" width={selected.preview.width} height={selected.preview.height} loading="lazy" decoding="async" />
          </figure>
          <div>
            <small>{selected.label}</small>
            <strong>Deterministic preservation constraint</strong>
            <p>1.0 outside-mask SSIM · PASS by construction, not intrinsic generator locality or an aesthetic claim.</p>
            <p>Alpha came from Adobe Firefly web Remove background; it is not a ground-truth mask.</p>
          </div>
        </li>
      </ol>

      <div className="firefly-evidence-grid">
        <article className="firefly-restyle">
          <img src={evidence.restyle.preview.source} alt="Firefly classical pastoral restyle with acceptance not computed" width={evidence.restyle.preview.width} height={evidence.restyle.preview.height} loading="lazy" decoding="async" />
          <div>
            <span>Global restyle</span>
            <strong>Restyle acceptance · not computed</strong>
            <p>Pixel diagnostics remain descriptive; no style, semantic-preservation, or preference acceptance was computed.</p>
          </div>
        </article>
        <article className="firefly-khive-proof">
          <span>Immutable evidence loop</span>
          <strong>Khive · lattice-embed 0.9.0</strong>
          <p>Three registered outputs · 1024D · lattice-embed 0.9.0 (Qwen visual checkpoint) · namespace {evidence.khive.namespace}.</p>
          <p>Canonical search-result bytes match across process restart.</p>
        </article>
      </div>

      <details className="firefly-audit">
        <summary>Audit &amp; identity</summary>
        <div className="firefly-identity">
          <span>Bridge · {fireflyBridge.bridge_id}</span>
          <span>Projection · {evidence.projection.sha256}</span>
          <span>Transport binary · {evidence.khive.transport.binary_sha256}</span>
          <span>Descriptor · {evidence.khive.descriptor.fingerprint}</span>
        </div>
        <ul aria-label="Firefly evidence non-claims">
          {evidence.nonclaims.map((claim) => <li key={claim}>{claim}</li>)}
        </ul>
      </details>
    </section>
  );
}

function PixelRagLab({ model }: { readonly model: ReportModel }): ReactNode {
  const artifact = verifiedPixelRagArtifact;
  const initialIntent = artifact.intents[0];
  if (!initialIntent) throw new Error("Verified Pixel RAG artifact has no intents");
  const [activeIntentId, setActiveIntentId] = useState(initialIntent.id);
  const intent = artifact.intents.find((candidate) => candidate.id === activeIntentId) ?? initialIntent;
  const source = resolvePixelRagMediaSource(artifact.source, model);
  const verificationLabel = intent.verification_status === "not_run"
    ? "Not run"
    : intent.verification_status === "passed" ? "Passed" : "Failed";

  return (
    <section className="pixel-rag-lab" aria-label="Pixel RAG intent lab">
      <header className="pixel-rag-header">
        <div>
          <p className="eyebrow">Pixel RAG · governed evidence routing</p>
          <h2>Same pixels. <em>Different evidence.</em></h2>
        </div>
        <div className={`pixel-run-status pixel-run-${artifact.evidence_status}`}>
          <span>{artifact.evidence_status === "measured_run" ? "Measured run" : "Contract fixture"}</span>
          <p>{artifact.status_label}</p>
        </div>
      </header>

      <p className="pixel-rag-deck">
        Intent changes the editable region, retrieval namespace, evidence corpus, conditioning plan,
        and verifier. The source bytes stay immutable.
      </p>

      <div className="intent-switcher" role="group" aria-label="Choose Pixel RAG intent">
        {artifact.intents.map((candidate, index) => (
          <button
            key={candidate.id}
            type="button"
            aria-pressed={candidate.id === intent.id}
            onClick={() => setActiveIntentId(candidate.id)}
          >
            <span>0{index + 1}</span>
            <span>
              <small>{candidate.eyebrow}</small>
              <strong>{candidate.title}</strong>
            </span>
          </button>
        ))}
      </div>

      <div className="pixel-rag-workbench">
        <aside className="pixel-source-panel">
          <div className="pixel-panel-title">
            <span>Source · immutable</span>
            <span>{intent.query.granularity === "confirmed_region" ? "Region query" : "Global query"}</span>
          </div>
          <div className={`pixel-source-frame pixel-source-${intent.query.granularity}`}>
            <SafeImage
              source={source}
              alt={`Source asset ${artifact.source.asset_id}`}
              fallback="Source fixture does not resolve in this report."
            />
            {intent.query.granularity === "confirmed_region" && intent.query.rectangle ? (
              <div
                className="pixel-region"
                aria-label="Confirmed editable rectangle overlay"
                style={{
                  height: `${intent.query.rectangle.height * 100}%`,
                  left: `${intent.query.rectangle.x * 100}%`,
                  top: `${intent.query.rectangle.y * 100}%`,
                  width: `${intent.query.rectangle.width * 100}%`,
                }}
              >
                <span>editable</span>
              </div>
            ) : (
              <div className="pixel-frame-ring" aria-hidden="true" />
            )}
          </div>
          <dl className="pixel-source-identity">
            <div><dt>Asset</dt><dd>{artifact.source.asset_id}</dd></div>
            <div><dt>SHA-256</dt><dd title={artifact.source.content_sha256}>{shortDigest(artifact.source.content_sha256)}</dd></div>
            <div><dt>BlobStore</dt><dd title={artifact.source.khive.content_ref}>{shortDigest(artifact.source.khive.content_ref)}</dd></div>
          </dl>
          <div className="pixel-prompt">
            <p className="eyebrow">Designer intent</p>
            <blockquote>{intent.prompt}</blockquote>
          </div>
        </aside>

        <div className="pixel-query-panel">
          <div className="pixel-query-facts">
            <article>
              <span>Query granularity</span>
              <strong>{intent.query.label}</strong>
              <small>
                {intent.query.region_query_ref
                  ? `region crop query · ${shortDigest(intent.query.region_query_ref)}`
                  : intent.verification_status === "not_run"
                    ? "all pixels editable; layout constraint declared; verifier not run"
                    : `all pixels editable; layout verifier ${intent.verification_status}`}
              </small>
            </article>
            <article>
              <span>Active Khive namespace</span>
              <strong>{intent.query.namespace}</strong>
              <small>{intent.query.corpus_label}</small>
            </article>
          </div>
          <p className="pixel-rationale">{intent.query.rationale}</p>

          <div className="pixel-evidence-heading">
            <div>
              <p className="eyebrow">Ranked visual evidence</p>
              <h3>References the generator may condition on</h3>
            </div>
            <span>Khive retrieval · Lattice descriptor · top 3</span>
          </div>
          <div className="pixel-evidence-grid">
            {intent.evidence.map((hit) => (
              <article className="pixel-evidence-card" key={hit.asset_id}>
                <div className="pixel-hit-image">
                  <SafeImage
                    source={resolvePixelRagMediaSource(hit, model)}
                    alt={`Retrieved evidence ${hit.title}`}
                    fallback="Evidence preview does not resolve in this report."
                  />
                  <span>#{hit.rank}</span>
                </div>
                <div className="pixel-hit-copy">
                  <div className="pixel-hit-score">
                    <span>cosine</span>
                    <strong>{formatNumber(hit.score.value)}</strong>
                  </div>
                  <h4>{hit.title}</h4>
                  <p>{hit.rationale}</p>
                  <dl>
                    <div><dt>Creator</dt><dd>{hit.creator}</dd></div>
                    <div><dt>License</dt><dd>{hit.license.label}</dd></div>
                    <div><dt>Content</dt><dd title={hit.content_sha256}>{shortDigest(hit.content_sha256)}</dd></div>
                    <div><dt>Khive ref</dt><dd title={hit.khive.content_ref}>{shortDigest(hit.khive.content_ref)}</dd></div>
                  </dl>
                </div>
              </article>
            ))}
          </div>
        </div>
      </div>

      <ol className="pixel-pipeline" aria-label="Pixel RAG control pipeline">
        {intent.pipeline.map((stage, index) => (
          <li key={stage.id} className={stage.id === "external_generation" ? "pixel-stage-external" : ""}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <strong>{stage.label}</strong>
              <small>{stage.detail}</small>
            </div>
          </li>
        ))}
      </ol>

      <div className="pixel-results-grid">
        <section className="pixel-verification" aria-label="Recorded retrieval and verification metrics">
          <div className="pixel-section-heading">
            <div><p className="eyebrow">Quantitative checks</p><h3>Retrieval and edit verification</h3></div>
            <span>{artifact.evidence_status === "measured_run" ? "recorded evaluation" : "fixture contract · not an empirical claim"}</span>
          </div>
          <div className="pixel-metrics">
            {intent.metrics.map((metric) => (
              <article key={metric.id}>
                <span>{metric.label}</span>
                <strong>{metric.display}</strong>
                <small>{metric.passed === null ? metric.target : `${metric.passed ? "meets" : "misses"} target ${metric.target}`}</small>
              </article>
            ))}
          </div>
          <p className={`pixel-verification-status pixel-verification-${intent.verification_status}`}>
            Selected edit verification · <strong>{verificationLabel}</strong>
          </p>
          {intent.raw_metrics ? (
            <div className="pixel-routing-comparison" aria-label="Raw and routed retrieval comparison">
              <div>
                <span>Raw Qwen geometry · ungated</span>
                <strong>{intent.raw_metrics.find((metric) => metric.id === "precision_at_3")?.display}</strong>
                <small>P@3 before the declared intent filter</small>
              </div>
              <b aria-hidden="true">→</b>
              <div>
                <span>Intent-routed control</span>
                <strong>{intent.metrics.find((metric) => metric.id === "precision_at_3")?.display}</strong>
                <small>P@3 after the explicit collection gate</small>
              </div>
              <p>routing control, not learned retrieval quality · metrics test deterministic filter integrity; values are not probabilities.</p>
            </div>
          ) : null}
          {intent.raw_score_order?.length ? (
            <details className="pixel-raw-score-order" open>
              <summary>Complete ungated Qwen score order</summary>
              <ol>
                {intent.raw_score_order.map((row) => (
                  <li key={`${row.source_search_rank}:${row.content_ref}`}>
                    <span>{row.asset_id}</span>
                    <small>source search rank {row.source_search_rank}</small>
                    <strong>{String(row.score)}</strong>
                  </li>
                ))}
              </ol>
              <p>Exact recorded cosine order before the intent collection gate; geometry, not probability.</p>
            </details>
          ) : null}
          <div className="pixel-output">
            <span>{intent.output.label}</span>
            <strong>{intent.output.state === "not_available" ? "No external output" : "Recorded external output"}</strong>
            <p>{intent.output.caveat}</p>
            <dl>
              <div>
                <dt>New asset</dt>
                <dd title={intent.output.content_ref ?? undefined}>
                  {intent.output.content_ref ? shortDigest(intent.output.content_ref) : "not recorded"}
                </dd>
              </div>
              <div><dt>Rollback</dt><dd title={intent.output.rollback_ref}>{shortDigest(intent.output.rollback_ref)}</dd></div>
            </dl>
            {intent.output.history?.length ? (
              <ol className="pixel-output-history" aria-label="Governed output iterations">
                {intent.output.history.map((entry) => (
                  <li key={entry.evidence_id}>
                    <span>Rejected predecessor</span>
                    <strong>
                      {entry.verification.map((metric) => `${metric.label} ${metric.display}`).join(" · ")}
                    </strong>
                    <small title={entry.content_ref}>
                      {shortDigest(entry.content_ref)} · failed {entry.verification
                        .filter((metric) => metric.passed === false)
                        .map((metric) => metric.target)
                        .join(", ")}
                    </small>
                  </li>
                ))}
                {intent.output.state === "not_available" ? null : (
                  <li className="pixel-output-selected">
                    <span>Selected output</span>
                    <strong>{intent.output.postprocess ? "Source-backed deterministic composite" : "Registered external output"}</strong>
                    <small>
                      {intent.output.postprocess
                        ? `${intent.output.postprocess.revision} · selected verifier ${verificationLabel.toLowerCase()}; no exact-RGB or aesthetic claim · provenance ${shortDigest(intent.output.postprocess.provenance_sha256)}`
                        : `immutable Khive registration · selected verifier ${verificationLabel.toLowerCase()}`}
                    </small>
                  </li>
                )}
              </ol>
            ) : null}
          </div>
          {artifact.qwen_diagnostics ? (
            <aside className="pixel-qwen-diagnostics" aria-label="Experimental Qwen diagnostics">
              <div><span>Local lemon − apple</span><strong>{artifact.qwen_diagnostics.local_lemon_minus_apple_margin.toFixed(6)}</strong></div>
              <div><span>Restyle retention</span><strong>{artifact.qwen_diagnostics.restyle_content_retention.toFixed(6)}</strong></div>
              <div><span>Claude − Van Gogh</span><strong>{artifact.qwen_diagnostics.style_margin.toFixed(6)}</strong></div>
              <p>{artifact.qwen_diagnostics.interpretation}. Cosines are geometry—not probability or validated style.</p>
            </aside>
          ) : null}
        </section>

      </div>
    </section>
  );
}

function ScoreOverview({ model, activeId }: { readonly model: ReportModel; readonly activeId: string | null }): ReactNode {
  const assets = rankedAssets(model);
  if (assets.length === 0) return null;
  return (
    <section className="score-overview" aria-labelledby="overview-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Conformal fit tiers</p>
          <h2 id="overview-heading">Intervals before point estimates.</h2>
        </div>
        <p>Fixed 0–1 conformal scale. Shared p-values remain one tie tier; no reranker breaks them apart.</p>
      </div>
      <div className="overview-grid">
        {assets.map((asset) => (
          <article key={asset.asset_id} className={`overview-row ${activeId === asset.asset_id ? "overview-active" : ""}`}>
            <div><span>{conformalFitTierLabel(asset, model).split(" · ")[0]}</span><strong>{asset.asset_id}</strong></div>
            <IntervalMark score={asset.score} interval={asset.interval} compact />
          </article>
        ))}
      </div>
    </section>
  );
}

function TieList({ model }: { readonly model: ReportModel }): ReactNode {
  const { ties, note } = model.report.comparisons;
  return (
    <section className="tie-section" aria-labelledby="tie-heading">
      <div>
        <p className="eyebrow">Paired comparisons</p>
        <h2 id="tie-heading">Ties are relations, not groups.</h2>
        <p>{note}</p>
      </div>
      {ties.length > 0 ? (
        <ol>
          {ties.map(([left, right]) => <li key={`${left}\u0000${right}`}><strong>{left}</strong><span>not distinguishable from</span><strong>{right}</strong></li>)}
        </ol>
      ) : <p className="empty-inline">No tie pair was reported.</p>}
    </section>
  );
}

function FilterBar({ filter, dispatch, scored, abstained }: {
  readonly filter: OutcomeFilter;
  readonly dispatch: Dispatch<ViewerAction>;
  readonly scored: number;
  readonly abstained: number;
}): ReactNode {
  const options: ReadonlyArray<{ readonly value: OutcomeFilter; readonly label: string; readonly count: number }> = [
    { value: "all", label: "All outcomes", count: scored + abstained },
    { value: "scored", label: "Ranked", count: scored },
    { value: "abstained", label: "Abstained", count: abstained },
  ];
  return (
    <div className="filter-bar" role="group" aria-label="Filter assets by outcome">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={filter === option.value}
          disabled={option.count === 0}
          onClick={() => dispatch({ type: "filter", filter: option.value })}
        >
          {option.label} <span>{option.count}</span>
        </button>
      ))}
    </div>
  );
}

function AssetCollection({ stateModel, activeId, filter, dispatch }: {
  readonly stateModel: ReportModel;
  readonly activeId: string | null;
  readonly filter: OutcomeFilter;
  readonly dispatch: Dispatch<ViewerAction>;
}): ReactNode {
  const scored = rankedAssets(stateModel);
  const abstained = abstainedAssets(stateModel) as readonly AbstainedAsset[];
  const visible: readonly Asset[] = filter === "scored"
    ? scored
    : filter === "abstained" ? abstained : [...scored, ...abstained];
  const selected = visible.find((asset) => asset.asset_id === activeId) ?? visible[0];
  if (scored.length + abstained.length === 0) {
    return <section className="empty-report"><p className="eyebrow">No candidates</p><h2>This report contains board evidence and provenance, but no assets.</h2></section>;
  }
  return (
    <details className="asset-record-audit">
      <summary>
        <span>Expanded candidate evidence</span>
        <small>{selected ? selected.asset_id : "No candidate matches the active filter"}</small>
      </summary>
      <section className="assets-section" aria-labelledby="assets-heading">
        <div className="section-heading assets-title">
          <div><p className="eyebrow">Selected candidate evidence</p><h2 id="assets-heading">One complete record, on request.</h2></div>
          <FilterBar filter={filter} dispatch={dispatch} scored={scored.length} abstained={abstained.length} />
        </div>
        {selected ? (
          <div className="asset-group selected-asset-group">
            <h3 className="group-title">
              {selected.state === "scored" ? conformalFitTierLabel(selected, stateModel).split(" · ")[0] : "Abstained"}
              <span>{selected.state === "scored" ? conformalFitTierLabel(selected, stateModel) : "unranked by design"}</span>
            </h3>
            <AssetCard asset={selected} model={stateModel} active dispatch={dispatch} />
          </div>
        ) : <p className="empty-inline">No candidates match this filter.</p>}
      </section>
    </details>
  );
}

function KeyValueRows({ value }: { readonly value: Readonly<Record<string, unknown>> }): ReactNode {
  const rows = Object.entries(value).toSorted(([left], [right]) => left.localeCompare(right));
  return (
    <dl className="key-value-rows">
      {rows.map(([key, raw]) => (
        <div key={key}><dt>{humanizeToken(key)}</dt><dd>{raw === null ? "null" : typeof raw === "object" ? JSON.stringify(raw) : String(raw)}</dd></div>
      ))}
    </dl>
  );
}

function DetailsAndProvenance({ model }: { readonly model: ReportModel }): ReactNode {
  const { board, board_stats: stats, provenance } = model.report;
  const engineSourceComplete = provenance.engine.source_repository && provenance.engine.source_revision && provenance.engine.source_dirty !== undefined;
  return (
    <section className="details-grid">
      <details>
        <summary><span>Board & fit</span><small>score-bearing policy</small></summary>
        <div className="details-body">
          <KeyValueRows value={{
            board_id: board.id,
            built_at: board.built_at,
            references: board.n_references,
            effective_references: board.n_eff,
            categories: board.categories.length,
            model: board.representation.style.model,
            model_revision: board.representation.style.revision,
            dimensions: board.representation.style.dim,
          }} />
          <h3>Fit policy</h3>
          <KeyValueRows value={board.fit} />
          <h3>Board flags</h3>
          <FlagList flags={stats.flags} />
          {stats.flags.length === 0 ? <p>No board-level flags were reported.</p> : null}
          <h3>Reference leverage</h3>
          <ol className="leverage-list">
            {stats.leverage.map((item) => <li key={item.reference_id}><span>#{item.rank} {item.reference_id}</span><strong>{formatNumber(item.delta_tightness)}</strong></li>)}
          </ol>
        </div>
      </details>
      <details>
        <summary><span>Provenance & identity</span><small>model, schema, invocation</small></summary>
        <div className="details-body">
          <h3>Engine</h3>
          <KeyValueRows value={{ name: provenance.engine.name, version: provenance.engine.version }} />
          {engineSourceComplete ? (
            <KeyValueRows value={{
              source_repository: provenance.engine.source_repository,
              source_revision: provenance.engine.source_revision,
              source_dirty: provenance.engine.source_dirty,
            }} />
          ) : <p className="unavailable-note">Engine source revision was not recorded.</p>}
          {provenance.engine.source_dirty ? <p className="warning-note">Dirty source: this report cannot be reproduced from the named revision alone.</p> : null}
          <h3>Visual model / descriptor</h3>
          <KeyValueRows value={{ ...provenance.model }} />
          <h3>Khive / BlobStore identity</h3>
          <p className="unavailable-note">
            Report 1.1 records each asset’s original SHA-256 and source string above, but it does
            not carry a Khive BlobStore content_ref. This viewer never guesses a locator from a
            path, asset id, or thumbnail.
          </p>
          <h3>Preference learning</h3>
          <p className="unavailable-note">
            A learned FANN preference probability is not part of this report. It remains separate
            from the exact conformal compatibility evidence.
          </p>
          <h3>Schema</h3>
          {provenance.schema ? <KeyValueRows value={{ ...provenance.schema }} /> : <p className="unavailable-note">Schema hash was not recorded in report version 1.0.</p>}
          <h3>Run</h3>
          <KeyValueRows value={{ created_at: provenance.created_at, seed: provenance.seed }} />
          <details className="command-details">
            <summary>Reveal potentially sensitive invocation</summary>
            <p>{provenance.command}</p>
            {provenance.argv ? <ol>{provenance.argv.map((token, index) => <li key={`${index}-${token}`}>{token}</li>)}</ol> : <p>Structured argv was not recorded in report version 1.0.</p>}
          </details>
        </div>
      </details>
    </section>
  );
}

function ReportDiagnostics({ model }: { readonly model: ReportModel }): ReactNode {
  if (model.diagnostics.length === 0) return null;
  return (
    <section className="diagnostics" aria-labelledby="diagnostics-heading">
      <div><p className="eyebrow">Compatibility diagnostics</p><h2 id="diagnostics-heading">The report opened with limits.</h2></div>
      <IssueList issues={model.diagnostics} />
    </section>
  );
}

function ReportView({
  model,
  activeId,
  filter,
  dispatch,
  onFile,
}: {
  readonly model: ReportModel;
  readonly activeId: string | null;
  readonly filter: OutcomeFilter;
  readonly dispatch: Dispatch<ViewerAction>;
  readonly onFile: (file: File) => void;
}): ReactNode {
  const report = model.report;
  return (
    <main className="report-page">
      <header className="report-header">
        <div className="report-nav">
          <Wordmark />
          <div className="report-nav-actions">
            <span className="experimental-pill">Experimental measurement</span>
            {model.origin.kind === "local-file" ? <FileControl onFile={onFile} /> : <span className="offline-pill">Standalone · offline</span>}
          </div>
        </div>
        <div className="report-title-grid">
          <div>
            <p className="eyebrow">Board review · schema {report.schema_version}</p>
            <h1>{report.board.name || "Untitled board"}</h1>
            <p className="report-deck">A governed reading of what fits, where the board stretches, and when the evidence refuses to decide.</p>
          </div>
          <dl className="board-identity">
            <div><dt>Board identity</dt><dd title={report.board.id}>{shortDigest(report.board.id)}</dd></div>
            <div><dt>Origin</dt><dd>{model.origin.label}</dd></div>
            <div><dt>Visual model</dt><dd>{report.board.representation.style.model}</dd></div>
          </dl>
        </div>
      </header>
      <MechanismGuide />
      <StoryStrip model={model} />
      <GovernedReferenceBoard model={model} />
      <CandidateHeroGrid model={model} activeId={activeId} dispatch={dispatch} />
      <CoreVerificationLine model={model} />
      <AssetCollection stateModel={model} activeId={activeId} filter={filter} dispatch={dispatch} />
      <PixelRagLab model={model} />
      <FireflyMeasuredLoop />
      <PreferenceReplayPanel evidence={measuredPreferenceReplayEvidence} model={model} />
      <details className="report-measurement-audit">
        <summary>Compatibility audit &amp; comparisons</summary>
        <div>
          <ReportDiagnostics model={model} />
          <ScoreOverview model={model} activeId={activeId} />
          <TieList model={model} />
        </div>
      </details>
      <DetailsAndProvenance model={model} />
      <footer className="report-footer">
        <Wordmark />
        <p>Contract and presentation evidence only. External aesthetic validity requires the registered evaluation gates and human reliability study.</p>
        <span>No network · no recomputation · no merged preference score</span>
      </footer>
    </main>
  );
}

export function ViewerApp(): ReactNode {
  const [state, dispatch] = useReducer(viewerReducer, initialViewerState);
  const nextRequestId = useRef(0);
  const embeddedStarted = useRef(false);

  const load = useCallback(async (source: ReportSource) => {
    const requestId = ++nextRequestId.current;
    dispatch({ type: "load-started", origin: source.origin, requestId });
    const read = await source.read();
    if (!read.ok) {
      dispatch({ type: "load-failed", origin: source.origin, requestId, issues: [read.issue] });
      return;
    }
    const decoded = await decoder.decode(read.bytes, source.origin);
    if (!decoded.ok) {
      dispatch({ type: "load-failed", origin: source.origin, requestId, issues: decoded.issues });
      return;
    }
    dispatch({ type: "load-succeeded", origin: source.origin, requestId, model: decoded.model });
  }, []);

  const onFile = useCallback((file: File) => void load(new LocalFileSource(file)), [load]);

  useEffect(() => {
    if (!embeddedStarted.current && hasEmbeddedPayload(document)) {
      embeddedStarted.current = true;
      void load(new EmbeddedSource(document));
    }
  }, [load]);

  const activeId = useMemo(() => activeAssetId(state), [state]);

  if (state.phase === "awaiting-file") return <AwaitingFileView onFile={onFile} />;
  if (state.phase === "loading") return <LoadingView label={state.origin.label} />;
  if (state.phase === "failed") {
    return <LoadErrorView label={state.origin?.label ?? "Unknown source"} issues={state.issues} allowFile={state.origin?.kind !== "embedded"} onFile={onFile} />;
  }
  return <ReportView model={state.model} activeId={activeId} filter={state.outcomeFilter} dispatch={dispatch} onFile={onFile} />;
}

export const __test = { FilterBar, ReportView, groupDeterministicVariants, preferenceFitTierLabel };
