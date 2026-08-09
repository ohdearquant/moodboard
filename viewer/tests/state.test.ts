import { activeAssetId, initialViewerState, viewerReducer } from "../src/state";
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
});
