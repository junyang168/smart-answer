// components/sermons/SermonFilterControls.tsx
"use client";

import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import { useDebouncedCallback } from 'use-debounce';

// 假設這些數據從你的後端或CMS獲取
const speakers = ["王守仁 牧師", "李長老", "客座講員"];
const bibleBooks = ["羅馬書", "以弗所書", "出埃及記", "箴言"];
// 新增的篩選選項
const topics = ["福音基礎", "家庭系列", "舊約中的基督"];
const years = ["2025", "2024"];
const statuses = ["已發佈", "編輯中", "草稿"];
const assignees = ["張三", "李四", "王五"];


const SermonFilterControls = () => {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const handleSearch = useDebouncedCallback((term: string) => {
    const params = new URLSearchParams(searchParams);
    params.set('page', '1');
    if (term) { params.set('q', term); } 
    else { params.delete('q'); }
    router.replace(`${pathname}?${params.toString()}`);
  }, 300);

  const handleFilterChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const { name, value } = e.target;
    const params = new URLSearchParams(searchParams);
    params.set('page', '1');
    if (value) { params.set(name, value); } 
    else { params.delete(name); }
    router.replace(`${pathname}?${params.toString()}`);
  };

  const clearFilters = () => {
    router.replace(pathname);
  }

  return (
    <div className="bg-white p-6 rounded-lg shadow-md mb-8">
      {/* 關鍵字搜索 */}
      <div className="mb-4">
        <input
          type="text"
          placeholder="按標題或經文搜索..."
          className="p-2 border border-gray-300 rounded-md w-full"
          onChange={(e) => handleSearch(e.target.value)}
          defaultValue={searchParams.get('q')?.toString()}
        />
      </div>
      {/* 下拉篩選器 */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <select name="speaker" onChange={handleFilterChange} defaultValue={searchParams.get('speaker') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有講員</option>
          {speakers.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select name="book" onChange={handleFilterChange} defaultValue={searchParams.get('book') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有書卷</option>
          {bibleBooks.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        {/* 新增篩選器 */}
        <select name="topic" onChange={handleFilterChange} defaultValue={searchParams.get('topic') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有主題</option>
          {topics.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select name="year" onChange={handleFilterChange} defaultValue={searchParams.get('year') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有年份</option>
          {years.map(y => <option key={y} value={y}>{y}</option>)}
        </select>
        <select name="status" onChange={handleFilterChange} defaultValue={searchParams.get('status') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有狀態</option>
          {statuses.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select name="assignee" onChange={handleFilterChange} defaultValue={searchParams.get('assignee') || ''} className="p-2 border border-gray-300 rounded-md w-full">
          <option value="">所有認領人</option>
          {assignees.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
      <div className="mt-4 text-right">
        <button onClick={clearFilters} className="bg-gray-200 text-gray-700 py-2 px-4 rounded-md hover:bg-gray-300 text-sm">
            清除所有篩選
        </button>
      </div>
    </div>
  );
};

export default SermonFilterControls;