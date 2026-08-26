// Test-only encoder standing in for the Python producer, so the decode
// self-test exercises the exact wire format the contract defines rather than
// a JS-side shortcut: base64.urlsafe_b64encode(zlib.compress(json)), padding
// kept as-is. Node's zlib.deflateSync emits the same zlib-wrapped deflate as
// Python's zlib.compress.
import { deflateSync } from "node:zlib";

export const encodeHashPayload = (doc: unknown): string => {
  const json = JSON.stringify(doc);
  const compressed = deflateSync(Buffer.from(json, "utf-8"));
  return compressed.toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
};
