import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
const indexHtml = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
const packagingScript = readFileSync(resolve(process.cwd(), "scripts/package-artifacts.mjs"), "utf8");
const screenCss = css.split("@media print", 1)[0] ?? css;

function declarationsFor(selector: string): string {
  const declarations: string[] = [];
  for (const match of screenCss.matchAll(/([^{}]+)\{([^{}]*)\}/gu)) {
    const selectors = (match[1] ?? "")
      .split(",")
      .map((candidate) => candidate.trim());
    if (selectors.includes(selector)) declarations.push(match[2] ?? "");
  }
  return declarations.join("\n");
}

describe("viewer presentation CSS contract", () => {
  it("uses a coherent near-black screen palette without legacy light surfaces", () => {
    const root = declarationsFor(":root");
    expect(root).toMatch(/color:\s*#f4f1e8/iu);
    expect(root).toMatch(/--canvas:\s*#0d0f0d/iu);
    expect(root).toMatch(/--paper:\s*#151815/iu);
    expect(root).toMatch(/--paper-strong:\s*#1b1e1a/iu);
    expect(root).toMatch(/--hairline:\s*rgb\(246 242 233 \/ 16%\)/iu);
    expect(screenCss).not.toMatch(/background:\s*(?:#f2eee5|#fbf8f1|#fffdf8|#ded9ce|#e7e2d8|#dbd5ca|#fff5f1|#fff)\s*;/iu);
    expect(screenCss).not.toContain("var(--rule)");
  });

  it("centers the report at 1280px and prevents horizontal page overflow", () => {
    expect(declarationsFor("body")).toMatch(/overflow-x:\s*hidden/iu);
    const reportPage = declarationsFor(".report-page");
    expect(reportPage).toMatch(/width:\s*min\(100%,\s*1280px\)/iu);
    expect(reportPage).toMatch(/margin-inline:\s*auto/iu);
  });

  it("uses subdued hairlines instead of bright foreground ink for structural rules", () => {
    const structuralSelectors = [
      ".report-nav",
      ".mechanism-guide",
      ".story-strip",
      ".reference-board-strip",
      ".candidate-hero-grid",
      ".variant-family-grid",
      ".section-heading",
      ".asset-card",
      ".report-footer",
    ] as const;
    for (const selector of structuralSelectors) {
      expect(declarationsFor(selector), selector).not.toContain("var(--ink)");
      expect(declarationsFor(selector), selector).toContain("var(--hairline)");
    }
  });

  it("centers every visual without cropping or upscaling its native pixels", () => {
    const imageSelectors = [
      ".reference-board-frame img",
      ".candidate-hero-image img",
      ".variant-family-image img",
      ".preference-pair-images figure img",
      ".pixel-source-frame img",
      ".pixel-hit-image img",
      ".firefly-step figure img",
      ".firefly-restyle img",
      ".candidate-frame img",
      ".reference-frame img",
    ] as const;
    for (const selector of imageSelectors) {
      const declarations = declarationsFor(selector);
      expect(declarations, selector).toMatch(/width:\s*auto/iu);
      expect(declarations, selector).toMatch(/height:\s*auto/iu);
      expect(declarations, selector).toMatch(/max-width:\s*100%/iu);
      expect(declarations, selector).toMatch(/max-height:\s*100%/iu);
      expect(declarations, selector).toMatch(/object-fit:\s*contain/iu);
    }
    expect(screenCss).not.toMatch(/hover[^{}]*img[^{}]*\{[^{}]*transform:\s*scale/iu);
    expect(declarationsFor(".firefly-restyle img")).not.toMatch(/min-height/iu);
    expect(declarationsFor(".preference-pair-images")).toMatch(
      /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*196px\)\)/iu,
    );
    expect(declarationsFor(".preference-pair-images figure")).toMatch(/width:\s*128px/iu);
    expect(declarationsFor(".preference-pair-images figure")).toMatch(/height:\s*128px/iu);
  });

  it("keeps a deliberate white print override", () => {
    const printCss = css.slice(css.indexOf("@media print"));
    expect(printCss).toMatch(/:root\s*\{[^}]*background:\s*#fff/isu);
    expect(printCss).toMatch(/\.report-page\s*\{[^}]*background:\s*#fff/isu);
  });

  it("declares the dark scheme in both Vite and standalone document heads", () => {
    expect(indexHtml).toContain('<meta name="color-scheme" content="dark" />');
    expect(indexHtml).not.toContain('name="color-scheme" content="light"');
    expect(packagingScript).toContain('<meta name="color-scheme" content="dark" />');
    expect(packagingScript).not.toContain('<meta name="color-scheme" content="light" />');
    expect(packagingScript).toMatch(/color-scheme[^\n]+dark[^\n]+\\n[^\n]+standaloneCspMeta/iu);
  });

  it("frames the preference replay shift as neutral evidence, not green success", () => {
    const snapshotArrow = declarationsFor(".preference-snapshots article > i");
    const delta = declarationsFor(".preference-delta");
    expect(snapshotArrow).toContain("background: var(--cobalt)");
    expect(snapshotArrow).not.toContain("#c9ffb5");
    expect(delta).toContain("border-left: 4px solid var(--cobalt-dark)");
    expect(delta).toContain("background: var(--cobalt-soft)");
    expect(delta).not.toContain("#c9ffb5");
  });
});
