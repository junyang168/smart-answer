// app/resources/qa/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { QABrowser } from '@/app/components/qa/QABrowser'; // 我們將創建這個客戶端組件
import { Suspense } from 'react';

const QAPageFallback = () => <div className="text-center py-20">正在準備信仰問答...</div>;

export default function QAPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '信仰問答' },
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold font-display text-gray-800">信仰問答</h1>
          <p className="mt-4 text-lg text-gray-600 max-w-3xl mx-auto">
            在這裡，您可以與我們的 AI 助教對話，或瀏覽、搜索由教會同工審核過的常見信仰問題解答。
          </p>
          <p className="mt-2 text-sm text-red-500">
            AI 助教對話功能尚在開發中，敬請期待！
          </p>
        </div>
        
        {/* 主要內容將由客戶端組件處理 */}
        <Suspense fallback={<QAPageFallback />}>
          <QABrowser />
        </Suspense>
      </div>
    </div>
  );
}