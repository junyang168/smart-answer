// components/qa/QASearchBar.tsx
"use client";

import { usePathname, useSearchParams, useRouter } from 'next/navigation';
import { useDebouncedCallback } from 'use-debounce';
import { Search } from 'lucide-react';

export const QASearchBar = () => {
    const searchParams = useSearchParams();
    const pathname = usePathname();
    const { replace } = useRouter();

    const handleSearch = useDebouncedCallback((term: string) => {
        const params = new URLSearchParams(searchParams);
        if (term) { params.set('q', term); } 
        else { params.delete('q'); }
        replace(`${pathname}?${params.toString()}`);
    }, 300);

    return (
        <div className="relative mb-6">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-5 h-5" />
            <input
                type="text"
                placeholder="搜索問題或答案..."
                onChange={(e) => handleSearch(e.target.value)}
                defaultValue={searchParams.get('q')?.toString() || ''}
                className="w-full p-3 pl-10 border rounded-lg"
            />
        </div>
    );
};