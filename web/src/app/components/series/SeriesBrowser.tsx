// components/series/SeriesBrowser.tsx
"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { CheckCircle, ListMusic } from 'lucide-react';
import { SermonSeries } from '@/app/interfaces/article';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// 新的、在此組件內部的系列區塊組件
const SeriesSection = ({ series }: { series: SermonSeries }) => {
  return (
    <section className="bg-white p-8 rounded-xl shadow-lg mb-12 border border-gray-100">
      <div className="border-b border-gray-200 pb-6 mb-6">
        <h2 className="text-3xl font-bold font-display text-gray-800">{series.title}</h2>

        {/* Topics Display */}
        {series.topics && (
          <div className="flex flex-wrap gap-2 mt-3 mb-2">
            {(Array.isArray(series.topics) ? series.topics : [series.topics]).map((topic, index) => (
              <span
                key={index}
                className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800"
              >
                {topic}
              </span>
            ))}
          </div>
        )}

        <h3 className="text-xl font-bold font-display text-gray-800">{series.id}</h3>
        {/* 使用 prose 來為 Markdown 內容應用樣式 */}
        <div className="mt-2 text-gray-600 leading-relaxed prose prose-slate max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{series.summary}</ReactMarkdown>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* 左側：要點和行動按鈕 */}
        <div>
          <Link href={`/resources/series/${series.id}`} className="inline-flex items-center justify-center bg-blue-600 text-white py-3 px-6 rounded-lg hover:bg-blue-700 transition-colors font-bold">
            <ListMusic className="w-5 h-5 mr-2" />
            <span>進入完整系列 ({series.sermons.length}篇)</span>
          </Link>
        </div>

      </div>
    </section>
  );
};


// 主客戶端組件
export const SeriesBrowser = () => {
  // --- State Management ---
  const [seriesList, setSeriesList] = useState<SermonSeries[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- Data Fetching ---
  useEffect(() => {
    const fetchSermonSeries = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const res = await fetch('/api/sc_api/sermon_series');
        if (!res.ok) {
          throw new Error(`API request failed with status ${res.status}`);
        }
        const allSeries: SermonSeries[] = await res.json();


        setSeriesList(allSeries);
      } catch (err: any) {
        setError(err.message || 'An unknown error occurred.');
      } finally {
        setIsLoading(false);
      }
    };

    fetchSermonSeries();
  }, []); // 空依賴數組確保只在客戶端首次掛載時運行

  // --- Render Logic ---
  if (isLoading) {
    return <div className="text-center py-20">正在從 API 加載講道系列...</div>;
  }

  if (error) {
    return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
  }

  if (seriesList.length === 0) {
    return <div className="text-center py-20">暫無講道系列。</div>;
  }

  return (
    <div>
      {seriesList.map(series => (
        <SeriesSection key={series.id} series={series} />
      ))}
    </div>
  );
};
