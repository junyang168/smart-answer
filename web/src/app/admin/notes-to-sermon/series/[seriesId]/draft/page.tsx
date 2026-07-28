"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface IntegrationChange {
    canonical_unit_id: string;
    change_type: "updated" | "new" | "appendix";
    target_project_id?: string | null;
    previous_title?: string | null;
    unit_title: string;
    change_summary: string;
    evidence_ids: string[];
    markdown: string;
}

interface DraftResponse {
    markdown: string;
    proposal_id?: string;
    project_id?: string;
    changed_unit_count: number;
    new_unit_count: number;
    evidence_count: number;
    changes: IntegrationChange[];
}

interface IntegratedManuscriptStatus {
    status: "idle" | "draft_generated_pending_patch_review";
    message: string;
    application_id?: string;
    local_unit_count: number;
    pending_patch_count: number;
    evidence_count: number;
    applied_patch_count: number;
    conflict_patch_count: number;
    patches: Array<{
        canonical_unit_id: string;
        target_project_id: string;
        status: "safe" | "applied" | "conflict";
        conflict_reason?: string | null;
    }>;
}

const CHANGE_LABELS = {
    updated: "更新既有单元",
    new: "新增正文单元",
    appendix: "新增附录单元",
};

const CHANGE_STYLES = {
    updated: "bg-blue-100 text-blue-800",
    new: "bg-green-100 text-green-800",
    appendix: "bg-amber-100 text-amber-800",
};

const conflictMessage = (reason?: string | null) => {
    if (reason === "Target Draft contains edits not present in final.md; manual merge required.") {
        return "目标 Draft 含有尚未发布的人工修改，系统已保留原文，需要手动合并。";
    }
    if (reason === "Target final.md changed after Proposal review.") {
        return "目标 published 稿在 Proposal 审核后发生变化，需要重新审核。";
    }
    return reason || "目标单元已发生变化，需要手动合并。";
};

