import { render, screen } from "@testing-library/react";

import {
  applyDecodeProgress,
  initialValidationProgress,
  ValidationProgressView,
} from "../src/validation-progress";

describe("validation progress presentation", () => {
  it("renders measured phases, image counts, elapsed time, and no bypass", () => {
    let progress = applyDecodeProgress(initialValidationProgress, { phase: "schema" });
    progress = applyDecodeProgress(progress, { phase: "hash" });
    progress = applyDecodeProgress(progress, { phase: "images", completed: 31, total: 56 });

    render(
      <ValidationProgressView
        label="Standalone report"
        progress={progress}
        elapsedSeconds={12.4}
      />,
    );

    expect(screen.getByText("Schema and contract verified")).toBeTruthy();
    expect(screen.getByText("Report payload hashed")).toBeTruthy();
    expect(screen.getByText("Inline images decoded — 31 / 56")).toBeTruthy();
    expect(screen.getByText("Evidence bindings checked")).toBeTruthy();
    expect(screen.getByText("Elapsed 12.4s")).toBeTruthy();
    expect(screen.getByText(/Runs entirely on this device.*Typical time: 30–60 seconds/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /skip/i })).toBeNull();
  });
});
