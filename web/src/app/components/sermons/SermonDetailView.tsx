// components/sermons/SermonDetailView.tsx
"use client";
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { useState, useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Sermon} from '@/app/interfaces/article';
import { BibleVerse} from '@/app/interfaces/article';

import { SermonDetailSidebar } from '@/app/components/sermons/SermonDetailSidebar';
import { FileText } from 'lucide-react';
import { useSession, signIn } from "next-auth/react"; // ✅ 引入 useSession 和 signIn
import { Lock } from 'lucide-react';
import { SermonMediaPlayer } from '@/app/components/sermons/SermonMediaPlayer';
import { SermonKeyPoints } from './SermonKeyPoints';

export const SermonDetailView = () => {

  // --- State Management ---
  const [sermon, setSermon] = useState<Sermon | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- Get ID from URL ---
  const params = useParams();
  const id = decodeURIComponent(Array.isArray(params.id) ? params.id[0] : params.id);

  const { data: session, status } = useSession(); // ✅ 獲取 session 狀態


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
            speaker: data.metadata.speaker || '王守仁',
            scripture: [], // 將所有經文合併為一個字符串
            book: data.metadata.book || '',
            topic: data.metadata.topic || '',
            videoUrl:  data.metadata.type == null || data.metadata.type != "audio" ? `/web/video/${id}.mp4` : null, 
            audioUrl:  data.metadata.type === "audio" ? `/web/video/${id}.mp3` : "",
            source: data.metadata.source || '',
            keypoints: data.metadata.keypoints || '',
            theme: data.metadata.theme || '',
            core_bible_verses: {},
        }

        if (data.metadata && data.metadata.core_bible_verse) {
            data.metadata.core_bible_verse.map((book_verse: BibleVerse) => {
                const key = `${book_verse.book} ${book_verse.chapter_verse}`;
                article.scripture.push(key);
                if (book_verse.text) {
                    article.core_bible_verses![key] = book_verse.text;
                }
            });
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
  }, [id,status]); // 依賴數組中放入 id，當 id 變化時會重新觸發 fetch

  if ( isLoading  ) {
    // 顯示加載中的條件：身份驗證中，或者已認證但在獲取數據中
    return <div className="text-center py-20">正在加載...</div>;
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
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '講道中心', href: '/resources/sermons' },
    { name: sermon.title }, // 當前講道標題，沒有 href
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
      {/* 左側主內容區 */}
      <main className="lg:col-span-2">
        <Breadcrumb links={breadcrumbLinks} />
        <div className="mb-3 flex items-center gap-3">
          <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900">{sermon.title}</h1>
          {status === "authenticated" && (
            <Link
              href={`/admin/surmons/${encodeURIComponent(id)}`}
              className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-blue-700 border border-blue-200 rounded-md bg-blue-50 hover:bg-blue-100"
            >
              編輯
            </Link>
          )}
        </div>
        <p className="text-gray-600 mb-6">{sermon.speaker} • {sermon.date} ｜ 认领人：{sermon.assigned_to_name}</p>

        <SermonMediaPlayer sermon={sermon} authenticated={status === "authenticated"} />

                {/* ✅ 新增的講道摘要區域 */}
        {sermon.summary && (
          <div className="bg-slate-50 border border-slate-200 rounded-lg p-6 mb-8">
            <div className="flex items-center mb-3">
              <FileText className="w-6 h-6 text-slate-600 mr-3" />
              <h2 className="text-xl font-bold font-display text-slate-800">內容摘要</h2>
            </div>
            <p className="text-slate-700 leading-relaxed">
              {sermon.summary}
            </p>
          </div>
        )}


        {status === "authenticated" ? (
          <article className="prose lg:prose-lg max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{sermon.markdownContent}</ReactMarkdown>
          </article>
        ) : (
          <SermonKeyPoints sermon={sermon} />
        )
         }
      </main>

      {/* 右側邊欄 */}
      <SermonDetailSidebar sermon={sermon} authenticated={status === "authenticated"} />
    </div>
  );
};
