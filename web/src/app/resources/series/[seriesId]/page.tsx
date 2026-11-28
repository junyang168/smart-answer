// app/resources/series/[seriesId]/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { requireSermonEditing } from '@/app/utils/requireSermonEditing';
import { SeriesPlayerView } from '@/app/components/series/SeriesPlayerView';
import { Suspense } from 'react';
import { permanentRedirect } from 'next/navigation';
import { seriesIdFromRoute } from '@/app/components/series/progress';

const previousSeriesId = '2021年 NYSC 專題：馬太福音釋經（九）';
const correctedSeriesId = '2022年 NYSC 專題：馬太福音釋經（九）';

type SeriesDetailProps = {
  params: Promise<{ seriesId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function SeriesDetailPage({ params, searchParams }: SeriesDetailProps) {
  await requireSermonEditing();

  if (seriesIdFromRoute((await params).seriesId) === previousSeriesId) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(await searchParams)) {
      for (const entry of Array.isArray(value) ? value : value === undefined ? [] : [value]) {
        query.append(key, entry);
      }
    }
    const suffix = query.size ? `?${query.toString()}` : '';
    permanentRedirect(`/resources/series/${encodeURIComponent(correctedSeriesId)}${suffix}`);
  }

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
