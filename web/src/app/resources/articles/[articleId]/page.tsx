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
        ? decodeURIComponent(params.articleId.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase()))
        : '文章詳情';

    const searchParams = useSearchParams();
    const seriesIdFromQuery : string = searchParams.get('seriesId') as string ;


    const breadcrumbLinks = [
        { name: '首頁', href: '/' },
        { name: 'AI 輔助查經', href: '/resources' },
        { name: '團契分享', href: '/resources/articles' },
        { name: seriesIdFromQuery, href: `/resources/articles#${seriesIdFromQuery}` },
        { name: articleTitle, href: '' }
    ];

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