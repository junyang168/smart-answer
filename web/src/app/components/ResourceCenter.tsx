// app/resources/page.tsx
"use client";

import { useState, useEffect } from 'react';
import type { Sermon } from '@/app/interfaces/article';


import ResourceCard from '@/app/components/resources/ResourceCard';
import LatestSermonCard from '@/app/components/resources/LatestSermonCard';

import { BookOpen, Mic, Users, Radio, Video } from 'lucide-react';

import { apiToUiSermon} from '@/app/utils/converter'
import { useSession } from 'next-auth/react';
import { hasEditorAccess } from '@/app/utils/roles';

// --- 模擬數據 (在真實應用中，這些數據應來自後端) ---



const resourceCardsData = [
  {
    icon: BookOpen,
    title: '按經文查經',
    description: '查找經文的研讀資料',
    link: '/demos/scripture-study.html',
    linkLabel: '選擇經文',
  },
  {
    icon: BookOpen,
    title: '王守仁教授聖經講論文庫',
    description: `聆聽王守仁教授的原聲講道，字幕同步，投影片列出要點與經文，幫助你聽懂每一段。也可閱讀整理成文的釋經文章。`,
    link: '/resources/wang-repository',
    linkLabel: '進入聖經講論文庫',
  },
  {
    icon: Users,
    title: '團契查經回顧',
    description: `回顧雙週團契查經的主題、學習重點與來源連結，也讓新朋友認識我們如何一起查考聖經。`,
    link: '/resources/fellowship',
    linkLabel: '查看回顧',
  },
  {
    icon: Mic,
    title: '講道中心',
    description: `同工利用 AI 整理、校對教授的音頻、視頻與筆記轉成的文字。`,
    link: '/resources/series',
    linkLabel: '進入講道中心',
    staffOnly: true, // 講道中心是同工的編輯入口（負責人 2026-11-06）
  },
  {
    icon: Video,
    title: '微講道',
    staffOnly: true,
    description: `用簡短、清晰的內容，幫助你理解神的話語，並在生活中經歷祂的同在。這是一個安靜、專注的靈修空間。`,
    link: '/resources/micro-sermon',
    linkLabel: '開始觀看',
  },
  {
    icon: Radio,
    title: '信仰的深度',
    staffOnly: true,
    description: `透過網播隨時隨地聆聽王守仁教授的聖經教導，讓信仰在日常生活中扎根、深化。`,
    link: '/resources/depth_of_faith',
    linkLabel: '開始聆聽',
  },
];


// --- 頁面組件本身 ---

export const ResourceCenter = () => {
  // --- State Management ---
  const [topSermons, setTopSermons] = useState<Sermon[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { data: session, status } = useSession();
  const isStaff = status === "authenticated" && hasEditorAccess(session?.user?.role);



  // --- Data Fetching ---
  useEffect(() => {
    if (!isStaff) return;
    // 這個 useEffect 只在組件首次掛載到瀏覽器時運行一次
    const fetchAllSermons = async () => {
      setIsLoading(true);
      setError(null);
      try {
        // ✅ fetch 在瀏覽器中運行，可以使用相對路徑或絕對路徑
        const res = await fetch('/api/sc_api/top_sermon_articles/2');
        const apiData = await res.json();
        const transformedSermons = apiData.sermons.map(apiToUiSermon);
        setTopSermons(transformedSermons);

      } catch (err: any) {
        setError(err.message || 'An unknown error occurred.');
      } finally {
        setIsLoading(false);
      }
    };

    fetchAllSermons();
  }, [isStaff]);


  return (
    <div className="bg-gray-50">
      <section className="bg-gray-50 py-5 sm:py-8">
        <div className={`container mx-auto px-6 ${isStaff ? "" : "max-w-4xl"}`}>
          <h1 className="sr-only">聖經學習資源</h1>
          <div className={`grid grid-cols-1 gap-6 text-left md:grid-cols-2 ${isStaff ? "lg:grid-cols-3" : ""}`}>
            {resourceCardsData.filter((card) => !card.staffOnly || isStaff).map((card) => (
              <ResourceCard
                key={card.title}
                icon={card.icon}
                title={card.title}
                description={card.description}
                link={card.link}
                linkLabel={card.linkLabel}
              />
            ))}
          </div>
        </div>
      </section>

      {isStaff && <section className="py-8 md:py-12">
        {/* 最新講道區 */}
        <div className="container mx-auto px-6 mb-16">
            <h2 className="text-3xl font-bold font-display text-center text-gray-800 mb-8">最新講道速覽</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                {topSermons.map(sermon => <LatestSermonCard key={sermon.title} sermon={sermon} />)}
            </div>
        </div>

      </section>}
    </div>
  );
};
