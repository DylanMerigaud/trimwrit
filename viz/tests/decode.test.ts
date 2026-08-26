import { describe, expect, it } from "vitest";

import { decodeHashPayload, parsePipelineDoc, PayloadDecodeError } from "../src/lib/decode";
import { exampleDoc } from "../src/lib/example-doc";
import { encodeHashPayload } from "./helpers/encode";

describe("decodeHashPayload", () => {
  it("round trips the example document through base64url(zlib(json)), the exact transport the Python producer uses", async () => {
    const payload = encodeHashPayload(exampleDoc);
    const decoded = await decodeHashPayload(`#v1:${payload}`);
    expect(decoded).toEqual(exampleDoc);
  });

  it("accepts the hash without its leading #", async () => {
    const payload = encodeHashPayload(exampleDoc);
    const decoded = await decodeHashPayload(`v1:${payload}`);
    expect(decoded.name).toBe(exampleDoc.name);
  });

  it("rejects a hash with no v1: prefix", async () => {
    await expect(decodeHashPayload("#garbage")).rejects.toThrow(PayloadDecodeError);
  });

  it("rejects invalid base64url with a readable error", async () => {
    await expect(decodeHashPayload("#v1:not*valid*base64")).rejects.toThrow(PayloadDecodeError);
  });
});

describe("parsePipelineDoc", () => {
  it("accepts the raw uncompressed example document, the paste-tab path", () => {
    expect(parsePipelineDoc(JSON.stringify(exampleDoc))).toEqual(exampleDoc);
  });

  it("rejects malformed JSON with a readable error", () => {
    expect(() => parsePipelineDoc("{not json")).toThrow(PayloadDecodeError);
  });

  it("rejects an unsupported document version", () => {
    const bad: unknown = { ...exampleDoc, v: 2 };
    expect(() => parsePipelineDoc(JSON.stringify(bad))).toThrow(PayloadDecodeError);
  });

  it("rejects a stage with an unknown status", () => {
    const bad: unknown = {
      ...exampleDoc,
      stages: [{ ...exampleDoc.stages[0], status: "unknown" }],
    };
    expect(() => parsePipelineDoc(JSON.stringify(bad))).toThrow(PayloadDecodeError);
  });

  it("rejects duplicate stage ids", () => {
    const bad: unknown = { ...exampleDoc, stages: [exampleDoc.stages[0], exampleDoc.stages[0]] };
    expect(() => parsePipelineDoc(JSON.stringify(bad))).toThrow(/duplicate/);
  });

  it("does not hardcode the kind enum: an unknown kind string still parses", () => {
    const withNewKind: unknown = {
      ...exampleDoc,
      stages: [{ ...exampleDoc.stages[0], kind: "something-new" }],
    };
    const parsed = parsePipelineDoc(JSON.stringify(withNewKind));
    expect(parsed.stages[0].kind).toBe("something-new");
  });
});
