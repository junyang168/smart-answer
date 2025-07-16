// components/sermons/SermonDetailView.tsx
"use client";
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { useState, useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Sermon} from '@/app/interfaces/article';
import { SermonDetailSidebar } from '@/app/components/sermons/SermonDetailSidebar';

export const SermonDetailView = () => {
  // --- State Management ---
  const [sermon, setSermon] = useState<Sermon | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- Get ID from URL ---
  const params = useParams();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;

  // --- Data Fetching ---
  useEffect(() => {
    if (!id) return; // 如果沒有 ID，則不執行任何操作

    const fetchSermon = async () => {
      setIsLoading(true);
      setError(null);
      
      // ✅ 使用您提供的新 API 端點
      const apiUrl = `/sc_api/final_sermon/junyang168@gmail.com/${id}`;

      try {
        const res = await fetch(apiUrl);
        if (!res.ok) {
          if (res.status === 404) {
             // 如果 API 明確返回 404，我們也認為是未找到
             throw new Error('404');
          }
          throw new Error(`API request failed with status ${res.status}`);
        }

        const data  = await res.json();
        const article : Sermon = {
            id: id,
            title: data.metadata.title,
            summary: data.metadata.summary,
            status: data.metadata.status,
            date: data.metadata.deliver_date,
            assigned_to_name: data.metadata.assigned_to_name,
            speaker: data.metadata.speaker || '王守仁 牧師',
            scripture: data.metadata.scripture || '',
            book: data.metadata.book || '',
            topic: data.metadata.topic || '',
            videoUrl: data.metadata.video_url || '',    
            audioUrl: data.metadata.audio_url || '',
            source: data.metadata.source || ''
        }


        const paragraphs = [];

        for (let i = 0; i < data.script.length; i++) {
            paragraphs.push(data.script[i].text);
        }

        article.markdownContent = paragraphs.join('\n\n');

        setSermon(article);

      } catch (err: any) {
        if (err.message === '404') {
          // 將 404 錯誤單獨處理，以便後續可以調用 notFound()
          setError('404');
        } else {
          setError(err.message || 'An unknown error occurred while fetching sermon data.');
        }
      } finally {
        setIsLoading(false);
      }
    };

    fetchSermon();
  }, [id]); // 依賴數組中放入 id，當 id 變化時會重新觸發 fetch

  // --- Render Logic ---
  if (isLoading) {
    return <div className="text-center py-20">正在加載講道詳情...</div>;
  }

  if (error === '404') {
    // 調用 notFound() 將會渲染 Next.js 內置的 404 頁面
    notFound();
    return null; // notFound() 會中斷渲染，但為類型安全返回 null
  }

  if (error) {
    return <div className="text-center py-20 text-red-500">加載失敗: {error}</div>;
  }

  if (!sermon) {
    return <div className="text-center py-20">未找到該篇講道。</div>;
  }

    const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '資源中心', href: '/resources' },
    { name: '講道中心', href: '/resources/sermons' },
    { name: sermon.title }, // 當前講道標題，沒有 href
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
      {/* 左側主內容區 */}
      <main className="lg:col-span-2">
        <Breadcrumb links={breadcrumbLinks} />
        <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900 mb-2">{sermon.title}</h1>
        <p className="text-gray-600 mb-6">{sermon.speaker} • {sermon.date} • {sermon.scripture}</p>
        <div className="aspect-w-16 aspect-h-9 mb-8 shadow-lg rounded-lg overflow-hidden">
            <iframe src={sermon.videoUrl} title={sermon.title} allowFullScreen className="w-full h-full"></iframe>
        </div>
        <article className="prose lg:prose-lg max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{sermon.markdownContent}</ReactMarkdown>
        </article>
      </main>

      {/* 右側邊欄 */}
      <SermonDetailSidebar sermon={sermon} />
    </div>
  );
};