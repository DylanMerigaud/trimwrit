// Shared shape of the trimwrit viz payload contract, v1.
// See: viz-payload-contract.md (the doc handed to both the Python producer
// and this consumer). Any change here is a contract-breaking change and
// bumps the document's `v`.

export type StageStatus = "pending" | "passed" | "failed" | "skipped";

export type StageMeta = {
  label: string;
  value: string;
};

export type Stage = {
  id: string;
  /** Free string. The producer emits one of correction, gate, case, rule,
   * but the consumer must render any string here without hardcoding the set. */
  kind: string;
  label: string;
  status: StageStatus;
  meta: StageMeta[];
  next: string[];
};

export type PipelineDoc = {
  v: 1;
  name: string;
  generated_at: string;
  stages: Stage[];
  roots: string[];
};
