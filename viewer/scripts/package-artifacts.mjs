import { createHash } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import { verifyPreferencePackageProjection } from "./preference-package-gate.mjs";

const viewerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(viewerRoot, "..");
const distributionRoot = path.join(viewerRoot, "dist-static");
const packageDataRoot = path.join(repositoryRoot, "moodboard", "viewer_dist");
const payloadToken = "__MOODBOARD_REPORT_BASE64__";
const standaloneCsp = "default-src 'none'; script-src data:; style-src data:; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'";
const standaloneCspMeta = `<meta http-equiv="Content-Security-Policy" content="${standaloneCsp}" />`;

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]));
  }
  return value;
}

function canonicalJson(value) {
  return `${JSON.stringify(canonicalize(value))}\n`;
}

function assertSafePath(value) {
  if (
    value.length === 0 ||
    path.posix.isAbsolute(value) ||
    value.includes("\\") ||
    value.includes("%") ||
    value.split("/").some((segment) => segment === "" || segment === "." || segment === "..")
  ) {
    throw new Error(`Unsafe artifact path: ${JSON.stringify(value)}`);
  }
}

function oneMatch(input, expression, label) {
  const matches = [...input.matchAll(expression)];
  if (matches.length !== 1 || typeof matches[0]?.[1] !== "string") {
    throw new Error(`Expected exactly one ${label} in Vite output; found ${matches.length}.`);
  }
  return matches[0][1];
}

