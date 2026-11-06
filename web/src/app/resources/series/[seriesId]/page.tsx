// app/resources/series/[seriesId]/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { StaffOnly } from '@/app/components/common/StaffOnly';
import { SeriesPlayerView } from '@/app/components/series/SeriesPlayerView';
import { Suspense } from 'react';

export default function SeriesDetailPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '講道中心', href: '/resources/sermons' },
    { name: '講道系列', href: '/resources/series' },
  ];

  return (
    <div className="min-h-screen bg-stone-50">
      <div className="container mx-auto max-w-5xl px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <StaffOnly>
          <Suspense fallback={null}>
            <SeriesPlayerView />
          </Suspense>
        </StaffOnly>
      </div>
    </div>
  );
}
