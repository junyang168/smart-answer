// app/resources/qa/[qaId]/page.tsx
"use client";

import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { QADetailView } from '@/app/components/qa/QADetailView';
import { Suspense } from 'react';
import { useParams } from 'next/navigation';

const QADetailFallback = () => <div className="text-center py-20">正在準備問題頁面...</div>;

export default function QADetailPage() {
    const params = useParams();
    // 創建一個臨時的麵包屑標題
    const questionTitle = typeof params.qaId === 'string'
        ? `問題 #${params.qaId}`
        : '問答詳情';

    const breadcrumbLinks = [
        { name: '首頁', href: '/' },
        { name: 'AI 辅助查经', href: '/resources' },
        { name: '信仰問答', href: '/resources/qa' },
        { name: questionTitle },
    ];
    
    // 在真實應用中，我們可能希望在 QADetailView 加載完數據後，再動態更新麵包屑的最後一項
    // 但為了簡潔，我們先使用臨時標題

    return (
        <div className="bg-white">
            <div className="container mx-auto px-6 py-12">
                <Breadcrumb links={breadcrumbLinks} />
                <Suspense fallback={<QADetailFallback />}>
                    <QADetailView />
                </Suspense>
            </div>
        </div>
    );
}