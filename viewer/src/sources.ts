import type { ReportIssue, ReportOrigin } from "./model";
import { estimatedBase64Bytes, MAX_REPORT_BYTES } from "./limits";

export type ReportReadResult =
  | { readonly ok: true; readonly bytes: Uint8Array }
  | { readonly ok: false; readonly issue: ReportIssue };

export interface ReportSource {
  readonly origin: ReportOrigin;
  read(): Promise<ReportReadResult>;
}

function sourceIssue(message: string): ReportReadResult {
  return {
    ok: false,
    issue: { severity: "fatal", code: "source-read", path: "$source", message },
  };
}

export class LocalFileSource implements ReportSource {
  readonly origin: ReportOrigin;

  constructor(private readonly file: File) {
    this.origin = { kind: "local-file", label: file.name };
  }

  async read(): Promise<ReportReadResult> {
    if (this.file.size > MAX_REPORT_BYTES) {
      return sourceIssue(
        `Report exceeds the ${MAX_REPORT_BYTES}-byte transport limit before reading.`,
      );
    }
    try {
      const bytes = new Uint8Array(await this.file.arrayBuffer());
      if (bytes.byteLength > MAX_REPORT_BYTES) {
        return sourceIssue(`Report grew beyond the ${MAX_REPORT_BYTES}-byte transport limit.`);
      }
      return { ok: true, bytes };
    } catch {
      return sourceIssue(`Could not read local report “${this.file.name}”.`);
    }
  }
}

export class EmbeddedSource implements ReportSource {
  readonly origin: ReportOrigin = { kind: "embedded", label: "Standalone report" };

  constructor(private readonly documentRoot: Document) {}

  async read(): Promise<ReportReadResult> {
    const nodes = this.documentRoot.querySelectorAll("#moodboard-report");
    if (nodes.length !== 1) return sourceIssue("The standalone report payload is missing or duplicated.");
    const node = nodes.item(0);
    if (!(node instanceof HTMLScriptElement) || node.type !== "application/octet-stream") {
      return sourceIssue("The standalone report payload has an unexpected element type.");
    }
    const payload = node.textContent?.trim() ?? "";
    if (estimatedBase64Bytes(payload) > MAX_REPORT_BYTES) {
      return sourceIssue(`Embedded report exceeds the ${MAX_REPORT_BYTES}-byte transport limit.`);
    }
    if (payload.length === 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(payload)) {
      return sourceIssue("The standalone report payload is not canonical base64.");
    }
    try {
      const raw = atob(payload);
      const bytes = Uint8Array.from(raw, (character) => character.charCodeAt(0));
      return { ok: true, bytes };
    } catch {
      return sourceIssue("The standalone report payload could not be decoded.");
    }
  }
}

export function hasEmbeddedPayload(documentRoot: Document): boolean {
  return documentRoot.querySelectorAll("#moodboard-report").length > 0;
}
