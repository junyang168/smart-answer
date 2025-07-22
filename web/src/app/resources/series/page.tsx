// app/resources/series/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { SeriesBrowser } from '@/app/components/series/SeriesBrowser';
import { Suspense } from 'react';

// Suspense 的回退 UI
const SeriesPageFallback = () => <div className="text-center py-20">正在準備系列列表...</div>;

export default function AllSeriesPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '資源中心', href: '/resources' },
    { name: '系列講道' },
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold font-display text-gray-800">系列講道</h1>
          <p className="mt-4 text-lg text-gray-600 max-w-3xl mx-auto">
            系統性地學習聖經真理。以下是我們所有的講道系列，每個系列都包含詳細的介紹、學習要點和完整的講道列表。
          </p>
        </div>
        
        {/* 使用 Suspense 包裹客戶端組件，以獲得更好的加載體驗 */}
        <Suspense fallback={<SeriesPageFallback />}>
          <SeriesBrowser />
        </Suspense>
      </div>
    </div>
  );
}