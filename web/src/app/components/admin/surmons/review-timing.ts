export function pendingTimingIds(
  rows: { index: string; text: string; type?: string }[],
  timing?: { status: string; paragraph_states?: Record<string, { status: string }> } | null,
): string[] {
  if (!timing || timing.status === "qualified") return [];
  const states = timing.paragraph_states || {};
  const active = rows.filter((p) => p.type !== "subtitle" && p.text.replace(/~~([^~]+?)~~/g, "\n").trim()).map((p) => p.index);
  const missing = active.filter((id) => states[id]?.status !== "qualified");
  if (missing.length) return missing;
  const bad = Object.keys(states).filter((id) => states[id].status !== "qualified");
  const known = new Set(rows.map((p) => p.index));
  if (timing.status === "withheld" && bad.length && bad.every((id) => known.has(id) && !active.includes(id))) return [];
  return ["unresolved_timing_quality"];
}
