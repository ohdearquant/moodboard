import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultRepositoryRoot = path.resolve(scriptRoot, "..");

function requiredDigest(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} must be a lowercase SHA-256 identity.`);
  }
  return value;
}

export function verifyPreferencePackageProjection({
  bridgePath,
  scriptText,
  repositoryRoot = defaultRepositoryRoot,
}) {
  try {
    execFileSync(
      "uv",
      [
        "run",
        "--frozen",
        "--project",
        repositoryRoot,
        "--python",
        "3.14.3",
        "python",
        "-m",
        "moodboard.preference_replay_viewer",
        "--check",
        bridgePath,
        "--require-projected",
      ],
      { cwd: repositoryRoot, encoding: "utf8", stdio: "pipe" },
    );
  } catch (error) {
    const detail = error && typeof error === "object" && "stderr" in error
      ? String(error.stderr).trim()
      : String(error);
    throw new Error(`Preference bridge is not a valid projected build input: ${detail}`);
  }

  const bridge = JSON.parse(readFileSync(bridgePath, "utf8"));
  if (bridge?.state !== "projected" || bridge.input === null || bridge.evidence === null) {
    throw new Error("Preference bridge must be projected before packaging.");
  }
  const identities = [
    requiredDigest(bridge.input?.replay?.replay_fingerprint, "replay fingerprint"),
    requiredDigest(bridge.input?.replay?.sha256, "replay SHA-256"),
    requiredDigest(bridge.input?.features?.sha256, "feature sidecar SHA-256"),
  ];
  for (const identity of identities) {
    if (!scriptText.includes(identity)) {
      throw new Error(`Application bundle does not bind projected preference identity ${identity}.`);
    }
  }
}

async function main(argv) {
  if (argv.length !== 2) {
    throw new Error("usage: preference-package-gate.mjs BRIDGE_JSON APPLICATION_JS");
  }
  const [bridgePath, applicationPath] = argv;
  const scriptText = await readFile(applicationPath, "utf8");
  verifyPreferencePackageProjection({ bridgePath, scriptText });
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  await main(process.argv.slice(2));
}
