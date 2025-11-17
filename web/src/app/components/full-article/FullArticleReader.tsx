"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";
import { buildArticleSections } from "@/app/components/full-article/section-utils";
import { CollapsibleSummary } from "@/app/components/full-article/CollapsibleSummary";

interface FullArticleReaderProps {
  markdown: string;
  articleTitle?: string;
  summaryMarkdown?: string;
  topAnchorId?: string;
}

export function FullArticleReader({ markdown, articleTitle, summaryMarkdown, topAnchorId }: FullArticleReaderProps) {
  const sections = useMemo(() => buildArticleSections(markdown), [markdown]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [mobileTOCOpen, setMobileTOCOpen] = useState(false);
  const normalizeTitle = useCallback((value: string) => value.replace(/\*\*/g, "").trim(), []);

  useEffect(() => {
    setActiveIndex(0);
  }, [sections]);

  const scrollToAnchor = useCallback(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (topAnchorId) {
      const node = document.getElementById(topAnchorId);
      if (node) {
        const targetTop = node.getBoundingClientRect().top + window.scrollY;
        const headerOffset = 120;
        window.scrollTo({ top: Math.max(0, targetTop - headerOffset), behavior: "smooth" });
        return;
      }
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [topAnchorId]);

  const goToSection = useCallback(
    (index: number, options?: { updateHash?: boolean }) => {
      if (index < 0 || index >= sections.length) {
        return;
      }
      setActiveIndex(index);
      setMobileTOCOpen(false);
      if (options?.updateHash !== false) {
        const targetId = sections[index]?.id;
        if (targetId && typeof window !== "undefined") {
          const hash = `#${targetId}`;
          if (window.location.hash !== hash) {
            window.history.replaceState(null, "", hash);
          }
        }
      }
      scrollToAnchor();
    },
    [scrollToAnchor, sections],
  );

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const applyHash = () => {
      const rawHash = window.location.hash.replace(/^#/, "");
      if (!rawHash) {
        return;
      }
      const index = sections.findIndex((section) => section.id === rawHash);
      if (index >= 0 && index !== activeIndex) {
        goToSection(index, { updateHash: false });
      }
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, [activeIndex, goToSection, sections]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable) {
        return;
      }
      if (event.key === "[" || event.key === "]") {
        event.preventDefault();
        const delta = event.key === "[" ? -1 : 1;
        goToSection(activeIndex + delta);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeIndex, goToSection]);

  const handleBackToTop = useCallback(() => {
    goToSection(0);
  }, [goToSection]);

  if (sections.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-6 text-center text-sm text-gray-500">
        本文尚未提供內容。
      </div>
    );
  }

  const currentSection = sections[activeIndex];
  const progressRatio = sections.length > 0 ? (activeIndex + 1) / sections.length : 0;

  return (
    <div className="space-y-6">
      <div className="lg:hidden border border-gray-200 rounded-lg">
        <button
          type="button"
          className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-gray-700"
          onClick={() => setMobileTOCOpen((prev) => !prev)}
        >
          <span>章節導覽</span>
          <span>{mobileTOCOpen ? "收合" : "展開"}</span>
        </button>
        {mobileTOCOpen && (
          <div className="border-t border-gray-200">
            <ul className="divide-y divide-gray-100">
              {sections.map((section) => (
                <li key={section.id}>
                  <button
                    type="button"
                    className="w-full px-4 py-2 text-left text-sm transition hover:bg-gray-50"
                    onClick={() => goToSection(section.index)}
                  >
                    {normalizeTitle(section.title)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {summaryMarkdown && activeIndex === 0 && (
        <CollapsibleSummary markdown={summaryMarkdown} defaultOpen />
      )}


      {currentSection && (
        <section
          key={currentSection.id}
          id={currentSection.id}
          className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
        >
          <div className="border-b border-gray-100 pb-4">

            <h2 className="mt-3 text-2xl font-semibold text-gray-900">{normalizeTitle(currentSection.title)}</h2>
          </div>

          <div id={`${currentSection.id}-content`} className="prose prose-base lg:prose-lg prose-slate mt-6 max-w-none">
            <ScriptureMarkdown markdown={currentSection.markdown} />
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3 text-sm">
            {activeIndex > 0 && (
              <>
                <button
                  type="button"
                  className="text-blue-700 hover:underline"
                  onClick={() => goToSection(activeIndex - 1)}
                >
                  ← 上一段
                </button>
                <span className="text-gray-300">•</span>
              </>
            )}
            {activeIndex < sections.length - 1 && (
              <>
                <button
                  type="button"
                  className="text-blue-700 hover:underline"
                  onClick={() => goToSection(activeIndex + 1)}
                >
                  下一段 →
                </button>
                <span className="text-gray-300">•</span>
              </>
            )}
            <button type="button" className="text-gray-600 hover:text-gray-900 hover:underline" onClick={handleBackToTop}>
              回到頁首 ↑
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
