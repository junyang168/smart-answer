// components/sermons/SermonDetailView.tsx
"use client";
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { useState, useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Sermon} from '@/app/interfaces/article';
import { BibleVerse} from '@/app/interfaces/article';

import { SermonDetailSidebar } from '@/app/components/sermons/SermonDetailSidebar';
import { FileText } from 'lucide-react';
import { useSession, signIn } from "next-auth/react"; // ✅ 引入 useSession 和 signIn
import { Lock } from 'lucide-react';

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
    if (status === "authenticated") {
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
    }
  }, [id,status]); // 依賴數組中放入 id，當 id 變化時會重新觸發 fetch

  if (status === "loading" || (isLoading && status === "authenticated")) {
    // 顯示加載中的條件：身份驗證中，或者已認證但在獲取數據中
    return <div className="text-center py-20">正在加載...</div>;
  }
  
  if (status === "unauthenticated") {
    // 如果用戶未登錄，顯示一個登錄提示界面
    return (
      <div className="text-center py-20 bg-gray-50 rounded-lg max-w-lg mx-auto">
        <Lock className="w-12 h-12 mx-auto text-gray-400 mb-4" />
        <h2 className="text-2xl font-bold mb-2">需要登錄</h2>
        <p className="text-gray-600 mb-6">此內容僅對已登錄用戶開放，請先登錄以繼續訪問。</p>
        <button
          onClick={() => signIn("google")}
          className="bg-blue-500 text-white font-semibold py-3 px-6 rounded-full hover:bg-blue-600 text-lg"
        >
          使用 Google 登錄
        </button>
      </div>
    );
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
        <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900 mb-2">{sermon.title}</h1>
        <p className="text-gray-600 mb-6">{sermon.speaker} • {sermon.date} </p>
        <div className="mb-8 shadow-lg rounded-lg overflow-hidden bg-gray-100 border">
          {sermon.videoUrl ? (
            // --- 如果有視頻，渲染視頻播放器 ---
            <video
              key={`${sermon.id}-video`} // 使用唯一的 key
              controls
              className="w-full h-auto bg-black"
            >
              <source src={sermon.videoUrl} type="video/mp4" />
              您的瀏覽器不支持 video 標籤。
            </video>
          ) : (
            // --- 如果沒有視頻，渲染音頻播放器作為主播放器 ---
            <div className="p-8 flex flex-col items-center justify-center text-center bg-gray-50">
                <h2 className="text-lg font-semibold text-gray-700 mb-4">本篇講道僅提供音頻格式</h2>
                <audio
                  key={`${sermon.id}-audio-main`} // 使用唯一的 key
                  controls
                  className="w-full max-w-md"
                >
                  <source src={sermon.audioUrl} type="audio/mpeg" />
                  您的瀏覽器不支持 audio 標籤。
                </audio>
            </div>
          )}
        </div>

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


        <article className="prose lg:prose-lg max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{sermon.markdownContent}</ReactMarkdown>
        </article>
      </main>

      {/* 右側邊欄 */}
      <SermonDetailSidebar sermon={sermon} />
    </div>
  );
};