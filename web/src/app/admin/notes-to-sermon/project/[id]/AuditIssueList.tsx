import React from 'react';

// Type definitions for the issues returned by our backend audits
export interface AuditIssue {
    type: string;
    risk?: string;
    location?: string;
    excerpt?: string;
    reason?: string;
    suggested_fix?: string; // Theological Audit
    proposed_fix?: string; // Fidelity Audit (unified to essentially same meaning)
    confidence?: number;
    note_evidence?: string;
    transcript_evidence?: string;
}

interface AuditIssueListProps {
    issues: AuditIssue[];
    listTitle: string;
    expandedIssueIndex: number | null;
    setExpandedIssueIndex: (index: number | null) => void;
    onHighlightText?: (text: string) => void;
    getTypeLabel: (type: string) => string;
}

export default function AuditIssueList({
    issues,
    listTitle,
    expandedIssueIndex,
    setExpandedIssueIndex,
    onHighlightText,
    getTypeLabel
}: AuditIssueListProps) {

    if (!issues || issues.length === 0) {
        return (
            <div className="bg-green-50 border border-green-200 rounded p-4">
                <h4 className="font-bold text-green-800 flex items-center gap-2">
                    🎉 未發現重大邊界問題
                </h4>
            </div>
        );
    }

    return (
        <div className="bg-red-50 border border-red-200 rounded p-4">
            <h4 className="font-bold text-red-800 border-b border-red-200 pb-2 mb-3">
                {listTitle} ({issues.length})
            </h4>
            <ul className="space-y-4">
                {issues.map((issue, i) => (
                    <li key={i} className="bg-white border rounded shadow-sm overflow-hidden">
                        {/* Header/Summary Area */}
                        <div
                            className="p-3 cursor-pointer hover:bg-gray-50 transition-colors"
                            onClick={() => setExpandedIssueIndex(expandedIssueIndex === i ? null : i)}
                        >
                            <div className="flex justify-between items-start mb-2">
                                <p className="font-bold text-red-800 flex items-center gap-1">
                                    <span>⚠️</span> {getTypeLabel(issue.type)} {issue.risk && `(風險: ${issue.risk})`}
                                </p>
                                <div className="flex items-center gap-2 text-xs text-gray-500">
                                    <span>{expandedIssueIndex === i ? '▲' : '▼'}</span>
                                </div>
                            </div>
                            <p className="text-gray-700 text-sm">
                                <strong>理由:</strong> {issue.reason}
                            </p>
                            {issue.location && (
                                <p className="mt-2 text-gray-500 text-xs border-l-2 border-gray-300 pl-2">
                                    <strong>位置:</strong> {issue.location}
                                </p>
                            )}
                            {issue.note_evidence && (
                                <p className="mt-2 text-gray-500 text-xs border-l-2 border-gray-300 pl-2">
                                    <strong>筆記證據:</strong> {issue.note_evidence}
                                </p>
                            )}
                        </div>

                        {/* Expanded Details Area */}
                        {expandedIssueIndex === i && (
                            <div className="p-3 pt-0 border-t bg-gray-50 space-y-3 mt-3">
                                {/* Proposed/Suggested Fix */}
                                {(issue.suggested_fix || issue.proposed_fix) && (
                                    <div className="bg-green-50 border border-green-200 p-2 rounded">
                                        <p className="text-green-800 text-sm font-bold mb-1">💡 建議修正方向:</p>
                                        <p className="text-green-700 text-sm">{issue.suggested_fix || issue.proposed_fix}</p>
                                    </div>
                                )}

                                {/* Excerpt (Clickable Highlight) */}
                                {issue.excerpt && onHighlightText ? (
                                    <button
                                        onClick={(e) => { e.stopPropagation(); onHighlightText(issue.excerpt!); }}
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

                                {/* Fallback Transcript Evidence if exact excerpt is missing */}
                                {(!issue.excerpt && issue.transcript_evidence) && (
                                    <div className="bg-yellow-50 p-2 rounded border border-yellow-200 text-yellow-800">
                                        <span className="font-semibold block mb-1">逐字稿片段:</span>
                                        {issue.transcript_evidence}
                                    </div>
                                )}
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
