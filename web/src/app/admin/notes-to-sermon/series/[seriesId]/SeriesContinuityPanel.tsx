"use client";

import React, { useEffect, useMemo, useState } from "react";

type ContinuityRelationship =
    | "new"
    | "duplicate"
    | "extension"
    | "correction"
    | "related_qa"
    | "tangential_qa"
    | "non_substantive";

interface EvidenceItem {
    evidence_id: string;
    content: string;
    scripture_refs?: string[];
    category?: string;
    type?: string;
}

interface PriorSection {
    section_id: string;
    project_id: string;
    heading_path: string[];
    text: string;
}

interface ContinuityDecision {
    current_evidence_ids: string[];
    relationship: ContinuityRelationship;
    matched_prior_section_ids: string[];
    new_contribution: string;
    recommended_action: string;
    reason: string;
    confidence: "high" | "medium" | "low";
    matched_prior_units?: Array<{
        project_id: string;
        unit_title: string;
        section_id: string;
    }>;
}

interface ContinuityProposal {
    proposal_id: string;
    created_at: string;
    summary: string;
    current_evidence: EvidenceItem[];
    prior_sections: PriorSection[];
    decisions: ContinuityDecision[];
}

interface ContinuityStatus {
    series_id: string;
    project_id: string;
    status: "idle" | "queued" | "running" | "completed" | "failed";
    message: string;
    proposal?: ContinuityProposal;
}

interface SeriesBuildStatus {
    series_id: string;
    project_id: string;
    status: "idle" | "queued" | "running" | "completed" | "failed";
    message: string;
    proposal_id?: string;
    changed_unit_count: number;
    new_unit_count: number;
    evidence_count: number;
}

const RELATIONSHIP_LABELS: Record<ContinuityRelationship, string> = {
    new: "新内容",
    duplicate: "完全重复",
    extension: "新增证据或推理",
    correction: "修正或限定",
    related_qa: "相关问答",
    tangential_qa: "延伸问答",
    non_substantive: "课堂流程",
};

const RELATIONSHIP_STYLES: Record<ContinuityRelationship, string> = {
    new: "bg-green-100 text-green-800",
    duplicate: "bg-gray-100 text-gray-700",
    extension: "bg-blue-100 text-blue-800",
    correction: "bg-red-100 text-red-800",
    related_qa: "bg-purple-100 text-purple-800",
    tangential_qa: "bg-amber-100 text-amber-800",
    non_substantive: "bg-slate-100 text-slate-600",
};

const ACTION_LABELS: Record<string, string> = {
    create_new_unit: "建立新单元",
    merge_into_existing: "合并进既有单元",
    move_to_appendix: "移入附录",
    omit_exact_duplicate: "省略完全重复",
    omit_non_substantive: "不进入文稿",
    needs_editor_decision: "需要编辑决定",
};

