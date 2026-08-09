import type { AxisDefinition } from "./model";

export function formatNumber(value: number): string {
  return String(value);
}

export function shortDigest(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 10)}…${value.slice(-6)}`;
}

export function humanizeToken(value: string): string {
  return value.replaceAll("_", " ").replace(/(^|\s)\S/g, (part) => part.toUpperCase());
}

export function axisDefinitionFallback(axisId: string): AxisDefinition {
  if (axisId === "style") {
    return {
      axis_id: "style",
      label: "Style fit",
      value_kind: "conformal_p_value",
      direction: "higher_is_better_fit",
      aggregation: "full_conformal_category",
      availability: "scored_only",
      uncertainty: "asset_interval",
      method: { name: "not-recorded-in-v1.0", revision: 1 },
    };
  }
  return {
    axis_id: axisId,
    label: `${humanizeToken(axisId)} distance`,
    value_kind: "normalized_distance",
    direction: "lower_is_closer",
    aggregation: "mean_over_exemplars",
    availability: "all_assets",
    uncertainty: "none",
    method: { name: "not-recorded-in-v1.0", revision: 1 },
  };
}

interface MeasurementLeaf {
  readonly path: string;
  readonly value: string;
}

function pointerEscape(value: string): string {
  return value.replaceAll("~", "~0").replaceAll("/", "~1");
}

function primitiveText(value: unknown): string | null {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return null;
}

export function flattenMeasurement(root: Readonly<Record<string, unknown>>): readonly MeasurementLeaf[] {
  const leaves: MeasurementLeaf[] = [];
  const stack: Array<{ readonly path: string; readonly value: unknown }> = Object.keys(root)
    .sort()
    .reverse()
    .map((key) => ({ path: `/${pointerEscape(key)}`, value: root[key] }));

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) break;
    const primitive = primitiveText(current.value);
    if (primitive !== null) {
      leaves.push({ path: current.path, value: primitive });
      continue;
    }
    if (Array.isArray(current.value)) {
      if (current.value.length === 0) {
        leaves.push({ path: current.path, value: "[]" });
      } else {
        for (let index = current.value.length - 1; index >= 0; index -= 1) {
          stack.push({ path: `${current.path}/${index}`, value: current.value[index] });
        }
      }
      continue;
    }
    if (typeof current.value === "object" && current.value !== null) {
      const record = current.value as Readonly<Record<string, unknown>>;
      const keys = Object.keys(record).sort();
      if (keys.length === 0) {
        leaves.push({ path: current.path, value: "{}" });
      } else {
        for (const key of keys.reverse()) {
          stack.push({ path: `${current.path}/${pointerEscape(key)}`, value: record[key] });
        }
      }
    }
  }
  return leaves;
}
