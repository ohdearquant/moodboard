import type { ReactNode } from "react";

import type { DecodeProgress } from "./model";

export interface ValidationProgress {
  readonly schema: boolean;
  readonly hash: boolean;
  readonly images: { readonly completed: number; readonly total: number } | null;
  readonly bindings: boolean;
}

export const initialValidationProgress: ValidationProgress = {
  schema: false,
  hash: false,
  images: null,
  bindings: false,
};

export function applyDecodeProgress(
  current: ValidationProgress,
  event: DecodeProgress,
): ValidationProgress {
  switch (event.phase) {
    case "schema":
      return { ...current, schema: true };
    case "hash":
      return { ...current, hash: true };
    case "images":
      return { ...current, images: { completed: event.completed, total: event.total } };
    case "bindings":
      return { ...current, bindings: true };
  }
}

function phaseClass(completed: boolean, active: boolean): string {
  return completed ? "validation-phase is-complete" : active ? "validation-phase is-active" : "validation-phase";
}

export function ValidationProgressView({
  label,
  progress,
  elapsedSeconds,
}: {
  readonly label: string;
  readonly progress: ValidationProgress;
  readonly elapsedSeconds: number;
}): ReactNode {
  const imageCompleted = progress.images?.completed ?? 0;
  const imageTotal = progress.images?.total ?? 0;
  const imagesDone = progress.images !== null && imageCompleted === imageTotal;
  return (
    <main className="status-page validation-progress" aria-live="polite" aria-busy={!progress.bindings}>
      <p className="eyebrow">{label}</p>
      <h1>Verifying before anything is shown</h1>
      <p>Values remain hidden until the complete offline contract passes.</p>
      <ol className="validation-phases">
        <li className={phaseClass(progress.schema, !progress.schema)}>
          <span>1/4</span><strong>Schema and contract verified</strong>
        </li>
        <li className={phaseClass(progress.hash, progress.schema && !progress.hash)}>
          <span>2/4</span><strong>Report payload hashed</strong>
        </li>
        <li className={phaseClass(imagesDone, progress.hash && !imagesDone)}>
          <span>3/4</span>
          <strong>
            {progress.images === null
              ? "Inline images queued"
              : `Inline images decoded — ${imageCompleted} / ${imageTotal}`}
          </strong>
        </li>
        <li className={phaseClass(progress.bindings, imagesDone && !progress.bindings)}>
          <span>4/4</span><strong>Evidence bindings checked</strong>
        </li>
      </ol>
      <div className="validation-meta">
        <span>Elapsed {elapsedSeconds.toFixed(1)}s</span>
        <span>Runs entirely on this device. Typical time: 30–60 seconds.</span>
      </div>
    </main>
  );
}
