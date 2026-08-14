import { activeAssetId, initialViewerState, rankedAssets, viewerReducer } from "../src/state";
import type { ReportModel } from "../src/model";

const origin = { kind: "local-file", label: "report.json" } as const;
const model = { report: { assets: [] } } as unknown as ReportModel;

describe("viewer state", () => {
  it("ignores stale asynchronous load completions", () => {
    const loadingA = viewerReducer(initialViewerState, { type: "load-started", origin, requestId: 1 });
    const loadingB = viewerReducer(loadingA, { type: "load-started", origin, requestId: 2 });
    const stale = viewerReducer(loadingB, {
      type: "load-succeeded",
      origin,
      requestId: 1,
      model,
    });
    expect(stale).toEqual(loadingB);
  });

  it("prioritizes hover, then focus, then deliberate selection", () => {
    let state = viewerReducer(
      viewerReducer(initialViewerState, { type: "load-started", origin, requestId: 1 }),
      { type: "load-succeeded", origin, requestId: 1, model },
    );
    state = viewerReducer(state, { type: "select", assetId: "selected" });
    state = viewerReducer(state, { type: "focus", assetId: "focused" });
    state = viewerReducer(state, { type: "hover", assetId: "hovered" });
    expect(activeAssetId(state)).toBe("hovered");
    state = viewerReducer(state, { type: "hover", assetId: null });
    expect(activeAssetId(state)).toBe("focused");
    state = viewerReducer(state, { type: "focus", assetId: null });
    expect(activeAssetId(state)).toBe("selected");
  });

  it("uses engine rank while preserving report order for equal ranks", () => {
    const rankedModel = {
      report: {
        assets: [
          { asset_id: "first-in-report-tie", rank: 2, state: "scored" },
          { asset_id: "rank-one", rank: 1, state: "scored" },
          { asset_id: "second-in-report-tie", rank: 2, state: "scored" },
          { asset_id: "refused", state: "abstained" },
        ],
      },
    } as unknown as ReportModel;
    expect(rankedAssets(rankedModel).map((asset) => asset.asset_id)).toEqual([
      "rank-one",
      "first-in-report-tie",
      "second-in-report-tie",
    ]);
  });

  it("keeps hero inspection and filtered detail atomically aligned", () => {
    const alignedModel = {
      report: {
        assets: [
          { asset_id: "scored", rank: 1, state: "scored" },
          { asset_id: "refused", state: "abstained" },
        ],
      },
    } as unknown as ReportModel;
    let state = viewerReducer(
      viewerReducer(initialViewerState, { type: "load-started", origin, requestId: 1 }),
      { type: "load-succeeded", origin, requestId: 1, model: alignedModel },
    );
    state = viewerReducer(state, { type: "select", assetId: "scored" });

    state = viewerReducer(state, { type: "filter", filter: "abstained" });
    expect(state.phase).toBe("ready");
    if (state.phase !== "ready") throw new Error("expected ready state");
    expect(state.outcomeFilter).toBe("abstained");
    expect(state.selectedAssetId).toBe("refused");
    expect(activeAssetId(state)).toBe("refused");

    state = viewerReducer(state, { type: "inspect", assetId: "scored" });
    expect(state.phase).toBe("ready");
    if (state.phase !== "ready") throw new Error("expected ready state");
    expect(state.outcomeFilter).toBe("all");
    expect(state.selectedAssetId).toBe("scored");
    expect(activeAssetId(state)).toBe("scored");
  });
});
