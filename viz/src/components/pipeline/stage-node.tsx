// vendored from approvals-ui (https://github.com/DylanMerigaud/approvals-ui), forked for trimwrit
//
// Forked from components/approvals-ui/approval-node.tsx and terminal-node.tsx,
// collapsed into one generic card since a pipeline stage has no approvers,
// quorum, SLA or Zod policy to render, only a kind, a label, meta rows and a
// status. The card shell and stateRing visual language are kept so the graph
// still reads as approvals-ui.
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { CircleCheck, CircleX, MinusCircle } from "lucide-react";

import { cn } from "../../lib/utils";
import type { StageMeta, StageStatus } from "../../lib/types";

export type StageNodeData = {
  id: string;
  kind: string;
  label: string;
  status: StageStatus;
  meta: StageMeta[];
  vertical?: boolean;
  hasIncoming?: boolean;
  hasOutgoing?: boolean;
  [key: string]: unknown;
};

export type StageFlowNode = Node<StageNodeData, "stage">;

/** Known kinds get a distinct chip color; any other string still renders,
 * just with the neutral fallback. The consumer must not hardcode the kind
 * enum, only style a few of them specially. */
const KIND_CHIP: Record<string, string> = {
  correction: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  gate: "bg-sky-500/15 text-sky-700 dark:text-sky-400",
  case: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  rule: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
};

export const stateRing = (status: StageStatus): string => {
  switch (status) {
    case "passed":
      return "ring-2 ring-emerald-500/70 border-emerald-500/40";
    case "failed":
      return "ring-2 ring-red-500/70 border-red-500/40";
    case "skipped":
      return "opacity-50 border-dashed";
    case "pending":
      return "ring-2 ring-amber-500/50";
  }
};

const StatusBadge = ({ status }: { status: StageStatus }) => {
  if (status === "passed") {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
        <CircleCheck className="size-3.5" /> Passed
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-red-600 dark:text-red-400">
        <CircleX className="size-3.5" /> Failed
      </span>
    );
  }
  if (status === "skipped") {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1 text-[11px] font-medium">
        <MinusCircle className="size-3.5" /> Skipped
      </span>
    );
  }
  return (
    <span className="text-foreground inline-flex items-center gap-1 text-[11px] font-medium">
      <span className="relative flex size-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-60" />
        <span className="relative inline-flex size-2 rounded-full bg-amber-500" />
      </span>
      Pending
    </span>
  );
};

const KindChip = ({ kind }: { kind: string }) => (
  <span
    className={cn(
      "inline-flex w-fit shrink-0 items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
      KIND_CHIP[kind] ?? "bg-muted text-muted-foreground"
    )}
  >
    {kind}
  </span>
);

export const StageNode = ({ data }: NodeProps<StageFlowNode>) => {
  const isVertical = data.vertical !== false;

  return (
    <div
      className={cn(
        "bg-card text-card-foreground w-64 rounded-xl border shadow-sm",
        data.status === "skipped" && "opacity-45",
        stateRing(data.status)
      )}
    >
      {data.hasIncoming !== false && (
        <Handle
          type="target"
          position={isVertical ? Position.Top : Position.Left}
          className="!bg-border !size-2 !border-none"
        />
      )}

      <div className="space-y-1.5 px-3.5 pt-3">
        <div className="flex items-start justify-between gap-2">
          <KindChip kind={data.kind} />
          <StatusBadge status={data.status} />
        </div>
        <p className="text-sm leading-tight font-medium" title={data.label}>
          {data.label}
        </p>
      </div>

      {data.meta.length > 0 && (
        <div className="space-y-1 px-3.5 py-3">
          {data.meta.map((row, index) => (
            <div key={`${row.label}-${index}`} className="flex items-baseline gap-2 text-xs">
              <span className="text-muted-foreground shrink-0">{row.label}</span>
              <span className="min-w-0 truncate" title={row.value}>
                {row.value}
              </span>
            </div>
          ))}
        </div>
      )}

      {data.hasOutgoing !== false && (
        <Handle
          type="source"
          position={isVertical ? Position.Bottom : Position.Right}
          className="!bg-border !size-2 !border-none"
        />
      )}
    </div>
  );
};
