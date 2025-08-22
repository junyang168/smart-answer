// components/articles/ArticleDetailView.tsx
"use client";

import { useState, useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookMarked } from 'lucide-react';
import { Sermon, SermonSeries } from '@/app/interfaces/article';
import { apiToUiSermon, apiToUiSermonSeries} from '@/app/utils/converter';
import { SidebarDownload } from './SidebarDownload'; // ✅ 引入新的側邊欄下載組件
import { RelatedQAs } from './RelatedQA'; // ✅ 引入新组件

export const ArticleDetailView = () => {
    const [currentArticle, setCurrentArticle] = useState<Sermon | null>(null);
    const [currentSeries, setCurrentSeries] = useState<SermonSeries | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const params = useParams();
    const articleId = decodeURIComponent(Array.isArray(params.articleId) ? params.articleId[0] : params.articleId);

    useEffect(() => {
        if (!articleId) return;

        const loadArticleData = async () => {
            setIsLoading(true);
            try {
                // 我們仍然需要獲取所有數據，然後在客戶端查找
                const res = await fetch(`/sc_api/article/${articleId}`)
                if (!res.ok) {
                    throw new Error('Failed to fetch article from API');
                }
                const article_with_series = await res.json();
                const foundSeries: SermonSeries =  apiToUiSermonSeries(article_with_series.series);
                const foundArticle: Sermon = apiToUiSermon(article_with_series);

                if (foundArticle && foundSeries) {
                    setCurrentArticle(foundArticle);
                    setCurrentSeries(foundSeries);
                } else {
                    throw new Error('404');
                }
            } catch (err: any) {
                setError(err.message || 'An unknown error occurred.');
            } finally {
                setIsLoading(false);
            }
        };

        loadArticleData();
    }, [articleId]);

    if (isLoading) return <div className="text-center py-20">正在加載文章內容...</div>;
    if (error === '404') notFound();
    if (error) return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
    if (!currentArticle) return null;

    return (
        <div className="flex flex-col lg:flex-row-reverse gap-8 lg:gap-12">
            {/* 側邊欄 (完全不變) */}
            <aside className="lg:w-1/3 lg:sticky lg:top-24 self-start">
                {/* 1. 將下載組件放置在側邊欄頂部 */}
                <SidebarDownload 
                    downloadUrl={`/web/data/article/presentations/${currentArticle.id}.pptx`}
                />
                { currentSeries && currentSeries.sermons && (
                    <div className="bg-gray-50 rounded-lg p-4">
                        <div className="flex items-center mb-4">
                            <BookMarked className="w-6 h-6 mr-3 text-gray-700"/>
                            <div>
                                <p className="text-xs text-gray-500">系列</p>
                                <h3 className="font-bold text-lg">{currentSeries.title}</h3>
                            </div>
                        </div>
                        <ul className="space-y-1">
                            {currentSeries.sermons.map((article, index) => {
                                const isActive = article.item === currentArticle.id;
                                return (
                                    <li key={article.item}>
                                        <Link href={`/resources/articles/${article.item}`} className={`block p-3 rounded-md transition-colors ${isActive ? 'bg-blue-100 text-blue-800 font-bold' : 'hover:bg-gray-200 text-gray-800'}`}>
                                            {`${index + 1}. ${article.title}`}
                                        </Link>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>

                )}
            </aside>
            <main className="lg:w-2/3">
                <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900">{currentArticle.title}</h1>
                <p className="text-gray-600 my-4 pb-4 border-b">作者: {currentArticle.speaker} | 發布日期: {currentArticle.date}</p>
                <article className="prose lg:prose-xl max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{currentArticle.markdownContent}</ReactMarkdown>
                </article>
                {/* ✅ 在文章底部渲染新的“相关问答”组件 */}
                <RelatedQAs 
                    articleId={currentArticle.id} 
                    articleTitle={currentArticle.title}
                />            
            </main>
        </div>
    );
};