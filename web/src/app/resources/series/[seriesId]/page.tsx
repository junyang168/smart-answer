// app/resources/series/[seriesId]/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { requireSermonEditing } from '@/app/utils/requireSermonEditing';
import { SeriesPlayerView } from '@/app/components/series/SeriesPlayerView';
import { Suspense } from 'react';

export default async function SeriesDetailPage() {
  await requireSermonEditing();
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '聖經學習資源', href: '/resources' },
    { name: '講道校對與編輯', href: '/resources/sermons' },
    { name: '講道系列', href: '/resources/series' },
  ];

  return (
    <div className="min-h-screen bg-stone-50">
      <div className="container mx-auto max-w-5xl px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
          <Suspense fallback={null}>
            <SeriesPlayerView />
          </Suspense>
      </div>
    </div>
  );
}
