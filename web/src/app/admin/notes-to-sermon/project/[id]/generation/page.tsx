"use client";

import { useEffect, useMemo, useState, use } from "react";
import { useRouter } from "next/navigation";
import {
    AlertCircle,
    ArrowLeft,
    CheckCircle2,
    FileText,
    Eye,
    Loader2,
    PenTool,
    Play,
    RefreshCcw,
    Search,
    Scissors,
} from "lucide-react";

type Stage1Log = {
    timestamp: string;
    role: string;
    message: string;
    unit_id?: string;
};

type Stage1Unit = {
    unit_id: string;
    chapter_title: string;
    section_title: string;
    unit_title: string;
    scripture_range: string;
    start_line: number;
    end_line: number;
    split_reason: string;
    status: "pending" | "running" | "completed" | "failed" | string;
    has_points: boolean;
    has_generated: boolean;
    error?: string | null;
    display_index: number;
    title?: string;
    central_question?: string | null;
    direct_answer?: string | null;
    objective?: string;
    evidence_ids?: string[];
    evidence_count?: number;
    source_ranges?: Array<{ start_line: number; end_line: number }>;
    plan_reason?: string;
};

type AuditFinding = {
    finding_id?: string;
    type?: string;
    severity?: string;
    unit_id?: string | null;
    evidence_ids?: string[];
    description?: string;
    recommended_fix?: string;
};

type Stage1Status = {
    job: {
        running: boolean;
        status?: string;
        mode?: string;
        unit_id?: string | null;
        force?: boolean;
        started_at?: string;
        completed_at?: string;
        failed_at?: string;
        error?: string;
    };
    project: {
        processing: boolean;
        processing_status?: string;
        processing_progress?: number;
        processing_error?: string;
        title?: string;
        project_type?: string;
        model?: string;
    };
    manifest: {
        status?: string;
        split_status?: string;
        failed_units?: Array<{ unit_id: string; error: string }>;
    };
    summary: {
        total_units: number;
        completed_units: number;
        running_units: number;
        failed_units: number;
        pending_units: number;
        split_completed: boolean;
        analysis_completed?: boolean;
        evidence_count?: number;
        plan_ready?: boolean;
        draft_ready: boolean;
        audit_status?: string | null;
        audit_finding_count?: number;
        current_unit_id?: string | null;
        integration_active?: boolean;
        integration_series_id?: string | null;
        pending_patch_count?: number;
        applied_patch_count?: number;
        remaining_patch_count?: number;
    };
    audit?: {
        overall_status?: string;
        findings?: AuditFinding[];
    } | null;
    units: Stage1Unit[];
    logs: Stage1Log[];
};

type PromptKey = string;

type Stage1PromptBundle = {
    project_type: string;
    model: string;
    reasoning_effort: string;
    prompts: Record<string, string>;
};

const PROMPT_LABELS: Record<string, string> = {
    unit_splitter: "1. 單元切割",
    point_extractor: "2. 細節與經文證據提取",
    unit_generator: "3. Manuscript 生成",
    evidence_inventory: "1. 全文證據清單",
    manuscript_planner: "2. 全文邏輯規劃",
    coverage_auditor: "4. 全文覆蓋審核",
};

const DEFAULT_STATUS: Stage1Status = {
    job: { running: false },
    project: { processing: false },
    manifest: {},
    summary: {
        total_units: 0,
        completed_units: 0,
        running_units: 0,
        failed_units: 0,
        pending_units: 0,
        split_completed: false,
        draft_ready: false,
        current_unit_id: null,
    },
    units: [],
    logs: [],
    audit: null,
};

function statusBadge(status: string) {
    switch (status) {
        case "completed":
            return "bg-green-100 text-green-700 border-green-200";
        case "running":
            return "bg-blue-100 text-blue-700 border-blue-200";
        case "failed":
            return "bg-red-100 text-red-700 border-red-200";
        default:
            return "bg-gray-100 text-gray-600 border-gray-200";
    }
}

function modeLabel(mode?: string, unitId?: string | null) {
    switch (mode) {
        case "split":
            return "教學單元切割";
        case "analyze":
            return "全文證據與邏輯分析";
        case "generate_all":
            return "全部單元生成";
        case "generate_unit":
            return unitId ? `單元生成 ${unitId}` : "單元生成";
        case "audit":
            return "全文覆蓋審核";
        default:
            return "Stage 1";
    }
}

