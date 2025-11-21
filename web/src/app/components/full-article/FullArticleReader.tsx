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

  const scrollToAnchor = useCallback((targetId?: string) => {
    if (typeof window === "undefined") {
      return;
    }
    const idToScroll = targetId || topAnchorId;
    if (idToScroll) {
      // Wait a tick for the DOM to update if we just switched sections
      setTimeout(() => {
        const node = document.getElementById(idToScroll);
        if (node) {
          const targetTop = node.getBoundingClientRect().top + window.scrollY;
          const headerOffset = 120;
          window.scrollTo({ top: Math.max(0, targetTop - headerOffset), behavior: "smooth" });
          return;
        }
        // Fallback if specific anchor not found (e.g. subsection)
        if (targetId !== topAnchorId && topAnchorId) {
          const topNode = document.getElementById(topAnchorId);
          if (topNode) {
            const targetTop = topNode.getBoundingClientRect().top + window.scrollY;
            const headerOffset = 120;
            window.scrollTo({ top: Math.max(0, targetTop - headerOffset), behavior: "smooth" });
          }
        }
      }, 100);
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [topAnchorId]);

  const goToSection = useCallback(
    (index: number, options?: { updateHash?: boolean; targetId?: string }) => {
      if (index < 0 || index >= sections.length) {
        return;
      }
      setActiveIndex(index);
      setMobileTOCOpen(false);

      const targetId = options?.targetId || sections[index]?.id;

      if (options?.updateHash !== false) {
        if (targetId && typeof window !== "undefined") {
          const hash = `#${targetId}`;
          if (window.location.hash !== hash) {
            window.history.replaceState(null, "", hash);
          }
        }
      }
      scrollToAnchor(targetId);
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

      // Check if hash matches a section ID
      let index = sections.findIndex((section) => section.id === rawHash);
      let targetId = rawHash;

      // If not a section ID, check if it matches a subsection ID
      if (index === -1) {
        for (let i = 0; i < sections.length; i++) {
          const section = sections[i];
          if (section.subsections?.some((sub) => sub.id === rawHash)) {
            index = i;
            targetId = rawHash;
            break;
          }
        }
      }

      if (index >= 0) {
        // Always go to section if found, even if index matches activeIndex, 
        // because we might need to scroll to a specific subsection
        goToSection(index, { updateHash: false, targetId });
      }
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, [goToSection, sections]);

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
        <section key={currentSection.id} id={currentSection.id} className="bg-white">
          <div className="border-b border-gray-100  pb-4">

            <h2 className="mt-3 text-3xl font-semibold text-gray-900">{normalizeTitle(currentSection.title)}</h2>
          </div>

          <div id={`${currentSection.id}-content`} className="prose prose-base lg:prose-lg prose-slate mt-6 max-w-none">
            <ScriptureMarkdown markdown={currentSection.markdown} sectionId={currentSection.id} />
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
