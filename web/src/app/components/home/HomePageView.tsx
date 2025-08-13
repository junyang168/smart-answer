// components/home/HomePageView.tsx
"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { ArrowRight, Film, Mic, PenSquare } from 'lucide-react';
import { Sermon, ArticleSeries, SermonSeries } from '@/app/interfaces/article'; // 假設類型已定義

// --- 模擬數據獲取 (在真實應用中替換為 API 調用) ---
async function fetchHomePageData() {
    // 假設 API 能返回最新的2篇講道、最新的2個文章系列和2個講道系列
    return {
        latestSermons: [
            { id: '1', title: '福音基礎 (一): 什麼是福音?', speaker: '王守仁 牧師', date: '2025-07-20', scripture: ['羅馬書 1:16-17'] },
            { id: '2', title: '家庭系列 (一): 基督化的家庭', speaker: '李長老', date: '2025-07-13', scripture: ['以弗所書 5:22-33'] },
        ] as Partial<Sermon>[],
        featuredArticleSeries: [
            { id: 'matthew-24-in-depth', title: '馬太福音24章深入研讀', summary: '詳細解釋“那行毀壞可憎的”以及信徒當如何應對。', articles: [] },
            { id: 'faith-lessons', title: '信心的功課', summary: '學習亞伯拉罕、摩西等信心偉人的榜樣。', articles: [] },
        ] as Partial<ArticleSeries>[],
        featuredSermonSeries: [
             { id: 'gospel-basics', title: '福音基礎', summary: '系統性地學習福音的核心真理。', sermons: [] },
             { id: 'family-series', title: '家庭系列', summary: '探討聖經中關於婚姻、親子關係的教導。', sermons: [] },
        ] as Partial<SermonSeries>[],
    };
}


