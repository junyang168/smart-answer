// app/qa/ai-assistant/[[...chatId]]/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { ChatLayout } from '@/app/components/ai-assistant/ChatLayout';
import { Suspense } from 'react';

export default function AIAssistantPage({ params }: { params: { chatId?: string[] } }) {
  // 從 URL 中解析出 chatId
  const chatId = params.chatId?.[0];

    const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: 'AI 信仰助教' },
  ];

  return (
    <div className="bg-gray-100">
      <div className="container mx-auto px-4 py-8">
        <Breadcrumb links={breadcrumbLinks} />
        {/* 我們將 chatId 傳遞給主佈局組件 */}
        <Suspense fallback={<div>正在加載 AI 助教...</div>}>
            <ChatLayout chatId={chatId} />
        </Suspense>
      </div>
    </div>
  );
}