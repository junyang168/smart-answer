// components/qa/QABrowser.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Search, ChevronRight } from 'lucide-react';
import { FaithQA } from '@/app/interfaces/article'; // 假設您已經定義了 FaithQA 類型
import { FacetSidebar, FacetDefinition } from '@/app/components/common/FacetSidebar'; 
import { QASearchBar } from './QASearchBar'; 

// 模擬 API 獲取函數
async function fetchVerifiedQAs(): Promise<FaithQA[]> {
    const user_id = 'junyang168@gmail.com'
    const res = await fetch(`/sc_api/qas/${user_id}`);
    const data = await res.json();
    return data;
}

export const QABrowser = () => {
    const [allQAs, setAllQAs] = useState<FaithQA[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const searchParams = useSearchParams();
    const query = searchParams.get('q') || '';

    useEffect(() => {
        const loadData = async () => {
            setIsLoading(true);
            try {
                const data = await fetchVerifiedQAs();
                setAllQAs(data);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setIsLoading(false);
            }
        };
        loadData();
    }, []);

    // ✅ 核心過濾和數據準備邏輯
    const processedData = useMemo(() => {
        // --- 1. 動態生成篩選選項和計數 ---
        const getOptionsWithCounts = (key: keyof FaithQA) => {
            const counts = new Map<string, number>();
            for (const qa of allQAs) {
                const value = qa[key];
                if (typeof value === 'string' && value) {
                    counts.set(value, (counts.get(value) || 0) + 1);
                }
            }
            return Array.from(counts.entries()).map(([value, count]) => ({ value, count }));
        };
        
        const facets: FacetDefinition[] = [
            { title: '相关文章', paramName: 'related_article', options: getOptionsWithCounts('related_article') },
            // 你可以為其他字段（如作者、年份等）添加更多 facet
        ];

        // --- 2. 應用 URL 中的篩選條件 ---
        let filtered = [...allQAs];
        const query = searchParams.get('q');
        const category = searchParams.get('category');

        if (query) {
            filtered = filtered.filter(qa =>
                qa.question.toLowerCase().includes(query.toLowerCase()) ||
                qa.shortAnswer.toLowerCase().includes(query.toLowerCase())
            );
        }
        if (category) {
            filtered = filtered.filter(qa => qa.category === category);
        }
        
        return { filteredQAs: filtered, facets };

    }, [allQAs, searchParams]);

    if (isLoading) return <div>正在加載歷史問答...</div>;
    if (error) return <div className="text-red-500">加載失敗: {error}</div>;

    return (
        <div className="flex flex-col lg:flex-row gap-8">
            {/* ✅ 左側邊欄 */}
            <FacetSidebar title="篩選問答" facets={processedData.facets} />

            {/* ✅ 右側主內容區 */}
            <main className="flex-1">
                <QASearchBar />

                
                <div className="space-y-4">
                    {processedData.filteredQAs.length > 0 ? (
                        processedData.filteredQAs.map(qa => (
                            <Link key={qa.id} href={`/resources/qa/${qa.id}`} className="block bg-white p-6 rounded-lg shadow-sm hover:shadow-md transition-all border">
                                <h3 className="font-bold text-lg text-gray-800 mb-2">{qa.question}</h3>
                                <p className="text-gray-600 text-sm line-clamp-2">{qa.shortAnswer}</p>
                                <div className="flex items-center justify-between mt-4 text-xs text-gray-400">
                                    <span>{qa.category}</span>
                                    <span className="flex items-center font-semibold text-blue-600">
                                        查看完整答案 <ChevronRight className="w-4 h-4 ml-1" />
                                    </span>
                                </div>
                            </Link>
                        ))
                    ) : (
                        <div className="text-center py-16 bg-white rounded-lg shadow-sm">
                            <h3 className="text-xl font-semibold">沒有找到匹配的問答</h3>
                            <p className="text-gray-500 mt-2">請嘗試調整您的篩選條件。</p>
                        </div>
                    )}
                </div>
            </main>
        </div>
    );
};