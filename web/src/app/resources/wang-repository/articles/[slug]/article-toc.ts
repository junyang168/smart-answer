export type ArticleTocHeading = {
  id: string;
  label: string;
  level: 2 | 3;
  line: number;
  ordinal: number;
};

function inlineMarkdownText(value: string): string {
  return value
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/[*_~`]/g, "")
    .replace(/\\([\\`*_[\]{}()#+.!-])/g, "$1")
    .trim();
}

export function extractArticleToc(markdown: string): ArticleTocHeading[] {
  const matches = [...markdown.matchAll(/^(#{2,3})\s+(.+)$/gm)].map((match) => {
    const offset = match.index ?? 0;
    return {
      level: match[1].length as 2 | 3,
      label: inlineMarkdownText(match[2]),
      line: markdown.slice(0, offset).split("\n").length,
    };
  });
  const preferredLevel: 2 | 3 | null = matches.some((heading) => heading.level === 2)
    ? 2
    : matches.some((heading) => heading.level === 3)
      ? 3
      : null;
  if (!preferredLevel) return [];
  return matches
    .filter((heading) => heading.level === preferredLevel && heading.label)
    .map((heading, ordinal) => ({
      ...heading,
      ordinal,
      id: `article-section-${ordinal + 1}`,
    }));
}
