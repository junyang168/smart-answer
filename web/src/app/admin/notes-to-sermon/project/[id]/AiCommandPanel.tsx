"use client";

import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface AiCommandPanelProps {
    projectId: string;
}

export default function AiCommandPanel({ projectId }: AiCommandPanelProps) {
    const [isAuditing, setIsAuditing] = useState(false);
    const [auditResult, setAuditResult] = useState<string | null>(null);

    const handleAuditClick = async () => {
        setIsAuditing(true);
        setAuditResult(null);

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
                setAuditResult(`Audit failed: Invalid JSON response (Status: ${res.status})`);
                setIsAuditing(false);
                return;
            }

            if (res.ok && data.audit_result) {
                setAuditResult(data.audit_result);
            } else {
                setAuditResult("Audit failed: " + (data.detail || "Unknown error"));
            }
        } catch (e: any) {
            clearTimeout(timeoutId);
            if (e.name === 'AbortError') {
                setAuditResult("Request timed out after 80 seconds. Please try again.");
            } else {
                setAuditResult("An error occurred during the audit process: " + e.message);
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

                {auditResult && (
                    <div className="bg-gray-50 border rounded p-3 text-sm text-gray-800 prose prose-sm max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {auditResult}
                        </ReactMarkdown>
                    </div>
                )}

                {!auditResult && !isAuditing && (
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
