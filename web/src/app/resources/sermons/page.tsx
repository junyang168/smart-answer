// app/resources/sermons/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb'; 
import { SermonBrowser } from "@/app/components/sermons/SermonBrowser";
import { Suspense } from 'react';
import { requireSermonEditing } from '@/app/utils/requireSermonEditing';

// Suspense 用於在客戶端組件及其子組件加載時顯示一個回退 UI
const SermonsPageFallback = () => <div className="text-center py-20">正在準備講道校對與編輯...</div>;

export default async function SermonsPage() {
  await requireSermonEditing();
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '聖經學習資源', href: '/resources' },
    { name: '講道校對與編輯' }, // 當前頁面，沒有 href
  ];
      
  return (
    <div className="container mx-auto px-6 py-12">
      <Breadcrumb links={breadcrumbLinks} />
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold font-display text-gray-800">講道校對與編輯</h1>
        <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
          選擇講道，查看逐字稿與校對進度。
        </p>
      </div>
      
      {/* 
        使用 Suspense 包裹客戶端組件。
        這是推薦的做法，因為它可以讓服務器先快速渲染頁面的靜態部分（標題等），
        然後在客戶端組件加載時顯示一個加載指示器。
      */}
      {/* 同工的編輯入口（負責人 2026-11-06）；普通信眾從講論文庫按書卷、章節找講道。 */}
        <Suspense fallback={<SermonsPageFallback />}>
          <SermonBrowser />
        </Suspense>
    </div>
  );
}
