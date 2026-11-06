// app/resources/series/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { StaffOnly } from '@/app/components/common/StaffOnly';
import { SeriesBrowser } from '@/app/components/series/SeriesBrowser';
import { Suspense } from 'react';

// 同工的編輯入口：每個系列的校對進度（負責人 2026-11-06）。普通信眾從講論文庫找講道。
export default function AllSeriesPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '講道中心', href: '/resources/sermons' },
    { name: '講道系列' },
  ];

  return (
    <div className="min-h-screen bg-stone-50">
      <div className="container mx-auto max-w-5xl px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <header className="mb-8">
          <h1 className="text-3xl font-bold text-stone-950">講道系列</h1>
          <p className="mt-2 text-stone-600">各系列的編輯進度。點一個系列，看每篇講道的認領人與發佈情況。</p>
        </header>
        <StaffOnly>
          <Suspense fallback={null}>
            <SeriesBrowser />
          </Suspense>
        </StaffOnly>
      </div>
    </div>
  );
}
