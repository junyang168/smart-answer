// app/admin/qa/page.tsx
import { QAEditorBrowser } from '@/app/components/admin/qa/QAEditorBrowser';
import { Suspense } from 'react';

// 假設這個頁面已經被某種管理員路由保護機制所保護
export default function QAEditorPage() {
  return (
    <div className="bg-gray-100 min-h-screen">
      <Suspense fallback={<div className="p-8">正在加載問答編輯器...</div>}>
        <QAEditorBrowser />
      </Suspense>
    </div>
  );
}