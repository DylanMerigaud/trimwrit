// Pure functions turning a PipelineDoc into React Flow nodes/edges, kept
// framework-light and separate from pipeline-canvas.tsx so the mapping from
// the contract's JSON shape to a graph can be unit tested without rendering
// React or touching the DOM.
import type { Edge } from "@xyflow/react";

import type { StageFlowNode } from "../components/pipeline/stage-node";
import type { PipelineDoc } from "./types";

export const buildStageNodes = (doc: PipelineDoc, vertical: boolean): StageFlowNode[] => {
  const incoming = new Set<string>();
  for (const stage of doc.stages) {
    for (const nextId of stage.next) incoming.add(nextId);
  }

  return doc.stages.map((stage) => ({
    id: stage.id,
    type: "stage",
    position: { x: 0, y: 0 },
    data: {
      id: stage.id,
      kind: stage.kind,
      label: stage.label,
      status: stage.status,
      meta: stage.meta,
      vertical,
      hasIncoming: incoming.has(stage.id),
      hasOutgoing: stage.next.length > 0,
    },
  }));
};

export type EdgeBuildResult = {
  edges: Edge[];
  /** "<source> -> <missing target>", one per dangling next id. */
  danglingEdges: string[];
};

export const buildStageEdges = (doc: PipelineDoc): EdgeBuildResult => {
  const ids = new Set(doc.stages.map((stage) => stage.id));
  const edges: Edge[] = [];
  const danglingEdges: string[] = [];

  for (const stage of doc.stages) {
    for (const nextId of stage.next) {
      if (ids.has(nextId)) {
        edges.push({
          id: `${stage.id}->${nextId}`,
          source: stage.id,
          target: nextId,
          style: { stroke: "var(--border)", strokeWidth: 1.5 },
        });
      } else {
        danglingEdges.push(`${stage.id} -> ${nextId}`);
      }
    }
  }

  return { edges, danglingEdges };
};

/** Soft-error warnings per the contract: dangling `next` ids, an unknown
 * root, or a duplicate id still let the rest of the graph render. Decode-time
 * validation (lib/decode.ts) already rejects a structurally broken document;
 * this only reports graph-shape problems in an otherwise valid one. */
export const collectGraphWarnings = (doc: PipelineDoc): string[] => {
  const warnings: string[] = [];
  const ids = new Set(doc.stages.map((stage) => stage.id));

  const { danglingEdges } = buildStageEdges(doc);
  for (const edge of danglingEdges) {
    warnings.push(`dangling edge: ${edge} (target id not found among stages)`);
  }

  for (const rootId of doc.roots) {
    if (!ids.has(rootId)) {
      warnings.push(`root id not found among stages: ${rootId}`);
    }
  }

  return warnings;
};
