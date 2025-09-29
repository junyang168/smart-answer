// components/articles/RelatedQAs.tsx
"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { MessageCircleQuestion, ChevronRight, PlusCircle } from 'lucide-react';
import { FaithQA } from '@/app/interfaces/article'; // 引入 FaithQA 类型

// 模拟 API，获取所有已验证的 Q&A
async function fetchRelatedQAs(articleId: string): Promise<FaithQA[]> {
    const res = await fetch(`/sc_api/qas?articleId=${articleId}`);
    const relatedQAs: FaithQA[] = await res.json();
    return relatedQAs;
}

interface RelatedQAsProps {
  articleId: string;
  articleTitle: string;
}

export const RelatedQAs = ({ articleId, articleTitle }: RelatedQAsProps) => {
    const [relatedQAs, setRelatedQAs] = useState<FaithQA[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const { data: session } = useSession();
    console.log(session?.user)
    const isAdmin = session?.user?.role === "admin"; 

    useEffect(() => {
        const loadRelatedQAs = async () => {
            setIsLoading(true);
            const filteredQAs = await fetchRelatedQAs(articleId);
            setRelatedQAs(filteredQAs);
            setIsLoading(false);
        };

        loadRelatedQAs();
    }, [articleId]);

    if (isLoading) {
        return (
            <div className="mt-16 pt-12 border-t">
                <div className="h-24 bg-gray-200 rounded-lg animate-pulse"></div>
            </div>
        );
    }

    return (
        <footer className="mt-16 pt-12 border-t">
            <div className="flex items-center gap-3 mb-6">
                <MessageCircleQuestion className="w-8 h-8 text-gray-700" />
                <h2 className="text-3xl font-bold font-display text-gray-800">相关问答</h2>
            </div>
            
            <div className="space-y-4">
                {relatedQAs.length > 0 ? (
                    relatedQAs.map(qa => (
                        <Link key={qa.id} href={`/resources/qa/${qa.id}`} className="group block bg-white p-4 rounded-lg shadow-sm hover:shadow-md transition-all border flex justify-between items-center">
                            <div>
                                <h3 className="font-semibold text-gray-800 group-hover:text-blue-600">{qa.question}</h3>
                                <p className="text-sm text-gray-500 line-clamp-1">{qa.shortAnswer}</p>
                            </div>
                            <ChevronRight className="w-5 h-5 text-gray-400 group-hover:text-blue-600 transition-transform group-hover:translate-x-1 flex-shrink-0 ml-4" />
                        </Link>
                    ))
                ) : (
                    <div className="text-center py-8 px-4 bg-gray-50 rounded-lg border-dashed">
                        <p className="text-gray-600">目前暂无与本文相关的问答。</p>
                    </div>
                )}
            </div>
            
            {/* 管理员专属的“创建问答”按钮 */}
            {isAdmin && (
                <div className="mt-8 pt-8 border-t border-gray-200 border-dashed text-center">
                    <Link
                        href={`/admin/qa?action=new&relatedArticleId=${articleId}`}
                        className="inline-flex items-center gap-2 bg-green-100 text-green-800 font-semibold py-2 px-4 rounded-lg hover:bg-green-200 transition-colors border border-green-200"
                    >
                        <PlusCircle className="w-5 h-5" />
                        <span>创建新问答</span>
                    </Link>
                </div>
            )}
        </footer>
    );
};