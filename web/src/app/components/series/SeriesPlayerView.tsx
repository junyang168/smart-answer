
// components/series/SeriesPlayerView.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import { useParams, useSearchParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PlayCircle, ListMusic, ChevronRight } from 'lucide-react';
import { SermonSeries } from '@/app/interfaces/article';
import { useSession } from 'next-auth/react';

export const SeriesPlayerView = () => {
    // --- State Management ---
  const [series, setSeries] = useState<SermonSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  // ✅ 1. 新增 state 來管理要點部分的折疊狀態
  const [isKeypointsOpen, setIsKeypointsOpen] = useState(false);

  // --- Get IDs from URL ---
  const params = useParams();
  const searchParams = useSearchParams();
  const seriesId = decodeURIComponent(params.seriesId as string);
  const currentSermonId = decodeURIComponent(searchParams.get('sermon') as string) || '';
  const { data: session } = useSession();

  const userRole = session?.user?.role;
  const isEditorRole = userRole === 'editor' || userRole === 'admin';
  const isEditor =
    isEditorRole || (process.env.NODE_ENV !== 'production');



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

  const handleGenerateMarkdown = async () => {
    if (!series) {
      return;
    }
    const fallbackUser = process.env.NODE_ENV !== 'production' ? 'junyang168@gmail.com' : '';
    const userId = session?.user?.internalId || session?.user?.email || fallbackUser;
    if (!userId) {
      setExportStatus('error');
      setExportMessage('請先登入以匯出 Markdown。');
      return;
    }
    setExportStatus('loading');
    setExportMessage(null);
    try {
      const response = await fetch(`/api/sc_api/series/${encodeURIComponent(series.id)}/markdown`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ user_id: userId }),
      });
      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || '匯出失敗');
      }
      const result = await response.json();
      setExportStatus('success');
      setExportMessage(`已匯出 ${result.sermonCount} 篇講道至 ${result.outputDir}`);
    } catch (err: any) {
      setExportStatus('error');
      setExportMessage(err.message || '匯出過程發生錯誤。');
    }
  };
  
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
        {isEditor ? (
          <div className="w-full rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900 lg:w-auto lg:min-w-[260px]">
            <p className="font-semibold mb-3">系列匯出工具</p>
            <button
              type="button"
              onClick={handleGenerateMarkdown}
              disabled={exportStatus === 'loading'}
              className="w-full rounded-md bg-blue-600 py-2 text-white font-semibold hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {exportStatus === 'loading' ? '生成中…' : '生成 Markdown'}
            </button>
            {exportMessage ? (
              <p
                className={`mt-3 text-xs ${
                  exportStatus === 'success' ? 'text-green-700' : 'text-red-600'
                }`}
              >
                {exportMessage}
              </p>
            ) : (
              <p className="mt-3 text-xs text-blue-700">
                產生後會將各講道匯出到 DATA_BASE_DIR/series/{series.id}
              </p>
            )}
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
