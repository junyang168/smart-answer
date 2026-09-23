import type { StageId } from "./operations-types";

export const operationStageLabels: Record<StageId, string> = {
  extraction: "抽取",
  cross_section: "跨段關係",
  review: "複審",
  adjudication: "仲裁",
  merge: "合併",
  ingest: "入庫",
};

export const operationStageOrder: StageId[] = [
  "extraction",
  "cross_section",
  "review",
  "adjudication",
  "merge",
  "ingest",
];

export const operationStageSummary =
  `${operationStageOrder.length} 個階段：` +
  operationStageOrder.map((stage) => operationStageLabels[stage]).join(" → ");
