// app/resources/page.tsx
"use client";

import { useState, useEffect, useMemo } from 'react';
import { FaithQA, Sermon } from '@/app/interfaces/article';


import ResourceCard from '@/app/components/resources/ResourceCard';
import LatestSermonCard from '@/app/components/resources/LatestSermonCard';
import FeaturedPostItem from '@/app/components/resources/FeaturedPostItem';

import { BookOpen, BrainCircuit, Mic, FileSignature, Users, MessageCircleQuestion, Search, ArrowRight,ChevronRight } from 'lucide-react';

import type { NextPage } from 'next';
import { apiToUiSermon} from '@/app/utils/converter'
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

// --- 模擬數據 (在真實應用中，這些數據應來自後端) ---



const resourceCardsData = [
  {
    icon: Mic,
    title: '講道中心',
    description: `所有講道都配備了由 AI 生成並經同工校對的**簡介、要點和完整文字稿**。您可以像搜索文章一樣， 精準定位任何講道內容。`,
    link: '/resources/sermons',
    linkLabel: '進入講道中心',
  },
  {
    icon: BookOpen,
    title: '團契分享',
    description: `我們的文章源自**團契查經的講稿**。在經過弟兄姐妹們的熱烈討論後，其精華內容再由 AI 輔助潤色成稿。`,
    link: '/resources/articles',
    linkLabel: '瀏覽所有分享',
  },
  {
    icon: MessageCircleQuestion,
    title: '信仰問答',
    description: `這裡的每一個問題，都源自**弟兄姐妹在團契查經中的真實探討**。我們利用 AI 技術將這些寶貴的討論整理、潤色，形成了一份真實、貼近生活的問答集。`,
    link: '/resources/qa',
    linkLabel: '尋找答案',
  },
];


// --- 頁面組件本身 ---

export const ResourceCenter = () => {
  // --- State Management ---
  const [topSermons, setTopSermons] = useState<Sermon[]>([]);
  const [topArticles, setTopArticles] = useState<any[]>([]);
  const [topQAs, setTopQAs] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);



  // --- Data Fetching ---
  useEffect(() => {
    // 這個 useEffect 只在組件首次掛載到瀏覽器時運行一次
    const fetchAllSermons = async () => {
      setIsLoading(true);
      setError(null);
      try {
        // ✅ fetch 在瀏覽器中運行，可以使用相對路徑或絕對路徑
        const res = await fetch('sc_api/top_sermon_articles/2');
        const apiData = await res.json();
        const transformedSermons = apiData.sermons.map(apiToUiSermon);
        setTopSermons(transformedSermons);
        const transformedArticles = apiData.articles.map((article: any) => ({
          title: article.title,
          category: '團契講稿',
          date: article.deliver_date,
          link: `/resources/articles/${article.item}`,
          author: article.author_name || '',
        }));
        setTopArticles(transformedArticles);

        const topQAS = apiData.qas.map((qa: any) => ({
          title: qa.question,
          category: '',
          date : qa.date_asked,
          link: `/resources/qa/${qa.id}`,
          author: qa.author_name || '',
        }));
        setTopQAs(topQAS);

      } catch (err: any) {
        setError(err.message || 'An unknown error occurred.');
      } finally {
        setIsLoading(false);
      }
    };

    fetchAllSermons();
  }, []); // 空依賴數組確保只運行一次


  return (
    <div className="bg-gray-50">
      {/* 1. 英雄區 */}
      <section className="relative bg-gray-900 text-white py-20 md:py-32 text-center overflow-hidden">
        <Image src="/images/ai-background.jpeg" alt="資源庫背景" layout="fill" objectFit="cover" className="opacity-30" />
        <div className="container mx-auto px-6 relative z-10">
          <h1 className="text-4xl md:text-5xl font-bold">探索真理的寶庫</h1>
      {/* ✅ 添加了与事工相关的介绍 */}
      <p className="mt-4 text-lg md:text-xl max-w-3xl mx-auto">
        我們致力於使用 AI 人工智能技術，將資深神學教育家
        <Link 
            href="/about/pastor-profile" 
            target="_blank" 
            className="text-[#FBBF24] font-semibold underline decoration-yellow-400/70 underline-offset-2 hover:decoration-yellow-400 transition-all mx-1"
        >
            王守仁教授
        </Link>
        歷年忠於聖經的深度教導，轉化為您眼前这些可供探索、搜索和學習的寶貴屬靈資源。
      </p>
      <div className="mt-8">
          <Link 
              href="/ministries" // 链接到事工介绍页面
              className="inline-block bg-white text-gray-800 font-bold py-3 px-8 rounded-full hover:bg-gray-200 transition-transform hover:scale-105 shadow-lg"
          >
              事工介紹
          </Link>
      </div>      
        </div>
      </section>

      <section className="bg-gray-50 py-8 md:py-12">
        <div className="container mx-auto px-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-8 text-left">
            {resourceCardsData.map((card) => (
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

      <section className="py-8 md:py-12">

        {/* 最新講道區 */}
        <div className="container mx-auto px-6 mb-16">
            <h2 className="text-3xl font-bold font-display text-center text-gray-800 mb-8">最新講道速覽</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                {topSermons.map(sermon => <LatestSermonCard key={sermon.title} sermon={sermon} />)}
            </div>
        </div>

        {/* 精選文章與問答區 */}
        <div className="container mx-auto px-6 mb-16">
            <h2 className="text-3xl font-bold font-display text-center text-gray-800 mb-8">最新文章與問答</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-12 max-w-5xl mx-auto">
                {/* 文章欄 */}
                <div className="bg-white p-6 rounded-lg shadow-sm">
                    <h3 className="text-xl font-bold mb-4">文章</h3>
                    <ul className="list-none p-0">
                        {topArticles.map(post => <FeaturedPostItem key={post.title} { ... post } />)}
                    </ul>
                </div>
                {/* 問答欄 */}
                <div className="bg-white p-6 rounded-lg shadow-sm">
                    <h3 className="text-xl font-bold mb-4">問答</h3>
                    <ul className="list-none p-0">
                        {topQAs.map(post => <FeaturedPostItem key={post.title} {...post} />)}
                    </ul>
                </div>
            </div>
        </div>
      
      </section>
      
    </div>
  );
};

