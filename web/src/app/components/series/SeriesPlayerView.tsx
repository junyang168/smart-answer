
// components/series/SeriesPlayerView.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PlayCircle, ListMusic } from 'lucide-react';
import { SermonSeries } from '@/app/interfaces/article';
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/app/components/hover-card';


export const SeriesPlayerView = () => {
  // --- State Management ---
  const [series, setSeries] = useState<SermonSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);



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

        let foundSeries = allSeries.find(s => s.id === seriesId) || null;
        if (foundSeries) {
          foundSeries.topics = [];
        }

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
      <div className="mb-8 flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex-1">
          <h1 className="text-4xl font-bold font-display text-gray-800">{series.title}</h1>
          <p className="mt-2 text-lg text-gray-600">{series.summary}</p>
          {series.keypoints && (
            <div className="mt-2">
              <HoverCard>
                <HoverCardTrigger asChild>
                  <button className="text-blue-600 hover:text-blue-800 underline text-sm font-medium cursor-pointer">
                    系列要點
                  </button>
                </HoverCardTrigger>
                <HoverCardContent className="w-96 max-h-[400px] overflow-y-auto">
                  <div className="prose prose-sm prose-slate max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{series.keypoints}</ReactMarkdown>
                  </div>
                </HoverCardContent>
              </HoverCard>
            </div>
          )}
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

      </div>


      <div className="flex flex-col lg:flex-row gap-8">

        {/* 右側播放列表 */}
        <aside className="lg:w-1/3">
          <div className="bg-gray-50 rounded-lg p-4 sticky top-24">
            <div className="flex items-center mb-4">
              <ListMusic className="w-6 h-6 mr-3 text-gray-700" />
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
                      <div className="text-gray-500 mt-1">{isActive ? <PlayCircle className="w-5 h-5 text-blue-600" /> : `${index + 1}`}</div>
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
          <div className="mt-8 pt-8 border-t border-gray-100">
            <h3 className="font-semibold text-lg text-gray-900 mb-4">講道要點</h3>
            <div className="prose prose-slate max-w-none text-gray-600">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeSermon.keypoints}</ReactMarkdown>
            </div>
          </div>
          {/* ✅ 新增：導航到詳情頁的按鈕 */}
          <div className="my-6">
            <Link
              href={`/resources/sermons/${activeSermon.item}`}
              className="inline-flex items-center justify-center rounded-md bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 transition-colors"
            >
              <span>觀看講道 →</span>
            </Link>
          </div>

        </div>
      </div>
    </>
  );
};
