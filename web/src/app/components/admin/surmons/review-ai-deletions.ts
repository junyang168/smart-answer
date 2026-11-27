export type AiExclusion = { paragraph_id: string; old: string; reason: string; kind: string };
export type AiDeletion = AiExclusion & { id: string; marker: string; start: number; text: string; contextBefore: string; contextAfter: string };

// Only source-bound AI exclusions still present verbatim can be restored here.
// Changed/split markers stay in the ordinary editor for manual inspection.
export function aiDeletions(
  rows: { index: string; text: string }[],
  bindings: Record<string, { source_paragraph_id: string }>,
  exclusions: AiExclusion[],
): AiDeletion[] {
  const bySource = new Map<string, typeof rows[number]>();
  const positions = new Map(rows.map((row, i) => [row.index, i]));
  for (const row of rows) {
    const source = bindings[row.index]?.source_paragraph_id;
    if (source) bySource.set(source, row);
  }
  const seen = new Set<string>();
  return exclusions.flatMap((e) => {
    const row = bySource.get(e.paragraph_id);
    if (!row || !e.old || e.old.includes("~~")) return [];
    const marker = `~~${e.old}~~`;
    const start = row.text.indexOf(marker);
    const key = `${row.index}:${start}`;
    if (start < 0 || row.text.indexOf(marker, start + marker.length) >= 0 || seen.has(key)) return [];
    seen.add(key);
    const pos = positions.get(row.index)!;
    return [{ ...e, id: row.index, marker, start, text: row.text,
      contextBefore: rows[pos - 1]?.text.slice(-180) || "",
      contextAfter: rows[pos + 1]?.text.slice(0, 180) || "" }];
  });
}

export function restoreAiDeletion(current: string, candidate: AiDeletion): string {
  if (current !== candidate.text || current.slice(candidate.start, candidate.start + candidate.marker.length) !== candidate.marker) {
    throw new Error("段落已變更，請重新查看刪除處");
  }
  return current.slice(0, candidate.start) + candidate.old + current.slice(candidate.start + candidate.marker.length);
}
