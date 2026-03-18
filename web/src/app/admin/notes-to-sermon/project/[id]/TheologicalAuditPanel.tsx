"use client";

import React, { useState } from 'react';
import AuditIssueList from './AuditIssueList';

interface TheologicalAuditPanelProps {
    projectId: string;
    selectedChunkId?: string | null;
    selectedChunkText?: string;
    onAuditComplete?: () => void;
    onHighlightText?: (text: string) => void;
    onForcePassSuccess?: () => void;
    mode?: 'theological' | 'fidelity' | 'both';
}

export default function TheologicalAuditPanel({ projectId, selectedChunkId, selectedChunkText, onAuditComplete, onHighlightText, onForcePassSuccess, mode = 'both' }: TheologicalAuditPanelProps) {
    const [activeTab, setActiveTab] = useState<'theological' | 'fidelity'>(mode === 'both' ? 'theological' : mode);

    // Theological Audit State
    const [isTheoAuditing, setIsTheoAuditing] = useState(false);
    const [theoResult, setTheoResult] = useState<any | null>(null);
    const [theoError, setTheoError] = useState<string | null>(null);
    const [expandedTheoIssue, setExpandedTheoIssue] = useState<number | null>(null);

    React.useEffect(() => {
        if (mode !== 'both') {
            setActiveTab(mode);
        }
    }, [mode]);

    // Fidelity Audit State
    const [isFidelityAuditing, setIsFidelityAuditing] = useState(false);
    const [isForcingPass, setIsForcingPass] = useState(false);
    const [fidelityResult, setFidelityResult] = useState<any | null>(null);
    const [fidelityError, setFidelityError] = useState<string | null>(null);
    const [expandedFidelityIssue, setExpandedFidelityIssue] = useState<number | null>(null);

    // Fetch existing theological audit result on mount if available
    React.useEffect(() => {
        if (!selectedChunkId) {
            setTheoResult(null);
            setTheoError(null);
            setFidelityResult(null);
            setFidelityError(null);
            return;
        }

        // Fetch Theological Audit
        const fetchTheo = async () => {
            try {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/theological-audit-result?chunk_id=${selectedChunkId}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.audit_result) {
                        if (data.audit_result.error) {
                            setTheoError(data.audit_result.error);
                            setTheoResult(null);
                        } else {
                            setTheoError(null);
                            setTheoResult(data.audit_result);
                        }
                    } else {
                        setTheoResult(null);
                        setTheoError(null);
                    }
                }
            } catch (e) {
                console.error("Failed to load existing theological audit result:", e);
            }
        };

        // Fetch Fidelity Audit
        const fetchFidelity = async () => {
            try {
                const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/audit-result?chunk_id=${selectedChunkId}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.audit_result) {
                        if (data.audit_result.error) {
                            setFidelityError(data.audit_result.error);
                            setFidelityResult(null);
                        } else {
                            setFidelityError(null);
                            setFidelityResult(data.audit_result);
                        }
                    } else {
                        setFidelityResult(null);
                        setFidelityError(null);
                    }
                }
            } catch (e) {
                console.error("Failed to load existing fidelity audit result:", e);
            }
        };

        fetchTheo();
        fetchFidelity();
    }, [projectId, selectedChunkId]);

    const handleAuditClick = async (auditType: 'theological' | 'fidelity') => {
        if (!selectedChunkId) return;

        const isTheo = auditType === 'theological';
        const setAuditing = isTheo ? setIsTheoAuditing : setIsFidelityAuditing;
        const setResult = isTheo ? setTheoResult : setFidelityResult;
        const setError = isTheo ? setTheoError : setFidelityError;
        const endpoint = isTheo ? 'theological-audit' : 'audit-draft';

        setAuditing(true);
        setResult(null);
        setError(null);

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);

        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/${endpoint}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ chunk_id: selectedChunkId }),
                signal: controller.signal
            });
            clearTimeout(timeoutId);

            let data;
            try {
                data = await res.json();
            } catch (err) {
                setError(`Audit failed: Invalid JSON response (Status: ${res.status})`);
                setAuditing(false);
                return;
            }

            if (res.ok && data.audit_result) {
                if (data.audit_result.error) {
                    setError(data.audit_result.error);
                } else {
                    setResult(data.audit_result);
                    if (onAuditComplete) onAuditComplete();
                }
            } else {
                setError("Audit failed: " + (data.detail || "Unknown error"));
            }
        } catch (e: any) {
            clearTimeout(timeoutId);
            if (e.name === 'AbortError') {
                setError("Request timed out after 120 seconds. Please try again.");
            } else {
                setError("An error occurred during the audit process: " + e.message);
            }
        }
        setAuditing(false);
    };

    const handleForcePass = async () => {
        if (!selectedChunkId) return;
        if (!confirm("Are you sure you want to force pass this audit?")) return;
        setIsForcingPass(true);
        try {
            const res = await fetch(`/api/admin/notes-to-sermon/sermon-project/${projectId}/force-audit-pass`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ chunk_id: selectedChunkId })
            });
            if (res.ok) {
                // Refresh local state
                setFidelityResult({
                    ...fidelityResult,
                    pass: true,
                    must_fix: fidelityResult?.must_fix ? fidelityResult.must_fix.map((x: string) => "(User Overridden) " + x) : []
                });
                if (onForcePassSuccess) {
                    onForcePassSuccess();
                }

                // Also trigger re-fetch of chunks if needed by calling onAuditComplete
                if (onAuditComplete) {
                    onAuditComplete();
                }
            } else {
                const data = await res.json();
                setFidelityError("Force pass failed: " + (data.detail || "Unknown error"));
            }
        } catch (e: any) {
            setFidelityError("An error occurred during force pass: " + e.message);
        }
        setIsForcingPass(false);
    };

    const getTypeLabel = (type: string) => {
        switch (type) {
            case "exegesis_error": return "明顯釋經錯誤 (Exegesis Error)";
            case "factual_error": return "客觀事實錯誤 (Factual Error)";
            case "overstatement": return "明顯過度推論 (Overstatement)";
            case "structural_issue": return "重大結構錯位 (Structural Issue)";
            case "addition": return "新增神學命题 (Addition)";
            case "deletion": return "刪除實質要點 (Deletion)";
            case "stance_upgrade": return "強化推測語氣為斷言 (Stance Upgrade)";
            default: return type;
        }
    };

    return (
        <div className="bg-white border-l h-full flex flex-col w-[350px] md:w-[450px] shadow-xl z-20 overflow-hidden">
            {/* Header / Tabs */}
            {mode === 'both' ? (
                <div className="bg-gray-100 border-b flex">
                    <button
                        onClick={() => setActiveTab('theological')}
                        className={`flex-1 py-3 text-sm font-bold border-b-2 transition-colors flex items-center justify-center gap-2
                            ${activeTab === 'theological' ? 'border-purple-600 text-purple-900 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
                    >
                        🏛️ 神學邊界審閱
                    </button>
                    <button
                        onClick={() => setActiveTab('fidelity')}
                        className={`flex-1 py-3 text-sm font-bold border-b-2 transition-colors flex items-center justify-center gap-2
                            ${activeTab === 'fidelity' ? 'border-indigo-600 text-indigo-900 bg-white' : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}
                    >
                        🛡️ 忠實度審核
                    </button>
                </div>
            ) : (
                <div className="bg-gray-100 border-b flex px-4">
                    <div className="flex-1 py-3 text-lg font-bold flex items-center justify-center gap-2 text-gray-800">
                        {mode === 'theological' ? '🏛️ 神學邊界審閱' : '🛡️ 忠實度審核'}
                    </div>
                </div>
            )}

            <div className="flex-1 overflow-auto p-4 space-y-4">
                {activeTab === 'theological' && (
                    <>
                        <div className="text-sm text-gray-600 mb-2">
                            <p>點擊下方按鈕，AI 將進行重大邊界神學與結構審閱，檢查此 Chunk 是否包含明顯的釋經錯誤、事實錯誤、過度推論或重大結構錯位。</p>
                        </div>

                        {theoError && (
                            <div className="bg-red-50 text-red-700 border border-red-200 rounded p-3 text-sm">
                                {theoError}
                            </div>
                        )}

                        {theoResult && !theoError && (
                            <div className="space-y-6 text-sm pb-10">
                                {/* Summary Block */}
                                <div className="bg-gray-50 border rounded p-4">
                                    <h4 className="font-bold flex items-center gap-2 border-b pb-2 mb-2">
                                        📊 審閱總結
                                    </h4>
                                    <p>{theoResult.summary}</p>
                                </div>

                                {/* Issues List */}
                                <AuditIssueList
                                    issues={theoResult.issues}
                                    listTitle="🔥 發現問題"
                                    expandedIssueIndex={expandedTheoIssue}
                                    setExpandedIssueIndex={setExpandedTheoIssue}
                                    onHighlightText={onHighlightText}
                                    getTypeLabel={getTypeLabel}
                                />
                            </div>
                        )}

                        {!theoResult && !isTheoAuditing && !theoError && (
                            <div className="flex flex-col items-center justify-center text-gray-400 text-sm h-32">
                                <p>尚無神學審核結果</p>
                            </div>
                        )}
                    </>
                )}

                {activeTab === 'fidelity' && (
                    <>
                        <div className="text-sm text-gray-600 mb-2">
                            <p>點擊下方按鈕，AI 將對照「原始筆記」與「此 Chunk 的草稿」，檢查是否遺漏核心要點，或是否有過度延伸的內容。</p>
                        </div>

                        {fidelityError && (
                            <div className="bg-red-50 text-red-700 border border-red-200 rounded p-3 text-sm">
                                {fidelityError}
                            </div>
                        )}

                        {fidelityResult && !fidelityError && (
                            <div className="space-y-6 text-sm pb-10">
                                {/* Status block */}
                                <div className="bg-gray-50 border rounded p-4">
                                    <div className="flex justify-between items-start border-b pb-2 mb-2">
                                        <h4 className="font-bold flex items-center gap-2">
                                            🏆 總體評分 & 狀態
                                        </h4>
                                        {!fidelityResult.pass && (
                                            <button
                                                onClick={handleForcePass}
                                                disabled={isForcingPass}
                                                className="text-xs bg-red-100 text-red-700 hover:bg-red-200 px-2 py-1 rounded font-bold border border-red-300"
                                                title="Manually override to Pass"
                                            >
                                                {isForcingPass ? "Forcing..." : "強制通過 (Force Pass)"}
                                            </button>
                                        )}
                                    </div>
                                    <p className="mb-1"><strong>審核結果:</strong> {fidelityResult.pass ? '✅ 通過 (Pass)' : '❌ 未通過 (Fail)'}</p>
                                    <p className="mb-1"><strong>忠實度:</strong> {fidelityResult.scores?.faithfulness} / 100</p>
                                </div>

                                {/* Issues block */}
                                <AuditIssueList
                                    issues={fidelityResult.diffs}
                                    listTitle="⚠️ 差異清單"
                                    expandedIssueIndex={expandedFidelityIssue}
                                    setExpandedIssueIndex={setExpandedFidelityIssue}
                                    onHighlightText={onHighlightText}
                                    getTypeLabel={getTypeLabel}
                                />

                                {/* Coverage block */}
                                {fidelityResult.coverage && fidelityResult.coverage.length > 0 && (
                                    <div className="bg-gray-50 border rounded p-4">
                                        <h4 className="font-bold border-b pb-2 mb-3">🔍 覆蓋率檢查</h4>
                                        <ul className="space-y-4">
                                            {fidelityResult.coverage.map((c: any, i: number) => (
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

                        {!fidelityResult && !isFidelityAuditing && !fidelityError && (
                            <div className="flex flex-col items-center justify-center text-gray-400 text-sm h-32">
                                <p>尚無忠實度審核結果</p>
                            </div>
                        )}
                    </>
                )}
            </div>

            {/* Bottom Button Panel */}
            <div className="p-4 border-t bg-gray-50 flex-shrink-0">
                {activeTab === 'theological' ? (
                    <button
                        onClick={() => handleAuditClick('theological')}
                        disabled={isTheoAuditing || !selectedChunkId || selectedChunkId === 'FULL_DOC'}
                        className={`w-full py-3 rounded font-bold text-white shadow transition-colors flex justify-center items-center gap-2 
                        ${isTheoAuditing || !selectedChunkId || selectedChunkId === 'FULL_DOC' ? 'bg-purple-400 cursor-not-allowed' : 'bg-purple-600 hover:bg-purple-700'}`}
                    >
                        {isTheoAuditing ? (
                            <>
                                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                神學審閱中 (約 30 秒)...
                            </>
                        ) : (
                            selectedChunkId && selectedChunkId !== 'FULL_DOC' ? "🚀 執行神學邊界審閱" : "請先選擇 Review Chunk"
                        )}
                    </button>
                ) : (
                    <button
                        onClick={() => handleAuditClick('fidelity')}
                        disabled={isFidelityAuditing || !selectedChunkId || selectedChunkId === 'FULL_DOC'}
                        className={`w-full py-3 rounded font-bold text-white shadow transition-colors flex justify-center items-center gap-2 
                        ${isFidelityAuditing || !selectedChunkId || selectedChunkId === 'FULL_DOC' ? 'bg-indigo-400 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-700'}`}
                    >
                        {isFidelityAuditing ? (
                            <>
                                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                忠實度審核中 (約 30 秒)...
                            </>
                        ) : (
                            selectedChunkId && selectedChunkId !== 'FULL_DOC' ? "🛡️ 執行忠實度審核" : "請先選擇 Review Chunk"
                        )}
                    </button>
                )}
            </div>
        </div>
    );
}
