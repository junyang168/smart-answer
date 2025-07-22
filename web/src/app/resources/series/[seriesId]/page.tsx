// app/resources/series/[seriesId]/page.tsx
"use client"; // ✅ 將頁面本身標記為客戶端組件，以便使用 hook

import { useParams } from 'next/navigation';
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { SeriesPlayerView } from '@/app/components/series/SeriesPlayerView';
import { Suspense } from 'react';

const SeriesDetailFallback = () => <div className="text-center py-20">正在準備播放列表...</div>;

export default function SeriesDetailPage() {
  const params = useParams();
  // 由於我們不知道系列標題，所以麵包屑可以稍微簡化，或在子組件加載後再更新
  // 這裡採用簡化方案
  const seriesTitle = typeof params.seriesId === 'string' 
    ? decodeURIComponent( params.seriesId.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())) 
    : '系列詳情';

  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '資源中心', href: '/resources' },
    { name: '系列講道', href: '/resources/series' },
    { name: seriesTitle },
  ];

  return (
    <div className="container mx-auto px-6 py-12">
      <Breadcrumb links={breadcrumbLinks} />
      <Suspense fallback={<SeriesDetailFallback />}>
        <SeriesPlayerView />
      </Suspense>
    </div>
  );
}