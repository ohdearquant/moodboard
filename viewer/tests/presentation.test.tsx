import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import { __test } from "../src/App";
import { createReportDecoder } from "../src/decoder";
import { verifiedFireflyEvidence } from "../src/firefly";
import type { Asset, ReferenceEntry } from "../src/model";
import { pixelRagBridge, verifiedPixelRagArtifact } from "../src/pixel-rag";
import { measuredPreferenceReplayEvidence } from "../src/preference-replay";
import { acceptingProbe, encodeReport, fixtureBytes, fixtureObject, toLegacy } from "./helpers";

const origin = { kind: "embedded", label: "showcase fixture" } as const;
const noop = () => undefined;

async function modelFor(bytes = fixtureBytes()) {
  const result = await createReportDecoder(acceptingProbe).decode(bytes, origin);
  if (!result.ok) throw new Error(JSON.stringify(result.issues));
  return result.model;
}

function withPreferenceSource(model: Awaited<ReturnType<typeof modelFor>>) {
  if (!measuredPreferenceReplayEvidence) throw new Error("expected projected preference evidence");
  return {
    ...model,
    documentSha256: measuredPreferenceReplayEvidence.bindings.source_report_sha256,
  };
}

describe("editorial report presentation", () => {
  it("labels every LOO band as board sensitivity rather than a coverage interval", async () => {
    const { container } = render(
      <__test.ReportView model={await modelFor()} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const ranges = [...container.querySelectorAll(".interval")];
    expect(ranges.length).toBeGreaterThan(0);
    for (const range of ranges) {
      expect(range.textContent).toMatch(/LOO board-sensitivity range/i);
      expect(range.textContent).toMatch(
        /leave-one-reference-out re-scores.*need not contain the full-board p-value/i,
      );
      expect(range.getAttribute("aria-label")).toMatch(
        /leave-one-reference-out re-scores.*need not contain the full-board p-value/i,
      );
    }
    expect(container.textContent).not.toMatch(/90% interval|Level 0\.900|Intervals before point estimates/i);
  });

  it("frames the payload as an independent showcase without mutating it", async () => {
    const model = await modelFor();
    const payloadBoardName = model.report.board.name;
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    expect(screen.getByRole("heading", {
      level: 1,
      name: "Governed visual evidence.",
    })).toBeTruthy();
    expect(screen.getByText("Independent showcase fixture · board review · schema 1.1", { exact: true })).toBeTruthy();
    expect(screen.getByText(
      "Independent demo fixture · no Adobe endorsement or production data",
      { exact: true },
    )).toBeTruthy();
    expect(model.report.board.name).toBe(payloadBoardName);
  });

  it("separates board fit, Pixel routing, Firefly iteration, and preference", async () => {
    render(
      <__test.ReportView model={await modelFor()} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const guide = screen.getByRole("region", { name: "Four independent evidence mechanisms" });
    expect(guide.querySelectorAll("article")).toHaveLength(4);
    expect(within(guide).getByText("02 · Pixel RAG routing")).toBeTruthy();
    expect(within(guide).getByText(/separately recorded output is not the Firefly run below/i)).toBeTruthy();
    expect(within(guide).getByText("03 · Firefly iteration")).toBeTruthy();
    expect(within(guide).getByText("04 · Pairwise preference")).toBeTruthy();
  });

  it("renders the exact policy_simulated evidence label", async () => {
    const model = withPreferenceSource(await modelFor());
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const panel = screen.getByRole("region", { name: "Governed preference replay" });
    expect(panel.textContent).toContain("policy_simulated");
  });

  it("renders the exact Pixel RAG routing-control honesty label", async () => {
    const model = await modelFor();
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const lab = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(lab.textContent).toContain("routing control, not learned retrieval quality");
  });

  it("distinguishes post-gate routing diagnostics from the protected-pixel gate", async () => {
    const model = withPreferenceSource(await modelFor());
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const lab = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(within(lab).getByText(/fixture metrics only.*no production traffic, external benchmark, or human relevance study/i)).toBeTruthy();
    expect(within(lab).getByText(/measured engine artifact.*retrieval and verification evidence shown below/i)).toBeTruthy();
    expect(lab.textContent).not.toContain("preference panel remains a governed fixture");
    expect(within(lab).getByRole("heading", {
      name: "Ranked within the declared collection gate",
    })).toBeTruthy();
    expect(within(lab).getByText(/no reranker.*ungated Khive rank retained on each card/i)).toBeTruthy();
    expect(lab.querySelector(".pixel-evidence-card")?.textContent).toMatch(
      /post-gate #1.*ungated Khive rank #4/i,
    );

    const cosine = lab.querySelector(".pixel-hit-score strong");
    expect(cosine?.textContent).toBe("0.843");
    expect(cosine?.getAttribute("title")).toBe("0.8432995826005936");
    expect(within(lab).queryByText("0.8432995826005936", { exact: true })).toBeNull();
    const ndcg = within(lab).getByText("0.922", { exact: true });
    expect(ndcg.getAttribute("title")).toBe("0.9217107771844201");
    expect(within(lab).getByText(/routing diagnostics.*outside-mask SSIM gate/i)).toBeTruthy();
    expect(within(lab).getByText(/routing metrics reported.*verification separately gated/i)).toBeTruthy();
    expect(lab.querySelector(".pixel-verification-status")?.textContent).toMatch(
      /outside-mask SSIM gate.*Passed/i,
    );
    expect(within(lab).getByText(
      /P@3 describes the explicit collection gate.*not an acceptance gate or probability/i,
    )).toBeTruthy();

    const rawTop = within(lab).getByText("0.963", { exact: true });
    expect(rawTop.getAttribute("title")).toBe("0.9627652950584888");
    expect(within(lab).queryByText("0.9627652950584888", { exact: true })).toBeNull();

    fireEvent.click(within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i }));
    expect(within(lab).getByText(/routing diagnostics.*edit verification not run/i)).toBeTruthy();
    expect(lab.querySelector(".pixel-verification-status")?.textContent).toMatch(
      /edit verification.*Not run/i,
    );
  });

  it("routes the same source through two honest, intent-specific Pixel RAG views", async () => {
    const model = withPreferenceSource(await modelFor());
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const lab = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(within(lab).getByRole("heading", { name: "Same pixels. Different evidence." })).toBeTruthy();
    expect(
      within(lab).getByRole("button", { name: /replace apple tree with lemon tree/i }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(within(lab).getByText("Confirmed · primary apple tree canopy and trunk")).toBeTruthy();
    expect(within(lab).getByText("showcase-replace-v1")).toBeTruthy();
    expect(within(lab).getAllByText(/Public domain|CC0/).length).toBeGreaterThanOrEqual(3);
    expect(within(lab).getByText(/Execute outside Moodboard\/Khive/i)).toBeTruthy();
    expect(within(lab).getByText("Immutable output")).toBeTruthy();
    expect(within(lab).getByText("Raw Qwen geometry · ungated")).toBeTruthy();
    expect(within(lab).getByText("Intent-routed control")).toBeTruthy();
    expect(within(lab).getByText(/P@3 describes the explicit collection gate/i)).toBeTruthy();
    expect(within(lab).getByText("Complete ungated Qwen score order")).toBeTruthy();
    expect(within(lab).getByText("fruit_apple_meadow")).toBeTruthy();
    expect(within(lab).getByText("0.963", { exact: true })).toBeTruthy();
    expect(within(lab).getByText("Rejected predecessor")).toBeTruthy();
    expect(within(lab).getByText("Source-backed deterministic composite")).toBeTruthy();
    expect(within(lab).getByText(/no exact-RGB or aesthetic claim/i)).toBeTruthy();
    expect(within(lab).getByLabelText("Experimental Qwen diagnostics")).toBeTruthy();
    expect(within(lab).getByText(/geometry—not probability or validated style/i)).toBeTruthy();

    fireEvent.click(within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i }));
    expect(within(lab).getByText("Whole frame")).toBeTruthy();
    expect(within(lab).getByText("showcase-restyle-v1")).toBeTruthy();
    expect(within(lab).getByText("nDCG@5")).toBeTruthy();
    expect(within(lab).getByText(/layout constraint declared; verifier not run/i)).toBeTruthy();
    expect(within(lab).getByText("Not run")).toBeTruthy();
    expect(within(lab).queryByRole("region", { name: "Governed preference replay" })).toBeNull();
    expect(screen.getByRole("region", { name: "Governed preference replay" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Measured Firefly iteration loop" })).toBeTruthy();
    expect(container.querySelectorAll(".pixel-evidence-card")).toHaveLength(3);
  });

  it("renders verified Pixel RAG media for both measured intents without placeholders", async () => {
    const model = withPreferenceSource(await modelFor());
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const lab = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    const expectFourVerifiedImages = (intentId: "local_replace" | "global_restyle") => {
      const intent = verifiedPixelRagArtifact.intents.find((candidate) => candidate.id === intentId);
      if (!intent) throw new Error(`expected measured Pixel RAG intent ${intentId}`);
      const images = [...lab.querySelectorAll("img")];
      expect(images).toHaveLength(4);
      expect(images.map((image) => image.alt)).toEqual([
        `Source asset ${verifiedPixelRagArtifact.source.asset_id}`,
        ...intent.evidence.map((hit) => `Retrieved evidence ${hit.title}`),
      ]);
      expect(images.every((image) => image.src.startsWith("data:image/"))).toBe(true);
      expect(lab.querySelectorAll(".image-fallback")).toHaveLength(0);
      expect(within(lab).queryByText("Source fixture does not resolve in this report.")).toBeNull();
      expect(within(lab).queryByText("Evidence preview does not resolve in this report.")).toBeNull();
    };

    expect(pixelRagBridge.state).toBe("projected");
    expect(verifiedPixelRagArtifact.evidence_status).toBe("measured_run");
    expect(within(lab).getByText("Measured run")).toBeTruthy();
    expect(
      within(lab).getByRole("button", { name: /replace apple tree with lemon tree/i })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expectFourVerifiedImages("local_replace");

    fireEvent.click(within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i }));
    expect(
      within(lab).getByRole("button", { name: /restyle as Claude Lorrain/i })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expectFourVerifiedImages("global_restyle");
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
    expect(screen.getAllByText(/conformal fit p-value /i).length).toBeGreaterThanOrEqual(1);
    expect(document.body.textContent).not.toMatch(/\bboard-fit p\b|\bp\s+0\.|\bp=/i);
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
    expect(within(strip as HTMLElement).getByText("0.542", { exact: true })).toBeTruthy();
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

  it("explains the baseline and ranking without internal engine terminology", async () => {
    const model = await modelFor();
    const dispatch = vi.fn();
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={dispatch} onFile={noop} />,
    );

    const references = screen.getByRole("region", { name: "Governed reference board" });
    expect(within(references).getByRole("heading", {
      name: `Scoring baseline · ${model.report.references.length} original artworks`,
    })).toBeTruthy();
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

    expect(screen.getAllByText(/k-nearest cosine nonconformity.*higher p.*less evidence of incompatibility.*not an approval probability.*no reranker/i)).toHaveLength(2);
    expect(container.textContent).not.toContain("engine-provided order");
    const audit = screen.getByText("Compatibility audit & comparisons").closest("details");
    expect(audit?.hasAttribute("open")).toBe(false);
  });

  it("opens with four independent mechanisms and never implies a hidden blended score", async () => {
    const model = await modelFor();
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const guide = screen.getByRole("region", { name: "Four independent evidence mechanisms" });
    expect(screen.getByText(/one offline evidence packet.*four independent decisions.*none is allowed to impersonate another/i)).toBeTruthy();
    expect(within(guide).getByText("01 · Embedding-relative board compatibility")).toBeTruthy();
    expect(within(guide).getByText(/k-nearest cosine nonconformity.*higher p.*less evidence of incompatibility.*no reranker/i)).toBeTruthy();
    expect(within(guide).getByText(/Pixel RAG routing/i)).toBeTruthy();
    expect(within(guide).getByText(/separately recorded output is not the Firefly run below/i)).toBeTruthy();
    expect(within(guide).getByText(/outputs do not change board fit/i)).toBeTruthy();
    expect(within(guide).getByText(/does not rerank the 24 images/i)).toBeTruthy();
  });

  it("groups original, crop, and mirror by source and marks reference-byte reuse", async () => {
    const model = await modelFor();
    const assets = model.report.assets.slice(0, 6).map((asset, index): typeof asset => {
      const source = index < 3 ? "source-alpha" : "source-beta";
      const variants = ["original.jpg", "center-crop-90pct.png", "horizontal-mirror.png"];
      const reference = index === 0
        ? model.report.references[0]
        : index === 3 ? model.report.references[1] : undefined;
      if (reference && asset.image) {
        return {
          ...asset,
          asset_id: `${source}--${variants[index % 3]}`,
          image: { ...asset.image, content_sha256: reference.content_sha256 },
        };
      }
      return { ...asset, asset_id: `${source}--${variants[index % 3]}` };
    });
    const candidateSources = new Map(
      assets.map((asset, index) => [
        asset.asset_id,
        model.candidateSources.get(model.report.assets[index]!.asset_id)!,
      ]),
    );
    const groupedModel: typeof model = {
      ...model,
      report: { ...model.report, assets },
      assetsById: new Map(assets.map((asset) => [asset.asset_id, asset])),
      candidateSources,
    };
    const grouped = __test.groupDeterministicVariants(groupedModel);

    expect(grouped).toHaveLength(2);
    expect(grouped[0]?.sourceId).toBe("source-alpha");
    expect(grouped[0]?.original.asset_id).toBe("source-alpha--original.jpg");
    expect(grouped[0]?.originalReusesReferenceBytes).toBe(true);
    expect(grouped[0]?.transforms.map((asset) => asset.asset_id)).toEqual([
      "source-alpha--center-crop-90pct.png",
      "source-alpha--horizontal-mirror.png",
    ]);

    render(
      <__test.ReportView
        model={groupedModel}
        activeId={null}
        filter="all"
        dispatch={noop}
        onFile={noop}
      />,
    );
    const stress = screen.getByRole("region", { name: "Deterministic variant stress test" });
    expect(within(stress).getAllByTestId("variant-source-family")).toHaveLength(2);
    expect(within(stress).getAllByText("Same bytes as reference")).toHaveLength(2);
    expect(within(stress).getAllByText("90% center crop")).toHaveLength(2);
    expect(within(stress).getAllByText("Horizontal mirror")).toHaveLength(2);
    expect(within(stress).getByText(/less evidence of incompatibility.*equal p-values share a tier.*no reranker/i)).toBeTruthy();
  });

  it("renders the real 8-by-3 integration corpus as eight source families, not 24 outputs", async () => {
    const fixture = fixtureObject() as any;
    const references = fixture.references.slice(0, 8);
    const template = (fixture.assets as Asset[]).find((asset) => asset.state === "scored");
    if (!template || !template.image) throw new Error("expected one scored image template");
    const rankSchedule = [
      ...Array.from({ length: 9 }, () => ({ rank: 1, score: 1 })),
      ...Array.from({ length: 8 }, () => ({ rank: 10, score: 8 / 9 })),
      ...Array.from({ length: 4 }, () => ({ rank: 18, score: 7 / 9 })),
      { rank: 22, score: 2 / 9 },
      ...Array.from({ length: 2 }, () => ({ rank: 23, score: 1 / 9 })),
    ] as const;
    const assets = (references as ReferenceEntry[]).flatMap((reference, sourceIndex) => {
      const source = sourceIndex === 3
        ? "style_claude_trojan_women"
        : `source-${String(sourceIndex + 1).padStart(2, "0")}`;
      return ["original.jpg", "center-crop-90pct.png", "horizontal-mirror.png"].map(
        (variant, variantIndex) => {
          const scheduled = rankSchedule[sourceIndex * 3 + variantIndex];
          if (!scheduled) throw new Error("expected a complete 24-candidate rank schedule");
          const referenceOutlier = sourceIndex === 3 && variantIndex === 0;
          return {
            ...template,
            asset_id: `${source}--${variant}`,
            source: `fixture://${source}/${variant}`,
            image: {
              ...template.image,
              content_sha256: variantIndex === 0
                ? reference.content_sha256
                : `${sourceIndex + 1}${variantIndex}`.padEnd(64, "0"),
            },
            axes: { ...template.axes, style: referenceOutlier ? 1 / 9 : scheduled.score },
            rank: referenceOutlier ? 23 : scheduled.rank,
            score: referenceOutlier ? 1 / 9 : scheduled.score,
          };
        },
      );
    });
    const bytes = encodeReport({
      ...fixture,
      board: {
        ...fixture.board,
        n_references: references.length,
        n_eff: 4,
        requested_alpha: 0.2,
        supported_alpha: 0.2,
        categories: [{
          ...fixture.board.categories[0],
          n_local: references.length,
          member_ids: (references as ReferenceEntry[]).map((reference) => reference.reference_id),
        }],
      },
      board_stats: {
        ...fixture.board_stats,
        flags: ["near_duplicate_references"],
      },
      references,
      assets,
      comparisons: { ...fixture.comparisons, ties: [] },
    });
    const model = await modelFor(bytes);

    expect(__test.preferenceFitTierLabel(model, "source-06--horizontal-mirror.png")).toMatch(
      /Fit tier 3\/5.*conformal fit p-value 0\.778.*tied with 3 others/i,
    );

    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const stress = screen.getByRole("region", { name: "Deterministic variant stress test" });
    expect(within(stress).getAllByTestId("variant-source-family")).toHaveLength(8);
    expect(within(stress).getAllByText("Same bytes as reference")).toHaveLength(8);
    expect(within(stress).getAllByText("90% center crop")).toHaveLength(8);
    expect(within(stress).getAllByText("Horizontal mirror")).toHaveLength(8);
    expect(within(stress).getByText("Full 24-candidate record")).toBeTruthy();
    expect(within(stress).getAllByText(/Fit tier 1\/5.*conformal fit p-value 1\.000.*tied with 8 others/i).length).toBeGreaterThan(0);
    expect(__test.tiePeerLabel(2)).toBe("tied with 1 other");
    const audit = within(stress).getByText("Full 24-candidate record").closest("details");
    expect(audit?.textContent).toContain("reported competition rank 1");
    expect(document.body.textContent).not.toContain("24 measured outputs");

    const overview = screen.getByRole("region", { name: "Board measurement overview" });
    expect(within(overview).getByText("8 sources · 24 deterministic checks", { exact: true })).toBeTruthy();
    const abstentionCopy = within(overview).getByText(/includes exact source copies.*0 abstained.*not a coverage result/i);
    expect(abstentionCopy.tagName).toBe("SPAN");

    const diagnostic = screen.getByRole("note", { name: "Board support diagnostic" });
    expect(diagnostic.textContent).toMatch(
      /Board support is weak.*8 references.*effective support 4\/8.*near-duplicate warning/i,
    );
    expect(diagnostic.textContent).toMatch(/α 0\.2.*coarse.*board-specific/i);
    expect(diagnostic.textContent).toMatch(
      /style_claude_trojan_women--original\.jpg.*conformal fit p-value 0\.111.*exact board member.*not a scoring malfunction/i,
    );
    expect(diagnostic.textContent).toMatch(/α 0\.2.*not abstention.*0 abstained.*not a coverage claim/i);
  });

  it("renders the real policy-simulated replay as a separate eight-probe mechanism", async () => {
    const model = withPreferenceSource(await modelFor());
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const pixel = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    expect(within(pixel).queryByRole("region", { name: "Governed preference replay" })).toBeNull();
    const panel = screen.getByRole("region", { name: "Governed preference replay" });
    const firefly = screen.getByRole("region", { name: "Measured Firefly iteration loop" });
    expect(pixel.nextElementSibling).toBe(firefly);
    expect(panel.previousElementSibling).toBe(firefly);
    expect(within(panel).getByText(
      "Immutable retrain + replay demonstration · policy_simulated · not trained on these Firefly outputs",
    )).toBeTruthy();
    expect(within(panel).getByRole("heading", {
      name: /snapshot isolation and non-mutation/i,
    })).toBeTruthy();
    expect(within(panel).getByText("0.025%")).toBeTruthy();
    expect(within(panel).getByText("50.114%")).toBeTruthy();
    expect(within(panel).getByText(/mean P\(policy-B-preferred side\).*roughly neutral/i)).toBeTruthy();
    expect(within(panel).getByText("Moved toward indifference", { exact: true })).toBeTruthy();
    expect(within(panel).getByText(
      /does not establish that Policy B was learned or that preference adaptation succeeded/i,
    )).toBeTruthy();
    expect(panel.textContent).not.toContain("Signal moved");
    expect(within(panel).getByText(/64 decisive train judgments.*publishes at event 112/i)).toBeTruthy();
    expect(within(panel).getByText(/96 additional decisive train judgments.*retrains on 160.*event 208.*no online update/i)).toBeTruthy();
    expect(within(panel).getByText(/\+96 simulated train judgments/)).toBeTruthy();
    expect(within(panel).getByText(/24 image records.*10 measured features each.*208 recorded pair events.*8 frozen, unjudged policy-disagreement probes/i)).toBeTruthy();
    expect(within(panel).getByText(/absent from all 208 recorded events/i)).toBeTruthy();
    expect(within(panel).getByText("WHAT THE MODEL IS", { exact: true })).toBeTruthy();
    expect(within(panel).getByText(/10-feature pairwise logistic model.*conditional on a decisive outcome.*left-minus-right/i)).toBeTruthy();
    expect(within(panel).getByText(/three visual-similarity features.*frozen Qwen embeddings.*seven.*conformal.*palette.*composition/i)).toBeTruthy();
    expect(within(panel).getByText("THE EXPERIMENT", { exact: true })).toBeTruthy();
    expect(within(panel).getByText(/64 decisive Policy-A train judgments.*16 Policy-A calibration decisions.*16 separately selected calibration ties.*16 Policy-A test judgments/i)).toBeTruthy();
    expect(within(panel).getByText(/96 decisive Policy-B train judgments.*160 cumulative train judgments.*event 208/i)).toBeTruthy();
    expect(within(panel).getByText(/Policy-B labeling rule.*exact sign-reversed 10-feature weights.*Policy-A labeling rule.*deliberately disagree/i)).toBeTruthy();
    expect(within(panel).getByText(/16 calibration ties.*separate disclosed lowest-margin tie-selection rule/i)).toBeTruthy();
    expect(within(panel).getByText(/Each row shows P\(policy-B-preferred side\).*Snapshot A.*Snapshot B/i)).toBeTruthy();
    expect(panel.textContent).not.toMatch(/changed its taste|held-out probes|counter-style side|untouched probes/i);
    expect(panel.textContent).not.toMatch(/generic score/i);
    const pair = within(panel).getByRole("region", { name: "Concrete frozen preference pair" });
    expect(within(pair).getByText(/cohesive style policy/i)).toBeTruthy();
    expect(within(pair).getByText(/counter style exploration policy/i)).toBeTruthy();
    expect(within(pair).getByText(/Claude.*Ford/i)).toBeTruthy();
    expect(within(pair).getByText(/Van Gogh.*Wheat field/i)).toBeTruthy();
    expect(within(pair).queryByText(/source fit tier 18/i)).toBeNull();
    expect(within(panel).getAllByTestId("preference-probe-row")).toHaveLength(8);
    expect(within(panel).getByText(/FANN A\+B.*A probe predictions unchanged.*restart predictions exact/)).toBeTruthy();
    const audit = within(panel).getByText("Audit & identity").closest("details");
    expect(audit?.hasAttribute("open")).toBe(false);
    expect(container.querySelectorAll(".preference-measured")).toHaveLength(1);
  });

  it("refuses to attach the frozen preference replay to a different report", async () => {
    const model = await modelFor();
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    expect(screen.queryByRole("region", { name: "Governed preference replay" })).toBeNull();
    expect(screen.getByRole("status", { name: /preference replay not shown/i }).textContent).toMatch(
      /Preference replay not shown.*source report does not match/i,
    );
  });

  it("shows the frozen Firefly loop between Pixel RAG and the independent preference replay", async () => {
    const model = withPreferenceSource(await modelFor());
    const { container } = render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const pixel = screen.getByRole("region", { name: "Pixel RAG intent lab" });
    const firefly = screen.getByRole("region", { name: "Measured Firefly iteration loop" });
    expect(__test.fireflySourceMatchesPixel()).toBe(true);
    expect(__test.fireflySourceMatchesPixel({
      ...verifiedFireflyEvidence.source,
      sha256: "f".repeat(64),
    })).toBe(false);
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
    const rawLocality = within(firefly).getByText(/0\.175.*0\.950.*FAIL/i);
    expect(rawLocality.getAttribute("title")).toContain("0.174819482254");
    expect(within(firefly).getByText(/Exact outside-mask RGB equality/i)).toBeTruthy();
    expect(within(firefly).getByText(/432,192 protected pixels.*0 changed.*max channel error 0/i)).toBeTruthy();
    expect(within(firefly).getByText(/deterministic compositor invariant.*not intrinsic generator locality.*not a seam-quality claim/i)).toBeTruthy();
    expect(within(firefly).getByText(/Restyle acceptance.*not computed/i)).toBeTruthy();
    expect(within(firefly).getAllByText(/not attached as direct generator image references/i).length).toBeGreaterThanOrEqual(1);
    expect(within(firefly).getByText(/premium Gemini 3\.1 model was not used/i)).toBeTruthy();

    const previews = within(firefly).getAllByRole("img");
    expect(previews).toHaveLength(4);
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

  it("compares the exact Firefly source, raw output, and governed selected output", async () => {
    const model = withPreferenceSource(await modelFor());
    render(
      <__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />,
    );

    const firefly = screen.getByRole("region", { name: "Measured Firefly iteration loop" });
    const comparison = within(firefly).getByRole("list", {
      name: "Frozen Firefly iteration timeline",
    });
    const images = within(comparison).getAllByRole("img");
    expect(images.map((image) => image.getAttribute("alt"))).toEqual([
      "Original governed apple-tree source",
      "Raw Firefly replacement output that failed locality",
      "Governed selected Firefly replacement output",
    ]);
    expect(images.every((image) => image.getAttribute("src")?.startsWith("data:image/"))).toBe(true);
    expect(images[0]?.getAttribute("width")).toBe("1280");
    expect(images[0]?.getAttribute("height")).toBe("960");
    expect(comparison.querySelector(".image-fallback")).toBeNull();
    expect(comparison.querySelector(".firefly-step-structural")).toBeNull();

    const rejection = within(firefly).getByRole("status", {
      name: "Iteration 1 structural rejection",
    });
    expect(rejection.textContent).toMatch(/iteration 1.*FAIL.*structural aspect ratio/i);
    expect(rejection.querySelector("img")).toBeNull();
    expect(within(comparison).getByText("RAW", { exact: true })).toBeTruthy();
    expect(within(comparison).getByText("GOV", { exact: true })).toBeTruthy();
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
    expect((container.textContent ?? "").split(payload).length - 1).toBeGreaterThanOrEqual(1);
    expect(container.querySelector("script[data-unsafe]")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });

  it("sanitizes rendered asset and invocation paths without changing payload values", async () => {
    const report: any = fixtureObject();
    const repo = "/Users/example/projects/moodboard/.worktrees/demo-e2e";
    const rawSource = `${repo}/.cache/showcase-preference-run/candidates/${report.assets[0].asset_id}`;
    const rawRunPath = `${repo}/.cache/showcase-rank-report-v1/run-20260813-resume3/work/report-v1.1.json`;
    const rawRepoPath = `${repo}/eval/thresholds.json`;
    report.assets[0].source = rawSource;
    report.provenance.command = `moodboard rank ${rawSource} --output ${rawRunPath} --thresholds ${rawRepoPath}`;
    report.provenance.argv = ["moodboard", "rank", rawSource, "--output", rawRunPath, "--thresholds", rawRepoPath];
    const model = await modelFor(encodeReport(report));
    const { container } = render(
      <__test.ReportView model={model} activeId={report.assets[0].asset_id} filter="all" dispatch={noop} onFile={noop} />,
    );

    expect(container.textContent).not.toContain("/Users/");
    expect(container.querySelector('[title*="/Users/"]')).toBeNull();
    expect(container.textContent).toContain(`$CACHE/showcase-preference-run/candidates/${report.assets[0].asset_id}`);
    expect(container.textContent).toContain("$RUN_DIR/work/report-v1.1.json");
    expect(container.textContent).toContain("$REPO/eval/thresholds.json");
    expect(model.report.assets[0]?.source).toBe(rawSource);
    expect(model.report.provenance.command).toContain(rawRunPath);
    expect(model.report.provenance.argv).toContain(rawRepoPath);
  });

  it("preserves the permanent v1.0 diagnostic presentation", async () => {
    const model = await modelFor(encodeReport(toLegacy(fixtureObject())));
    render(<__test.ReportView model={model} activeId={null} filter="all" dispatch={noop} onFile={noop} />);
    expect(screen.getAllByText("Candidate image was not included in report version 1.0.")).toHaveLength(1);
    expect(screen.getAllByText("Legacy axis values")).toHaveLength(1);
    expect(screen.getByText("Schema hash was not recorded in report version 1.0.")).toBeTruthy();
  });
});
