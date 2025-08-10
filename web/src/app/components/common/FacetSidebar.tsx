// components/common/FacetSidebar.tsx
"use client";

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { ChevronRight } from 'lucide-react';

// --- 通用類型定義 ---
export type FacetOption = {
  value: string;
  count: number;
}

export interface FacetDefinition {
  title: string;
  paramName: string;
  options: FacetOption[];
}

// --- 通用 Facet 組件 ---
const Facet = ({ title, paramName, options }: FacetDefinition) => {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const selectedValue = searchParams.get(paramName);

    const createQueryString = (name: string, value: string) => {
        const params = new URLSearchParams(searchParams);
        if (value === selectedValue) {
            params.delete(name);
        } else {
            params.set(name, value);
        }
        params.delete('page'); // 篩選時重置分頁
        return `${pathname}?${params.toString()}`;
    };

    return (
        <div className="border-b border-gray-200 py-2">
            <details className="group" open={!!selectedValue}>
                <summary className="flex justify-between items-center p-2 cursor-pointer list-none hover:bg-gray-100 rounded-md">
                    <span className="font-semibold text-gray-800">{title}</span>
                    <ChevronRight className="w-5 h-5 text-gray-500 transition-transform duration-300 group-open:rotate-90" />
                </summary>
                <div className="mt-2 pl-2 pr-1 space-y-1">
                    {options.map((option) => (
                        <Link
                            key={option.value}
                            href={createQueryString(paramName, option.value)}
                            className={`flex justify-between items-center p-2 rounded-md text-sm text-gray-700 hover:bg-gray-100 ${selectedValue === option.value ? 'font-bold bg-blue-50 text-blue-700' : ''}`}
                        >
                            <span>{option.value}</span>
                            <span className={`text-xs ${selectedValue === option.value ? 'font-semibold' : 'text-gray-400'}`}>
                                {option.count}
                            </span>
                        </Link>
                    ))}
                </div>
            </details>
        </div>
    );
};

// --- 通用側邊欄組件 ---
interface FacetSidebarProps {
    facets: FacetDefinition[];
    title: string;
}

export const FacetSidebar = ({ facets, title }: FacetSidebarProps) => {
    const pathname = usePathname();
    return (
        <aside className="w-full lg:w-64 xl:w-72 lg:pr-8">
            <div>
                <div className="flex justify-between items-center mb-4">
                    <h2 className="text-xl font-bold">{title}</h2>
                    <Link href={pathname} className="text-sm text-gray-500 hover:underline">
                        清除全部
                    </Link>
                </div>
                {facets.filter(f => f.options.length > 0).map(facet => (
                    <Facet key={facet.title} {...facet} />
                ))}
            </div>
        </aside>
    );
};