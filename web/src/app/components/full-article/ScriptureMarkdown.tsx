"use client";

import React, { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { Popover, PopoverTrigger, PopoverContent } from "@/app/components/popover";
import * as Tabs from "@radix-ui/react-tabs";

type Lang = "zh" | "en" | "el" | "he";

const LANGUAGE_ORDER: Lang[] = ["zh", "en", "el", "he"];
const LABELS: Record<Lang, string> = {
  zh: "中文",
  en: "英文",
  el: "希腊文",
  he: "希伯来",
};

interface ScriptureResponse {
  reference: string;
  passages: Record<string, string>;
}

interface ScriptureMarkdownProps {
  markdown: string;
}

function extractText(node: React.ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(extractText).join("");
  }
  if (React.isValidElement(node)) {
    return extractText(node.props.children);
  }
  return "";
}

function BibleReferenceLink({ href, label, children }: React.PropsWithChildren<{ href: string; label?: string }>) {
  const [open, setOpen] = useState(false);
  const [activeLang, setActiveLang] = useState<Lang>("zh");
  const [referenceLabel, setReferenceLabel] = useState<string | null>(label ?? null);
  const [passages, setPassages] = useState<Partial<Record<Lang, string>>>({});

  const [basicLoaded, setBasicLoaded] = useState(false);
  const [basicLoading, setBasicLoading] = useState(false);
  const [basicError, setBasicError] = useState<string | null>(null);

  const [originalLoaded, setOriginalLoaded] = useState(false);
  const [originalLoading, setOriginalLoading] = useState(false);
  const [originalError, setOriginalError] = useState<string | null>(null);

  const slug = useMemo(() => href.replace(/^#/, ""), [href]);

  async function loadBasic() {
    if (basicLoaded || basicLoading) {
      return;
    }
    setBasicLoading(true);
    setBasicError(null);
    try {
      const response = await fetch(`/api/scripture/basic/${slug}`);
      if (!response.ok) {
        throw new Error(`(${response.status}) 無法取得經文`);
      }
      const payload = (await response.json()) as ScriptureResponse;
      setReferenceLabel((prev) => prev ?? payload.reference);
      setPassages((prev) => ({ ...prev, ...payload.passages }));
      setBasicLoaded(true);
    } catch (err) {
      setBasicError(err instanceof Error ? err.message : "未知錯誤");
    } finally {
      setBasicLoading(false);
    }
  }

  async function loadOriginal() {
    if (originalLoaded || originalLoading) {
      return;
    }
    setOriginalLoading(true);
    setOriginalError(null);
    try {
      const response = await fetch(`/api/scripture/original/${slug}`);
      if (!response.ok) {
        throw new Error(`(${response.status}) 無法取得原文經文`);
      }
      const payload = (await response.json()) as ScriptureResponse;
      setReferenceLabel((prev) => prev ?? payload.reference);
      setPassages((prev) => ({ ...prev, ...payload.passages }));
      setOriginalLoaded(true);
    } catch (err) {
      setOriginalError(err instanceof Error ? err.message : "未知錯誤");
    } finally {
      setOriginalLoading(false);
    }
  }

  const handleOpenChange = (value: boolean) => {
    setOpen(value);
    if (value) {
      void loadBasic();
    }
  };

  const handleMouseEnter = () => {
    setOpen(true);
    void loadBasic();
  };

  const handleClick = (event: React.MouseEvent | React.TouchEvent) => {
    event.preventDefault();
    setOpen(true);
    void loadBasic();
  };

  const handleTabChange = (lang: string) => {
    const value = lang as Lang;
    setActiveLang(value);
    if ((value === "el" || value === "he") && !originalLoaded && !originalLoading) {
      void loadOriginal();
    }
  };

  const anyContent = Object.values(passages).some(Boolean);

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <a
          href={href}
          className="cursor-pointer text-blue-600 underline decoration-dotted"
          onClick={handleClick}
          onTouchStart={handleClick}
          onMouseEnter={handleMouseEnter}
        >
          {children}
        </a>
      </PopoverTrigger>
      <PopoverContent
        align="center"
        sideOffset={8}
        className="w-[520px] max-w-2xl rounded-md border border-gray-200 bg-white p-4 shadow-lg"
      >
        {basicLoading && !basicLoaded && <p className="text-sm text-gray-500">載入經文中...</p>}
        {basicError && !basicLoaded && <p className="text-sm text-red-600">{basicError}</p>}
        {anyContent ? (
          <div className="space-y-3 text-sm text-gray-800">
            <p className="font-semibold text-gray-900">{referenceLabel || label}</p>
            <Tabs.Root value={activeLang} onValueChange={handleTabChange}>
              <Tabs.List className="mb-3 flex gap-2">
                {LANGUAGE_ORDER.map((lang) => (
                  <Tabs.Trigger
                    key={lang}
                    value={lang}
                    className="relative flex items-center rounded-t-md border border-b-0 border-gray-200 px-3 py-1.5 text-xs transition-colors data-[state=active]:bg-white data-[state=active]:text-blue-700 data-[state=active]:shadow-sm data-[state=active]:border-gray-300 data-[state=inactive]:bg-gray-100 data-[state=inactive]:text-gray-500"
                  >
                    {LABELS[lang]}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>
              {LANGUAGE_ORDER.map((lang) => {
                const content = passages[lang];
                const isOriginalLang = lang === "el" || lang === "he";
                const isBasicLang = lang === "zh" || lang === "en";
                let body: React.ReactNode;
                if (content) {
                  body = (
                    <div
                      className="prose prose-sm max-w-none"
                      dangerouslySetInnerHTML={{ __html: content }}
                    />
                  );
                } else if (isOriginalLang && originalLoading) {
                  body = <p className="text-xs text-gray-500">載入經文中...</p>;
                } else if (isOriginalLang && originalError) {
                  body = <p className="text-xs text-red-600">{originalError}</p>;
                } else if (isBasicLang && basicLoading) {
                  body = <p className="text-xs text-gray-500">載入經文中...</p>;
                } else if (isBasicLang && basicError) {
                  body = <p className="text-xs text-red-600">{basicError}</p>;
                } else {
                  body = <p className="text-xs text-gray-500">此語言暫無內容。</p>;
                }
                return (
                  <Tabs.Content
                    key={lang}
                    value={lang}
                    className="rounded-md border border-gray-200 bg-white p-3 shadow-sm focus-visible:outline-none"
                  >
                    {body}
                  </Tabs.Content>
                );
              })}
            </Tabs.Root>
          </div>
        ) : (
          !basicLoading && !originalLoading && !basicError && (
            <p className="text-xs text-gray-500">目前無法取得經文內容。</p>
          )
        )}
      </PopoverContent>
    </Popover>
  );
}

export function ScriptureMarkdown({ markdown, sectionId }: ScriptureMarkdownProps & { sectionId?: string }) {
  const NOTE_MARKER_DETECT_REGEX = /\[!note\]/i;
  const NOTE_MARKER_REMOVE_REGEX = /\s*\[!note\]\s*/gi;

  const normalizeSrc = (value?: string) => {
    if (!value) {
      return value;
    }
    const trimmed = value.trim();
    return trimmed.replace(/^["'“”‘’]+|["'“”‘’]+$/g, "");
  };

  const removeNoteMarkers = (node: React.ReactNode): React.ReactNode => {
    if (typeof node === "string") {
      const cleaned = node.replace(NOTE_MARKER_REMOVE_REGEX, " ").replace(/\s{2,}/g, " ").trimStart();
      return cleaned.length > 0 ? cleaned : null;
    }

    if (Array.isArray(node)) {
      return node.map((child) => removeNoteMarkers(child)).filter((child) => child !== null);
    }

    if (React.isValidElement(node)) {
      const childNodes = React.Children.toArray(node.props.children);
      const newChildren = childNodes.map((child) => removeNoteMarkers(child)).filter((child) => child !== null);
      return React.cloneElement(node, node.props, newChildren);
    }

    return node;
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={{
        h4({ children, ...props }) {
          const text = extractText(children);
          // We need to match the ID generation logic from section-utils.ts
          // slugifySectionTitle logic:
          const slug = text
            .trim()
            .toLowerCase()
            .normalize("NFKD")
            .replace(/[\u0300-\u036f]/g, "")
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "");

          const id = sectionId ? `${sectionId}--${slug}` : undefined;

          return (
            <h4 id={id} className="scroll-mt-24" {...props}>
              {children}
            </h4>
          );
        },
        a({ children, href, ...props }) {
          if (href && href.startsWith("#scripture-")) {
            const label = extractText(children);
            return (
              <BibleReferenceLink href={href} label={label}>
                <span {...props}>{children}</span>
              </BibleReferenceLink>
            );
          }
          return (
            <a href={href} {...props}>
              {children}
            </a>
          );
        },
        img({ node, ...props }) {
          const htmlProps = props as React.ImgHTMLAttributes<HTMLImageElement>;
          const nodeProps = (node as { properties?: { src?: string; alt?: string } })?.properties ?? {};
          const rawSrc = htmlProps.src ?? (typeof nodeProps.src === "string" ? nodeProps.src : undefined);
          const rawAlt = htmlProps.alt ?? (typeof nodeProps.alt === "string" ? nodeProps.alt : undefined);
          const { src: _ignoredSrc, alt: _ignoredAlt, ...rest } = htmlProps;
          // Accuracy matters more than Next.js image optimization for markdown content
          // eslint-disable-next-line @next/next/no-img-element
          return <img src={normalizeSrc(rawSrc)} alt={rawAlt} {...rest} />;
        },
        p({ children, ...props }) {
          return <p {...props}>{children}</p>;
        },
        blockquote({ children, ...props }) {
          // Detect marker anywhere in the blockquote content
          const blockquoteText = extractText(children);
          if (!NOTE_MARKER_DETECT_REGEX.test(blockquoteText)) {
            return <blockquote {...props}>{children}</blockquote>;
          }

          const cleanedChildren = React.Children.map(children, (child) => removeNoteMarkers(child));

          return (
            <div className="my-4 rounded-r border-l-4 border-blue-400 bg-blue-50 p-4 text-blue-900" data-is-alert="true">
              <div className="mb-2 flex items-center gap-2 font-bold text-blue-700">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                  className="h-5 w-5"
                >
                  <path
                    fillRule="evenodd"
                    d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z"
                    clipRule="evenodd"
                  />
                </svg>
                Editor&apos;s Note
              </div>
              <div className="text-sm leading-relaxed opacity-90 [&>p]:my-0 space-y-2">
                {cleanedChildren}
              </div>
            </div>
          );
        },
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
