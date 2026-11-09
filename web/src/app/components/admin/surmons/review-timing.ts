export function pendingTimingIds(
  rows: { index: string; text: string; type?: string }[],
  timing?: { status: string; paragraph_states?: Record<string, { status: string }>; source_holds?: Record<string, unknown> } | null,
): string[] {
  if (!timing) return [];
  const states = timing.paragraph_states || {};
  const active = rows.filter((p) => p.type !== "subtitle" && p.text.replace(/~~([^~]+?)~~/g, "\n").trim()).map((p) => p.index);
  const held = active.filter(id => id in (timing.source_holds || {}));
  if (held.length) return held;
  if (timing.status === "qualified") return [];
  const missing = active.filter((id) => states[id]?.status !== "qualified");
  if (missing.length) return missing;
  const bad = Object.keys(states).filter((id) => states[id].status !== "qualified");
  const known = new Set(rows.map((p) => p.index));
  if (["withheld", "editor_draft_only"].includes(timing.status) && bad.length && bad.every((id) => known.has(id) && !active.includes(id))) return [];
  return ["unresolved_timing_quality"];
}
