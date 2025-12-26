"use client";

import React, { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeKatex from "rehype-katex";
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

// Regex to detect any of the 5 alert types.
// We use a capturing group for the type to identify which one it is.
const ALERT_MARKER_DETECT_REGEX = /\[!(note|tip|important|warning|caution)\]/i;
const ALERT_MARKER_REMOVE_REGEX = /\s*\[!(note|tip|important|warning|caution)\]\s*/gi;

const normalizeSrc = (value?: string) => {
  if (!value) {
    return value;
  }
  const trimmed = value.trim();
  return trimmed.replace(/^["'“”‘’]+|["'“”‘’]+$/g, "");
};

const removeAlertMarkers = (node: React.ReactNode): React.ReactNode => {
  if (typeof node === "string") {
    const cleaned = node.replace(ALERT_MARKER_REMOVE_REGEX, " ").replace(/\s{2,}/g, " ").trimStart();
    return cleaned.length > 0 ? cleaned : null;
  }

  if (Array.isArray(node)) {
    return node.map((child) => removeAlertMarkers(child)).filter((child) => child !== null);
  }

  if (React.isValidElement(node)) {
    // Void elements cannot have children, so we shouldn't try to process them
    // or clone them with a children array (even an empty one).
    const voidElements = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
    if (typeof node.type === 'string' && voidElements.has(node.type)) {
      return node;
    }

    const childNodes = React.Children.toArray(node.props.children);
    const newChildren = childNodes.map((child) => removeAlertMarkers(child)).filter((child) => child !== null);
    return React.cloneElement(node, node.props, newChildren);
  }

  return node;
};

const getAlertConfig = (text: string) => {
  const match = text.match(ALERT_MARKER_DETECT_REGEX);
  if (!match) return null;

  const type = match[1].toLowerCase();

  switch (type) {
    case "note":
      return {
        title: "Editor's Note",
        colorClass: "text-blue-900 border-blue-400 bg-blue-50",
        iconColorClass: "text-blue-700",
        defaultOpen: true,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
        )
      };
    case "tip":
      return {
        title: "原文釋經",
        colorClass: "text-teal-900 border-teal-400 bg-teal-50",
        iconColorClass: "text-teal-700",
        defaultOpen: false,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 384 512" fill="currentColor" className="h-5 w-5">
            <path d="M176 0c-26.5 0-48 21.5-48 48v80H48c-26.5 0-48 21.5-48 48v32c0 26.5 21.5 48 48 48h80V464c0 26.5 21.5 48 48 48h32c26.5 0 48-21.5 48-48V256h80c26.5 0 48-21.5 48-48V176c0-26.5-21.5-48-48-48H256V48c0-26.5-21.5-48-48-48H176z" />
          </svg>
        )
      };
    case "important":
      return {
        title: "Important",
        colorClass: "text-purple-900 border-purple-400 bg-purple-50",
        iconColorClass: "text-purple-700",
        defaultOpen: true,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
        )
      };
    case "warning":
      return {
        title: "Warning",
        colorClass: "text-amber-900 border-amber-400 bg-amber-50",
        iconColorClass: "text-amber-700",
        defaultOpen: true,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
        )
      };
    case "caution":
      return {
        title: "Caution",
        colorClass: "text-red-900 border-red-400 bg-red-50",
        iconColorClass: "text-red-700",
        defaultOpen: true,
        icon: (
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
          </svg>
        )
      };
    default:
      return null;
  }
};

export function ScriptureMarkdown({ markdown, sectionId }: ScriptureMarkdownProps & { sectionId?: string }) {

  const components = useMemo(() => ({
    h2({ children, ...props }: any) {
      return (
        <h2 className="scroll-mt-24 text-3xl font-bold mt-8 mb-4 text-gray-900" {...props}>
          {children}
        </h2>
      );
    },
    h3({ children, ...props }: any) {
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
        <h3 id={id} className="scroll-mt-24 text-2xl font-semibold mt-6 mb-3 text-gray-800" {...props}>
          {children}
        </h3>
      );
    },
    h4({ children, ...props }: any) {
      return (
        <h4 className="scroll-mt-24 text-xl font-semibold mt-5 mb-2 text-gray-800" {...props}>
          {children}
        </h4>
      );
    },
    a({ children, href, ...props }: any) {
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
    img({ node, ...props }: any) {
      // Accuracy matters more than Next.js image optimization for markdown content
      // eslint-disable-next-line @next/next/no-img-element
      return <img {...props} />;
    },
    p({ children, ...props }: any) {
      return <p {...props}>{children}</p>;
    },
    blockquote({ children, ...props }: any) {
      // Detect marker anywhere in the blockquote content
      const blockquoteText = extractText(children);
      const config = getAlertConfig(blockquoteText);

      if (!config) {
        return <blockquote {...props}>{children}</blockquote>;
      }

      const cleanedChildren = React.Children.map(children, (child) => removeAlertMarkers(child));

      return (
        <CollapsibleAlert
          title={config.title}
          icon={config.icon}
          colorClass={config.colorClass}
          iconColorClass={config.iconColorClass}
          defaultOpen={config.defaultOpen}
        >
          {cleanedChildren}
        </CollapsibleAlert>
      );
    },
  }), [sectionId]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeRaw, rehypeKatex]}
      components={components}
    >
      {markdown}
    </ReactMarkdown>
  );
}

function CollapsibleAlert({
  title,
  icon,
  colorClass,
  iconColorClass,
  defaultOpen,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  colorClass: string;
  iconColorClass: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className={`my-4 rounded-r border-l-4 ${colorClass}`} data-is-alert="true">
      <div
        className={`flex items-center justify-between px-4 py-3 cursor-pointer select-none ${!isOpen ? 'rounded-r' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className={`flex items-center gap-2 font-bold ${iconColorClass}`}>
          {icon}
          {title}
        </div>
        <div className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''} ${iconColorClass} print:hidden`}>
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
            <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
          </svg>
        </div>
      </div>

      <div className={`px-4 pb-4 text-sm leading-relaxed opacity-90 [&>p]:my-0 space-y-2 ${isOpen ? 'block' : 'hidden print:block'}`}>
        {children}
      </div>
    </div>
  );
}
