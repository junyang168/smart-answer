// components/sermons/SermonSidebar.tsx
"use client";

import { usePathname, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { ChevronDown, Library } from 'lucide-react';

// ✅ 定義新的選項類型
type FacetOption = {
  value: string;
  count: number;
}

// ✅ 更新 FacetProps 以使用新的選項類型
interface FacetProps {
  title: string;
  paramName: string;
  options: FacetOption[];
  defaultOpen?: boolean; // 新增屬性  
}

const Facet = ({ title, paramName, options,defaultOpen = false }: FacetProps) => {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedValue = searchParams.get(paramName);

  // 如果當前有篩選值被選中，則強制默認展開
  const isOpen = defaultOpen || !!selectedValue;  

  const createQueryString = (name: string, value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value === selectedValue) {
      params.delete(name);
    } else {
      params.set(name, value);
    }
    params.set('page', '1');
    return `${pathname}?${params.toString()}`;
  };

  return (
    <div className="border-b border-gray-200 py-4">
      <details className="group" open={isOpen} key={title + isOpen}>
        <summary className="flex justify-between items-center cursor-pointer font-bold text-gray-800">
          {title}
          <ChevronDown className="w-5 h-5 transition-transform details-open:rotate-180" />
        </summary>
        <div className="mt-3 space-y-2">
          {/* ✅ 更新 map 循環以處理新的數據結構 */}
          {options.map((option) => (
            <Link
              key={option.value}
              href={createQueryString(paramName, option.value)}
              className={`flex justify-between items-center text-sm text-gray-700 hover:text-[#D4AF37] ${selectedValue === option.value ? 'font-bold text-[#D4AF37]' : ''}`}
            >
              <span>{option.value}</span>
              <span className={`text-xs ${selectedValue === option.value ? 'text-[#D4AF37]' : 'text-gray-400'}`}>
                {option.count}
              </span>
            </Link>
          ))}
        </div>
      </details>
    </div>
  );
};

// ✅ 更新 SermonSidebarProps
interface SermonSidebarProps {
  options: {
    books?: FacetOption[];
    topics?: FacetOption[];
    speakers?: FacetOption[];
    years?: FacetOption[];
    statuses?: FacetOption[];
    assignees?: FacetOption[];
  }
}

export const SermonSidebar = ({ options }: SermonSidebarProps) => {
  // ... 組件的其餘部分與之前相同 ...
  const pathname = usePathname();

  const facets = [
    { title: '編輯狀態', paramName: 'status', options: options.statuses || [] },
    { title: '認領人', paramName: 'assignee', options: options.assignees || [] },
    { title: '講道主題', paramName: 'topic', options: options.topics || [] },
    { title: '聖經書卷', paramName: 'book', options: options.books || [] },
  ].filter(f => f.options.length > 0);

  return (
    <aside className="w-full lg:w-64 xl:w-72 lg:pr-8">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">篩選</h2>
        <Link href={pathname} className="text-sm text-gray-500 hover:underline">
          清除全部
        </Link>
      </div>
      {facets.map(facet => <Facet key={facet.title} {...facet} />)}
    {/* 
        ✅ 新增：導航到系列頁面的獨立模塊
      */}
      <div className="mt-10 pt-6 border-t border-gray-200">
        <h2 className="text-xl font-bold mb-4">系列講道</h2>
        <Link
          href="/resources/series"
          className="flex items-center gap-3 p-3 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors shadow-sm"
        >
          <Library className="w-5 h-5" />
          <span>查看所有講道系列</span>
        </Link>
      </div>      
    </aside>
  );
};