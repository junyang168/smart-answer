// app/resources/articles/[articleId]/page.tsx
"use client"; // 因為我們需要使用 useParams 來構造一個基本的麵包屑

import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { ArticleDetailView } from '@/app/components/articles/ArticleDetailView';
import { Suspense } from 'react';
import { useParams } from 'next/navigation';
import { useSearchParams } from "next/navigation";

const ArticleDetailFallback = () => <div className="text-center py-20">正在準備文章頁面...</div>;

export default function ArticleDetailPage() {
    const params = useParams();
    const articleTitle = typeof params.articleId === 'string'
        ? params.articleId.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
        : '文章詳情';

    const breadcrumbLinks = [
        { name: '首頁', href: '/' },
        { name: 'AI 輔助查經', href: '/resources' },
        { name: '文章薈萃', href: '/resources/articles' }
    ];
    const searchParams = useSearchParams();
    const seriesIdFromQuery = searchParams.get('seriesId') as string | undefined;

    return (
        <div className="bg-white">
            <div className="container mx-auto px-6 py-12">
                <Breadcrumb links={breadcrumbLinks} />
                <Suspense fallback={<ArticleDetailFallback />}>
                    <ArticleDetailView />
                </Suspense>
            </div>
        </div>
    );
}