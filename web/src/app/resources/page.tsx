// app/resources/sermons/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb'; 
import { ResourceCenter } from "@/app/components/ResourceCenter";
import { Suspense } from 'react';

// Suspense 用於在客戶端組件及其子組件加載時顯示一個回退 UI
const SermonsPageFallback = () => <div className="text-center py-20">正在準備講道中心...</div>;

export default function ResourcesPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' }
  ];
      
  return (
    <div className="container mx-auto px-6 py-12">
      <Breadcrumb links={breadcrumbLinks} />
      
      {/* 
        使用 Suspense 包裹客戶端組件。
        這是推薦的做法，因為它可以讓服務器先快速渲染頁面的靜態部分（標題等），
        然後在客戶端組件加載時顯示一個加載指示器。
      */}
      <Suspense fallback={<SermonsPageFallback />}>
        <ResourceCenter />
      </Suspense>
    </div>
  );
}