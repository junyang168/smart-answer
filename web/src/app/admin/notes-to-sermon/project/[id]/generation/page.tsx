"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, CheckCircle, Circle, Brain, BookOpen, PenTool, MessageSquare, Feather, Layout, RefreshCcw } from "lucide-react";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

interface AgentLog {
    timestamp: string;
    role: string;
    message: string;
}

interface ProjectStatus {
    is_processing: boolean;
    processing_status?: string;
    processing_progress?: number;
    processing_error?: string;
}

const AGENTS = [
    { role: "exegete", label: "Exegetical Scholar", icon: BookOpen, color: "bg-purple-100 text-purple-700" },
    { role: "theologian", label: "Theologian", icon: Brain, color: "bg-yellow-100 text-yellow-700" },
    { role: "illustrator", label: "Illustrator", icon: Feather, color: "bg-pink-100 text-pink-700" },
    { role: "structuring_specialist", label: "Architect", icon: Layout, color: "bg-gray-100 text-gray-700" },
    { role: "drafter", label: "Homiletician", icon: PenTool, color: "bg-blue-100 text-blue-700" },
    { role: "critic", label: "Critic", icon: MessageSquare, color: "bg-red-100 text-red-700" },
];

export default function GenerationPage({ params }: { params: { id: string } }) {
    const router = useRouter();
    const [logs, setLogs] = useState<AgentLog[]>([]);
    const [status, setStatus] = useState<ProjectStatus | null>(null);
    const logContainerRef = useRef<HTMLDivElement>(null);
    const [mounted, setMounted] = useState(false);

    const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
    const [agentState, setAgentState] = useState<any>(null);

    useEffect(() => {
        setMounted(true);
        const interval = setInterval(fetchData, 2000);
        fetchData();
        return () => clearInterval(interval);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (logContainerRef.current) {
            logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
        }
    }, [logs]);

    const fetchData = async () => {
        try {
            const [logsRes, projectRes, stateRes] = await Promise.all([
                fetch(`/api/admin/notes-to-sermon/sermon-project/${params.id}/agent-logs`),
                fetch(`/api/admin/notes-to-sermon/sermon-project/${params.id}`),
                fetch(`/api/admin/notes-to-sermon/sermon-project/${params.id}/agent-state`)
            ]);

            if (logsRes.ok) {
                const logsData = await logsRes.json();
                setLogs(logsData);
            }
            if (projectRes.ok) {
                const projectData = await projectRes.json();
                setStatus(projectData);
            }
            if (stateRes.ok) {
                const stateData = await stateRes.json();
                setAgentState(stateData);
            } else if (stateRes.status === 404) {
                setAgentState(null); // Clear state if file deleted
            }
        } catch (e) {
            console.error(e);
        }
    };

    if (!mounted) return null;

    // Determine current active agent based on last log?
    const lastLog = logs.length > 0 ? logs[logs.length - 1] : null;
    const activeRole = lastLog ? lastLog.role : null;

    const getAgentContent = (role: string) => {
        if (!agentState) return "No data available yet.";
        switch (role) {
            case "exegete": return agentState.exegetical_notes || "Waiting for output...";
            case "theologian": return agentState.theological_analysis || "Waiting for output...";
            case "illustrator": return agentState.illustration_ideas || "Waiting for output...";
            case "structuring_specialist":
                const beats = agentState.beats;
                if (Array.isArray(beats)) {
                    return beats.map((b: string, i: number) =>
                        `> [!NOTE]\n> **Macro-Beat ${i + 1}**\n>\n> ${b.replace(/\n/g, "\n> ")}`
                    ).join("\n\n");
                }
                return "Waiting for structure...";
            case "drafter":
                if (agentState.draft_chunks?.length > 0) return agentState.draft_chunks.join("\n\n");
                return agentState.full_manuscript || "Drafting in progress...";
            case "critic":
                // Return passed beats logs?
                return logs.filter(l => l.role === "critic").map(l => l.message).join("\n");
            default: return "No specific output for this agent.";
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col items-center py-10">
            <div className="w-full max-w-4xl px-4 mb-4 flex justify-between items-center">
                <button onClick={() => router.push(`/admin/notes-to-sermon/project/${params.id}`)} className="flex items-center text-gray-600 hover:text-gray-900">
                    <ArrowLeft className="w-4 h-4 mr-2" />
                    Back to Project
                </button>
                <h1 className="text-xl font-bold">Multi-Agent Generation</h1>
            </div>

            {/* Workflow Viz */}
            <div className="w-full max-w-4xl bg-white rounded-xl shadow-sm border p-6 mb-6">
                <div className="flex justify-between items-center relative">
                    {/* Connecting Line */}
                    <div className="absolute top-1/2 left-0 w-full h-1 bg-gray-100 -z-0" />

                    {AGENTS.map((agent) => {
                        const Icon = agent.icon;
                        const isActive = activeRole === agent.role || (status?.processing_status?.toLowerCase().includes(agent.role));
                        const hasData = (
                            (agent.role === "critic" && logs.some(l => l.role.toLowerCase() === "critic")) ||
                            (agentState && (
                                (agent.role === "exegete" && agentState.exegetical_notes) ||
                                (agent.role === "theologian" && agentState.theological_analysis) ||
                                (agent.role === "illustrator" && agentState.illustration_ideas) ||
                                (agent.role === "structuring_specialist" && agentState.beats) ||
                                (agent.role === "drafter" && agentState.draft_chunks?.length > 0)
                            ))
                        );

                        return (
                            <div key={agent.role} className="z-10 flex flex-col items-center cursor-pointer group" onClick={() => setSelectedAgent(agent.role)}>
                                <div className={`w-12 h-12 rounded-full flex items-center justify-center border-2 transition-all ${isActive ? agent.color + " border-current scale-110 shadow-lg" : hasData ? "bg-white border-green-500 text-green-600 shadow-md" : "bg-white border-gray-200 text-gray-300"}`}>
                                    <Icon className="w-6 h-6" />
                                </div>
                                <span className={`text-xs mt-2 font-medium ${isActive ? "text-gray-900" : hasData ? "text-green-700" : "text-gray-400"} group-hover:text-blue-600`}>
                                    {agent.label}
                                </span>
                                {hasData && <span className="text-[10px] text-green-600 mt-0.5">(View)</span>}
                            </div>
                        );
                    })}
                </div>

                {status?.processing_error && (
                    <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 flex items-start">
                        <div className="font-semibold mr-2">Error:</div>
                        <div>{status.processing_error}</div>
                    </div>
                )}

                {/* Progress Bar */}
                <div className="mt-8">
                    <div className="flex justify-between text-sm mb-1">
                        <span className="font-semibold text-gray-700">{status?.processing_status || "Initializing..."}</span>
                        <span className="text-gray-500">{status?.processing_progress || 0}%</span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                        <div
                            className="bg-blue-600 h-full transition-all duration-500"
                            style={{ width: `${status?.processing_progress || 0}%` }}
                        />
                    </div>
                </div>
            </div>

            {/* Live Logs */}
            <div className="w-full max-w-4xl bg-white rounded-xl shadow-sm border flex-grow flex flex-col h-[500px]">
                <div className="p-4 border-b bg-gray-50 flex justify-between items-center rounded-t-xl">
                    <h2 className="font-semibold flex items-center text-gray-700">
                        <MessageSquare className="w-4 h-4 mr-2" />
                        Live Agent Thoughts
                    </h2>
                    <div className="flex items-center gap-2">
                        {!status?.is_processing && logs.length > 0 && (
                            <button
                                onClick={async () => {
                                    if (!confirm("Are you sure you want to RESTART? This will wipe all current progress.")) return;
                                    try {
                                        await fetch(`/api/admin/notes-to-sermon/sermon-project/${params.id}/generate-draft`, {
                                            method: "POST",
                                            headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ use_mas: true, restart: true })
                                        });
                                        setLogs([]);
                                        setAgentState(null); // Fix: Clear local state immediately to reset icons
                                        setStatus({ is_processing: true, processing_status: "Restarting..." });
                                    } catch (e) {
                                        alert("Failed to restart: " + e);
                                    }
                                }}
                                className="text-gray-500 hover:text-red-600 px-3 py-1.5 rounded-lg text-sm transition-colors flex items-center mr-2"
                                title="Restart Generation"
                            >
                                <RefreshCcw className="w-4 h-4 mr-1" />
                                Restart
                            </button>
                        )}
                        {status?.processing_progress === 100 && (
                            <button
                                onClick={() => router.push(`/admin/notes-to-sermon/project/${params.id}`)}
                                className="bg-green-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-green-700 transition-colors flex items-center"
                            >
                                <CheckCircle className="w-4 h-4 mr-2" />
                                View Draft
                            </button>
                        )}
                    </div>
                </div>

                <div ref={logContainerRef} className="flex-1 overflow-y-auto p-4 space-y-4 font-mono text-sm">
                    {logs.length === 0 ? (
                        <div className="text-gray-400 text-center mt-20 italic">
                            Waiting for agents to start...
                        </div>
                    ) : (
                        logs.map((log, i) => {
                            const agent = AGENTS.find(a => a.role === log.role) || { label: log.role, color: "bg-gray-100" };
                            return (
                                <div key={i} className="flex gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300">
                                    <div className="flex-shrink-0 w-24 text-right pt-1">
                                        <span className={`inline-block px-2 py-0.5 rounded text-xs font-bold ${agent.color}`}>
                                            {agent.label}
                                        </span>
                                    </div>
                                    <div className="flex-1 bg-white border-l-2 pl-3 border-gray-200 py-1 text-gray-800">
                                        {log.message}
                                    </div>
                                    <div className="text-xs text-gray-400 pt-1 w-16">
                                        {new Date(log.timestamp).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                                    </div>
                                </div>
                            )
                        })
                    )}

                    {status?.is_processing && logs.length > 0 && (
                        <div className="flex justify-center py-4">
                            <span className="animate-pulse text-gray-400">...</span>
                        </div>
                    )}
                </div>
            </div>

            {/* Agent Output Modal */}
            {selectedAgent && (
                <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm" onClick={() => setSelectedAgent(null)}>
                    <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col" onClick={e => e.stopPropagation()}>
                        <div className="p-4 border-b flex justify-between items-center bg-gray-50 rounded-t-xl">
                            <h3 className="text-lg font-bold flex items-center">
                                {(() => {
                                    const a = AGENTS.find(x => x.role === selectedAgent);
                                    const Icon = a?.icon;
                                    return (
                                        <>
                                            {Icon && <Icon className="w-5 h-5 mr-2" />}
                                            {a?.label} - Artifacts
                                        </>
                                    )
                                })()}
                            </h3>
                            <button onClick={() => setSelectedAgent(null)} className="text-gray-500 hover:text-gray-800">
                                Close
                            </button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-6 bg-white">
                            <div className="prose max-w-none text-gray-800">
                                <ScriptureMarkdown markdown={getAgentContent(selectedAgent)} />
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
