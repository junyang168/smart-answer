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
import { BrainCircuit } from 'lucide-react';
import { useSession } from "next-auth/react";

export const SermonBrowser = () => {
  // --- State Management ---
  const [allSermons, setAllSermons] = useState<Sermon[]>([]);
  const [isLoadingInitialData, setIsLoadingInitialData] = useState(true);
  const [initialError, setInitialError] = useState<string | null>(null);

  const [isSearching, setIsSearching] = useState(false);
  const [searchResultIds, setSearchResultIds] = useState<string[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const query = searchParams.get('q') || '';
  const { data: session, status } = useSession(); // ✅ 獲取 session 狀態
  const authenticated = status === 'authenticated';

  // --- Data Fetching ---
  useEffect(() => {
    // 這個 useEffect 只在組件首次掛載到瀏覽器時運行一次
    const fetchAllSermons = async () => {
      setIsLoadingInitialData(true);      
      try {
        // ✅ fetch 在瀏覽器中運行，可以使用相對路徑或絕對路徑
        const transformedSermons : Sermon[] = await fetchSermons();
        transformedSermons.sort((a, b) =>  new Date(a.date).getTime() - new Date(b.date).getTime());
        setAllSermons(transformedSermons);
      } catch (err: any) {
        setInitialError('无法加载讲道列表，请稍后刷新重试。' + err.message);
      } finally {
        setIsLoadingInitialData(false);
      }
    };

    console.debug('here')

    fetchAllSermons();
  }, []); // 空依賴數組確保只運行一次

  // 逻辑与之前相同，只要有 query 就触发
  useEffect(() => {
      if (!query) {
          setSearchResultIds([]);
          setIsSearching(false);
          return;
      }

      const triggerSearch = async () => {
          setIsSearching(true);
          setSearchError(null);
          try {
              const response = await fetch(`/sc_api/quick_search/${encodeURIComponent(query)}`);
              if (!response.ok) throw new Error('AI 搜索服务暂时不可用。');
              const ids: string[] = await response.json();
              setSearchResultIds(ids);
          } catch (err: any) {
              setSearchError(err.message);
              setSearchResultIds([]);
          } finally {
              setIsSearching(false);
          }
      };
      triggerSearch();

  }, [query]);


  // ✅ 1. 更新計數邏輯以處理數組
  const filterOptions = useMemo(() => {
    if (allSermons.length === 0) return {};
    
    const getOptionsWithCounts = (key: keyof Sermon, isArray: boolean = false) => {
        const counts = new Map<string, number>();
        for (const sermon of allSermons) {
            let value = sermon[key];
            if(key == 'source') {
              value = value ? '公開' : '聖道教會';
            }

            if (isArray && Array.isArray(value)) {
                // 如果是數組，遍歷數組中的每一項
                for (const item of value) {
                    if (item) counts.set(item, (counts.get(item) || 0) + 1);
                }
            } else if (typeof value === 'string' && value) {
                // 如果是字符串，直接計數
                counts.set(value, (counts.get(value) || 0) + 1);
            }
        }
        return Array.from(counts.entries())
            .map(([value, count]) => ({ value, count }))
            .sort((a, b) => a.value.localeCompare(b.value));
    };

    
    return  {
        books: getOptionsWithCounts('book', true), // 標記 book 為數組
        topics: getOptionsWithCounts('topic', true), // 標記 topic 為數組
        speakers: getOptionsWithCounts('speaker'),
//        years: [...new Set(allSermons.map(s => s.date.substring(0, 4)).filter(Boolean))].map(y => ({value: y, count: allSermons.filter(s => s.date.startsWith(y)).length})).sort((a,b)=>b.value.localeCompare(a.value)),
        statuses: authenticated ? getOptionsWithCounts('status') : [],
        assignees: authenticated ? getOptionsWithCounts('assigned_to_name') : [],
        source: getOptionsWithCounts('source')
    };
  }, [allSermons]);

  // ✅ 2. 在客戶端進行篩選和分頁 (保持不變)
  const filteredAndPaginatedData = useMemo(() => {
    let filtered = [...allSermons];
    const q = searchParams.get('q');
    const rawQuery = searchParams.get('q');
    const query = rawQuery ? decodeURIComponent(rawQuery).toLowerCase() : null;
    const speaker = searchParams.get('speaker');
    const book = searchParams.get('book');
    const topic = searchParams.get('topic');
    const year = searchParams.get('year');
    const status = searchParams.get('status');
    const assignee = searchParams.get('assignee');
    const page = Number(searchParams.get('page') ?? '1');
    const source = searchParams.get('source');
    const limit = 12;

    if (query) {
      const idSet = new Set(searchResultIds);
      filtered = filtered.filter(sermon => idSet.has(sermon.id));
    }

    if (book) { filtered = filtered.filter(s => s.book.includes(book)); }
    if (topic) { filtered = filtered.filter(s => s.topic.includes(topic)); }    
    
    if (speaker) { filtered = filtered.filter(s => s.speaker === speaker); }
    if (year) { filtered = filtered.filter(s => s.date.startsWith(year)); }
    if (status) { filtered = filtered.filter(s => s.status === status); }
    if (assignee) { filtered = filtered.filter(s => s.assigned_to_name === assignee); }
    if (source) { filtered = filtered.filter(s => (s.source ? '公開' : '聖道教會') === source); }

    const totalCount = filtered.length;
    const startIndex = (page - 1) * limit;
    const endIndex = page * limit;
    
    return {
      paginatedSermons: filtered.slice(startIndex, endIndex),
      totalCount,
      hasNextPage: endIndex < totalCount,
      hasPrevPage: startIndex > 0,
    };
  }, [allSermons, searchParams, searchResultIds, query]);


  // --- Render Logic ---
    if (isLoadingInitialData) {
        return <div className="text-center py-20">正在初始化讲道中心...</div>;
    }

    if (initialError) {
        return <div className="text-center py-20 text-red-50innerHTML">{initialError}</div>;
    }

    if(isSearching) {
      return (
            // 1. 如果正在搜索，只显示加载状态
            <div className="text-center p-8 bg-gray-50 rounded-lg">
                <div className="flex justify-center items-center gap-2">
                    <BrainCircuit className="w-6 h-6 text-blue-600 animate-pulse" />
                    <p className="font-semibold text-blue-700">正在进行 AI 搜索...</p>
                </div>
            </div>        
      )
    }

    if(searchError) {
      return (
          <div className="text-center p-8 bg-red-50 text-red-700 rounded-lg">{searchError}</div>
      );
    }

  const { paginatedSermons, totalCount, hasNextPage, hasPrevPage } = filteredAndPaginatedData;

  return (
    <div className="flex flex-col lg:flex-row">
      <SermonSidebar options={filterOptions} />
      <main className="flex-1">
        <SermonSearchBar isSearching={isSearching} />
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