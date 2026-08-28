// components/sermons/SermonDetailView.tsx
"use client";
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { useState, useEffect, useCallback } from 'react';
import { useParams, notFound, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';

import { Sermon, SermonSeries } from '@/app/interfaces/article';
import { BibleVerse } from '@/app/interfaces/article';

import { SermonDetailSidebar } from '@/app/components/sermons/SermonDetailSidebar';
import { FileText } from 'lucide-react';
import { useSession, signIn } from "next-auth/react"; // ✅ 引入 useSession 和 signIn
import { Lock } from 'lucide-react';
import { SermonMediaPlayer } from '@/app/components/sermons/SermonMediaPlayer';
import { SermonKeyPoints } from './SermonKeyPoints';
import type { SermonSlideDeck } from './SermonPptSlide';

export const SermonDetailView = () => {

  // --- State Management ---
  const [sermon, setSermon] = useState<Sermon | null>(null);
  const [seriesContext, setSeriesContext] = useState<SermonSeries | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [citationStartTime, setCitationStartTime] = useState<number | undefined>();
  const [citationNotice, setCitationNotice] = useState<string | null>(null);
  const [slideDeck, setSlideDeck] = useState<SermonSlideDeck | null>(null);

  // --- Get ID from URL ---
  const params = useParams();
  const searchParams = useSearchParams();
  const citationId = searchParams.get("citation");
  const rawId = params?.id;
  const id = typeof rawId === 'string'
    ? decodeURIComponent(rawId)
    : Array.isArray(rawId) && rawId[0]
      ? decodeURIComponent(rawId[0])
      : '';

  const { data: session, status: sessionStatus } = useSession(); // ✅ 獲取 session 狀態
  let status = sessionStatus;
  const userRole = session?.user?.role;
  let isEditor = userRole === "editor" || userRole === "admin";

  if (process.env.NODE_ENV !== 'production') {
    status = 'authenticated';
    isEditor = true;
  }

  const [copyStatus, setCopyStatus] = useState<"idle" | "success" | "error">("idle");

  const handleCopyMarkdown = useCallback(async (content?: string) => {
    if (!content) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setCopyStatus("success");
      setTimeout(() => setCopyStatus("idle"), 2000);
    } catch (error) {
      setCopyStatus("error");
      setTimeout(() => setCopyStatus("idle"), 2000);
    }
  }, []);


  // --- Data Fetching ---
  useEffect(() => {
    if (!id) return; // 如果沒有 ID，則不執行任何操作
    const fetchSermon = async () => {
      setIsLoading(true);
      setError(null);
      setSeriesContext(null);
      setSlideDeck(null);

      // ✅ 使用您提供的新 API 端點
      const apiUrl = `/api/sc_api/final_sermon/junyang168@gmail.com/${id}`;
      const slideRequest = fetch(
        `/api/sc_api/sermon_slides/junyang168@gmail.com/${encodeURIComponent(id)}`,
        { cache: "no-store" },
      ).catch(() => null);

      try {
        const res = await fetch(apiUrl);
        if (!res.ok) {
          if (res.status === 404) {
            // 如果 API 明確返回 404，我們也認為是未找到
            throw new Error('404');
          }
          throw new Error(`API request failed with status ${res.status}`);
        }

        const data = await res.json();
        const slideResponse = await slideRequest;
        if (slideResponse?.ok) {
          const deck = await slideResponse.json();
          if (Array.isArray(deck?.slides) && deck.slides.length > 0) {
            setSlideDeck(deck as SermonSlideDeck);
          }
        }

        const article: Sermon = {
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
          videoUrl: data.metadata.type == null || data.metadata.type != "audio" ? `/web/video/${id}.mp4` : null,
          audioUrl: data.metadata.type === "audio" ? `/web/video/${id}.mp3` : "",
          source: data.metadata.source || '',
          keypoints: data.metadata.keypoints || '',
          theme: data.metadata.theme || '',
          core_bible_verses: {},
          series_id: data.metadata.series_id,
          series_title: data.metadata.series_title,
          series_order: data.metadata.series_order,
          organization_mode: data.metadata.organization_mode,
          organization_mode_label: data.metadata.organization_mode_label,
          catalog_primary_passage: data.metadata.catalog_primary_passage,
          substantial_passages: data.metadata.substantial_passages || [],
          supporting_passages: data.metadata.supporting_passages || [],
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

        let markdownContent = paragraphs.join('\n\n');
        if (citationId) {
          const citationResponse = await fetch(`/api/canonical-repository/citations/${encodeURIComponent(citationId)}`);
          if (citationResponse.ok) {
            const citation = await citationResponse.json();
            if (citation.state === "valid" && citation.highlight_text) {
              const escapedHighlight = String(citation.highlight_text)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;");
              markdownContent = markdownContent.replace(
                citation.highlight_text,
                `<mark id="citation-highlight" class="rounded bg-amber-200 px-1">${escapedHighlight}</mark>`,
              );
              setCitationStartTime(citation.locator?.start_time ?? undefined);
              setCitationNotice("已定位到支持此單元的原始講道內容");
              window.setTimeout(() => document.getElementById("citation-highlight")?.scrollIntoView({ behavior: "smooth", block: "center" }), 150);
            } else {
              setCitationNotice(citation.message || "此引用需要重新確認，未自動高亮");
            }
          }
        }

        article.markdownContent = markdownContent;

        setSermon(article);

        if (article.series_id) {
          const seriesResponse = await fetch('/api/sc_api/sermon_series');
          if (seriesResponse.ok) {
            const allSeries: SermonSeries[] = await seriesResponse.json();
            const matchedSeries = allSeries.find(item => item.id === article.series_id) || null;
            setSeriesContext(matchedSeries);
            if (matchedSeries) {
              const matchedOrder = matchedSeries.sermons.findIndex(
                item => (item.item || item.id) === article.id,
              );
              setSermon(current => current ? {
                ...current,
                series_title: current.series_title || matchedSeries.title,
                series_order: current.series_order || (matchedOrder >= 0 ? matchedOrder + 1 : undefined),
              } : current);
            }
          }
        }

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
  }, [citationId, id, status]); // 依賴數組中放入 id，當 id 變化時會重新觸發 fetch

  if (isLoading) {
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

  const seriesSermons = seriesContext?.sermons ?? [];
  const currentSeriesIndex = seriesSermons.findIndex(item => (item.item || item.id) === sermon.id);
  const previousSermon = currentSeriesIndex > 0 ? seriesSermons[currentSeriesIndex - 1] : null;
  const nextSermon = currentSeriesIndex >= 0 && currentSeriesIndex < seriesSermons.length - 1
    ? seriesSermons[currentSeriesIndex + 1]
    : null;
  const seriesHref = sermon.series_id
    ? `/resources/series/${encodeURIComponent(sermon.series_id)}?sermon=${encodeURIComponent(sermon.id)}`
    : undefined;

  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: 'AI 輔助查經', href: '/resources' },
    { name: '講道中心', href: '/resources/sermons' },
    ...(seriesHref ? [{ name: sermon.series_title || '講道系列', href: seriesHref }] : []),
    { name: sermon.title }, // 當前講道標題，沒有 href
  ];

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
      {/* 左側主內容區 */}
      <main className="lg:col-span-2">
        <Breadcrumb links={breadcrumbLinks} />
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900">{sermon.title}</h1>
          {isEditor ? (
            <div className="flex items-center gap-2">
              <Link
                href={`/admin/surmons/${encodeURIComponent(id)}`}
                className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-blue-700 border border-blue-200 rounded-md bg-blue-50 hover:bg-blue-100"
              >
                編輯
              </Link>
              <button
                type="button"
                onClick={() => handleCopyMarkdown(sermon.markdownContent)}
                className="inline-flex items-center rounded-md border border-blue-200 px-3 py-1.5 text-sm text-blue-700 transition hover:bg-blue-50"
              >
                {copyStatus === "success"
                  ? "已複製"
                  : copyStatus === "error"
                    ? "複製失敗"
                    : "複製 Markdown"}
              </button>
            </div>
          ) : null}
        </div>
        <p className="text-gray-600 mb-6">{sermon.speaker} • {sermon.date} ｜ 认领人：{sermon.assigned_to_name}</p>

        {sermon.summary && (
          <div className="mb-8 rounded-lg border border-slate-200 bg-slate-50 p-6">
            <div className="mb-3 flex items-center">
              <FileText className="mr-3 h-6 w-6 text-slate-600" />
              <h2 className="font-display text-xl font-bold text-slate-800">內容摘要</h2>
            </div>
            <p className="leading-relaxed text-slate-700">
              {sermon.summary}
            </p>
          </div>
        )}

        {citationNotice ? (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {citationNotice}
          </div>
        ) : null}
        <SermonMediaPlayer
          sermon={sermon}
          authenticated={status === "authenticated"}
          startTime={citationStartTime}
          slideDeck={slideDeck}
        />

        {seriesHref ? (
          <nav className="mb-6 rounded-xl border border-indigo-100 bg-indigo-50 px-4 py-4" aria-label="講道系列導航">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-xs font-bold tracking-wide text-indigo-600">原始講課次序</div>
                <Link href={seriesHref} className="mt-1 block font-bold text-indigo-950 hover:underline">
                  {sermon.series_title || seriesContext?.title || '查看完整系列'}
                  {currentSeriesIndex >= 0 ? ` · 第 ${currentSeriesIndex + 1} 講／共 ${seriesSermons.length} 講` : ''}
                </Link>
              </div>
              <div className="flex flex-wrap gap-2 text-sm font-semibold">
                {previousSermon ? (
                  <Link
                    href={`/resources/sermons/${encodeURIComponent(previousSermon.item || previousSermon.id)}`}
                    className="rounded-lg border border-indigo-200 bg-white px-3 py-2 text-indigo-700 hover:bg-indigo-100"
                  >
                    ← 上一講
                  </Link>
                ) : null}
                {nextSermon ? (
                  <Link
                    href={`/resources/sermons/${encodeURIComponent(nextSermon.item || nextSermon.id)}`}
                    className="rounded-lg border border-indigo-200 bg-white px-3 py-2 text-indigo-700 hover:bg-indigo-100"
                  >
                    下一講 →
                  </Link>
                ) : null}
              </div>
            </div>
          </nav>
        ) : null}

        {status === "authenticated" ? (
          <details className="rounded-xl border border-slate-200 bg-white px-5 py-4">
            <summary className="cursor-pointer select-none font-semibold text-slate-700">
              展開完整逐字稿（核對用）
            </summary>
            <article className="prose mt-6 max-w-none lg:prose-lg">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>{sermon.markdownContent}</ReactMarkdown>
            </article>
          </details>
        ) : (
          <SermonKeyPoints sermon={sermon} />
        )
        }
      </main>

      {/* 右側邊欄 */}
      <SermonDetailSidebar
        sermon={sermon}
        authenticated={status === "authenticated"}
        canReviewRepository={isEditor}
      />
    </div>
  );
};
