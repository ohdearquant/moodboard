// The packaging script is JavaScript-only and intentionally exposes this pure policy helper
// as a narrow test seam.
// @ts-expect-error -- no declaration file is shipped for the build-only script.
import { assertNoLocalPathLiterals } from "../scripts/package-artifacts.mjs";

describe("standalone package local-path policy", () => {
  it.each([
    ["macOS user home", "/Users/example/project/report.json"],
    ["file URL", "file:///Users/example/project/report.json"],
    ["Linux user home", "/home/example/project/report.json"],
    ["macOS temporary root", "/private/var/folders/ab/build/report.json"],
    ["worktree absolute path", "/repo/.worktrees/demo/report.json"],
    ["cache absolute path", "/repo/.cache/demo/report.json"],
    ["Windows drive path", String.raw`C:\Users\example\project\report.json`],
  ])("rejects a literal %s path", (_label, literal) => {
    expect(() => assertNoLocalPathLiterals(`<meta content="${literal}">`, "fixture")).toThrow(
      /local absolute path literal/iu,
    );
  });

  it("allows relative documentation paths and encoded exact-byte transport", () => {
    const reportBytes = Buffer.from(
      '{"source":"/Users/example/project/.worktrees/demo/.cache/report.json"}\n',
      "utf8",
    );
    const encoded = reportBytes.toString("base64");
    const standalone = [
      '<script type="application/octet-stream" id="moodboard-report">',
      encoded,
      "</script>",
      "<!-- use .cache/demo/report.json while developing -->",
    ].join("");

    expect(() => assertNoLocalPathLiterals(standalone, "fixture")).not.toThrow();
    expect(Buffer.from(encoded, "base64")).toEqual(reportBytes);
  });
});
