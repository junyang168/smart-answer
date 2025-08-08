// components/sermons/SermonSearchBar.tsx
"use client";

import { usePathname, useSearchParams, useRouter } from 'next/navigation';
import { useDebouncedCallback } from 'use-debounce';
import { Search } from 'lucide-react';

export const SermonSearchBar = () => {
    const searchParams = useSearchParams();
    const pathname = usePathname();
    const { replace } = useRouter();

    // 使用 debounce 來防止用戶每輸入一個字符就觸發一次 URL 更新
    // 這有助於性能和用戶體驗
    const handleSearch = useDebouncedCallback((term: string) => {
        const params = new URLSearchParams(searchParams);
        params.set('page', '1'); // 每次新搜索都重置到第一頁
        if (term) {
            params.set('q', term);
        } else {
            params.delete('q');
        }
        // replace() 會更新 URL 而不重新加載頁面，這對於客戶端過濾至關重要
        replace(`${pathname}?${params.toString()}`);
    }, 300); // 延遲 300 毫秒

    return (
        <div className="relative mb-8">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Search className="w-5 h-5 text-gray-400" />
            </div>
            <input
                type="text"
                placeholder="按標題或經文搜索..."
                className="w-full p-3 pl-10 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-[#D4AF37] focus:border-[#D4AF37]"
                onChange={(e) => handleSearch(e.target.value)}
                // defaultValue 讓搜索框在頁面刷新或跳轉後仍能保留搜索詞
                defaultValue={searchParams.get('q')?.toString() || ''}
            />
        </div>
    );
};