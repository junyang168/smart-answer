export type TextChange = { kind: "equal" | "delete" | "insert"; text: string };

// Compare raw Markdown without changing source text or interpreting soft deletes.
// Trim common edges first; bound work for exceptionally large pasted paragraphs.
export function textChanges(before: string, after: string): TextChange[] {
  const a = Array.from(before),
    b = Array.from(after);
  const result: TextChange[] = [];
  const append = (kind: TextChange["kind"], text: string) => {
    if (!text) return;
    const last = result.at(-1);
    if (last?.kind === kind) last.text += text;
    else result.push({ kind, text });
  };
  let left = 0,
    right = 0;
  while (left < a.length && left < b.length && a[left] === b[left]) left++;
  while (
    right < a.length - left &&
    right < b.length - left &&
    a[a.length - right - 1] === b[b.length - right - 1]
  )
    right++;
  append("equal", a.slice(0, left).join(""));
  const old = a.slice(left, a.length - right),
    next = b.slice(left, b.length - right);
  if ((old.length + 1) * (next.length + 1) > 1_000_000) {
    append("delete", old.join(""));
    append("insert", next.join(""));
  } else {
    const width = next.length + 1;
    const lengths = new Uint32Array((old.length + 1) * width);
    for (let i = old.length - 1; i >= 0; i--)
      for (let j = next.length - 1; j >= 0; j--)
        lengths[i * width + j] =
          old[i] === next[j]
            ? 1 + lengths[(i + 1) * width + j + 1]
            : Math.max(
                lengths[(i + 1) * width + j],
                lengths[i * width + j + 1],
              );
    let i = 0,
      j = 0;
    while (i < old.length || j < next.length) {
      if (i < old.length && j < next.length && old[i] === next[j]) {
        append("equal", old[i++]);
        j++;
      } else if (
        i < old.length &&
        (j === next.length ||
          lengths[(i + 1) * width + j] >= lengths[i * width + j + 1])
      ) {
        append("delete", old[i++]);
      } else append("insert", next[j++]);
    }
  }
  append("equal", a.slice(a.length - right).join(""));
  return result;
}
