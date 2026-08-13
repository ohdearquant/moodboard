import fireflyBridgeDocument from "./generated/firefly-bridge.json";

export type FireflyPreviewSource = `data:image/jpeg;base64,${string}`;

export interface FireflyPreview {
  readonly height: number;
  readonly sha256: string;
  readonly source: FireflyPreviewSource;
  readonly width: number;
}

export interface FireflyTimelineStep {
  readonly content_ref: string | null;
  readonly decision: "fail_structural_aspect_ratio" | "fail" | "pass";
  readonly id: "iteration_01_square" | "iteration_02_raw" | "iteration_02_cutout_composite";
  readonly label: string;
  readonly outside_mask_ssim: number | null;
  readonly output_sha256: string;
  readonly pass_semantics: "deterministic_preservation_constraint_not_intrinsic_generator_locality" | null;
  readonly preview: FireflyPreview | null;
  readonly record_id: string | null;
  readonly revision: string;
}

export interface FireflyMeasuredEvidence {
  readonly capture: {
    readonly authenticated_session: true;
    readonly cost_display: "Uses 0 credits";
    readonly model: "Gemini 2.5 (Nano Banana)";
    readonly native_firefly_api: false;
    readonly provider_boundary: "Google partner model served through Adobe Firefly";
    readonly surface: "Adobe Firefly web Edit > Prompt";
  };
  readonly khive: {
    readonly assets: readonly {
      readonly content_ref: string;
      readonly embedding_dimensions: 1024;
      readonly output_sha256: string;
      readonly record_id: string;
      readonly role: "raw" | "selected" | "restyle";
    }[];
    readonly descriptor: {
      readonly checkpoint_sha256: string;
      readonly dimensions: 1024;
      readonly fingerprint: string;
      readonly inference: { readonly provider: "lattice-embed"; readonly version: "0.9.0" };
    };
    readonly namespace: "adobe-demo-firefly-v1";
    readonly restart: {
      readonly canonical_search_byte_exact: true;
      readonly first_search_sha256: string;
      readonly restart_search_sha256: string;
    };
    readonly transport: Readonly<Record<string, string>>;
  };
  readonly nonclaims: readonly string[];
  readonly projection: { readonly revision: string; readonly sha256: string };
  readonly replacement: {
    readonly intent: string;
    readonly retrieved_reference: {
      readonly asset_id: string;
      readonly content_ref: string;
      readonly direct_generator_reference: false;
      readonly license_id: string;
      readonly raw_cosine: number;
      readonly sha256: string;
    };
    readonly threshold: 0.95;
    readonly timeline: readonly FireflyTimelineStep[];
    readonly verifier_revision: string;
  };
  readonly restyle: {
    readonly acceptance_decision: "not_computed";
    readonly content_ref: string;
    readonly diagnostics: Readonly<Record<string, number>>;
    readonly direct_generator_reference: false;
    readonly output_sha256: string;
    readonly preview: FireflyPreview;
    readonly record_id: string;
  };
}

interface FireflyBridge {
  readonly bridge_id: string;
  readonly evidence: FireflyMeasuredEvidence;
  readonly format_version: "moodboard.viewer-firefly-measured-loop-bridge.v1";
  readonly generator_revision: "moodboard.firefly-viewer-bridge.v1";
  readonly inputs: Readonly<Record<string, {
    readonly byte_size: number;
    readonly schema_version: string;
    readonly sha256: string;
  }>>;
  readonly state: "projected";
}

const shaPattern = /^[0-9a-f]{64}$/u;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const structuralOutputSha = "76abe16ec31fdfa4448094fc27e9b62debf933c566973264719722efc7f9acef";
const rawOutputSha = "8e33e4e6485ab776d5794cfa8ebba2f20687dfc391de1cd97d587bb4e3632f27";
const selectedOutputSha = "53b601c226fa9997fcce2e7e8bfeb80f4a1e6322d25e7d5293ea4436c2c9d35d";

