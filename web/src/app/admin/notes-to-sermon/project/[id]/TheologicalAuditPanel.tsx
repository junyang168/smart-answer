"use client";

import React, { useState } from 'react';

interface TheologicalAuditPanelProps {
    projectId: string;
    onAuditComplete?: () => void;
    onHighlightText?: (text: string) => void;
}

export default function TheologicalAuditPanel({ projectId, onAuditComplete, onHighlightText }: TheologicalAuditPanelProps) {
    const [isAuditing, setIsAuditing] = useState(false);
    const [auditResult, setAuditResult] = useState<any | null>(null);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    // Fetch existing audit result on mount if available
    React.useEffect(() => {
        const fetchAudit = async () => {
            try {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/theological-audit-result`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.audit_result) {
                        if (data.audit_result.error) {
                            setErrorMsg(data.audit_result.error);
                        } else {
                            setAuditResult(data.audit_result);
                        }
                    }
                }
            } catch (e) {
                console.error("Failed to load existing theological audit result:", e);
            }
        };
        fetchAudit();
    }, [projectId]);

    const handleAuditClick = async () => {
        setIsAuditing(true);
        setAuditResult(null);
        setErrorMsg(null);

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);

        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/theological-audit`, {
                method: "POST",
                signal: controller.signal
            });
            clearTimeout(timeoutId);

            let data;
            try {
                data = await res.json();
            } catch (err) {
                setErrorMsg(`Audit failed: Invalid JSON response (Status: ${res.status})`);
                setIsAuditing(false);
                return;
            }

            if (res.ok && data.audit_result) {
                if (data.audit_result.error) {
                    setErrorMsg(data.audit_result.error);
                } else {
                    setAuditResult(data.audit_result);
                    if (onAuditComplete) onAuditComplete();
                }
            } else {
                setErrorMsg("Audit failed: " + (data.detail || "Unknown error"));
            }
        } catch (e: any) {
            clearTimeout(timeoutId);
            if (e.name === 'AbortError') {
                setErrorMsg("Request timed out after 120 seconds. Please try again.");
            } else {
                setErrorMsg("An error occurred during the audit process: " + e.message);
            }
        }
        setIsAuditing(false);
    };

    const getTypeLabel = (type: string) => {
        switch (type) {
            case "exegesis_error": return "明顯釋經錯誤 (Exegesis Error)";
            case "factual_error": return "客觀事實錯誤 (Factual Error)";
            case "overstatement": return "明顯過度推論 (Overstatement)";
            case "structural_issue": return "重大結構錯位 (Structural Issue)";
            default: return type;
        }
    };

    return (
        <div className="bg-white border-l h-full flex flex-col w-[350px] md:w-[450px] shadow-xl z-20 overflow-hidden">
            <div className="p-4 bg-purple-50 border-b flex justify-between items-center">
                <h3 className="font-bold text-purple-900 flex items-center gap-2">
                    🏛️ 神學邊界審閱 (Theological Review)
                </h3>
            </div>

            <div className="flex-1 overflow-auto p-4 space-y-4">
                <div className="text-sm text-gray-600 mb-2">
                    <p>點擊下方按鈕，AI 將進行重大邊界神學與結構審閱，檢查草稿是否包含明顯的釋經錯誤、事實錯誤、過度推論或重大結構錯位。</p>
                </div>

                {errorMsg && (
                    <div className="bg-red-50 text-red-700 border border-red-200 rounded p-3 text-sm">
                        {errorMsg}
                    </div>
                )}

                {auditResult && !errorMsg && (
                    <div className="space-y-6 text-sm pb-10">
                        {/* Summary Block */}
                        <div className="bg-gray-50 border rounded p-4">
                            <h4 className="font-bold flex items-center gap-2 border-b pb-2 mb-2">
                                📊 審閱總結
                            </h4>
                            <p>{auditResult.summary}</p>
                        </div>

                        {/* Issues List */}
                        {auditResult.issues && auditResult.issues.length > 0 ? (
                            <div className="bg-red-50 border border-red-200 rounded p-4">
                                <h4 className="font-bold text-red-800 border-b border-red-200 pb-2 mb-3">🔥 發現問題 ({auditResult.issues.length})</h4>
                                <ul className="space-y-4">
                                    {auditResult.issues.map((issue: any, i: number) => (
                                        <li key={i} className="bg-white border p-3 rounded shadow-sm">
                                            <p className="font-bold text-red-800 mb-1 flex items-center gap-1">
                                                <span>⚠️</span> {getTypeLabel(issue.type)}
                                            </p>
                                            <p className="mb-2 text-gray-700"><strong>說明:</strong> {issue.reason}</p>
                                            {issue.location && <p className="mb-2 text-gray-600 border-l-2 border-gray-300 pl-2"><strong>位置:</strong> {issue.location}</p>}
                                            {issue.excerpt && onHighlightText ? (
                                                <button
                                                    onClick={() => onHighlightText(issue.excerpt)}
                                                    className="text-left w-full text-purple-800 bg-purple-50 hover:bg-purple-100 p-2 rounded border border-purple-200 transition-colors shadow-sm"
                                                    title="在編輯器中定位"
                                                >
                                                    <span className="font-semibold block mb-1">原文片段 (點擊定位):</span>
                                                    {issue.excerpt}
                                                </button>
                                            ) : (
                                                issue.excerpt && (
                                                    <div className="bg-purple-50 p-2 rounded border border-purple-200 text-purple-800">
                                                        <span className="font-semibold block mb-1">原文片段:</span>
                                                        {issue.excerpt}
                                                    </div>
                                                )
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : (
                            <div className="bg-green-50 border border-green-200 rounded p-4">
                                <h4 className="font-bold text-green-800 flex items-center gap-2">
                                    🎉 未發現重大邊界問題
                                </h4>
                            </div>
                        )}
                    </div>
                )}
            </div>

            <div className="p-4 bg-gray-50 border-t flex-shrink-0">
                <button
                    onClick={handleAuditClick}
                    disabled={isAuditing}
                    className={`w-full py-3 rounded font-bold text-white shadow transition-colors flex justify-center items-center gap-2 
                        ${isAuditing ? 'bg-purple-400 cursor-not-allowed' : 'bg-purple-600 hover:bg-purple-700'}`}
                >
                    {isAuditing ? (
                        <>
                            <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            神學審閱中 (約 30 秒)...
                        </>
                    ) : (
                        "🚀 執行神學邊界審閱"
                    )}
                </button>
            </div>
        </div>
    );
}