const localAbsolutePathPatterns = [
  ["local file URL", /file:\/\/\/(?:Users|home|private|tmp)\//iu],
  ["macOS user-home path", /\/Users\/[^/\s"'<>]+\//u],
  ["Linux user-home path", /\/home\/[^/\s"'<>]+\//u],
  ["macOS private temporary path", /\/private\/(?:tmp|var)\//u],
  ["temporary-directory path", /\/tmp\//u],
  [
    "absolute worktree/cache path",
    /(?:^|[\s"'(=])\/(?:[^/\s"'<>]+\/)*\.(?:worktrees|cache)(?:\/|$)/u,
  ],
  ["Windows drive path", /(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]/u],
];

export function assertNoLocalPathLiterals(value, label) {
  const text = typeof value === "string" ? value : Buffer.from(value).toString("utf8");
  for (const [kind, expression] of localAbsolutePathPatterns) {
    if (expression.test(text)) {
      throw new Error(`${label} contains a local absolute path literal (${kind}).`);
    }
  }
}

async function packageArtifacts() {
  const packageJson = JSON.parse(await readFile(path.join(viewerRoot, "package.json"), "utf8"));
  const viewerVersion = packageJson.version;
  if (!/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(viewerVersion)) {
    throw new Error(`Viewer version is not a release semver: ${viewerVersion}`);
  }

  const staticIndexPath = path.join(distributionRoot, "index.html");
  const staticIndex = await readFile(staticIndexPath, "utf8");
  assertNoLocalPathLiterals(staticIndex, "Static entry point");
  const scriptHref = oneMatch(staticIndex, /<script type="module" crossorigin src="\.\/([^"]+)"><\/script>/g, "application script");
  const cssHref = oneMatch(staticIndex, /<link rel="stylesheet" crossorigin href="\.\/([^"]+)">/g, "application stylesheet");
  assertSafePath(scriptHref);
  assertSafePath(cssHref);

  const scriptBytes = await readFile(path.join(distributionRoot, scriptHref));
  const cssBytes = await readFile(path.join(distributionRoot, cssHref));
  const scriptText = scriptBytes.toString("utf8");
  const cssText = cssBytes.toString("utf8");
  assertNoLocalPathLiterals(scriptText, "Application bundle");
  assertNoLocalPathLiterals(cssText, "Application stylesheet");
  verifyPreferencePackageProjection({
    bridgePath: path.join(viewerRoot, "src", "generated", "preference-replay-bridge.json"),
    scriptText,
    repositoryRoot,
  });
  if (scriptBytes.includes(Buffer.from("import("))) throw new Error("Application bundle contains a dynamic import.");
  if (scriptBytes.includes(Buffer.from("sourceMappingURL"))) throw new Error("Application bundle contains a source-map reference.");
  if (/\bfetch\s*\(/.test(scriptText)) throw new Error("Application bundle contains a runtime fetch call.");
  if (/\beval\s*\(|\bnew\s+Function\s*\(/.test(scriptText)) throw new Error("Application bundle contains dynamic code evaluation.");
  if (/url\s*\(/i.test(cssText)) throw new Error("Application CSS contains a runtime url() dependency.");

  let standaloneTemplate = staticIndex
    .replace(
      '    <meta name="color-scheme" content="dark" />',
      `    <meta name="color-scheme" content="dark" />\n    ${standaloneCspMeta}`,
    )
    .replace(
      `<script type="module" crossorigin src="./${scriptHref}"></script>`,
      `<script type="application/octet-stream" id="moodboard-report">${payloadToken}</script>\n    <script type="module" src="data:text/javascript;base64,${scriptBytes.toString("base64")}"></script>`,
    )
    .replace(
      `<link rel="stylesheet" crossorigin href="./${cssHref}">`,
      `<link rel="stylesheet" href="data:text/css;base64,${cssBytes.toString("base64")}">`,
    );
  if (standaloneTemplate.split(payloadToken).length - 1 !== 1) throw new Error("Standalone payload token count is not one.");
  const cspTags = standaloneTemplate.match(/<meta(?=[^>]*\bhttp-equiv=["']Content-Security-Policy["'])[^>]*>/gi) ?? [];
  if (cspTags.length !== 1 || cspTags[0] !== standaloneCspMeta) {
    throw new Error("Standalone template does not contain exactly the pinned Content Security Policy.");
  }
  if (/\b(?:src|href)=["'](?!data:)/i.test(standaloneTemplate)) throw new Error("Standalone template contains a non-data runtime asset reference.");
  if (!standaloneTemplate.endsWith("\n")) standaloneTemplate += "\n";
  assertNoLocalPathLiterals(standaloneTemplate, "Standalone template");

  const sourceFiles = [
    ["artifact-manifest.schema.json", path.join(viewerRoot, "artifact-manifest.schema.json")],
    ["verification-toolchain.json", path.join(viewerRoot, "verification-toolchain.json")],
    ["consumer-contract.json", path.join(viewerRoot, "consumer-contract.json")],
    ["schemas/report_v1_0.schema.json", path.join(repositoryRoot, "moodboard", "schema", "report_v1_0.schema.json")],
    ["schemas/report_v1_1.schema.json", path.join(repositoryRoot, "moodboard", "schema", "report_v1_1.schema.json")],
  ];
  for (const [relativePath, sourcePath] of sourceFiles) {
    assertSafePath(relativePath);
    const destination = path.join(distributionRoot, relativePath);
    await mkdir(path.dirname(destination), { recursive: true });
    await cp(sourcePath, destination);
  }

  const templatePath = path.join(distributionRoot, "standalone-template.html");
  await writeFile(templatePath, standaloneTemplate, "utf8");

  const fileDigest = async (relativePath) => sha256(await readFile(path.join(distributionRoot, relativePath)));
  const manifest = {
    format_version: 1,
    viewer_version: viewerVersion,
    hash_algorithm: "sha256",
    manifest_schema: { path: "artifact-manifest.schema.json", sha256: await fileDigest("artifact-manifest.schema.json") },
    verification_toolchain: { path: "verification-toolchain.json", sha256: await fileDigest("verification-toolchain.json") },
    consumer_contract: { path: "consumer-contract.json", sha256: await fileDigest("consumer-contract.json") },
    writer_schemas: [
      { schema_version: "1.0", path: "schemas/report_v1_0.schema.json", sha256: await fileDigest("schemas/report_v1_0.schema.json") },
      { schema_version: "1.1", path: "schemas/report_v1_1.schema.json", sha256: await fileDigest("schemas/report_v1_1.schema.json") },
    ],
    static_entry: { path: "index.html", sha256: await fileDigest("index.html") },
    template: {
      path: "standalone-template.html",
      sha256: await fileDigest("standalone-template.html"),
      payload_element_id: "moodboard-report",
      payload_token: payloadToken,
      payload_token_count: 1,
      content_security_policy: standaloneCsp,
    },
    assets: [
      { role: "application-js", path: scriptHref, sha256: sha256(scriptBytes) },
      { role: "application-css", path: cssHref, sha256: sha256(cssBytes) },
    ],
  };

  const manifestSchema = JSON.parse(await readFile(path.join(viewerRoot, "artifact-manifest.schema.json"), "utf8"));
  const validate = new Ajv2020({ strict: true, allErrors: true }).compile(manifestSchema);
  if (!validate(manifest)) throw new Error(`Generated artifact manifest is invalid: ${JSON.stringify(validate.errors)}`);

  const manifestBytes = canonicalJson(manifest);
  await writeFile(path.join(distributionRoot, "artifact-manifest.json"), manifestBytes, "utf8");

  await rm(packageDataRoot, { recursive: true, force: true });
  await mkdir(packageDataRoot, { recursive: true });
  const stagedPaths = [
    "artifact-manifest.json",
    "artifact-manifest.schema.json",
    "verification-toolchain.json",
    "consumer-contract.json",
    "schemas/report_v1_0.schema.json",
    "schemas/report_v1_1.schema.json",
    "index.html",
    "standalone-template.html",
    scriptHref,
    cssHref,
  ];
  for (const relativePath of stagedPaths) {
    const destination = path.join(packageDataRoot, relativePath);
    await mkdir(path.dirname(destination), { recursive: true });
    await cp(path.join(distributionRoot, relativePath), destination);
  }

  process.stdout.write(`Packaged viewer ${viewerVersion}: ${scriptHref}, ${cssHref}, standalone-template.html\n`);
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  await packageArtifacts();
}
