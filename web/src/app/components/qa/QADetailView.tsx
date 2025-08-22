// components/qa/QADetailView.tsx
"use client";

import { useState, useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { FaithQA } from '@/app/interfaces/article';
import { ScriptureHover } from '@/app/components/sermons/ScriptureHover'; // 複用經文懸停組件
import { Tag, BookOpen } from 'lucide-react';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '';

// 模擬 API 獲取函數
async function fetchQAById(id: string): Promise<FaithQA | null> {
    const user_id = 'junyang168@gmail.com'
    const res = await fetch(`/sc_api/qas/${user_id}/${id}`);
    if (!res.ok) {
        if (res.status === 404) return null;
        throw new Error('Failed to fetch QA data');
    }
    return res.json();

}


export const QADetailView = () => {
    const [qa, setQa] = useState<FaithQA | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const params = useParams();
    const qaId = params.qaId as string;

    useEffect(() => {
        if (!qaId) return;

        const loadData = async () => {
            setIsLoading(true);
            try {
                const data = await fetchQAById(qaId);
                if (!data) {
                    throw new Error('404');
                }
                setQa(data);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setIsLoading(false);
            }
        };
        loadData();
    }, [qaId]);

    if (isLoading) return <div className="text-center py-20">正在加載問題詳情...</div>;
    if (error === '404') notFound();
    if (error) return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
    if (!qa) return null;

    return (
        <div className="max-w-4xl mx-auto">
            {/* 問題標題 */}
            <h1 className="text-3xl md:text-5xl font-bold font-serif !leading-tight text-gray-900 mb-6">
                {qa.question}
            </h1>

            {/* 元數據 */}
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-gray-500 mb-8 pb-8 border-b">
                <div className="flex items-center gap-2">
                    <Tag className="w-4 h-4" />
                    <span className="font-medium">{qa.category}</span> |
                    <span className="font-medium">{qa.date_asked}</span>
                </div>

            </div>

            {/* 簡短答案 */}
            <div className="bg-gray-50 border-l-4 border-blue-500 p-6 mb-10">
                <p className="text-lg text-gray-700 italic leading-relaxed">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{qa.shortAnswer}</ReactMarkdown>
                </p>
            </div>
            
            {/* 完整答案 */}
            <article className="prose prose-lg prose-serif prose-slate dark:prose-invert max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {qa.fullAnswerMarkdown}
                </ReactMarkdown>
            </article>
        </div>
    );
};