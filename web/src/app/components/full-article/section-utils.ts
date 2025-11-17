export interface ArticleSection {
  id: string;
  title: string;
  markdown: string;
  index: number;
}

export function slugifySectionTitle(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function buildArticleSections(markdown: string): ArticleSection[] {
  if (!markdown || markdown.trim().length === 0) {
    return [];
  }

  const normalized = markdown.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const sections: ArticleSection[] = [];
  const slugCounter = new Map<string, number>();

  let buffer: string[] = [];
  let currentHeading: string | null = null;

  const pushSection = () => {
    const body = buffer.join("\n").trim();
    if (!body && !currentHeading) {
      buffer = [];
      return;
    }

    const hasExisting = sections.length > 0;
    const title =
      currentHeading && currentHeading.trim().length > 0
        ? currentHeading.trim()
        : hasExisting
          ? `段落 ${sections.length + 1}`
          : "導言";
    const slugBase = slugifySectionTitle(title) || "section";
    const slugIndex = (slugCounter.get(slugBase) ?? 0) + 1;
    slugCounter.set(slugBase, slugIndex);
    const id = slugIndex === 1 ? slugBase : `${slugBase}-${slugIndex}`;
    sections.push({
      id,
      title,
      markdown: body,
      index: sections.length,
    });
    buffer = [];
    currentHeading = null;
  };

  for (const line of lines) {
    const headingMatch = line.match(/^###\s+(.*)$/);
    if (headingMatch) {
      pushSection();
      currentHeading = headingMatch[1]?.trim() ?? null;
      buffer = [];
    } else {
      buffer.push(line);
    }
  }
  pushSection();

  if (sections.length === 0) {
    return [
      {
        id: "section-1",
        title: "全文",
        markdown: markdown.trim(),
        index: 0,
      },
    ];
  }

  return sections.map((section, index) => ({ ...section, index }));
}