export default function SeriesDraftPage() {
    const params = useParams<{ seriesId: string }>();
    const seriesId = params.seriesId;
    const [markdown, setMarkdown] = useState("");
    const [changes, setChanges] = useState<IntegrationChange[]>([]);
    const [counts, setCounts] = useState({ changed: 0, added: 0, evidence: 0 });
    const [view, setView] = useState<"changes" | "full">("changes");
    const [expanded, setExpanded] = useState<string | null>(null);
    const [draftMeta, setDraftMeta] = useState<{ proposalId: string; projectId: string } | null>(null);
    const [applicationStatus, setApplicationStatus] = useState<IntegratedManuscriptStatus | null>(null);
    const [generating, setGenerating] = useState(false);
    const [applyingPatches, setApplyingPatches] = useState(false);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const load = async () => {
            try {
                const response = await fetch(
                    `/api/admin/notes-to-sermon/series/${seriesId}/series-draft`,
                    { cache: "no-store" }
                );
                const body = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(body.detail || "Unable to load Series Draft.");
                const draft = body as DraftResponse;
                setMarkdown(draft.markdown || "");
                setChanges(draft.changes || []);
                setCounts({
                    changed: draft.changed_unit_count || 0,
                    added: draft.new_unit_count || 0,
                    evidence: draft.evidence_count || 0,
                });
                if (draft.proposal_id && draft.project_id) {
                    setDraftMeta({ proposalId: draft.proposal_id, projectId: draft.project_id });
                    const statusResponse = await fetch(
                        `/api/admin/notes-to-sermon/series/${seriesId}/integrated-manuscript/${encodeURIComponent(draft.project_id)}`,
                        { cache: "no-store" }
                    );
                    const statusBody = await statusResponse.json().catch(() => ({}));
                    if (statusResponse.ok) setApplicationStatus(statusBody);
                }
            } catch (reason) {
                setError(reason instanceof Error ? reason.message : "Unable to load Series Draft.");
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [seriesId]);

    const generateIntegratedManuscript = async () => {
        if (!draftMeta) return;
        setGenerating(true);
        setError("");
        try {
            const response = await fetch(
                `/api/admin/notes-to-sermon/series/${seriesId}/integrated-manuscript`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        project_id: draftMeta.projectId,
                        proposal_id: draftMeta.proposalId,
                    }),
                }
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to generate integrated manuscript.");
            setApplicationStatus(body);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to generate integrated manuscript.");
        } finally {
            setGenerating(false);
        }
    };

    const applySafePatches = async () => {
        if (!draftMeta || !applicationStatus?.application_id) return;
        const safeCount = applicationStatus.patches?.filter(item => item.status === "safe").length || 0;
        if (!safeCount) return;
        if (!window.confirm(`将 ${safeCount} 个安全补丁应用到对应 Project Draft？published final.md 不会被修改。`)) return;
        setApplyingPatches(true);
        setError("");
        try {
            const response = await fetch(
                `/api/admin/notes-to-sermon/series/${seriesId}/integrated-manuscript/apply-patches`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        project_id: draftMeta.projectId,
                        application_id: applicationStatus.application_id,
                    }),
                }
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to apply integration patches.");
            setApplicationStatus(body);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to apply integration patches.");
        } finally {
            setApplyingPatches(false);
        }
    };

    return (
        <main className="min-h-screen bg-slate-50 px-6 py-8">
            <div className="mx-auto max-w-5xl">
                <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
                    <div>
                        <Link
                            href={`/admin/notes-to-sermon/series/${seriesId}`}
                            className="text-sm text-slate-500 hover:text-indigo-700"
                        >
                            ← 返回 Series
                        </Link>
                        <h1 className="mt-2 text-3xl font-bold text-slate-900">本次整合变更</h1>
                        <p className="mt-1 text-sm text-slate-600">审核第四讲对既有文稿带来的更新与新增内容。</p>
                    </div>
                </div>

                {loading ? <div className="rounded-xl border bg-white p-10 text-center text-slate-500">正在载入…</div> : null}
                {error ? <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-700">{error}</div> : null}
                {!loading && !error ? (
                    <>
                        <div className="mb-5 grid gap-3 sm:grid-cols-3">
                            <div className="rounded-xl border bg-white p-4"><div className="text-2xl font-bold text-blue-700">{counts.changed}</div><div className="text-sm text-slate-500">既有单元更新</div></div>
                            <div className="rounded-xl border bg-white p-4"><div className="text-2xl font-bold text-green-700">{counts.added}</div><div className="text-sm text-slate-500">新增正文与附录</div></div>
                            <div className="rounded-xl border bg-white p-4"><div className="text-2xl font-bold text-indigo-700">{counts.evidence}</div><div className="text-sm text-slate-500">已处理 Evidence</div></div>
                        </div>

                        <div className="mb-5 flex items-center gap-2 rounded-lg border bg-white p-1.5">
                            <button onClick={() => setView("changes")} className={`rounded-md px-4 py-2 text-sm font-semibold ${view === "changes" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}>本次变更</button>
                            <button onClick={() => setView("full")} className={`rounded-md px-4 py-2 text-sm font-semibold ${view === "full" ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}>完整 Series 全文</button>
                        </div>

                        <div className="mb-5 rounded-xl border border-indigo-200 bg-indigo-50 p-5">
                            <div className="flex flex-wrap items-center justify-between gap-4">
                                <div>
                                    <h2 className="font-bold text-indigo-950">生成第四讲的整合后 Manuscript</h2>
                                    <p className="mt-1 text-sm leading-6 text-indigo-800">
                                        第四讲 Draft 包含新增正文与附录；既有 Project 的更新先成为待审核补丁，安全时只更新 Draft，绝不覆盖 published 稿。
                                    </p>
                                </div>
                                {applicationStatus?.status === "draft_generated_pending_patch_review" && draftMeta ? (
                                    <div className="flex flex-wrap gap-2">
                                        {(applicationStatus.patches?.filter(item => item.status === "safe").length || 0) > 0 ? (
                                            <button type="button" onClick={applySafePatches} disabled={applyingPatches} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:bg-indigo-300">
                                                {applyingPatches ? "正在应用…" : `应用 ${applicationStatus.patches.filter(item => item.status === "safe").length} 个安全补丁`}
                                            </button>
                                        ) : null}
                                        <a href={`/admin/notes-to-sermon/project/${encodeURIComponent(draftMeta.projectId)}`} className="rounded-lg bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700">
                                            打开第四讲 Draft
                                        </a>
                                    </div>
                                ) : (
                                    <button type="button" onClick={generateIntegratedManuscript} disabled={!draftMeta || generating} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300">
                                        {generating ? "正在生成…" : "生成整合后 Manuscript"}
                                    </button>
                                )}
                            </div>
                            {applicationStatus?.status === "draft_generated_pending_patch_review" ? (
                                <div className="mt-3 space-y-1 text-xs">
                                    <p className="text-green-800">已生成 {applicationStatus.local_unit_count} 个本讲单元；{applicationStatus.evidence_count} 项 Evidence 已登记去向。</p>
                                    <p className="text-indigo-800">已应用 {applicationStatus.applied_patch_count || 0} 个既有单元补丁；冲突 {applicationStatus.conflict_patch_count || 0} 个。</p>
                                </div>
                            ) : null}
                        </div>

                        {view === "changes" ? (
                            <div className="space-y-4">
                                {changes.map(change => {
                                    const isExpanded = expanded === change.canonical_unit_id;
                                    const patchStatus = applicationStatus?.patches?.find(item => item.canonical_unit_id === change.canonical_unit_id);
                                    return (
                                        <section key={change.canonical_unit_id} className="overflow-hidden rounded-xl border bg-white shadow-sm">
                                            <button type="button" onClick={() => setExpanded(isExpanded ? null : change.canonical_unit_id)} className="flex w-full items-start justify-between gap-5 p-5 text-left">
                                                <div>
                                                    <div className="mb-2 flex flex-wrap items-center gap-2">
                                                        <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${CHANGE_STYLES[change.change_type]}`}>{CHANGE_LABELS[change.change_type]}</span>
                                                        {patchStatus ? (
                                                            <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${patchStatus.status === "applied" ? "bg-green-100 text-green-800" : patchStatus.status === "conflict" ? "bg-red-100 text-red-800" : "bg-slate-100 text-slate-700"}`}>
                                                                {patchStatus.status === "applied" ? "已应用到 Draft" : patchStatus.status === "conflict" ? "需要手动合并" : "可安全应用"}
                                                            </span>
                                                        ) : null}
                                                        {change.target_project_id ? <span className="text-xs text-slate-500">目标 Project：{change.target_project_id}</span> : null}
                                                    </div>
                                                    <h2 className="text-lg font-bold text-slate-900">{change.unit_title}</h2>
                                                    {change.previous_title && change.previous_title !== change.unit_title ? <p className="mt-1 text-xs text-slate-500">原单元：{change.previous_title}</p> : null}
                                                    <p className="mt-2 text-sm leading-6 text-slate-600">{change.change_summary}</p>
                                                    <p className="mt-2 text-xs text-indigo-700">Evidence：{change.evidence_ids.join(", ")}</p>
                                                    {patchStatus?.conflict_reason ? <p className="mt-2 text-xs font-medium text-red-700">冲突原因：{conflictMessage(patchStatus.conflict_reason)}</p> : null}
                                                    {patchStatus?.status === "applied" && patchStatus.target_project_id ? (
                                                        <a href={`/admin/notes-to-sermon/project/${encodeURIComponent(patchStatus.target_project_id)}`} onClick={event => event.stopPropagation()} className="mt-2 inline-block text-xs font-semibold text-green-700 underline hover:text-green-900">打开目标 Project Draft</a>
                                                    ) : null}
                                                </div>
                                                <span className="mt-1 shrink-0 text-slate-400">{isExpanded ? "▲" : "▼"}</span>
                                            </button>
                                            {isExpanded ? (
                                                <article className="prose prose-slate max-w-none border-t bg-slate-50 px-8 py-7 prose-headings:scroll-mt-24">
                                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{change.markdown}</ReactMarkdown>
                                                </article>
                                            ) : null}
                                        </section>
                                    );
                                })}
                            </div>
                        ) : (
                            <article className="prose prose-slate max-w-none rounded-xl border bg-white px-10 py-10 shadow-sm prose-headings:scroll-mt-24">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                            </article>
                        )}

                        <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                            {applicationStatus?.applied_patch_count
                                ? `这是整合审核页：${applicationStatus.applied_patch_count} 个补丁已写入目标 Project Draft，${applicationStatus.conflict_patch_count || 0} 个仍待手动合并；published 稿和前台内容均未改变。`
                                : "这是整合审核页；尚未应用的变更不会写入 Project，也不会发布到前台。"}
                        </div>
                    </>
                ) : null}
            </div>
        </main>
    );
}
