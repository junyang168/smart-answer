// components/sermons/SermonBrowser.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import { useSearchParams } from 'next/navigation';

import { SermonListItem } from '@/app/components/sermons/SermonListItem';
import { PaginationControls } from '@/app/components/sermons/PaginationControls';
import { Sermon } from '@/app/interfaces/article';
import {SermonSearchBar} from '@/app/components/sermons/SermonSearchBar';
import {SermonSidebar} from '@/app/components/sermons/SermonSidebar';
import { fetchSermons } from '@/app/utils/fetch-articles'


export const SermonBrowser = () => {
  // --- State Management ---
  const [allSermons, setAllSermons] = useState<Sermon[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const searchParams = useSearchParams();

  // --- Data Fetching ---
  useEffect(() => {
    // 這個 useEffect 只在組件首次掛載到瀏覽器時運行一次
    const fetchAllSermons = async () => {
      setIsLoading(true);
      setError(null);
      try {
        // ✅ fetch 在瀏覽器中運行，可以使用相對路徑或絕對路徑
        const transformedSermons : Sermon[] = await fetchSermons();
        setAllSermons(transformedSermons);
      } catch (err: any) {
        setError(err.message || 'An unknown error occurred.');
      } finally {
        setIsLoading(false);
      }
    };

    fetchAllSermons();
  }, []); // 空依賴數組確保只運行一次

  // --- Filtering and Pagination (在客戶端內存中進行) ---
  const filteredAndPaginatedData = useMemo(() => {
    let filtered = [...allSermons];

    // 從 URL 中讀取篩選參數
    const q = searchParams.get('q');
    const speaker = searchParams.get('speaker');
    const book = searchParams.get('book');
    const topic = searchParams.get('topic');
    const year = searchParams.get('year');
    const status = searchParams.get('status');
    const assignee = searchParams.get('assignee');
    const page = Number(searchParams.get('page') ?? '1');
    const limit = 12;
    
    // 應用篩選邏輯
    if (q) { filtered = filtered.filter(s => s.title.toLowerCase().includes(q.toLowerCase()) || s.scripture.toLowerCase().includes(q.toLowerCase())); }
    if (speaker) { filtered = filtered.filter(s => s.speaker === speaker); }
    if (book) { filtered = filtered.filter(s => s.book === book); }
    if (topic) { filtered = filtered.filter(s => s.topic === topic); }
    if (year) { filtered = filtered.filter(s => s.date.startsWith(year)); }
    if (status) { filtered = filtered.filter(s => s.status === status); }
    if (assignee) { filtered = filtered.filter(s => s.assigned_to_name === assignee); }

    const totalCount = filtered.length;
    const startIndex = (page - 1) * limit;
    const endIndex = page * limit;
    
    return {
      paginatedSermons: filtered.slice(startIndex, endIndex),
      totalCount,
      hasNextPage: endIndex < totalCount,
      hasPrevPage: startIndex > 0,
    };
  }, [allSermons, searchParams]); // 僅當數據或篩選參數變化時才重新計算

  // --- 動態生成篩選選項 ---
  const filterOptions = useMemo(() => {
    const getUniqueOptions = (key: keyof Sermon) => [...new Set(allSermons.map(s => s[key]).filter(Boolean))] as string[];
    return {
      books: getUniqueOptions('book'),
      topics: getUniqueOptions('topic'),
      speakers: getUniqueOptions('speaker'),
      years: [],
      statuses: getUniqueOptions('status'),
      assignees: getUniqueOptions('assigned_to_name'),
    };
  }, [allSermons]);

  // --- Render Logic ---
  if (isLoading) {
    return <div className="text-center py-20">正在從 API 加載講道數據...</div>;
  }

  if (error) {
    return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
  }

  const { paginatedSermons, totalCount, hasNextPage, hasPrevPage } = filteredAndPaginatedData;

  return (
    <div className="flex flex-col lg:flex-row">
      <SermonSidebar options={filterOptions} />
      <main className="flex-1">
        <SermonSearchBar />
        <div className="mb-4 text-sm text-gray-600">
          共找到 <span className="font-bold">{totalCount}</span> 篇講道
        </div>
        {paginatedSermons.length > 0 ? (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
              {paginatedSermons.map((sermon) => (<SermonListItem key={sermon.id} sermon={sermon} />))}
            </div>
            <PaginationControls hasNextPage={hasNextPage} hasPrevPage={hasPrevPage} />
          </>
        ) : (
          <div className="text-center py-16 bg-white rounded-lg shadow-sm">
            <h3 className="text-xl font-semibold">沒有找到符合條件的講道</h3>
          </div>
        )}
      </main>
    </div>
  );
};