export const HomePageView = () => {
    const [homeData, setHomeData] = useState<any>(null);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        fetchHomePageData().then(data => {
            setHomeData(data);
            setIsLoading(false);
        });
    }, []);

    if (isLoading) {
        return <div className="h-screen w-full bg-gray-200 animate-pulse"></div>;
    }

    return (
        <div>
            {/* 1. 英雄區 (Hero Section) */}
            <section className="relative h-[60vh] md:h-[70vh] flex items-center justify-center text-white text-center bg-gray-800">
                <Image src="/images/church.jpeg" alt="教會歡迎圖" layout="fill" objectFit="cover" className="opacity-40" />
                <div className="relative z-10 p-6">
                    <h1 className="text-4xl md:text-6xl font-bold font-serif !leading-tight">在真理中生根建造</h1>
                    <p className="mt-4 text-lg md:text-xl max-w-2xl">
                        達拉斯聖道教會 (Dallas Holy Logos Church) 歡迎您！
                    </p>
                    <Link href="/resources" className="mt-8 inline-block bg-[#D4AF37] text-gray-900 font-bold py-3 px-8 rounded-full hover:bg-opacity-90 transition-transform hover:scale-105">
                        探索神的真理
                    </Link>
                </div>
            </section>

            {/* 2. 歡迎與聚會信息 */}
            <section className="bg-white py-16 md:py-20">
                <div className="container mx-auto px-6 text-center max-w-4xl">
                    <h2 className="text-3xl font-bold font-display text-gray-800">歡迎來到我們的家</h2>
                    <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                        達拉斯聖道教會是一所華人基督教會。位于達拉斯地區。我們注重正確深度釋經。依靠神的恩典, 達拉斯聖道教會追求藉著深度, 準確釋經, 幫助弟兄姐妹們真明白, 遵行, 持守聖經真理, 並在愛的環境中訓練，造就他們成為主的門徒，以完成主的命令， 使萬民做祂的門徒.
                    </p>
                    <div className="mt-10 p-8 border-2 border-dashed border-gray-300 rounded-lg">
                        <h3 className="text-2xl font-bold">主日崇拜</h3>
                        <p className="mt-2 text-xl">每週日上午 11:00 - 12:30</p>
                        <p className="mt-2 text-gray-500">903 W. Parker Road, Plano, TX 75023</p>
                        <Link href="/new-here" className="mt-6 inline-flex items-center gap-2 bg-gray-800 text-white font-semibold py-3 px-6 rounded-lg hover:bg-gray-700">
                            新朋友指南 <ArrowRight className="w-5 h-5"/>
                        </Link>
                    </div>
                </div>
            </section>
            
            {/* 3. 異象與使命 */}
            <section className="bg-gray-50 py-16 md:py-20">
                <div className="container mx-auto px-6 text-center max-w-4xl">
                     <h2 className="text-3xl font-bold font-display text-gray-800">我們的異象</h2>
                     <p className="mt-4 text-xl text-gray-600 italic leading-relaxed">
                        “藉著深度、準確釋經，幫助弟兄姐妹們真明白、遵行、持守聖經真理，並在愛的環境中訓練，造就他們成為主的門徒。”
                     </p>
                </div>
            </section>

            {/* 4. 最新講道與文章 */}
            <section className="py-16 md:py-20">
                <div className="container mx-auto px-6">
                    <h2 className="text-3xl font-bold font-display text-center mb-10">最新動態</h2>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-5xl mx-auto">
                        {/* 最新講道 */}
                        {homeData?.latestSermons.map((sermon: Partial<Sermon>) => (
                            <Link key={sermon.id} href={`/resources/sermons/${sermon.id}`} className="block bg-white p-6 rounded-lg shadow-md hover:shadow-lg transition-shadow">
                                <div className="flex items-center text-sm text-gray-500 mb-2"><Mic className="w-4 h-4 mr-2"/> 講道</div>
                                <h3 className="font-bold text-xl mb-2">{sermon.title}</h3>
                                <p className="text-gray-600">{sermon.speaker} • {sermon.date}</p>
                            </Link>
                        ))}
                    </div>
                </div>
            </section>

            {/* 5. 系列推薦 */}
            <section className="bg-gray-800 text-white py-16 md:py-20">
                <div className="container mx-auto px-6">
                    <h2 className="text-3xl font-bold font-display text-center mb-12">探索我們的系列內容</h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-6xl mx-auto">
                        {/* 講道系列 */}
                        <div className="bg-gray-700 p-6 rounded-lg">
                            <h3 className="text-2xl font-bold mb-4 flex items-center gap-3"><Film/> 講道系列</h3>
                            <div className="space-y-3">
                                {homeData?.featuredSermonSeries.map((series: Partial<SermonSeries>) => (
                                    <Link key={series.id} href={`/resources/series/${series.id}`} className="block p-3 rounded-md hover:bg-gray-600">
                                        <p className="font-semibold">{series.title}</p>
                                    </Link>
                                ))}
                            </div>
                        </div>
                        {/* 文章系列 */}
                        <div className="bg-gray-700 p-6 rounded-lg">
                            <h3 className="text-2xl font-bold mb-4 flex items-center gap-3"><PenSquare/> 文章系列</h3>
                             <div className="space-y-3">
                                {homeData?.featuredArticleSeries.map((series: Partial<ArticleSeries>) => (
                                    <Link key={series.id} href={`/resources/articles`} className="block p-3 rounded-md hover:bg-gray-600">
                                        <p className="font-semibold">{series.title}</p>
                                    </Link>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            </section>



             {/* 7. 行動呼籲 */}
            <section className="bg-blue-50 py-16 md:py-20">
                <div className="container mx-auto px-6 text-center max-w-3xl">
                    <h2 className="text-3xl font-bold font-display text-gray-800">期待與您相遇</h2>
                    <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                        如果您有任何信仰上的問題，或需要任何幫助，請隨時與我們聯繫。我們期待著在教會見到您！
                    </p>
                    <Link href="/contact" className="mt-8 inline-block bg-blue-600 text-white font-bold py-3 px-8 rounded-full hover:bg-blue-700 transition-transform hover:scale-105">
                        聯絡我們
                    </Link>
                </div>
            </section>
        </div>
    );
};