import type { AnyNode, Claim, Kind, Observation, Position, Question, Source, Step } from "./types";

/** Everything that has a place on the time axis; a claim is placed by its evidence. */
type TimelineNode = Step | Observation | Question | Position;

export const COL_W = 132;
export const GAP_X = 10;
export const LANE_H = 112;
export const CLAIM_H = 66;
export const CLAIM_GAP = 6;
export const PAD_L = 16;
export const PAD_T = 12;
export const ZONE_GAP = 16;

export type Placed = {
  id: string;
  kind: Kind;
  node: AnyNode;
  lane: number;
  col: number;
  linked: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
  /** Claims only: the column of their earliest evidence step. */
  reachFrom?: number;
};

export type Layout = {
  placed: Placed[];
  byId: Map<string, Placed>;
  claimRows: number;
  laneTop: number;
  width: number;
  height: number;
};

export const colX = (col: number) => PAD_L + col * (COL_W + GAP_X);

type Options = { laneCount: number; showObs: boolean; showClaims: boolean };

/**
 * Place a source on one canvas.
 *
 * Horizontal position is the order the professor said it, so reading the canvas
 * left to right is reading the source in order.  Vertical position is what kind
 * of move the node is.  A claim sits at its last piece of evidence — where the
 * professor arrives at it — and reaches back to its first.
 */
export function buildLayout(source: Source, options: Options): Layout {
  const obsLane = options.laneCount;
  const timeline: Placed[] = [];
  const push = (node: AnyNode, kind: Kind, lane: number) =>
    timeline.push({
      id: node.id,
      kind,
      node,
      lane,
      col: 0,
      linked: false,
      x: 0,
      y: 0,
      w: COL_W,
      h: kind === "observation" ? 74 : 92,
    });

  source.questions.forEach((item) => push(item, "question", 0));
  source.positions.forEach((item) => push(item, "position", 0));
  source.steps.forEach((item) => push(item, "step", item.lane));
  source.observations.forEach((item) => push(item, "observation", obsLane));

  timeline.sort((a, b) => {
    const ar = (a.node as TimelineNode).rank ?? 1e9;
    const br = (b.node as TimelineNode).rank ?? 1e9;
    return ar - br || a.node.ordinal - b.node.ordinal || a.id.localeCompare(b.id);
  });
  timeline.forEach((item, index) => {
    item.col = index;
  });

  const byId = new Map(timeline.map((item) => [item.id, item]));
  const linked = new Set<string>();
  source.edges.forEach((edge) => {
    linked.add(edge.from);
    linked.add(edge.to);
  });
  timeline.forEach((item) => {
    item.linked = linked.has(item.id);
  });

  const claims: Placed[] = source.claims.map((claim: Claim) => {
    const cols = claim.step_ids.map((id) => byId.get(id)).filter(Boolean).map((item) => item!.col);
    return {
      id: claim.id,
      kind: "claim" as const,
      node: claim,
      lane: -1,
      col: cols.length ? Math.max(...cols) : 0,
      reachFrom: cols.length ? Math.min(...cols) : 0,
      linked: cols.length > 0,
      x: 0,
      y: 0,
      w: COL_W,
      h: CLAIM_H,
    };
  });
  claims.sort((a, b) => a.col - b.col || a.node.ordinal - b.node.ordinal);

  const rowEnd: number[] = [];
  const rowOf = new Map<string, number>();
  claims.forEach((claim) => {
    let row = rowEnd.findIndex((end) => end < claim.col);
    if (row === -1) {
      row = rowEnd.length;
      rowEnd.push(-1);
    }
    rowEnd[row] = claim.col;
    rowOf.set(claim.id, row);
  });

  const claimRows = options.showClaims ? Math.max(rowEnd.length, 1) : 0;
  const laneTop = PAD_T + claimRows * (CLAIM_H + CLAIM_GAP) + (claimRows ? ZONE_GAP : 0);

  timeline.forEach((item) => {
    item.x = colX(item.col);
    item.y = laneTop + item.lane * LANE_H;
  });
  claims.forEach((claim) => {
    claim.x = colX(claim.col);
    claim.y = PAD_T + (rowOf.get(claim.id) ?? 0) * (CLAIM_H + CLAIM_GAP);
  });

  const placed = options.showClaims ? [...timeline, ...claims] : timeline;
  placed.forEach((item) => byId.set(item.id, item));

  const laneRows = options.laneCount + (options.showObs ? 1 : 0);
  return {
    placed,
    byId,
    claimRows,
    laneTop,
    width: colX(timeline.length) + 40,
    height: laneTop + laneRows * LANE_H + 24,
  };
}
