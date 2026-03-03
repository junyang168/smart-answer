"use client";

import React, { useState } from 'react';

interface AiCommandPanelProps {
    projectId: string;
    onAuditComplete?: () => void;
    onHighlightText?: (text: string) => void;
}

export default function AiCommandPanel({ projectId, onAuditComplete, onHighlightText }: AiCommandPanelProps) {
    const [isAuditing, setIsAuditing] = useState(false);
    const [auditResult, setAuditResult] = useState<any | null>(null);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    // Fetch existing audit result on mount if available
    React.useEffect(() => {
        const fetchAudit = async () => {
            try {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/audit-result`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.audit_result) {
                        if (typeof data.audit_result === 'string') {
                            setErrorMsg("Old format markdown detected. Please re-run Audit.");
                        } else if (data.audit_result.error) {
                            setErrorMsg(data.audit_result.error);
                        } else {
                            setAuditResult(data.audit_result);
                        }
                    }
                }
            } catch (e) {
                console.error("Failed to load existing audit result:", e);
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
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/audit-draft`, {
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

    return (
        <div className="bg-white border-l h-full flex flex-col w-[350px] md:w-[450px] shadow-xl z-20 overflow-hidden">
            <div className="p-4 bg-indigo-50 border-b flex justify-between items-center">
                <h3 className="font-bold text-indigo-900 flex items-center gap-2">
                    🛡️ AI 審核 (Audit)
                </h3>
            </div>

            <div className="flex-1 overflow-auto p-4 space-y-4">
                <div className="text-sm text-gray-600 mb-2">
                    <p>點擊下方按鈕，AI 將對照「原始筆記」與「生成的講章草稿」，檢查草稿是否遺漏核心要點，或是否有過度延伸的內容。</p>
                </div>

                {errorMsg && (
                    <div className="bg-red-50 text-red-700 border border-red-200 rounded p-3 text-sm">
                        {errorMsg}
                    </div>
                )}

                {auditResult && !errorMsg && (
                    <div className="space-y-6 text-sm pb-10">
                        {/* Status block */}
                        <div className="bg-gray-50 border rounded p-4">
                            <h4 className="font-bold flex items-center gap-2 border-b pb-2 mb-2">
                                🏆 總體評分 & 狀態
                            </h4>
                            <p className="mb-1"><strong>審核結果:</strong> {auditResult.pass ? '✅ 通過 (Pass)' : '❌ 未通過 (Fail)'}</p>
                            <p className="mb-1"><strong>忠實度:</strong> {auditResult.scores?.faithfulness} / 100</p>
                        </div>

                        {/* Must Fix block */}
                        {auditResult.must_fix && auditResult.must_fix.length > 0 && (
                            <div className="bg-red-50 border border-red-200 rounded p-4">
                                <h4 className="font-bold text-red-800 border-b border-red-200 pb-2 mb-3">🔥 必須修正</h4>
                                <ul className="list-disc pl-5 space-y-2 text-red-900">
                                    {auditResult.must_fix.map((fix: string, i: number) => (
                                        <li key={i}>{fix}</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {/* Diffs block */}
                        {auditResult.diffs && auditResult.diffs.length > 0 && (
                            <div className="bg-gray-50 border rounded p-4">
                                <h4 className="font-bold border-b pb-2 mb-3">⚠️ 差異清單</h4>
                                <ul className="space-y-4">
                                    {auditResult.diffs.map((d: any, i: number) => (
                                        <li key={i} className="bg-white border p-3 rounded shadow-sm">
                                            <p className="font-bold text-red-800 mb-1">風險: {d.risk}</p>
                                            <p className="mb-2 text-gray-700"><strong>理由:</strong> {d.reason}</p>
                                            {d.note_evidence && <p className="mb-2 text-gray-600 border-l-2 border-gray-300 pl-2"><strong>筆記證據:</strong> {d.note_evidence}</p>}
                                            {d.transcript_evidence && onHighlightText ? (
                                                <button
                                                    onClick={() => onHighlightText(d.transcript_evidence)}
                                                    className="text-left w-full text-yellow-800 bg-yellow-50 hover:bg-yellow-100 p-2 rounded border border-yellow-200 transition-colors shadow-sm"
                                                    title="在編輯器中定位"
                                                >
                                                    <span className="font-semibold block mb-1">逐字稿證據 (點擊定位):</span>
                                                    {d.transcript_evidence}
                                                </button>
                                            ) : (
                                                d.transcript_evidence && (
                                                    <div className="bg-yellow-50 p-2 rounded border border-yellow-200 text-yellow-800">
                                                        <span className="font-semibold block mb-1">逐字稿證據:</span>
                                                        {d.transcript_evidence}
                                                    </div>
                                                )
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {/* Coverage block */}
                        {auditResult.coverage && auditResult.coverage.length > 0 && (
                            <div className="bg-gray-50 border rounded p-4">
                                <h4 className="font-bold border-b pb-2 mb-3">🔍 覆蓋率檢查</h4>
                                <ul className="space-y-4">
                                    {auditResult.coverage.map((c: any, i: number) => (
                                        <li key={i} className={`bg-white border p-3 rounded shadow-sm ${!c.matched ? 'border-red-200' : ''}`}>
                                            <p className="font-bold mb-1">筆記 [{c.note_id}]</p>
                                            <p className="text-gray-700 italic mb-2 border-l-2 pl-2">&quot;{c.note_excerpt}&quot;</p>
                                            <p className="text-gray-600 mb-2"><strong>狀態:</strong> {c.matched ? '✅ 已覆蓋' : '❌ 未覆蓋'}</p>

                                            {c.transcript_evidence && onHighlightText ? (
                                                <button
                                                    onClick={() => onHighlightText(c.transcript_evidence)}
                                                    className="text-left w-full text-indigo-700 bg-indigo-50 hover:bg-indigo-100 p-2 rounded border border-indigo-200 transition-colors shadow-sm"
                                                    title="在編輯器中定位"
                                                >
                                                    <span className="font-semibold block mb-1">逐字稿證據 (點擊定位):</span>
                                                    {c.transcript_evidence}
                                                </button>
                                            ) : (
                                                c.transcript_evidence && (
                                                    <div className="bg-indigo-50 p-2 rounded border border-indigo-200 text-indigo-700">
                                                        <span className="font-semibold block mb-1">逐字稿證據:</span>
                                                        {c.transcript_evidence}
                                                    </div>
                                                )
                                            )}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                    </div>
                )}

                {!auditResult && !isAuditing && !errorMsg && (
                    <div className="flex flex-col items-center justify-center text-gray-400 text-sm h-32">
                        <p>尚無審核結果</p>
                    </div>
                )}
            </div>

            <div className="p-4 border-t bg-gray-50 shrink-0">
                <button
                    onClick={handleAuditClick}
                    disabled={isAuditing}
                    className={`w-full py-2 px-4 rounded font-bold text-white transition-all
                        ${isAuditing
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-indigo-600 hover:bg-indigo-700 shadow-lg'}`}
                >
                    {isAuditing ? (
                        <span className="flex items-center justify-center gap-2">
                            <span className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></span>
                            審核中...
                        </span>
                    ) : (
                        "審核 (Audit Draft)"
                    )}
                </button>
            </div>
        </div>
    );
}
