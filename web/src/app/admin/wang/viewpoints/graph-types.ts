export type GraphNode = {
  id: string;
  text: string;
  scripture: string[];
  sermons: number;
  claims: number;
  group: number | null;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  label: string;
};

export type ViewpointGraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  relation_labels: Record<string, string>;
  stats: { viewpoints: number; relations: number; connected: number; isolated: number; groups: number };
};

export type SermonRef = { source_id: string; title: string; source_type?: string | null };

export type ViewpointDetailData = {
  id: string;
  text: string;
  summary: string | null;
  scripture: string[];
  routes: {
    id: string;
    label: string | null;
    steps: { key: string; role: string; text: string }[];
    sermons: (SermonRef & { completeness: string | null })[];
  }[];
  observations: { summary: string | null; supports: string | null; scope_note: string | null; sermon: SermonRef }[];
  claims: { id: string; text: string; sermons: SermonRef[] }[];
  relations: { type: string; label: string; direction: "in" | "out"; other_id: string; other_text: string; reason: string | null }[];
  history: { revision: number | null; text: string; approved_at: string | null; current: boolean }[];
};

// Tension is the relation a reader most needs to see, so it gets the loudest colour.
export const RELATION_COLORS: Record<string, string> = {
  applies: "#0ea5e9",
  extends: "#22c55e",
  specializes: "#a855f7",
  generalizes: "#eab308",
  entails: "#64748b",
  qualifies: "#f97316",
  tensions_with: "#dc2626",
};

export function sermonHref(sourceId: string, claimId?: string): string {
  const query = claimId ? `?claim=${encodeURIComponent(claimId)}` : "";
  return `/admin/wang/sermons/${encodeURIComponent(sourceId)}${query}`;
}
