"use client";

import Link from "next/link";
import { isValidElement, useEffect, useMemo, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Headphones, Loader2, ShieldCheck, XCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationMediaPlayer } from "@/app/components/canonical-repository/CitationMediaPlayer";

type PresentationSource = {
  source_type: string;
  public_url: string;
  media?: { kind?: "audio" | "video" | "unknown"; url?: string | null };
};

type SourcePresentation = {
  presentation_id: string;
  source_title?: string;
  start_seconds?: number | null;
  end_seconds?: number | null;
  source?: PresentationSource | null;
};

type DecisionMediaSection = {
  decision_id: string;
  markdown_heading: string;
  passage?: string;
  section_title?: string;
  source_presentations: SourcePresentation[];
  source_presentation_summary?: {
    mode?: "continuous" | "segment_group" | "unavailable";
    status?: string;
    note?: string;
  } | null;
};

type EditorialDraft = {
  draft_id: string;
  decision_id: string;
  candidate_id: string | null;
  title: string;
  status: string;
  status_label: string;
  passage: string;
  decision_title: string;
  markdown: string;
  decision_media_sections: DecisionMediaSection[];
  audit: EditorialDraftAudit | null;
  audit_error: string;
};

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function IntegratedSourceMedia({ section }: { section: DecisionMediaSection }) {
  const playable = section.source_presentations.filter((item) => item.source?.public_url);
  if (playable.length === 0) return null;
  const grouped = section.source_presentation_summary?.mode === "segment_group";
  return (
    <aside className="not-prose my-5 rounded-2xl border border-indigo-100 bg-indigo-50/50 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2 font-bold text-slate-900">
          <Headphones className="h-5 w-5 text-indigo-600" />
          聽王教授原聲講解
        </div>
        {section.passage && <span className="rounded-full bg-white px-3 py-1 text-xs font-bold text-indigo-700">{section.passage}</span>}
      </div>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        {grouped
          ? `本節由 ${playable.length} 段連續原聲共同支持，依原講道次序排列。`
          : "此段原聲已按本節編排定位，可直接聆聽相關講解。"}
      </p>
      <div className="mt-3 space-y-3">
        {playable.map((presentation) => (
          <div key={presentation.presentation_id}>
            {presentation.source_title && playable.length > 1 && (
              <div className="mb-1 text-xs font-semibold text-slate-500">{presentation.source_title}</div>
            )}
            <CitationMediaPlayer
              source={presentation.source!}
              startTime={presentation.start_seconds}
              endTime={presentation.end_seconds}
            />
          </div>
        ))}
      </div>
    </aside>
  );
}

type AuditFinding = {
  code: string;
  severity: "error" | "warning";
  title: string;
  detail: string;
  decision_id: string | null;
  claim_id: string | null;
};

type EditorialDraftAudit = {
  status: "pass" | "pass_with_warnings" | "fail";
  scope: string;
  summary: {
    decision_total: number;
    decision_headings_found: number;
    claim_total: number;
    evidence_step_total: number;
    source_fragment_total: number;
    valid_source_fragment_total: number;
    error_total: number;
    warning_total: number;
  };
  findings: AuditFinding[];
};

