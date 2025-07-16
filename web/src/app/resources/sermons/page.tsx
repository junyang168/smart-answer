// app/resources/sermons/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb'; 
import { SermonBrowser } from "@/app/components/sermons/SermonBrowser";
import { Suspense } from 'react';

// Suspense 用於在客戶端組件及其子組件加載時顯示一個回退 UI
const SermonsPageFallback = () => <div className="text-center py-20">正在準備講道中心...</div>;

export default function SermonsPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '資源中心', href: '/resources' },
    { name: '講道中心' }, // 當前頁面，沒有 href
  ];
      
  return (
    <div className="container mx-auto px-6 py-12">
      <Breadcrumb links={breadcrumbLinks} />
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold font-display text-gray-800">講道中心</h1>
        <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
          使用左側的篩選器來精準定位您想重溫的寶貴信息。
        </p>
      </div>
      
      {/* 
        使用 Suspense 包裹客戶端組件。
        這是推薦的做法，因為它可以讓服務器先快速渲染頁面的靜態部分（標題等），
        然後在客戶端組件加載時顯示一個加載指示器。
      */}
      <Suspense fallback={<SermonsPageFallback />}>
        <SermonBrowser />
      </Suspense>
    </div>
  );
}