export default function GenerationPage(props: { params: Promise<{ id: string }> }) {
    const params = use(props.params);
    const router = useRouter();
    const projectId = decodeURIComponent(params.id);
    const [state, setState] = useState<Stage1Status>(DEFAULT_STATUS);
    const [loading, setLoading] = useState(true);
    const [requesting, setRequesting] = useState<string | null>(null);
    const [promptBundle, setPromptBundle] = useState<Stage1PromptBundle | null>(null);
    const [showPromptReview, setShowPromptReview] = useState(false);
    const [activePrompt, setActivePrompt] = useState<PromptKey>("unit_generator");

    const fetchStatus = async () => {
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/status`, {
                headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
            });
            if (!res.ok) {
                throw new Error("Failed to load Stage 1 status");
            }
            const data = await res.json();
            setState({
                ...DEFAULT_STATUS,
                ...data,
                job: { ...DEFAULT_STATUS.job, ...(data.job || {}) },
                project: { ...DEFAULT_STATUS.project, ...(data.project || {}) },
                manifest: { ...DEFAULT_STATUS.manifest, ...(data.manifest || {}) },
                summary: { ...DEFAULT_STATUS.summary, ...(data.summary || {}) },
                audit: data.audit || null,
                units: Array.isArray(data.units) ? data.units : [],
                logs: Array.isArray(data.logs) ? data.logs : [],
            });
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchStatus();
        const interval = setInterval(fetchStatus, 2000);
        return () => clearInterval(interval);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId]);

    useEffect(() => {
        fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/prompts`, {
            headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
        })
            .then(async (res) => {
                if (!res.ok) throw new Error("Failed to load Stage 1 prompts");
                return res.json();
            })
            .then(setPromptBundle)
            .catch((error) => console.error(error));
    }, [projectId]);

    useEffect(() => {
        if (!promptBundle?.prompts) return;
        const preferred = promptBundle.project_type === "transcript" ? "evidence_inventory" : "unit_generator";
        if (promptBundle.prompts[preferred]) setActivePrompt(preferred);
    }, [promptBundle]);

    const launchJob = async (path: string, options?: { force?: boolean; confirmMessage?: string }) => {
        if (options?.confirmMessage && !window.confirm(options.confirmMessage)) {
            return;
        }
        setRequesting(path);
        try {
            const res = await fetch(path, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ force: options?.force ?? false }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data.detail || "Failed to start Stage 1 job");
            }
            await fetchStatus();
        } catch (error: any) {
            alert(error.message || "Failed to start Stage 1 job");
        } finally {
            setRequesting(null);
        }
    };

    const overallProgress = useMemo(() => {
        if (typeof state.project.processing_progress === "number") {
            return state.project.processing_progress;
        }
        if (!state.summary.total_units) {
            return state.summary.analysis_completed ? 30 : (state.summary.split_completed ? 15 : 0);
        }
        const completed = state.summary.completed_units + state.summary.failed_units;
        return Math.max(15, Math.min(100, Math.round((completed / state.summary.total_units) * 100)));
    }, [state.project.processing_progress, state.summary]);

    const running = state.job.running;
    const isTranscript = state.project.project_type === "transcript" || promptBundle?.project_type === "transcript";
    const splitReady = state.summary.split_completed;
    const analysisReady = Boolean(state.summary.analysis_completed);
    const integrationActive = Boolean(state.summary.integration_active);
    const failedUnits = state.units.filter((unit) => unit.status === "failed");
    const auditFindings = Array.isArray(state.audit?.findings) ? state.audit.findings : [];

    return (
        <div className="min-h-screen bg-gray-50 py-10">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                    <div className="space-y-2">
                        <button
                            onClick={() => router.push(`/admin/notes-to-sermon/project/${projectId}`)}
                            className="flex items-center text-sm text-gray-500 hover:text-gray-800"
                        >
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            Back to Project
                        </button>
                        <div>
                            <h1 className="text-2xl font-bold text-gray-900">Stage 1 Pipeline</h1>
                            <p className="text-sm text-gray-500">
                                {state.project.title || projectId}
                            </p>
                        </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-3">
                        <button
                            onClick={() => setShowPromptReview((value) => !value)}
                            className="inline-flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-100"
                        >
                            <Eye className="mr-2 h-4 w-4" />
                            {showPromptReview ? "Hide Prompts" : "Review Prompts"}
                        </button>
                        {isTranscript ? (
                            <button
                                onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/analyze`, {
                                    force: analysisReady,
                                    confirmMessage: analysisReady ? "Rerun full transcript evidence extraction and logical planning?" : undefined,
                                })}
                                disabled={running || requesting !== null || integrationActive}
                                className="inline-flex items-center rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                            >
                                {requesting?.endsWith("/stage1/analyze") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Search className="mr-2 h-4 w-4" />}
                                {analysisReady ? "Rerun Analysis" : "Analyze Transcript"}
                            </button>
                        ) : (
                            <button
                                onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/split`, {
                                    force: splitReady,
                                    confirmMessage: splitReady ? "Rerun unit splitting and refresh the Stage 1 split result?" : undefined,
                                })}
                                disabled={running || requesting !== null}
                                className="inline-flex items-center rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                            >
                                {requesting?.endsWith("/stage1/split") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Scissors className="mr-2 h-4 w-4" />}
                                {splitReady ? "Rerun Split" : "Run Unit Split"}
                            </button>
                        )}
                        <button
                            onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/generate-all`, {
                                force: isTranscript ? state.summary.draft_ready : splitReady,
                                confirmMessage: isTranscript
                                    ? (analysisReady ? "Generate the manuscript from the reviewed evidence and logical plan?" : "Analyze the full transcript, build the plan, and generate the manuscript now?")
                                    : (splitReady ? "Generate manuscripts for all units now?" : "Run the full Stage 1 pipeline now?"),
                            })}
                            disabled={running || requesting !== null || integrationActive}
                            className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                        >
                            {requesting?.endsWith("/stage1/generate-all") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                            {isTranscript
                                ? (integrationActive ? "Integrated Manuscript Ready" : "Generate Manuscript")
                                : (splitReady ? "Generate All Units" : "Run Full Pipeline")}
                        </button>
                        {isTranscript
                            && state.summary.total_units > 0
                            && state.summary.completed_units === state.summary.total_units
                            && (
                            <button
                                onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/audit`, { force: true })}
                                disabled={running || requesting !== null}
                                className="inline-flex items-center rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:bg-gray-100"
                            >
                                {requesting?.endsWith("/stage1/audit") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                                {integrationActive && state.summary.audit_status === "pass"
                                    ? "Rerun Coverage Audit (Optional)"
                                    : "Coverage Audit"}
                            </button>
                        )}
                        <button
                            onClick={fetchStatus}
                            disabled={loading}
                            className="inline-flex items-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
                        >
                            <RefreshCcw className="mr-2 h-4 w-4" />
                            Refresh
                        </button>
                        <button
                            onClick={() => router.push(`/admin/notes-to-sermon/project/${projectId}`)}
                            disabled={!state.summary.draft_ready}
                            className="inline-flex items-center rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-sm font-medium text-green-700 transition hover:bg-green-100 disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-gray-100 disabled:text-gray-400"
                        >
                            <FileText className="mr-2 h-4 w-4" />
                            Open Draft
                        </button>
                    </div>
                </div>

                {integrationActive ? (
                    <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm leading-6 text-indigo-900">
                        <p className="min-w-0 flex-1">
                            本 Project 已使用审核通过的跨讲整合生成。独立分析和 Generate Manuscript 已锁定，以免重新引入与前讲重复的内容。
                            已应用 {state.summary.applied_patch_count || 0} 个既有单元补丁，尚有 {state.summary.remaining_patch_count ?? state.summary.pending_patch_count ?? 0} 个需要处理；你可以打开 Draft 编辑本讲新增内容，并按需要重新执行 Coverage Audit。
                        </p>
                        {state.summary.integration_series_id ? (
                            <button
                                type="button"
                                onClick={() => router.push(`/admin/notes-to-sermon/series/${encodeURIComponent(state.summary.integration_series_id || "")}/draft`)}
                                className="shrink-0 rounded-lg border border-indigo-300 bg-white px-4 py-2 font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-100"
                            >
                                查看全部 {state.summary.pending_patch_count || 0} 个补丁
                            </button>
                        ) : null}
                    </div>
                ) : null}

                {showPromptReview && (
                    <div className="rounded-2xl border border-indigo-200 bg-white p-6 shadow-sm">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div>
                                <h2 className="text-lg font-semibold text-gray-900">Runtime Prompt Review</h2>
                                <p className="mt-1 text-sm text-gray-500">
                                    These are the exact resolved prompts that this Project will use. This view is read-only.
                                </p>
                            </div>
                            <div className="flex flex-wrap gap-2 text-xs font-semibold">
                                <span className="rounded-full bg-purple-100 px-3 py-1 text-purple-700">
                                    Workflow: {promptBundle?.project_type || state.project.project_type || "loading"}
                                </span>
                                <span className="rounded-full bg-blue-100 px-3 py-1 text-blue-700">
                                    Model: {promptBundle?.model || state.project.model || "loading"}
                                </span>
                                <span className="rounded-full bg-gray-100 px-3 py-1 text-gray-700">
                                    Reasoning: {promptBundle?.reasoning_effort || "medium"}
                                </span>
                            </div>
                        </div>

                        <div className="mt-5 flex flex-wrap gap-2">
                            {Object.keys(promptBundle?.prompts || {}).map((key) => (
                                <button
                                    key={key}
                                    onClick={() => setActivePrompt(key)}
                                    className={`rounded-lg px-3 py-2 text-sm font-medium transition ${activePrompt === key
                                        ? "bg-indigo-600 text-white"
                                        : "border border-gray-200 bg-white text-gray-700 hover:bg-gray-50"
                                        }`}
                                >
                                    {PROMPT_LABELS[key] || key}
                                </button>
                            ))}
                        </div>

                        <pre className="mt-4 max-h-[560px] overflow-auto whitespace-pre-wrap rounded-xl border border-gray-200 bg-gray-50 p-5 text-sm leading-6 text-gray-800">
                            {promptBundle?.prompts?.[activePrompt] || "Loading prompt..."}
                        </pre>
                    </div>
                )}

                <div className="grid gap-6 xl:grid-cols-[2fr,1fr]">
                    <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                                <h2 className="text-lg font-semibold text-gray-900">Pipeline Status</h2>
                                <p className="text-sm text-gray-500">
                                    {running
                                        ? `${modeLabel(state.job.mode, state.job.unit_id)} 正在執行`
                                        : isTranscript
                                            ? (analysisReady
                                                ? "Full evidence inventory and logical manuscript plan are ready for review."
                                                : "Analyze the full transcript first; no mechanical line-based split is used.")
                                            : splitReady
                                                ? "Split result is ready. You can generate individual units or run all."
                                                : "Run unit splitting first, then inspect the split result."}
                                </p>
                            </div>
                            <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold ${running ? "border-blue-200 bg-blue-50 text-blue-700" : "border-gray-200 bg-gray-50 text-gray-600"}`}>
                                {running ? "Running" : (state.manifest.status || "Idle")}
                            </span>
                        </div>

                        <div className="mt-6">
                            <div className="mb-2 flex items-center justify-between text-sm">
                                <span className="font-medium text-gray-700">
                                    {state.project.processing_status || state.manifest.status || "Not started"}
                                </span>
                                <span className="text-gray-500">{overallProgress}%</span>
                            </div>
                            <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                                <div
                                    className="h-full rounded-full bg-blue-600 transition-all duration-500"
                                    style={{ width: `${overallProgress}%` }}
                                />
                            </div>
                        </div>

                        <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
                            {(isTranscript ? [
                                ["Evidence", state.summary.evidence_count || 0],
                                ["Planned Units", state.summary.total_units],
                                ["Generated", state.summary.completed_units],
                                ["Pending", state.summary.pending_units],
                                ["Audit Findings", state.summary.audit_finding_count || 0],
                            ] : [
                                ["Total Units", state.summary.total_units],
                                ["Completed", state.summary.completed_units],
                                ["Running", state.summary.running_units],
                                ["Pending", state.summary.pending_units],
                                ["Failed", state.summary.failed_units],
                            ]).map(([label, value]) => (
                                <div key={label} className="rounded-xl border border-gray-100 bg-gray-50 p-4">
                                    <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
                                    <div className="mt-2 text-2xl font-semibold text-gray-900">{value}</div>
                                </div>
                            ))}
                        </div>

                        {state.project.processing_error && (
                            <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                                <div className="mb-1 flex items-center font-semibold">
                                    <AlertCircle className="mr-2 h-4 w-4" />
                                    Stage 1 Error
                                </div>
                                <div>{state.project.processing_error}</div>
                            </div>
                        )}

                        {failedUnits.length > 0 && (
                            <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                                <div className="mb-2 font-semibold">Failed Units</div>
                                <div className="space-y-1">
                                    {failedUnits.map((unit) => (
                                        <div key={unit.unit_id}>
                                            {unit.unit_id} {unit.unit_title}: {unit.error || "Unknown error"}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}

                        {auditFindings.length > 0 && (
                            <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
                                <div className="mb-3 flex items-center font-semibold">
                                    <AlertCircle className="mr-2 h-4 w-4" />
                                    Coverage Audit Findings
                                </div>
                                <div className="space-y-3">
                                    {auditFindings.map((finding, index) => (
                                        <div
                                            key={finding.finding_id || `${finding.unit_id || "document"}-${index}`}
                                            className="rounded-lg border border-amber-200 bg-white p-3"
                                        >
                                            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide text-amber-700">
                                                <span>{finding.severity || "review"}</span>
                                                <span>·</span>
                                                <span>{finding.type || "coverage"}</span>
                                                {finding.unit_id ? <span>· {finding.unit_id}</span> : null}
                                            </div>
                                            {finding.description ? (
                                                <p className="mt-2 leading-6 text-gray-900">{finding.description}</p>
                                            ) : null}
                                            {finding.evidence_ids?.length ? (
                                                <p className="mt-1 text-xs text-gray-500">
                                                    Evidence: {finding.evidence_ids.join(", ")}
                                                </p>
                                            ) : null}
                                            {finding.recommended_fix ? (
                                                <p className="mt-2 border-l-2 border-amber-300 pl-3 leading-6 text-gray-700">
                                                    建议：{finding.recommended_fix}
                                                </p>
                                            ) : null}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
                        <h2 className="text-lg font-semibold text-gray-900">Live Logs</h2>
                        <div className="mt-4 h-[420px] space-y-3 overflow-y-auto rounded-xl border border-gray-100 bg-gray-50 p-4">
                            {loading ? (
                                <div className="flex h-full items-center justify-center text-sm text-gray-400">
                                    Loading Stage 1 logs...
                                </div>
                            ) : state.logs.length === 0 ? (
                                <div className="flex h-full items-center justify-center text-sm text-gray-400">
                                    No Stage 1 logs yet.
                                </div>
                            ) : (
                                state.logs.map((log, index) => (
                                    <div key={`${log.timestamp}-${index}`} className="rounded-lg border border-gray-100 bg-white p-3 text-sm">
                                        <div className="flex items-start justify-between gap-3">
                                            <div>
                                                <div className="font-medium text-gray-900">{log.message}</div>
                                                <div className="mt-1 text-xs uppercase tracking-wide text-gray-400">{log.role}</div>
                                            </div>
                                            <div className="text-xs text-gray-400">
                                                {new Date(log.timestamp).toLocaleTimeString([], {
                                                    hour: "2-digit",
                                                    minute: "2-digit",
                                                    second: "2-digit",
                                                    hour12: false,
                                                })}
                                            </div>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </div>

                <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
                    <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
                        <div>
                            <h2 className="text-lg font-semibold text-gray-900">
                                {isTranscript ? "Manuscript Plan" : "Teaching Units"}
                            </h2>
                            <p className="text-sm text-gray-500">
                                {isTranscript
                                    ? "Logical units are built from the full evidence inventory and may combine non-contiguous source ranges."
                                    : "Split first, inspect the boundaries, then generate manuscripts per unit or all at once."}
                            </p>
                        </div>
                        {state.summary.current_unit_id && (
                            <div className="inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                Current: {state.summary.current_unit_id}
                            </div>
                        )}
                    </div>

                    {!(isTranscript ? analysisReady : splitReady) ? (
                        <div className="p-8 text-center text-sm text-gray-500">
                            {isTranscript
                                ? "No manuscript plan yet. Analyze the transcript to build the evidence inventory and logical plan."
                                : "No split result yet. Run unit splitting to inspect the Stage 1 boundaries."}
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-100 text-sm">
                                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                                    <tr>
                                        <th className="px-6 py-3">Unit</th>
                                        <th className="px-6 py-3">Scripture</th>
                                        <th className="px-6 py-3">{isTranscript ? "Evidence / Source" : "Source Lines"}</th>
                                        <th className="px-6 py-3">{isTranscript ? "Logical Role" : "Split Reason"}</th>
                                        <th className="px-6 py-3">Status</th>
                                        <th className="px-6 py-3 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                    {state.units.map((unit) => {
                                        const unitActionPath = `/api/admin/notes-to-sermon/sermon-project/${projectId}/stage1/unit/${unit.unit_id}/generate`;
                                        const isUnitRequesting = requesting === unitActionPath;
                                        const regenerate = unit.has_generated;
                                        return (
                                            <tr key={unit.unit_id} className="align-top">
                                                <td className="px-6 py-4">
                                                    <div className="font-semibold text-gray-900">
                                                        {unit.display_index}. {unit.title || unit.unit_title}
                                                    </div>
                                                    {isTranscript ? (
                                                        <div className="mt-2 max-w-sm space-y-1 text-xs leading-5 text-gray-500">
                                                            {unit.central_question && <div><span className="font-semibold text-gray-600">Question:</span> {unit.central_question}</div>}
                                                            {unit.direct_answer && <div><span className="font-semibold text-gray-600">Answer:</span> {unit.direct_answer}</div>}
                                                        </div>
                                                    ) : (
                                                        <div className="mt-1 text-xs text-gray-500">
                                                            {unit.chapter_title || "未標明章標題"}
                                                            {unit.section_title ? ` / ${unit.section_title}` : ""}
                                                        </div>
                                                    )}
                                                </td>
                                                <td className="px-6 py-4 text-gray-700">{unit.scripture_range || "未標明"}</td>
                                                <td className="px-6 py-4 text-gray-700">
                                                    {isTranscript ? (
                                                        <div className="space-y-1">
                                                            <div>{unit.evidence_count ?? unit.evidence_ids?.length ?? 0} evidence items</div>
                                                            <div className="text-xs text-gray-500">
                                                                {(unit.source_ranges || []).map((range) => `${range.start_line}–${range.end_line}`).join(", ") || "No source range"}
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <>lines {unit.start_line}-{unit.end_line}</>
                                                    )}
                                                </td>
                                                <td className="max-w-md px-6 py-4 text-gray-600">
                                                    {isTranscript ? (unit.plan_reason || unit.objective) : unit.split_reason}
                                                </td>
                                                <td className="px-6 py-4">
                                                    <div className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${statusBadge(unit.status)}`}>
                                                        {unit.status === "completed" && <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status === "running" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                                                        {unit.status === "pending" && <PenTool className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status === "failed" && <AlertCircle className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status}
                                                    </div>
                                                    <div className="mt-2 text-xs text-gray-400">
                                                        {isTranscript
                                                            ? `${unit.evidence_count ?? unit.evidence_ids?.length ?? 0} evidence · ${unit.has_generated ? "draft ready" : "no draft"}`
                                                            : `${unit.has_points ? "points ready" : "no points"} · ${unit.has_generated ? "draft ready" : "no draft"}`}
                                                    </div>
                                                    {unit.error && (
                                                        <div className="mt-2 max-w-xs text-xs leading-5 text-red-600">
                                                            {unit.error}
                                                        </div>
                                                    )}
                                                </td>
                                                <td className="px-6 py-4 text-right">
                                                    <button
                                                        onClick={() => launchJob(unitActionPath, {
                                                            force: regenerate,
                                                            confirmMessage: regenerate ? `Regenerate manuscript for ${unit.unit_id}?` : undefined,
                                                        })}
                                                        disabled={running || requesting !== null}
                                                        className="inline-flex items-center rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
                                                    >
                                                        {isUnitRequesting ? (
                                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                        ) : (
                                                            <PenTool className="mr-2 h-4 w-4" />
                                                        )}
                                                        {regenerate ? "Regenerate" : "Generate"}
                                                    </button>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
