
// components/series/SeriesPlayerView.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PlayCircle, ListMusic, ChevronRight } from 'lucide-react';
import { SermonSeries,  Sermon } from '@/app/interfaces/article';

export const SeriesPlayerView = () => {
    // --- State Management ---
  const [series, setSeries] = useState<SermonSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // ✅ 1. 新增 state 來管理要點部分的折疊狀態
  const [isKeypointsOpen, setIsKeypointsOpen] = useState(false);

  // --- Get IDs from URL ---
  const params = useParams();
  const searchParams = useSearchParams();
  const seriesId = decodeURIComponent(params.seriesId as string);
  const currentSermonId = decodeURIComponent(searchParams.get('sermon') as string) || '';



  // --- Data Fetching ---
  useEffect(() => {
    if (!seriesId) return;

    const fetchSeriesData = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const res = await fetch('/api/sc_api/sermon_series');
        if (!res.ok) {
          throw new Error(`API request failed with status ${res.status}`);
        }
        const allSeries: SermonSeries[] = await res.json();

        const foundSeries = allSeries.find(s => s.id === seriesId) || null;

        if (foundSeries) {
          setSeries(foundSeries);
        } else {
          // 如果循環結束都沒找到，拋出 404 錯誤
          throw new Error('404');
        }

      } catch (err: any) {
        setError(err.message || 'An unknown error occurred.');
      } finally {
        setIsLoading(false);
      }
    };

    fetchSeriesData();
  }, [seriesId]); // 依賴 seriesId，如果 URL 的 seriesId 變化，會重新獲取數據

  // --- Derived State (計算當前播放的講道) ---
  const activeSermon = useMemo(() => {
    if (!series) return null;
    return series.sermons.find(s => s.item === currentSermonId) || series.sermons[0];
  }, [currentSermonId, series]);

  const topics = useMemo(() => {
    if (!series || !series.topics) {
      return [] as string[];
    }
    if (Array.isArray(series.topics)) {
      return series.topics;
    }
    return series.topics
      .split(',')
      .map((topic) => topic.trim())
      .filter(Boolean);
  }, [series]);
  
  // --- Render Logic ---
  if (isLoading) {
    return <div className="text-center py-20">正在加載系列數據...</div>;
  }

  if (error === '404') {
    notFound();
    return null;
  }

  if (error) {
    return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
  }

  if (!series || !activeSermon) {
    return <div className="text-center py-20">系列或講道數據不存在。</div>;
  }

  return (
    <>
      <div className="mb-8">
        <h1 className="text-4xl font-bold font-display text-gray-800">{series.title}</h1>
        <p className="mt-2 text-lg text-gray-600">{series.summary}</p>
        {topics.length ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {topics.map((topic) => (
              <span
                key={topic}
                className="inline-flex items-center rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700"
              >
                #{topic}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {/* ✅ 新增：系列要點區域 */}
      {series.keypoints  && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 mb-8 shadow-sm">
          <h3 className="font-bold text-xl text-gray-800 mb-4">本系列要點</h3>
            <div className="prose prose-slate max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{series.keypoints}</ReactMarkdown>
            </div>
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-8">

        {/* 右側播放列表 */}
        <aside className="lg:w-1/3">
        <div className="bg-gray-50 rounded-lg p-4 sticky top-24">
          <div className="flex items-center mb-4">
            <ListMusic className="w-6 h-6 mr-3 text-gray-700"/>
            <div>
              <p className="text-xs text-gray-500">系列</p>
              <h3 className="font-bold text-lg">{series.title}</h3>
            </div>
          </div>
          <ul className="space-y-2 max-h-[70vh] overflow-y-auto">
            {series.sermons.map((sermon, index) => {
              const isActive = sermon.item === activeSermon.item;
              return (
                <li key={sermon.id}>
                  <Link href={`/resources/series/${series.id}?sermon=${sermon.item}`} className={`flex items-start gap-3 p-3 rounded-md transition-colors ${isActive ? 'bg-blue-100' : 'hover:bg-gray-200'}`}>
                    <div className="text-gray-500 mt-1">{isActive ? <PlayCircle className="w-5 h-5 text-blue-600"/> : `${index + 1}`}</div>
                    <div>
                      <p className={`font-semibold ${isActive ? 'text-blue-800' : 'text-gray-800'}`}>{sermon.title}</p>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>        
        </aside>

        {/* 左側主內容區 */}
        <div className="lg:w-2/3">
            <article className="prose lg:prose-lg max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeSermon.summary}</ReactMarkdown>
            </article>        
            <div className="bg-white border border-gray-200 rounded-lg p-6 mb-8 shadow-sm">
                {/*             👇 2. 標題變成一個可點擊的 <button>  */}
                <button
                    onClick={() => setIsKeypointsOpen(!isKeypointsOpen)}
                    className="w-full flex justify-between items-center p-6 text-left"
                    aria-expanded={isKeypointsOpen}
                    aria-controls="keypoints-content"
                >
                    <h3 className="font-bold text-xl text-gray-800">講道要點</h3>
                    <ChevronRight
                    className={`w-6 h-6 text-gray-500 transition-transform duration-300 ${isKeypointsOpen ? 'rotate-90' : 'rotate-0'}`}
                    />
                </button>
                {isKeypointsOpen && (
                    <div id="keypoints-content" className="prose prose-slate max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeSermon.keypoints}</ReactMarkdown>
                    </div>
                )}
            </div>
            {/* ✅ 新增：導航到詳情頁的按鈕 */}
            <div className="my-6">
                <Link
                href={`/resources/sermons/${activeSermon.item}`}
                className="inline-flex items-center gap-2 bg-slate-100 text-slate-700 font-semibold py-2 px-4 rounded-lg hover:bg-slate-200 transition-colors text-sm"
                >
                    <div className="mt-4 flex items-center gap-4 text-sm font-bold text-[#8B4513]">
                        <span>觀看講道 →</span>
                    </div>
                </Link>
            </div>

            </div>        
      </div>
    </>
  );
};
