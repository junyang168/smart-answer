// components/sermons/SermonSearchBar.tsx
"use client";

import { usePathname, useSearchParams, useRouter } from 'next/navigation';
import { useDebouncedCallback } from 'use-debounce';

export const SermonSearchBar = () => {
    const searchParams = useSearchParams();
    const pathname = usePathname();
    const { replace } = useRouter();

    const handleSearch = useDebouncedCallback((term: string) => {
        const params = new URLSearchParams(searchParams);
        params.set('page', '1');
        if (term) {
            params.set('q', term);
        } else {
            params.delete('q');
        }
        replace(`${pathname}?${params.toString()}`);
    }, 300);

    return (
        <div className="mb-8">
            <input
                type="text"
                placeholder="按標題或經文搜索..."
                className="p-3 border border-gray-300 rounded-md w-full shadow-sm focus:ring-2 focus:ring-[#D4AF37] focus:border-[#D4AF37]"
                onChange={(e) => handleSearch(e.target.value)}
                defaultValue={searchParams.get('q')?.toString() || ''}
            />
        </div>
    );
};