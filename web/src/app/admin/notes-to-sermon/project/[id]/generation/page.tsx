"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
    AlertCircle,
    ArrowLeft,
    CheckCircle2,
    FileText,
    Loader2,
    PenTool,
    Play,
    RefreshCcw,
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
        draft_ready: boolean;
        current_unit_id?: string | null;
    };
    units: Stage1Unit[];
    logs: Stage1Log[];
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
        case "generate_all":
            return "全部單元生成";
        case "generate_unit":
            return unitId ? `單元生成 ${unitId}` : "單元生成";
        default:
            return "Stage 1";
    }
}

export default function GenerationPage({ params }: { params: { id: string } }) {
    const router = useRouter();
    const [state, setState] = useState<Stage1Status>(DEFAULT_STATUS);
    const [loading, setLoading] = useState(true);
    const [requesting, setRequesting] = useState<string | null>(null);

    const fetchStatus = async () => {
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${params.id}/stage1/status`, {
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
    }, [params.id]);

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
            return state.summary.split_completed ? 15 : 0;
        }
        const completed = state.summary.completed_units + state.summary.failed_units;
        return Math.max(15, Math.min(100, Math.round((completed / state.summary.total_units) * 100)));
    }, [state.project.processing_progress, state.summary]);

    const running = state.job.running;
    const splitReady = state.summary.split_completed;
    const failedUnits = state.units.filter((unit) => unit.status === "failed");

    return (
        <div className="min-h-screen bg-gray-50 py-10">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                    <div className="space-y-2">
                        <button
                            onClick={() => router.push(`/admin/notes-to-sermon/project/${params.id}`)}
                            className="flex items-center text-sm text-gray-500 hover:text-gray-800"
                        >
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            Back to Project
                        </button>
                        <div>
                            <h1 className="text-2xl font-bold text-gray-900">Stage 1 Pipeline</h1>
                            <p className="text-sm text-gray-500">
                                {state.project.title || params.id}
                            </p>
                        </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-3">
                        <button
                            onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${params.id}/stage1/split`, {
                                force: splitReady,
                                confirmMessage: splitReady ? "Rerun unit splitting and refresh the Stage 1 split result?" : undefined,
                            })}
                            disabled={running || requesting !== null}
                            className="inline-flex items-center rounded-lg bg-purple-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                        >
                            {requesting?.endsWith("/stage1/split") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Scissors className="mr-2 h-4 w-4" />}
                            {splitReady ? "Rerun Split" : "Run Unit Split"}
                        </button>
                        <button
                            onClick={() => launchJob(`/api/admin/notes-to-sermon/sermon-project/${params.id}/stage1/generate-all`, {
                                force: splitReady,
                                confirmMessage: splitReady ? "Generate manuscripts for all units now?" : "Run the full Stage 1 pipeline now?",
                            })}
                            disabled={running || requesting !== null}
                            className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                        >
                            {requesting?.endsWith("/stage1/generate-all") ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                            {splitReady ? "Generate All Units" : "Run Full Pipeline"}
                        </button>
                        <button
                            onClick={fetchStatus}
                            disabled={loading}
                            className="inline-flex items-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50"
                        >
                            <RefreshCcw className="mr-2 h-4 w-4" />
                            Refresh
                        </button>
                        <button
                            onClick={() => router.push(`/admin/notes-to-sermon/project/${params.id}`)}
                            disabled={!state.summary.draft_ready}
                            className="inline-flex items-center rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-sm font-medium text-green-700 transition hover:bg-green-100 disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-gray-100 disabled:text-gray-400"
                        >
                            <FileText className="mr-2 h-4 w-4" />
                            Open Draft
                        </button>
                    </div>
                </div>

                <div className="grid gap-6 xl:grid-cols-[2fr,1fr]">
                    <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                                <h2 className="text-lg font-semibold text-gray-900">Pipeline Status</h2>
                                <p className="text-sm text-gray-500">
                                    {running
                                        ? `${modeLabel(state.job.mode, state.job.unit_id)} 正在執行`
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
                            {[
                                ["Total Units", state.summary.total_units],
                                ["Completed", state.summary.completed_units],
                                ["Running", state.summary.running_units],
                                ["Pending", state.summary.pending_units],
                                ["Failed", state.summary.failed_units],
                            ].map(([label, value]) => (
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
                            <h2 className="text-lg font-semibold text-gray-900">Teaching Units</h2>
                            <p className="text-sm text-gray-500">
                                Split first, inspect the boundaries, then generate manuscripts per unit or all at once.
                            </p>
                        </div>
                        {state.summary.current_unit_id && (
                            <div className="inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                Current: {state.summary.current_unit_id}
                            </div>
                        )}
                    </div>

                    {!splitReady ? (
                        <div className="p-8 text-center text-sm text-gray-500">
                            No split result yet. Run unit splitting to inspect the Stage 1 boundaries.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-100 text-sm">
                                <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                                    <tr>
                                        <th className="px-6 py-3">Unit</th>
                                        <th className="px-6 py-3">Scripture</th>
                                        <th className="px-6 py-3">Source Lines</th>
                                        <th className="px-6 py-3">Split Reason</th>
                                        <th className="px-6 py-3">Status</th>
                                        <th className="px-6 py-3 text-right">Actions</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                    {state.units.map((unit) => {
                                        const unitActionPath = `/api/admin/notes-to-sermon/sermon-project/${params.id}/stage1/unit/${unit.unit_id}/generate`;
                                        const isUnitRequesting = requesting === unitActionPath;
                                        const regenerate = unit.has_generated;
                                        return (
                                            <tr key={unit.unit_id} className="align-top">
                                                <td className="px-6 py-4">
                                                    <div className="font-semibold text-gray-900">
                                                        {unit.display_index}. {unit.unit_title}
                                                    </div>
                                                    <div className="mt-1 text-xs text-gray-500">
                                                        {unit.chapter_title || "未標明章標題"}
                                                        {unit.section_title ? ` / ${unit.section_title}` : ""}
                                                    </div>
                                                </td>
                                                <td className="px-6 py-4 text-gray-700">{unit.scripture_range || "未標明"}</td>
                                                <td className="px-6 py-4 text-gray-700">
                                                    lines {unit.start_line}-{unit.end_line}
                                                </td>
                                                <td className="max-w-md px-6 py-4 text-gray-600">{unit.split_reason}</td>
                                                <td className="px-6 py-4">
                                                    <div className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${statusBadge(unit.status)}`}>
                                                        {unit.status === "completed" && <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status === "running" && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                                                        {unit.status === "pending" && <PenTool className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status === "failed" && <AlertCircle className="mr-1.5 h-3.5 w-3.5" />}
                                                        {unit.status}
                                                    </div>
                                                    <div className="mt-2 text-xs text-gray-400">
                                                        {unit.has_points ? "points ready" : "no points"} · {unit.has_generated ? "draft ready" : "no draft"}
                                                    </div>
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
