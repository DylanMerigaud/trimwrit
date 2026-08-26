// Decode side of the trimwrit viz payload contract, v1.
//
// Transport: base64url(zlib_compress(minified_utf8_json)), padding kept as-is
// (Python's base64.urlsafe_b64encode does not strip it). The Python side
// compresses with zlib.compress, which is zlib-wrapped deflate; the "deflate"
// format name of DecompressionStream is that same zlib wrapper, not raw
// deflate, so the two sides line up without any header stripping.
//
// This module is intentionally framework-free (no React import) so it can be
// unit tested directly, in Node or in the browser: both expose atob, TextDecoder
// and DecompressionStream as globals.

import type { PipelineDoc, Stage, StageMeta, StageStatus } from "./types";

export class PayloadDecodeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PayloadDecodeError";
  }
}

const STATUSES: readonly StageStatus[] = ["pending", "passed", "failed", "skipped"];

const base64UrlToBytes = (payload: string): Uint8Array<ArrayBuffer> => {
  const standard = payload.replace(/-/g, "+").replace(/_/g, "/");
  let binary: string;
  try {
    binary = atob(standard);
  } catch {
    throw new PayloadDecodeError("the hash payload is not valid base64url");
  }
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
};

const inflateToText = async (bytes: Uint8Array<ArrayBuffer>): Promise<string> => {
  try {
    const ds = new DecompressionStream("deflate");
    const writer = ds.writable.getWriter();
    void writer.write(bytes);
    void writer.close();
    const buffer = await new Response(ds.readable).arrayBuffer();
    return new TextDecoder("utf-8").decode(buffer);
  } catch {
    throw new PayloadDecodeError("could not inflate the payload, it may be corrupt or truncated");
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> => {
  return typeof value === "object" && value !== null && !Array.isArray(value);
};

const asMeta = (raw: unknown, where: string): StageMeta[] => {
  if (!Array.isArray(raw)) throw new PayloadDecodeError(`${where}.meta must be an array`);
  return raw.map((row, i) => {
    if (!isRecord(row) || typeof row.label !== "string" || typeof row.value !== "string") {
      throw new PayloadDecodeError(`${where}.meta[${i}] must be {label, value} strings`);
    }
    return { label: row.label, value: row.value };
  });
};

const asStage = (raw: unknown, index: number): Stage => {
  if (!isRecord(raw)) throw new PayloadDecodeError(`stages[${index}] is not an object`);
  const where = `stages[${index}]`;
  if (typeof raw.id !== "string" || raw.id.length === 0) {
    throw new PayloadDecodeError(`${where}.id must be a non-empty string`);
  }
  if (typeof raw.kind !== "string") throw new PayloadDecodeError(`${where}.kind must be a string`);
  if (typeof raw.label !== "string") {
    throw new PayloadDecodeError(`${where}.label must be a string`);
  }
  if (typeof raw.status !== "string" || !STATUSES.includes(raw.status as StageStatus)) {
    throw new PayloadDecodeError(`${where}.status must be one of ${STATUSES.join(", ")}`);
  }
  if (!Array.isArray(raw.next) || raw.next.some((n) => typeof n !== "string")) {
    throw new PayloadDecodeError(`${where}.next must be an array of strings`);
  }
  return {
    id: raw.id,
    kind: raw.kind,
    label: raw.label,
    status: raw.status as StageStatus,
    meta: asMeta(raw.meta, where),
    next: raw.next as string[],
  };
};

/** Structural validation only. Dangling `next` ids are a soft, render-time
 * warning (see lib/graph.ts), not a decode failure: the contract asks the
 * rest of the graph to still render. */
export const parsePipelineDoc = (jsonText: string): PipelineDoc => {
  let raw: unknown;
  try {
    raw = JSON.parse(jsonText);
  } catch {
    throw new PayloadDecodeError("the payload is not valid JSON");
  }
  if (!isRecord(raw)) throw new PayloadDecodeError("the document is not a JSON object");
  if (raw.v !== 1) {
    throw new PayloadDecodeError(`unsupported document version: ${JSON.stringify(raw.v)}`);
  }
  if (typeof raw.name !== "string") throw new PayloadDecodeError("document.name must be a string");
  if (typeof raw.generated_at !== "string") {
    throw new PayloadDecodeError("document.generated_at must be a string");
  }
  if (!Array.isArray(raw.stages)) throw new PayloadDecodeError("document.stages must be an array");
  if (!Array.isArray(raw.roots) || raw.roots.some((r) => typeof r !== "string")) {
    throw new PayloadDecodeError("document.roots must be an array of strings");
  }

  const seen = new Set<string>();
  const stages = raw.stages.map((s, i) => {
    const stage = asStage(s, i);
    if (seen.has(stage.id)) {
      throw new PayloadDecodeError(`duplicate stage id: ${stage.id}`);
    }
    seen.add(stage.id);
    return stage;
  });

  return {
    v: 1,
    name: raw.name,
    generated_at: raw.generated_at,
    stages,
    roots: raw.roots as string[],
  };
};

/** Decode a `#v1:<payload>` hash fragment into a document. Accepts the hash
 * with or without its leading `#`. */
export const decodeHashPayload = async (hash: string): Promise<PipelineDoc> => {
  const stripped = hash.startsWith("#") ? hash.slice(1) : hash;
  const match = /^v1:(.+)$/s.exec(stripped);
  if (!match) {
    throw new PayloadDecodeError("expected a #v1:<payload> hash");
  }
  const bytes = base64UrlToBytes(match[1]);
  const jsonText = await inflateToText(bytes);
  return parsePipelineDoc(jsonText);
};
