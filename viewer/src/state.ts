import type { Asset, ReportIssue, ReportModel, ReportOrigin, ScoredAsset } from "./model";

export type OutcomeFilter = "all" | "scored" | "abstained";

export type ViewerState =
  | { readonly phase: "awaiting-file" }
  | { readonly phase: "loading"; readonly origin: ReportOrigin; readonly requestId: number }
  | {
      readonly phase: "failed";
      readonly origin: ReportOrigin | null;
      readonly issues: readonly ReportIssue[];
    }
  | {
      readonly phase: "ready";
      readonly origin: ReportOrigin;
      readonly model: ReportModel;
      readonly selectedAssetId: string | null;
      readonly hoveredAssetId: string | null;
      readonly focusedAssetId: string | null;
      readonly outcomeFilter: OutcomeFilter;
    };

export type ViewerAction =
  | { readonly type: "load-started"; readonly origin: ReportOrigin; readonly requestId: number }
  | {
      readonly type: "load-succeeded";
      readonly origin: ReportOrigin;
      readonly requestId: number;
      readonly model: ReportModel;
    }
  | {
      readonly type: "load-failed";
      readonly origin: ReportOrigin;
      readonly requestId: number;
      readonly issues: readonly ReportIssue[];
    }
  | { readonly type: "select"; readonly assetId: string | null }
  | { readonly type: "inspect"; readonly assetId: string }
  | { readonly type: "hover"; readonly assetId: string | null }
  | { readonly type: "focus"; readonly assetId: string | null }
  | { readonly type: "filter"; readonly filter: OutcomeFilter }
  | { readonly type: "await-file" };

export const initialViewerState: ViewerState = { phase: "awaiting-file" };

export function viewerReducer(state: ViewerState, action: ViewerAction): ViewerState {
  switch (action.type) {
    case "load-started":
      return { phase: "loading", origin: action.origin, requestId: action.requestId };
    case "load-succeeded":
      if (state.phase !== "loading" || state.requestId !== action.requestId) return state;
      return {
        phase: "ready",
        origin: action.origin,
        model: action.model,
        selectedAssetId: null,
        hoveredAssetId: null,
        focusedAssetId: null,
        outcomeFilter: "all",
      };
    case "load-failed":
      if (state.phase !== "loading" || state.requestId !== action.requestId) return state;
      return { phase: "failed", origin: action.origin, issues: action.issues };
    case "await-file":
      return initialViewerState;
    case "select":
      return state.phase === "ready" ? { ...state, selectedAssetId: action.assetId } : state;
    case "inspect":
      return state.phase === "ready"
        ? {
            ...state,
            selectedAssetId: action.assetId,
            hoveredAssetId: null,
            focusedAssetId: null,
            outcomeFilter: "all",
          }
        : state;
    case "hover":
      return state.phase === "ready" ? { ...state, hoveredAssetId: action.assetId } : state;
    case "focus":
      return state.phase === "ready" ? { ...state, focusedAssetId: action.assetId } : state;
    case "filter": {
      if (state.phase !== "ready") return state;
      if (action.filter === "all") {
        return { ...state, hoveredAssetId: null, focusedAssetId: null, outcomeFilter: "all" };
      }
      const selected = state.model.report.assets.find(
        (asset) => asset.asset_id === state.selectedAssetId,
      );
      const firstMatch = state.model.report.assets.find((asset) => asset.state === action.filter);
      return {
        ...state,
        selectedAssetId: selected?.state === action.filter
          ? state.selectedAssetId
          : firstMatch?.asset_id ?? null,
        hoveredAssetId: null,
        focusedAssetId: null,
        outcomeFilter: action.filter,
      };
    }
  }
}

export function activeAssetId(state: ViewerState): string | null {
  if (state.phase !== "ready") return null;
  return state.hoveredAssetId ?? state.focusedAssetId ?? state.selectedAssetId;
}

export function rankedAssets(model: ReportModel): readonly ScoredAsset[] {
  return model.report.assets
    .filter((asset): asset is ScoredAsset => asset.state === "scored")
    .toSorted((left, right) => left.rank - right.rank);
}

export function abstainedAssets(model: ReportModel): readonly Asset[] {
  return model.report.assets.filter((asset) => asset.state === "abstained");
}
