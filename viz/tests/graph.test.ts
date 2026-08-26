import { describe, expect, it } from "vitest";

import { decodeHashPayload } from "../src/lib/decode";
import { exampleDoc } from "../src/lib/example-doc";
import { buildStageEdges, buildStageNodes, collectGraphWarnings } from "../src/lib/graph";
import type { PipelineDoc } from "../src/lib/types";
import { encodeHashPayload } from "./helpers/encode";

describe("buildStageNodes", () => {
  it("builds one node per stage, flagging incoming/outgoing edges", () => {
    const nodes = buildStageNodes(exampleDoc, false);
    expect(nodes).toHaveLength(exampleDoc.stages.length);

    const rootCorrection = nodes.find((n) => n.id === "c0001");
    expect(rootCorrection?.data.hasIncoming).toBe(false);
    expect(rootCorrection?.data.hasOutgoing).toBe(true);

    const terminalRule = nodes.find((n) => n.id === "R0002");
    expect(terminalRule?.data.hasIncoming).toBe(true);
    expect(terminalRule?.data.hasOutgoing).toBe(false);
  });
});

describe("buildStageEdges", () => {
  it("builds one edge per next id on the example document, with no dangling edges", () => {
    const { edges, danglingEdges } = buildStageEdges(exampleDoc);
    const expectedEdgeCount = exampleDoc.stages.reduce((n, s) => n + s.next.length, 0);
    expect(edges).toHaveLength(expectedEdgeCount);
    expect(danglingEdges).toEqual([]);
    expect(edges.map((e) => e.id)).toContain("c0001->g-em-dash");
    expect(edges.map((e) => e.id)).toContain("k0002-no-em-dash-anywhere->R0002");
  });

  it("keeps a dangling next id as a soft warning instead of dropping the rest of the graph", () => {
    const doc: PipelineDoc = {
      ...exampleDoc,
      stages: [{ ...exampleDoc.stages[0], next: ["does-not-exist"] }],
    };
    const { edges, danglingEdges } = buildStageEdges(doc);
    expect(edges).toEqual([]);
    expect(danglingEdges).toEqual(["c0001 -> does-not-exist"]);
  });
});

describe("collectGraphWarnings", () => {
  it("is empty for the example document", () => {
    expect(collectGraphWarnings(exampleDoc)).toEqual([]);
  });

  it("reports a dangling edge and an unknown root by a readable message", () => {
    const doc: PipelineDoc = {
      ...exampleDoc,
      roots: ["does-not-exist-either"],
      stages: [{ ...exampleDoc.stages[0], next: ["does-not-exist"] }],
    };
    const warnings = collectGraphWarnings(doc);
    expect(warnings.some((w) => w.includes("does-not-exist "))).toBe(true);
    expect(warnings.some((w) => w.includes("does-not-exist-either"))).toBe(true);
  });
});

describe("decode + graph build, end to end", () => {
  it("decodes a compressed hash payload and builds the expected nodes and edges from it", async () => {
    const payload = encodeHashPayload(exampleDoc);
    const doc = await decodeHashPayload(`#v1:${payload}`);

    const nodes = buildStageNodes(doc, false);
    const { edges, danglingEdges } = buildStageEdges(doc);

    expect(nodes.map((n) => n.id).sort()).toEqual(exampleDoc.stages.map((s) => s.id).sort());
    expect(edges).toHaveLength(exampleDoc.stages.reduce((n, s) => n + s.next.length, 0));
    expect(danglingEdges).toEqual([]);
  });
});
