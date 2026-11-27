type Region = { id: string; text: string };

export const ocrText = (regions: readonly Region[]) =>
  regions
    .filter((r) => r.text !== "")
    .map((r) => r.text)
    .join("\n");

/** Preserve source region IDs and geometry while applying a whole-slide edit. */
export function editOcrText<T extends Region>(
  regions: readonly T[],
  next: string,
): T[] {
  const previous = ocrText(regions);
  if (previous === next || regions.length === 0) return [...regions];
  const result = regions.map((r) => ({ ...r }));
  const visible = regions
    .map((r, i) => ({ ...r, i }))
    .filter((r) => r.text !== "");
  if (!visible.length) {
    result[0].text = next;
    return result;
  }
  let start = 0;
  while (
    start < previous.length &&
    start < next.length &&
    previous[start] === next[start]
  )
    start++;
  let end = previous.length;
  let nextEnd = next.length;
  while (
    end > start &&
    nextEnd > start &&
    previous[end - 1] === next[nextEnd - 1]
  ) {
    end--;
    nextEnd--;
  }
  let offset = 0;
  const spans = visible.map((r) => {
    const span = { ...r, start: offset, end: offset + r.text.length };
    offset = span.end + 1;
    return span;
  });
  const first = spans.findIndex((r) => start <= r.end);
  const last = spans.findIndex((r) => end <= r.end);
  const a = spans[first];
  const b = spans[last];
  result[a.i].text =
    a.text.slice(0, start - a.start) +
    next.slice(start, nextEnd) +
    b.text.slice(end - b.start);
  for (let i = first + 1; i <= last; i++) result[spans[i].i].text = "";
  // Leading/trailing empty regions cannot encode an intentional blank line.
  // Keep the exact editor value rather than silently dropping user whitespace.
  if (ocrText(result) !== next) {
    result.forEach((r, i) => {
      r.text = i === a.i ? next : "";
    });
  }
  return result;
}
