"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ArticleSection } from "@/app/components/full-article/section-utils";

interface ArticleTOCProps {
    sections: ArticleSection[];
}

export function ArticleTOC({ sections }: ArticleTOCProps) {
    const [expandedSectionId, setExpandedSectionId] = useState<string | null>(null);

    const toggleSection = (sectionId: string) => {
        setExpandedSectionId((prev) => (prev === sectionId ? null : sectionId));
    };

    if (sections.length === 0) {
        return <p className="mt-4 text-sm text-gray-500">目前沒有章節可供導覽。</p>;
    }

    return (
        <ul className="mt-4 space-y-2">
            {sections.map((section) => {
                const plainTitle = section.title.replace(/\*\*/g, "").trim();
                const hasSubsections = section.subsections && section.subsections.length > 0;
                const isExpanded = expandedSectionId === section.id;

                return (
                    <li key={section.id}>
                        <div className="flex items-center justify-between rounded-md border border-gray-100 px-3 py-2 text-sm text-gray-700 transition hover:border-blue-200 hover:text-blue-700">
                            <a
                                href={`#${section.id}`}
                                className="flex-1 truncate font-medium"
                                onClick={() => {
                                    if (hasSubsections) {
                                        toggleSection(section.id);
                                    }
                                }}
                            >
                                {plainTitle}
                            </a>
                            {hasSubsections && (
                                <button
                                    onClick={(e) => {
                                        e.preventDefault();
                                        toggleSection(section.id);
                                    }}
                                    className="ml-2 p-1 text-gray-400 hover:text-gray-600"
                                >
                                    {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                </button>
                            )}
                        </div>
                        {hasSubsections && isExpanded && (
                            <ul className="mt-1 ml-4 space-y-1 border-l border-gray-100 pl-2">
                                {section.subsections.map((sub) => (
                                    <li key={sub.id}>
                                        <a
                                            href={`#${sub.id}`}
                                            className="block rounded-md px-2 py-1.5 text-xs text-gray-500 transition hover:bg-gray-50 hover:text-blue-600"
                                        >
                                            <span className="truncate">{sub.title}</span>
                                        </a>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </li>
                );
            })}
        </ul>
    );
}
