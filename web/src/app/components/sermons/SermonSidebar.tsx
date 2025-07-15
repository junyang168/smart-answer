// components/sermons/SermonSidebar.tsx
"use client";

import { usePathname, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { ChevronDown, ChevronRight } from 'lucide-react';

// 定義每個篩選塊的屬性
interface FacetProps {
  title: string;
  paramName: string; // URL參數名, e.g., 'book'
  options: string[];
}

// 篩選塊組件
const Facet = ({ title, paramName, options }: FacetProps) => {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedValue = searchParams.get(paramName);

  // 幫助函數: 創建帶有新篩選參數的URL
  const createQueryString = (name: string, value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value === selectedValue) {
      // 如果再次點擊已選中的選項，則取消篩選
      params.delete(name);
    } else {
      params.set(name, value);
    }
    params.set('page', '1'); // 每次篩選都重置到第一頁
    return `${pathname}?${params.toString()}`;
  };

  return (
    <div className="border-b border-gray-200 py-4">
      <details open={true}>
        <summary className="flex justify-between items-center cursor-pointer font-bold text-gray-800">
          {title}
          <ChevronDown className="w-5 h-5 transition-transform details-open:rotate-180" />
        </summary>
        <div className="mt-3 space-y-2">
          {options.map((option) => (
            <Link
              key={option}
              href={createQueryString(paramName, option)}
              className={`block text-sm text-gray-700 hover:text-[#D4AF37] ${selectedValue === option ? 'font-bold text-[#D4AF37]' : ''}`}
            >
              {option}
            </Link>
          ))}
        </div>
      </details>
    </div>
  );
};

// 完整的側邊欄組件
export const SermonSidebar = () => {
  const pathname = usePathname();
  // 這些選項應從後端獲取，或在父組件中定義
  const facets: FacetProps[] = [
    { title: '聖經書卷', paramName: 'book', options: ["羅馬書", "以弗所書", "出埃及記", "箴言"] },
    { title: '講道主題', paramName: 'topic', options: ["福音基礎", "家庭系列", "舊約中的基督"] },
    { title: '講道年份', paramName: 'year', options: ["2025", "2024"] },
    { title: '講員', paramName: 'speaker', options: ["王守仁 牧師", "李長老", "客座講員"] },
    { title: '編輯狀態', paramName: 'status', options: ["已發佈", "編輯中", "草稿"] },
    { title: '認領人', paramName: 'assignee', options: ["張三", "李四", "王五"] },
  ];

  return (
    <aside className="w-full lg:w-64 xl:w-72 lg:pr-8">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">篩選</h2>
        <Link href={pathname} className="text-sm text-gray-500 hover:underline">
          清除全部
        </Link>
      </div>
      {facets.map(facet => <Facet key={facet.title} {...facet} />)}
    </aside>
  );
};

// 添加一個簡單的CSS來處理details圖標的旋轉
// 在你的 globals.css 文件中添加:
// details[open] > summary .details-open\:rotate-180 {
//   transform: rotate(180deg);
// }