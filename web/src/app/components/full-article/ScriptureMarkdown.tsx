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

export function ScriptureMarkdown({ markdown }: ScriptureMarkdownProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw]}
      components={{
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
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
