"use client";

import { useState } from "react";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

interface CollapsibleSummaryProps {
  markdown: string;
  defaultOpen?: boolean;
}

export function CollapsibleSummary({ markdown, defaultOpen = true }: CollapsibleSummaryProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="bg-slate-50 border border-slate-200 rounded-lg">
      <button
        type="button"
        className="flex w-full items-center justify-between px-6 py-4 text-left"
        onClick={() => setOpen((prev) => !prev)}
      >
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-blue-500" />
          <h2 className="text-xl font-semibold text-slate-800">內容摘要</h2>
        </div>
        <span className="text-sm text-blue-700">{open ? "收合" : "展開"}</span>
      </button>
      {open && (
        <div className="px-6 pb-6">
          <div className="prose prose-sm max-w-none text-slate-800">
            <ScriptureMarkdown markdown={markdown} />
          </div>
        </div>
      )}
    </section>
  );
}