export default function EditorialDraftPage({ params }: { params: { draftId: string } }) {
  const [draft, setDraft] = useState<EditorialDraft | null>(null);
  const [error, setError] = useState("");

  const mediaByHeading = useMemo(
    () => new Map((draft?.decision_media_sections ?? []).map((section) => [section.markdown_heading.trim(), section])),
    [draft],
  );

  useEffect(() => {
    async function loadDraft() {
      try {
        const response = await fetch(`/api/admin/thought-review/drafts/${encodeURIComponent(params.draftId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error((await response.json()).detail ?? "無法載入編輯初稿");
        setDraft(await response.json());
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "無法載入編輯初稿");
      }
    }
    void loadDraft();
  }, [params.draftId]);

  if (error) return <div className="mx-auto mt-12 max-w-2xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800">{error}</div>;
  if (!draft) return <div className="flex min-h-[60vh] items-center justify-center gap-3 text-slate-600"><Loader2 className="h-6 w-6 animate-spin" />正在載入編輯初稿…</div>;

  return (
    <main className="min-h-screen bg-slate-50 pb-16">
      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <Link href="/admin/thought-review/candidates?axis=scripture" className="inline-flex items-center gap-1 text-sm font-semibold text-indigo-700">
          <ChevronLeft className="h-4 w-4" />返回釋經候選
        </Link>
        <header className="mt-5 rounded-3xl bg-slate-900 p-7 text-white shadow-sm sm:p-9">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-indigo-500/20 px-3 py-1 text-xs font-bold text-indigo-200">{draft.status_label}</span>
            {draft.passage && <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-bold text-slate-200">{draft.passage}</span>}
          </div>
          <h1 className="mt-4 text-3xl font-bold leading-tight sm:text-4xl">{draft.title}</h1>
          <p className="mt-3 text-sm text-slate-300">這是編輯部依據已校對材料與共享主張整理的初稿，尚未等同於正式出版稿。</p>
          {draft.candidate_id && (
            <Link href={`/admin/thought-review?tab=validation&plan=${encodeURIComponent(draft.candidate_id)}`} className="mt-5 inline-flex items-center gap-1 font-semibold text-indigo-200 hover:text-white">
              查看對應編排計劃<ChevronRight className="h-4 w-4" />
            </Link>
          )}
        </header>
        {draft.audit ? (
          <section className={`mt-7 rounded-3xl border p-6 shadow-sm sm:p-7 ${draft.audit.status === "fail" ? "border-rose-200 bg-rose-50" : draft.audit.status === "pass_with_warnings" ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`}>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-sm font-bold text-slate-600">
                  <ShieldCheck className="h-5 w-5" />結構與溯源自動審核
                </div>
                <h2 className="mt-2 flex items-center gap-2 text-2xl font-bold text-slate-950">
                  {draft.audit.status === "fail" ? <XCircle className="h-6 w-6 text-rose-600" /> : draft.audit.status === "pass_with_warnings" ? <AlertTriangle className="h-6 w-6 text-amber-600" /> : <CheckCircle2 className="h-6 w-6 text-emerald-600" />}
                  {draft.audit.status === "fail" ? "尚未通過自動審核" : draft.audit.status === "pass_with_warnings" ? "主體已通過，仍有出版前事項" : "已通過自動審核"}
                </h2>
              </div>
              <span className="rounded-full bg-white/80 px-3 py-1 text-sm font-bold text-slate-700">
                {draft.audit.summary.decision_headings_found}/{draft.audit.summary.decision_total} 個編排段落已對應
              </span>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl bg-white/75 p-4"><div className="text-2xl font-bold text-slate-950">{draft.audit.summary.claim_total}</div><div className="mt-1 text-sm text-slate-600">條共享主張</div></div>
              <div className="rounded-2xl bg-white/75 p-4"><div className="text-2xl font-bold text-slate-950">{draft.audit.summary.evidence_step_total}</div><div className="mt-1 text-sm text-slate-600">個證據步驟</div></div>
              <div className="rounded-2xl bg-white/75 p-4"><div className="text-2xl font-bold text-slate-950">{draft.audit.summary.valid_source_fragment_total}/{draft.audit.summary.source_fragment_total}</div><div className="mt-1 text-sm text-slate-600">個有效來源定位</div></div>
            </div>
            {draft.audit.findings.length > 0 && (
              <div className="mt-5 space-y-3">
                {draft.audit.findings.map((finding, index) => (
                  <div key={`${finding.code}-${finding.decision_id ?? index}`} className="rounded-2xl border border-white/80 bg-white/80 p-4">
                    <div className="font-bold text-slate-900">{finding.title}</div>
                    <p className="mt-1 text-sm leading-6 text-slate-700">{finding.detail}</p>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-4 text-xs leading-5 text-slate-500">審核範圍：{draft.audit.scope}</p>
          </section>
        ) : draft.audit_error ? (
          <div className="mt-7 rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800">無法執行初稿自動審核：{draft.audit_error}</div>
        ) : null}
        <article className="prose prose-slate mt-7 max-w-none rounded-3xl border border-slate-200 bg-white px-6 py-8 shadow-sm sm:px-10 sm:py-10 prose-headings:scroll-mt-24 prose-h1:hidden prose-blockquote:border-indigo-300 prose-blockquote:bg-indigo-50/60 prose-blockquote:px-5 prose-blockquote:py-2">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h4: ({ children }) => {
                const section = mediaByHeading.get(nodeText(children).trim());
                return (
                  <>
                    <h4>{children}</h4>
                    {section && <IntegratedSourceMedia section={section} />}
                  </>
                );
              },
            }}
          >
            {draft.markdown}
          </ReactMarkdown>
        </article>
      </div>
    </main>
  );
}
