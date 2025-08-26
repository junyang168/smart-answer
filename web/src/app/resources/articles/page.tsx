// app/resources/articles/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { ArticleSeriesBrowser } from '@/app/components/articles/ArticleSeriesBrowser';
import { Suspense } from 'react';
import { BookText } from 'lucide-react';

const ArticlePageFallback = () => <div className="text-center py-20">正在準備文章列表...</div>;

export default function AllArticleSeriesPage() {
   const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '文章薈萃', href: '/resources/articles' }
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <div className="text-center mb-12">
            <BookText className="w-16 h-16 mx-auto text-gray-400 mb-4" />
            <h1 className="text-4xl font-bold font-display text-gray-800">團契分享</h1>
            <p className="mt-4 text-lg text-gray-600 max-w-3xl mx-auto">
                彙集了教會同工團契中的講稿，深入探索信仰的各個層面。
            </p>
        </div>
        <Suspense fallback={<ArticlePageFallback />}>
          <ArticleSeriesBrowser />
        </Suspense>
      </div>
    </div>
  );
}