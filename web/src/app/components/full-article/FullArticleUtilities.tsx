"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { ClipboardCopy, Share2, Printer } from "lucide-react";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

interface FullArticleUtilitiesProps {
  articleTitle?: string;
  articleMarkdown?: string | null;
}

export function FullArticleUtilities({ articleTitle, articleMarkdown }: FullArticleUtilitiesProps) {
  const [status, setStatus] = useState<string | null>(null);
  const renderedContentRef = useRef<HTMLDivElement | null>(null);
  const safeMarkdown = useMemo(() => articleMarkdown ?? "", [articleMarkdown]);

  const withMessage = useCallback((message: string) => {
    setStatus(message);
    window.setTimeout(() => setStatus(null), 2000);
  }, []);

  const handleCopyArticle = useCallback(async () => {
    try {
      if (!safeMarkdown) {
        withMessage("沒有可複製的內容");
        return;
      }
      await navigator.clipboard.writeText(safeMarkdown);
      withMessage("已複製全文 Markdown");
    } catch (error) {
      withMessage("複製失敗，請稍後再試");
    }
  }, [safeMarkdown, withMessage]);

  const handleShareArticle = useCallback(async () => {
    const url = typeof window !== "undefined" ? window.location.href : "";
    if (navigator.share) {
      try {
        await navigator.share({
          title: articleTitle ?? "全文文章",
          url,
        });
        return;
      } catch (error) {
        // ignore cancellation
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      withMessage("已複製分享連結");
    } catch (error) {
      withMessage("無法分享，請稍後再試");
    }
  }, [articleTitle, withMessage]);

  const handlePrintArticle = useCallback(() => {
    if (!safeMarkdown) {
      withMessage("沒有可列印的內容");
      return;
    }
    const container = renderedContentRef.current;
    const htmlContent = container?.innerHTML ?? `<pre>${safeMarkdown}</pre>`;
    const printWindow = window.open("", "_blank", "width=900,height=700");
    if (!printWindow) {
      withMessage("無法開啟列印視窗");
      return;
    }
    const styles = `<style>
      body { font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 40px; }
      h1, h2, h3, h4 { color: #0f172a; }
      img { max-width: 100%; height: auto; }
    </style>`;
    printWindow.document.write(`<html><head><title>${articleTitle ?? "全文文章"}</title>${styles}</head><body>`);
    if (articleTitle) {
      printWindow.document.write(`<h1>${articleTitle}</h1>`);
    }
    printWindow.document.write(htmlContent);
    printWindow.document.write("</body></html>");
    printWindow.document.close();
    printWindow.focus();
    printWindow.print();
  }, [articleTitle, safeMarkdown, withMessage]);

  return (
    <div className="bg-white p-5">
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="rounded-full border border-gray-200 bg-white p-2 text-gray-600 transition hover:border-blue-200 hover:text-blue-700"
          onClick={handleCopyArticle}
          aria-label="複製全文 Markdown"
        >
          <ClipboardCopy className="h-4 w-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          className="rounded-full border border-gray-200 bg-white p-2 text-gray-600 transition hover:border-blue-200 hover:text-blue-700"
          onClick={handleShareArticle}
          aria-label="分享文章"
        >
          <Share2 className="h-4 w-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          className="rounded-full border border-gray-200 bg-white p-2 text-gray-600 transition hover:border-blue-200 hover:text-blue-700"
          onClick={handlePrintArticle}
          aria-label="列印文章"
        >
          <Printer className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      {status && <p className="mt-3 text-xs text-blue-600">{status}</p>}
      <div className="hidden" aria-hidden="true">
        <div ref={renderedContentRef}>
          <ScriptureMarkdown markdown={safeMarkdown} />
        </div>
      </div>
    </div>
  );
}
