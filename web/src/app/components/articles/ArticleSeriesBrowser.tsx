// components/articles/ArticleBrowser.tsx
"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { ChevronRight } from 'lucide-react';
import { SermonSeries, Sermon } from '@/app/interfaces/article'; // 假設這些類型已經定義好
import ReactMarkdown from 'react-markdown'; 
import remarkGfm from 'remark-gfm';          



async function fetchArticlesSeries(): Promise<SermonSeries[]> {
    // 假設您的 API 返回的是一個扁平的文章列表
    const res = await fetch('/sc_api/article_series'); // 假設這是您獲取所有文章的 API 端點
    if (!res.ok) {
        throw new Error('Failed to fetch articles from API');
    }
    const allSeries: SermonSeries[] = await res.json();

    return allSeries

}


// 文章系列區塊組件 (與之前相同)
const ArticleSeriesSection = ({ series }: { series: SermonSeries }) => (
    <section className="bg-white p-6 md:p-8 rounded-xl shadow-lg mb-10 border border-gray-100">
      <h2 className="text-2xl md:text-3xl font-bold font-display text-gray-800">{series.title}</h2>
        {/* 使用 prose 來為 Markdown 內容應用樣式 */}
        <div className="mt-2 text-gray-600 leading-relaxed prose prose-slate max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{series.summary}</ReactMarkdown>
        </div>      
      
      <div className="mt-6 border-t border-gray-200 pt-6">
        <h3 className="font-bold text-lg text-gray-700 mb-3">系列文章 ({series.articles.length}篇)</h3>
        <div className="space-y-3">
          {series.articles.map((article, index) => (
            <Link key={article.id} href={`/resources/articles/${series.id}/${article.item}`} className="group flex items-center justify-between p-3 rounded-md hover:bg-gray-100 transition-colors">
                <div>
                    <p className="font-semibold text-gray-800 group-hover:text-blue-600">{`${index + 1}. ${article.title}`}</p>
                    <p className="text-xs text-gray-500">{article.author_name} • {article.deliver_date}</p>
                </div>
                <ChevronRight className="w-5 h-5 text-gray-400 group-hover:text-blue-600 transition-transform group-hover:translate-x-1" />
            </Link>
          ))}
        </div>
      </div>
    </section>
);


// 主瀏覽器組件
export const ArticleSeriesBrowser = () => {
    const [seriesList, setSeriesList] = useState<SermonSeries[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadData = async () => {
            setIsLoading(true);
            try {
                const data = await fetchArticlesSeries();
                setSeriesList(data);
            } catch (err: any) {
                setError(err.message || 'An unknown error occurred.');
            } finally {
                setIsLoading(false);
            }
        };
        loadData();
    }, []);

    if (isLoading) return <div className="text-center py-20">正在加載文章系列...</div>;
    if (error) return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;

    return (
        <div>
            {seriesList.map(series => (
                <ArticleSeriesSection key={series.id} series={series} />
            ))}
        </div>
    );
};