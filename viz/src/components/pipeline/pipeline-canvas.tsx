// vendored from approvals-ui (https://github.com/DylanMerigaud/approvals-ui), forked for trimwrit
//
// Forked from components/approvals-ui/workflow-canvas.tsx. The original
// builds nodes/edges from an ApprovalPolicy's steps and overlays diff,
// validation and live-run state; a pipeline stage graph has none of that, so
// this fork only carries the parts every React Flow graph needs: build nodes
// and edges from `stages[].next`, auto-layout on the real measured sizes
// (react-flow-auto-layout, same as the original), and the aligned step edge
// so fan-outs and joins line up.
import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  type EdgeTypes,
  type NodeTypes,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { useEffect, useMemo } from "react";
import { AlignedStepEdge, useAutoLayout, withAlignedElbows } from "react-flow-auto-layout/react";

import { StageNode } from "./stage-node";
import { cn } from "../../lib/utils";
import { buildStageEdges, buildStageNodes } from "../../lib/graph";
import type { PipelineDoc } from "../../lib/types";

const NODE_TYPES = { stage: StageNode } satisfies NodeTypes;
const EDGE_TYPES = { alignedStep: AlignedStepEdge } satisfies EdgeTypes;

export type PipelineCanvasProps = {
  doc: PipelineDoc;
  /** "LR" (default) lays out left to right, "TB" top to bottom. */
  direction?: "TB" | "LR";
  className?: string;
};

const CanvasInner = ({ doc, direction = "LR" }: PipelineCanvasProps) => {
  const isVertical = direction !== "LR";
  const reactFlow = useReactFlow();

  const sourceNodes = useMemo(() => buildStageNodes(doc, isVertical), [doc, isVertical]);
  const sourceEdges = useMemo(() => {
    const { edges } = buildStageEdges(doc);
    return withAlignedElbows(edges).map((edge) => ({ ...edge, type: "alignedStep" }));
  }, [doc]);

  const { nodes, edges, onNodesChange, onEdgesChange } = useAutoLayout({
    nodes: sourceNodes,
    edges: sourceEdges,
    vertical: isVertical,
  });

  const structureKey = useMemo(
    () => `${direction}:${doc.stages.map((s) => `${s.id}>${s.next.join("|")}`).join(",")}`,
    [doc, direction]
  );

  useEffect(() => {
    const frame = setTimeout(() => {
      void reactFlow.fitView({ duration: 300, padding: 0.2, maxZoom: 1 });
    }, 180);
    return () => clearTimeout(frame);
  }, [structureKey, reactFlow]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.2}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      className="bg-background"
    >
      <Background gap={18} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
};

const FLOW_THEME: React.CSSProperties & Record<`--${string}`, string> = {
  "--xy-controls-button-background-color": "var(--card)",
  "--xy-controls-button-background-color-hover": "var(--muted)",
  "--xy-controls-button-color": "var(--foreground)",
  "--xy-controls-button-color-hover": "var(--foreground)",
  "--xy-controls-button-border-color": "var(--border)",
  "--xy-attribution-background-color": "transparent",
};

/**
 * Renders a PipelineDoc (corrections, gates, cases, rules) as a laid-out
 * React Flow graph. Layout is automatic: the document is the only input, no
 * positions to manage. The parent element must have a height.
 */
export const PipelineCanvas = ({ className, ...props }: PipelineCanvasProps) => {
  return (
    <div className={cn("h-full w-full", className)} style={FLOW_THEME}>
      <ReactFlowProvider>
        <CanvasInner {...props} />
      </ReactFlowProvider>
    </div>
  );
};