function objectAt(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function closed(record: Record<string, unknown>, keys: readonly string[], path: string): void {
  const expected = [...keys].sort();
  const actual = Object.keys(record).sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${path} is not closed`);
  }
}

function stringAt(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${path} must be a string`);
  return value;
}

function shaAt(value: unknown, path: string): string {
  const digest = stringAt(value, path);
  if (!shaPattern.test(digest)) throw new Error(`${path} must be a SHA-256 digest`);
  return digest;
}

function uuidAt(value: unknown, path: string): string {
  const id = stringAt(value, path);
  if (!uuidPattern.test(id)) throw new Error(`${path} must be a canonical UUID`);
  return id;
}

function numberAt(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${path} must be finite`);
  return value;
}

function previewAt(value: unknown, path: string): FireflyPreview {
  const record = objectAt(value, path);
  closed(record, ["data_base64", "height", "mime", "sha256", "width"], path);
  if (record.mime !== "image/jpeg") throw new Error(`${path}.mime drifted`);
  const payload = stringAt(record.data_base64, `${path}.data_base64`);
  if (!/^[A-Za-z0-9+/]+={0,2}$/u.test(payload)) throw new Error(`${path}.data_base64 is invalid`);
  const width = numberAt(record.width, `${path}.width`);
  const height = numberAt(record.height, `${path}.height`);
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1 || width > 480 || height > 480) {
    throw new Error(`${path} dimensions are invalid`);
  }
  return {
    height,
    sha256: shaAt(record.sha256, `${path}.sha256`),
    source: `data:image/jpeg;base64,${payload}`,
    width,
  };
}

function timelineAt(value: unknown): readonly FireflyTimelineStep[] {
  if (!Array.isArray(value) || value.length !== 3) throw new Error("$firefly.evidence.replacement.timeline drifted");
  return value.map((item, index) => {
    const path = `$firefly.evidence.replacement.timeline[${index}]`;
    const record = objectAt(item, path);
    closed(record, ["content_ref", "decision", "id", "label", "outside_mask_ssim", "output_sha256", "pass_semantics", "preview", "record_id", "revision"], path);
    const decision = stringAt(record.decision, `${path}.decision`) as FireflyTimelineStep["decision"];
    if (!["fail_structural_aspect_ratio", "fail", "pass"].includes(decision)) throw new Error(`${path}.decision drifted`);
    const id = stringAt(record.id, `${path}.id`) as FireflyTimelineStep["id"];
    const expectedIds = ["iteration_01_square", "iteration_02_raw", "iteration_02_cutout_composite"] as const;
    if (id !== expectedIds[index]) throw new Error(`${path}.id drifted`);
    const contentRef = record.content_ref === null ? null : shaAt(record.content_ref, `${path}.content_ref`);
    const recordId = record.record_id === null ? null : uuidAt(record.record_id, `${path}.record_id`);
    const ssim = record.outside_mask_ssim === null ? null : numberAt(record.outside_mask_ssim, `${path}.outside_mask_ssim`);
    const semantics = record.pass_semantics === null ? null : stringAt(record.pass_semantics, `${path}.pass_semantics`) as FireflyTimelineStep["pass_semantics"];
    if (index === 0 && (decision !== "fail_structural_aspect_ratio" || contentRef !== null || recordId !== null || ssim !== null || record.preview !== null || semantics !== null)) {
      throw new Error(`${path} structural evidence drifted`);
    }
    if (index === 0 && (record.output_sha256 !== structuralOutputSha || record.revision !== "firefly-gemini25-replace-v1")) {
      throw new Error(`${path} structural identity drifted`);
    }
    if (index === 1 && (decision !== "fail" || ssim !== 0.174819482254 || semantics !== null)) {
      throw new Error(`${path} raw failure drifted`);
    }
    if (index === 1 && (record.output_sha256 !== rawOutputSha || record.revision !== "firefly-gemini25-replace-v2")) {
      throw new Error(`${path} raw identity drifted`);
    }
    if (index === 2 && (decision !== "pass" || ssim !== 1 || semantics !== "deterministic_preservation_constraint_not_intrinsic_generator_locality")) {
      throw new Error(`${path} compositor pass semantics drifted`);
    }
    if (index === 2 && (record.output_sha256 !== selectedOutputSha || record.revision !== "pixel-rag-firefly-cutout-compositor-v1")) {
      throw new Error(`${path} selected identity drifted`);
    }
    return {
      content_ref: contentRef,
      decision,
      id,
      label: stringAt(record.label, `${path}.label`),
      outside_mask_ssim: ssim,
      output_sha256: shaAt(record.output_sha256, `${path}.output_sha256`),
      pass_semantics: semantics,
      preview: record.preview === null ? null : previewAt(record.preview, `${path}.preview`),
      record_id: recordId,
      revision: stringAt(record.revision, `${path}.revision`),
    };
  });
}

function decodePythonPrevalidatedFireflyBridge(value: unknown): FireflyBridge {
  const bridge = objectAt(value, "$firefly");
  closed(bridge, ["bridge_id", "evidence", "format_version", "generator_revision", "inputs", "state"], "$firefly");
  if (bridge.format_version !== "moodboard.viewer-firefly-measured-loop-bridge.v1" || bridge.generator_revision !== "moodboard.firefly-viewer-bridge.v1" || bridge.state !== "projected") {
    throw new Error("$firefly contract identity drifted");
  }
  const evidence = objectAt(bridge.evidence, "$firefly.evidence");
  closed(evidence, ["capture", "khive", "nonclaims", "projection", "replacement", "restyle"], "$firefly.evidence");
  const capture = objectAt(evidence.capture, "$firefly.evidence.capture");
  closed(capture, ["authenticated_session", "cost_display", "model", "native_firefly_api", "provider_boundary", "surface"], "$firefly.evidence.capture");
  if (capture.authenticated_session !== true || capture.cost_display !== "Uses 0 credits" || capture.model !== "Gemini 2.5 (Nano Banana)" || capture.native_firefly_api !== false || capture.provider_boundary !== "Google partner model served through Adobe Firefly" || capture.surface !== "Adobe Firefly web Edit > Prompt") {
    throw new Error("$firefly capture boundary drifted");
  }
  const khive = objectAt(evidence.khive, "$firefly.evidence.khive");
  closed(khive, ["assets", "descriptor", "namespace", "restart", "transport"], "$firefly.evidence.khive");
  const descriptor = objectAt(khive.descriptor, "$firefly.evidence.khive.descriptor");
  closed(descriptor, ["checkpoint_sha256", "dimensions", "fingerprint", "inference"], "$firefly.evidence.khive.descriptor");
  const inference = objectAt(descriptor.inference, "$firefly.evidence.khive.descriptor.inference");
  closed(inference, ["provider", "version"], "$firefly.evidence.khive.descriptor.inference");
  if (descriptor.dimensions !== 1024 || inference.provider !== "lattice-embed" || inference.version !== "0.9.0") throw new Error("$firefly Lattice descriptor drifted");
  const restart = objectAt(khive.restart, "$firefly.evidence.khive.restart");
  closed(restart, ["canonical_search_byte_exact", "first_search_sha256", "restart_search_sha256"], "$firefly.evidence.khive.restart");
  const firstSearch = shaAt(restart.first_search_sha256, "$firefly.evidence.khive.restart.first_search_sha256");
  if (restart.canonical_search_byte_exact !== true || shaAt(restart.restart_search_sha256, "$firefly.evidence.khive.restart.restart_search_sha256") !== firstSearch) throw new Error("$firefly restart evidence drifted");
  const replacement = objectAt(evidence.replacement, "$firefly.evidence.replacement");
  closed(replacement, ["intent", "retrieved_reference", "threshold", "timeline", "verifier_revision"], "$firefly.evidence.replacement");
  if (replacement.threshold !== 0.95) throw new Error("$firefly threshold drifted");
  const reference = objectAt(replacement.retrieved_reference, "$firefly.evidence.replacement.retrieved_reference");
  closed(reference, ["asset_id", "content_ref", "direct_generator_reference", "license_id", "raw_cosine", "sha256"], "$firefly.evidence.replacement.retrieved_reference");
  if (
    reference.asset_id !== "fruit_lemon_santa_clara"
    || reference.content_ref !== "cf72f06b425eb52039d6926e057f7f5720f16435341625ce2fc9b92f5b52069d"
    || reference.direct_generator_reference !== false
    || reference.license_id !== "CC0-1.0"
    || reference.raw_cosine !== 0.843299582601
    || reference.sha256 !== "d53ca28eb2d59727fc577118d2d23dd0a16af8f0b8670d54fec6993428d71429"
  ) throw new Error("$firefly replacement reference drifted");
  const timeline = timelineAt(replacement.timeline);
  const restyle = objectAt(evidence.restyle, "$firefly.evidence.restyle");
  closed(restyle, ["acceptance_decision", "content_ref", "diagnostics", "direct_generator_reference", "output_sha256", "preview", "record_id"], "$firefly.evidence.restyle");
  if (restyle.acceptance_decision !== "not_computed" || restyle.direct_generator_reference !== false) throw new Error("$firefly restyle acceptance/input boundary drifted");
  const nonclaims = evidence.nonclaims;
  if (!Array.isArray(nonclaims) || nonclaims.length !== 6 || !nonclaims.every((claim) => typeof claim === "string" && claim.length > 0)) throw new Error("$firefly nonclaims drifted");
  return {
    bridge_id: shaAt(bridge.bridge_id, "$firefly.bridge_id"),
    evidence: {
      capture: {
        authenticated_session: true,
        cost_display: "Uses 0 credits",
        model: "Gemini 2.5 (Nano Banana)",
        native_firefly_api: false,
        provider_boundary: "Google partner model served through Adobe Firefly",
        surface: "Adobe Firefly web Edit > Prompt",
      },
      khive: {
        assets: khive.assets as FireflyMeasuredEvidence["khive"]["assets"],
        descriptor: {
          checkpoint_sha256: shaAt(descriptor.checkpoint_sha256, "$firefly descriptor checkpoint"),
          dimensions: 1024,
          fingerprint: shaAt(descriptor.fingerprint, "$firefly descriptor fingerprint"),
          inference: { provider: "lattice-embed", version: "0.9.0" },
        },
        namespace: "adobe-demo-firefly-v1",
        restart: {
          canonical_search_byte_exact: true,
          first_search_sha256: firstSearch,
          restart_search_sha256: firstSearch,
        },
        transport: khive.transport as Readonly<Record<string, string>>,
      },
      nonclaims,
      projection: evidence.projection as FireflyMeasuredEvidence["projection"],
      replacement: {
        intent: stringAt(replacement.intent, "$firefly replacement intent"),
        retrieved_reference: reference as unknown as FireflyMeasuredEvidence["replacement"]["retrieved_reference"],
        threshold: 0.95,
        timeline,
        verifier_revision: stringAt(replacement.verifier_revision, "$firefly verifier revision"),
      },
      restyle: {
        acceptance_decision: "not_computed",
        content_ref: shaAt(restyle.content_ref, "$firefly restyle content_ref"),
        diagnostics: restyle.diagnostics as Readonly<Record<string, number>>,
        direct_generator_reference: false,
        output_sha256: shaAt(restyle.output_sha256, "$firefly restyle output_sha256"),
        preview: previewAt(restyle.preview, "$firefly restyle preview"),
        record_id: uuidAt(restyle.record_id, "$firefly restyle record_id"),
      },
    },
    format_version: "moodboard.viewer-firefly-measured-loop-bridge.v1",
    generator_revision: "moodboard.firefly-viewer-bridge.v1",
    inputs: bridge.inputs as FireflyBridge["inputs"],
    state: "projected",
  };
}

/** The mandatory Python `firefly:check` is the full semantic and identity authority. */
export const fireflyBridge = decodePythonPrevalidatedFireflyBridge(fireflyBridgeDocument);
export const verifiedFireflyEvidence = fireflyBridge.evidence;
