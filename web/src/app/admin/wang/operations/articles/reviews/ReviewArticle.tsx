"use client";

import { useState, type ReactNode } from "react";
import { BookOpenText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { slugifyHeadingAnchor } from "@/app/components/full-article/heading-anchor";
import { SourceEvidenceDisclosure } from "./SourceEvidenceDisclosure";
import type { ReviewSourceAnnotation } from "./types";

type Footnote = { id: string; markdown: string };

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return nodeText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function nodeHref(node: ReactNode): string | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const href = nodeHref(child);
      if (href) return href;
    }
  }
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { href?: unknown; children?: ReactNode } }).props;
    if (typeof props?.href === "string") return props.href;
    return nodeHref(props?.children);
  }
  return null;
}

function prepareFootnotes(markdown: string): { body: string; footnotes: Footnote[] } {
  const footnotes: Footnote[] = [];
  const body = markdown
    .split("\n")
    .filter((line) => {
      const match = line.match(/^\[\^([^\]]+)\]:\s*(.+)$/);
      if (!match) return true;
      footnotes.push({ id: match[1], markdown: match[2] });
      return false;
    })
    .join("\n")
    .replace(/\[\^([^\]]+)\]/g, (_match, id: string) => `[${id}](#review-footnote-${id})`);
  return { body, footnotes };
}

export function ReviewArticle({
  markdown,
  sourceAnnotations,
}: {
  markdown: string;
  sourceAnnotations: ReviewSourceAnnotation[];
}) {
  const [showSources, setShowSources] = useState(false);
  const withoutTitle = markdown.replace(/^#\s+.+(?:\r?\n)+/, "");
  const { body, footnotes } = prepareFootnotes(withoutTitle);
  const annotations = new Map(sourceAnnotations.map((item) => [item.annotation_id, item.sources]));
  return (
    <>
      <div className="not-prose mb-9 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-3.5">
        <div className="flex items-center gap-3">
          <span className="rounded-xl bg-amber-100 p-2 text-amber-900">
            <BookOpenText className="h-4 w-4" />
          </span>
          <div>
            <p className="text-sm font-bold text-stone-900">显示原文来源</p>
            <p className="mt-0.5 text-xs text-stone-500">逐字稿、时间点 Audio 与母本片段</p>
          </div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={showSources}
          aria-label="显示段落原文来源"
          onClick={() => setShowSources((current) => !current)}
          className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-700 focus-visible:ring-offset-2 ${
            showSources ? "bg-amber-800" : "bg-stone-300"
          }`}
        >
          <span
            aria-hidden="true"
            className={`h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
              showSources ? "translate-x-6" : "translate-x-1"
            }`}
          />
        </button>
      </div>
      <div className="prose prose-stone max-w-none prose-headings:font-serif prose-h2:scroll-mt-32 prose-h2:border-b prose-h2:border-stone-200 prose-h2:pb-3 prose-h2:text-2xl prose-p:text-[1.05rem] prose-p:leading-8 prose-blockquote:border-amber-700 prose-blockquote:bg-amber-50/70 prose-blockquote:px-5 prose-blockquote:py-2 prose-blockquote:not-italic prose-li:leading-8 sm:prose-p:text-[1.1rem]">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            p: ({ children }) => {
              const href = nodeHref(children);
              const match = href?.match(/^#review-source-evidence-(p\d+)$/);
              const sources = match ? annotations.get(match[1]) : null;
              if (!sources) return <p>{children}</p>;
              return showSources ? <SourceEvidenceDisclosure sources={sources} /> : null;
            },
            h2: ({ children }) => {
              const title = nodeText(children).trim();
              return <h2 id={slugifyHeadingAnchor(title)}>{children}</h2>;
            },
            a: ({ href, children }) => (
              <a href={href} className="font-semibold text-amber-900 underline-offset-4 hover:underline">{children}</a>
            ),
          }}
        >{body}</ReactMarkdown>
      </div>
      {footnotes.length > 0 && (
        <aside aria-label="文章注释" className="mt-14 border-t border-stone-200 pt-7">
          <p className="text-xs font-bold tracking-[0.14em] text-stone-500">注释</p>
          <ol className="mt-4 space-y-4 text-sm leading-7 text-stone-600">
            {footnotes.map((footnote) => (
              <li key={footnote.id} id={`review-footnote-${footnote.id}`} className="flex scroll-mt-32 items-start gap-2">
                <span className="font-bold text-stone-900">{footnote.id}.</span>
                <div className="prose prose-stone max-w-none text-sm leading-7 prose-p:m-0">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{footnote.markdown}</ReactMarkdown>
                </div>
              </li>
            ))}
          </ol>
        </aside>
      )}
    </>
  );
}
