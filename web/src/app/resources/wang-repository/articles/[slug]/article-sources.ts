import type { ReviewSourceAnnotation } from "@/app/admin/wang/operations/articles/reviews/types";

export type ArticleSource = {
  kind: "sermon" | "notes";
  title: string;
  url: string | null;
  paragraphs: number;
};

/** Every source the article's paragraphs cite, once each, with how many paragraphs cite it. */
export function collectArticleSources(annotations: ReviewSourceAnnotation[]): ArticleSource[] {
  const found = new Map<string, ArticleSource>();
  for (const annotation of annotations) {
    const seen = new Set<string>();
    for (const source of annotation.sources) {
      const kind = source.source_type === "notes_manuscript" ? "notes" : "sermon";
      const key = `${kind}|${source.title}`;
      if (!found.has(key)) found.set(key, { kind, title: source.title, url: source.full_source_url, paragraphs: 0 });
      const entry = found.get(key)!;
      if (!entry.url && source.full_source_url) entry.url = source.full_source_url;
      if (!seen.has(key)) {
        seen.add(key);
        entry.paragraphs += 1;
      }
    }
  }
  return [...found.values()].sort(
    (a, b) => (a.kind === b.kind ? 0 : a.kind === "sermon" ? -1 : 1) || b.paragraphs - a.paragraphs || a.title.localeCompare(b.title, "zh"),
  );
}