export default function SeriesContinuityPanel({
    seriesId,
    projectId,
    projectTitle,
    onClose,
}: {
    seriesId: string;
    projectId: string;
    projectTitle: string;
    onClose: () => void;
}) {
    const [status, setStatus] = useState<ContinuityStatus | null>(null);
    const [buildStatus, setBuildStatus] = useState<SeriesBuildStatus | null>(null);
    const [error, setError] = useState("");
    const [expandedDecision, setExpandedDecision] = useState<number | null>(null);

    const fetchStatus = async () => {
        try {
            const response = await fetch(
                `/api/admin/notes-to-sermon/series/${seriesId}/continuity/${encodeURIComponent(projectId)}`,
                { cache: "no-store" }
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to load continuity status.");
            setStatus(body);
            setError("");
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to load continuity status.");
        }
    };

    const fetchBuildStatus = async () => {
        try {
            const response = await fetch(
                `/api/admin/notes-to-sermon/series/${seriesId}/series-draft/${encodeURIComponent(projectId)}`,
                { cache: "no-store" }
            );
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to load Series Draft status.");
            setBuildStatus(body);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to load Series Draft status.");
        }
    };

    useEffect(() => {
        fetchStatus();
        fetchBuildStatus();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [seriesId, projectId]);

    useEffect(() => {
        if (status?.status !== "queued" && status?.status !== "running") return;
        const timer = window.setInterval(fetchStatus, 2000);
        return () => window.clearInterval(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status?.status, seriesId, projectId]);

    useEffect(() => {
        if (buildStatus?.status !== "queued" && buildStatus?.status !== "running") return;
        const timer = window.setInterval(fetchBuildStatus, 2000);
        return () => window.clearInterval(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [buildStatus?.status, seriesId, projectId]);

    const startAnalysis = async () => {
        setError("");
        try {
            const response = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/continuity`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: projectId }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to start continuity analysis.");
            setStatus(body);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to start continuity analysis.");
        }
    };

    const approveAndBuild = async () => {
        if (!proposal) return;
        setError("");
        try {
            const response = await fetch(`/api/admin/notes-to-sermon/series/${seriesId}/series-draft`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: projectId, proposal_id: proposal.proposal_id }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(body.detail || "Unable to build Series Draft.");
            setBuildStatus(body);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : "Unable to build Series Draft.");
        }
    };

    const proposal = status?.proposal;
    const evidenceById = useMemo(
        () => new Map((proposal?.current_evidence || []).map(item => [item.evidence_id, item])),
        [proposal]
    );
    const priorById = useMemo(
        () => new Map((proposal?.prior_sections || []).map(item => [item.section_id, item])),
        [proposal]
    );
    const counts = useMemo(() => {
        const values: Partial<Record<ContinuityRelationship, number>> = {};
        for (const decision of proposal?.decisions || []) {
            values[decision.relationship] = (values[decision.relationship] || 0) + decision.current_evidence_ids.length;
        }
        return values;
    }, [proposal]);

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
            <div className="my-6 w-full max-w-6xl rounded-xl bg-white shadow-2xl">
                <div className="flex items-start justify-between border-b p-5">
                    <div>
                        <h2 className="text-xl font-bold text-gray-900">跨讲内容整合</h2>
                        <p className="mt-1 text-sm text-gray-600">{projectTitle}</p>
                        <p className="mt-1 text-xs text-gray-500">
                            根据正文、经文和论证与较早的已审核稿比较；Project 标题不参与重复判断。
                        </p>
                    </div>
                    <button onClick={onClose} className="text-2xl text-gray-400 hover:text-gray-700" aria-label="Close">
                        ×
                    </button>
                </div>

                <div className="p-5">
                    {error ? <div className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div> : null}

                    {!status || status.status === "idle" || status.status === "failed" ? (
                        <div className="rounded-lg border border-dashed p-8 text-center">
                            <p className="mb-4 text-sm text-gray-600">
                                {status?.status === "failed" ? status.message : "尚未建立跨讲重复与增量报告。"}
                            </p>
                            <button
                                onClick={startAnalysis}
                                className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
                            >
                                分析与前文的关系
                            </button>
                        </div>
                    ) : null}

                    {status?.status === "queued" || status?.status === "running" ? (
                        <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-8 text-center">
                            <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-4 border-indigo-200 border-t-indigo-600" />
                            <p className="text-sm font-medium text-indigo-900">{status.message}</p>
                        </div>
                    ) : null}

                    {proposal ? (
                        <>
                            <div className="mb-5 rounded-lg border border-indigo-100 bg-indigo-50 p-4">
                                <div className="flex items-start justify-between gap-4">
                                    <p className="text-sm leading-6 text-indigo-950">{proposal.summary}</p>
                                    <button onClick={startAnalysis} className="shrink-0 text-xs font-semibold text-indigo-700 hover:underline">
                                        重新分析
                                    </button>
                                </div>
                            </div>

                            <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
                                {Object.entries(counts).map(([relationship, count]) => (
                                    <div key={relationship} className="rounded-lg border p-3">
                                        <div className="text-2xl font-bold text-gray-900">{count}</div>
                                        <div className="text-xs text-gray-500">
                                            {RELATIONSHIP_LABELS[relationship as ContinuityRelationship]}
                                        </div>
                                    </div>
                                ))}
                            </div>

                            <div className="space-y-3">
                                {proposal.decisions.map((decision, index) => {
                                    const expanded = expandedDecision === index;
                                    const evidence = decision.current_evidence_ids.map(id => evidenceById.get(id)).filter(Boolean) as EvidenceItem[];
                                    const prior = decision.matched_prior_section_ids.map(id => priorById.get(id)).filter(Boolean) as PriorSection[];
                                    const matchedUnitTitles = decision.matched_prior_units?.map(item => item.unit_title) || Array.from(
                                        new Set(prior.map(item => item.heading_path[0] || item.project_id))
                                    );
                                    return (
                                        <div key={`${decision.relationship}-${index}`} className="rounded-lg border bg-white">
                                            <button
                                                type="button"
                                                onClick={() => setExpandedDecision(expanded ? null : index)}
                                                className="flex w-full items-start justify-between gap-4 p-4 text-left"
                                            >
                                                <div>
                                                    <div className="mb-2 flex flex-wrap items-center gap-2">
                                                        <span className={`rounded-full px-2 py-1 text-xs font-semibold ${RELATIONSHIP_STYLES[decision.relationship]}`}>
                                                            {RELATIONSHIP_LABELS[decision.relationship]}
                                                        </span>
                                                        <span className="text-xs font-medium text-gray-600">
                                                            {ACTION_LABELS[decision.recommended_action] || decision.recommended_action}
                                                        </span>
                                                        <span className="text-xs text-gray-400">可信度：{decision.confidence}</span>
                                                    </div>
                                                    <p className="text-sm font-medium leading-6 text-gray-900">{decision.new_contribution}</p>
                                                    <p className="mt-1 text-xs text-gray-500">
                                                        {decision.current_evidence_ids.join(", ")}
                                                        {prior.length ? ` · 对应 ${prior.length} 个既有段落` : ""}
                                                    </p>
                                                    {matchedUnitTitles.length ? (
                                                        <p className="mt-2 text-xs leading-5 text-indigo-700">
                                                            <strong>既有单元：</strong>{matchedUnitTitles.join("；")}
                                                        </p>
                                                    ) : null}
                                                </div>
                                                <span className="mt-1 text-gray-400">{expanded ? "▲" : "▼"}</span>
                                            </button>

                                            {expanded ? (
                                                <div className="border-t bg-gray-50 p-4">
                                                    <p className="mb-4 text-sm leading-6 text-gray-700"><strong>判断理由：</strong>{decision.reason}</p>
                                                    <div className="grid gap-4 lg:grid-cols-2">
                                                        <div>
                                                            <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">本讲证据</h4>
                                                            <div className="space-y-2">
                                                                {evidence.map(item => (
                                                                    <div key={item.evidence_id} className="rounded border bg-white p-3 text-sm leading-6">
                                                                        <div className="mb-1 text-xs font-semibold text-indigo-700">{item.evidence_id} · {item.category || item.type}</div>
                                                                        {item.content}
                                                                        {item.scripture_refs?.length ? (
                                                                            <div className="mt-2 text-xs text-gray-500">经文：{item.scripture_refs.join("、")}</div>
                                                                        ) : null}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                        <div>
                                                            <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-gray-500">既有文稿位置</h4>
                                                            <div className="space-y-2">
                                                                {prior.length ? prior.map(item => (
                                                                    <div key={item.section_id} className="rounded border bg-white p-3 text-sm leading-6">
                                                                        <div className="mb-1 text-xs font-semibold text-gray-600">
                                                                            {item.heading_path.join(" › ") || item.project_id}
                                                                        </div>
                                                                        <p className="line-clamp-6 whitespace-pre-wrap text-gray-700">{item.text}</p>
                                                                    </div>
                                                                )) : <p className="text-sm italic text-gray-400">没有对应的既有段落。</p>}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            ) : null}
                                        </div>
                                    );
                                })}
                            </div>

                            <div className="mt-5 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                                <div className="flex flex-wrap items-center justify-between gap-4">
                                    <div>
                                        <p className="text-sm font-semibold text-indigo-950">审核完成后，建立 Series Draft</p>
                                        <p className="mt-1 text-xs leading-5 text-indigo-800">
                                            只写入系列整合稿，不改动任何 Project 的 final.md，也不会发布到前台。
                                        </p>
                                    </div>
                                    {buildStatus?.status === "completed" ? (
                                        <a
                                            href={`/admin/notes-to-sermon/series/${seriesId}/draft`}
                                            className="rounded bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700"
                                        >
                                            审核本次整合变更
                                        </a>
                                    ) : (
                                        <button
                                            type="button"
                                            onClick={approveAndBuild}
                                            disabled={buildStatus?.status === "queued" || buildStatus?.status === "running"}
                                            className="rounded bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300"
                                        >
                                            {buildStatus?.status === "queued" || buildStatus?.status === "running"
                                                ? "正在建立 Series Draft…"
                                                : "批准并建立 Series Draft"}
                                        </button>
                                    )}
                                </div>
                                {buildStatus?.status === "completed" ? (
                                    <p className="mt-3 text-xs text-green-800">
                                        已更新 {buildStatus.changed_unit_count} 个既有单元，新增 {buildStatus.new_unit_count} 个单元，
                                        共处理 {buildStatus.evidence_count} 项证据。
                                    </p>
                                ) : null}
                                {buildStatus?.status === "failed" ? (
                                    <p className="mt-3 text-xs text-red-700">建立失败：{buildStatus.message}</p>
                                ) : null}
                            </div>
                        </>
                    ) : null}
                </div>
            </div>
        </div>
    );
}
