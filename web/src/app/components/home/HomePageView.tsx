// components/home/HomePageView.tsx
"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { ArrowRight, Film, Mic, PenSquare,Search, BrainCircuit,Users, Church  } from 'lucide-react';

// --- 模擬數據獲取 (在真實應用中替換為 API 調用) ---
async function fetchHomePageData() {
    // 假設 API 能返回最新的2篇講道、最新的2個文章系列和2個講道系列
    const response = await fetch('/api/sc_api/fellowship');
    const data = await response.json();
    return data;
}


export const HomePageView = () => {
    const [fellowshipData, setFellowshipData] = useState<any>('');
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        fetchHomePageData().then(data => {
            setFellowshipData(data);
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
                    <h1 className="text-4xl md:text-6xl font-bold !leading-tight">在真理中生根建造</h1>
                    <p className="mt-4 text-lg md:text-xl max-w-2xl">
                        達拉斯聖道教會 (Dallas Holy Logos Church) 歡迎您！
                    </p>
                    <Link href="/resources" className="mt-8 inline-block bg-[#D4AF37] text-gray-900 font-bold py-3 px-8 rounded-full hover:bg-opacity-90 transition-transform hover:scale-105">
                        探索神的真理
                    </Link>
                </div>
            </section>

            {/* 2. 欢迎与核心聚会 */}
            <section className="bg-white py-16 md:py-20">
                <div className="container mx-auto px-6">
                    <div className="text-center max-w-4xl mx-auto">
                        <h2 className="text-3xl font-bold font-display text-gray-800">歡迎來到我們的家</h2>
                        <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                            <b>達拉斯聖道教會</b>是一所華人基督教會。位于達拉斯地區。我們注重依靠神的恩典,正確深入地理解遵行聖經。無論您是初次到訪還是尋求歸屬，我們都誠摯地邀請您參與我們的核心聚會。
                        </p>
                    </div>

                    {/* ✅ 新的并列卡片布局 */}
                    <div className="mt-12 max-w-5xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-8">

                        {/* 卡片一：主日崇拜 */}
                        <div className="bg-gray-50 border border-gray-200 rounded-lg p-8 text-center flex flex-col">
                            <div className="mx-auto bg-gray-200 p-4 rounded-full mb-4">
                                <Church className="w-8 h-8 text-gray-700" />
                            </div>
                            <h3 className="text-2xl font-bold">主日崇拜</h3>
                            <p className="mt-2 text-lg font-semibold">每週日上午 11:00 CST</p>
                            <p className="text-gray-500 mt-1">903 W. Parker Road, Plano, TX 75023</p>
                            <p className="mt-4 text-gray-600 flex-grow">
                                與眾聖徒一同敬拜讚美，聆聽忠於聖經的深度信息，領受從神而來的恩典與力量。
                            </p>
                            <div className="mt-6">
                                <Link href="/contact" className="inline-flex items-center gap-2 bg-gray-800 text-white font-semibold py-2 px-5 rounded-lg hover:bg-gray-700">
                                    新朋友指南
                                </Link>
                            </div>
                        </div>

                        {/* 卡片二：周末团契 */}
                        <div className="bg-gray-50 border border-gray-200 rounded-lg p-8 text-center flex flex-col">
                            <div className="mx-auto bg-gray-200 p-4 rounded-full mb-4">
                                <Users className="w-8 h-8 text-gray-700" />
                            </div>
                            <h3 className="text-2xl font-bold">团契</h3>
                            <p className="mt-2 text-lg font-semibold">每兩週一次 週五晚 8:00 CST</p>
                            <p className="text-gray-500 mt-1">下一次團契時間{fellowshipData.date}</p>
                            <p className="mt-4 text-gray-600 flex-grow">
                                與弟兄姊妹們在線上，線下深入查经、分享生活、彼此代祷。这里是建立真实关系、经历生命同行的温馨家园。
                            </p>
                            <div className="mt-6">
                                <Link href="/contact" className="inline-flex items-center gap-2 bg-gray-800 text-white font-semibold py-2 px-5 rounded-lg hover:bg-gray-700">
                                    了解团契详情
                                </Link>
                            </div>
                        </div>

                    </div>
                </div>
            </section>
            
            {/* 3. 異象與使命 */}
            <section className="bg-gray-50 py-16 md:py-20">
                <div className="container mx-auto px-6 text-center max-w-4xl">
                     <h2 className="text-3xl font-bold font-display text-gray-800">我們的使命</h2>
                     <p className="mt-4 text-xl text-gray-600 italic leading-relaxed">
                        “追求深度、準確釋經，幫助弟兄姐妹們真明白、遵行、持守聖經真理，並在愛的環境中訓練，造就他們成為主的門徒。”
                     </p>
                </div>
            </section>


           {/* ✅ 新增：“教会事工”介绍版块 */}
            <section className="bg-white py-16 md:py-20">
                <div className="container mx-auto px-6">
                    <div className="grid lg:grid-cols-2 gap-12 items-center">
                        {/* 左侧图片 */}
                        <div className="w-full h-80 relative rounded-lg overflow-hidden shadow-lg">
                            <Image 
                                src="/images/ai-background.jpeg" // 使用一张象征性的图片
                                alt="AI 与信仰事工"
                                layout="fill"
                                objectFit="cover"
                            />
                        </div>

                        {/* 右侧文字 */}
                        <div>
                            <h2 className="text-3xl font-bold font-display text-gray-800">科技赋能的独特事工</h2>
                            <p className="mt-4 text-lg text-gray-600">
                                我们相信，神赐予的科技可以成为传扬福音、造就门徒的有力工具。
                            </p>
                            <p className="mt-2 text-gray-600">
                                我们致力于使用 AI 技术，将王守仁教授历年忠于圣经的深度教导，转化为一个不断成长的、可供您随时探索的数字资源宝库。
                            </p>
                            
                            <ul className="mt-6 space-y-3">
                                <li className="flex items-start">
                                    <Search className="w-5 h-5 text-blue-500 mr-3 mt-1 flex-shrink-0" />
                                    <span><b>智慧讲道库：</b> 快速搜索历年讲道的每一句话。</span>
                                </li>
                                <li className="flex items-start">
                                    <BrainCircuit className="w-5 h-5 text-blue-500 mr-3 mt-1 flex-shrink-0" />
                                    <span><b>AI 信仰助教：</b> 随时向基于讲道内容训练的 AI 提问。</span>
                                </li>
                            </ul>

                            <Link href="/ministries" className="mt-8 inline-flex items-center gap-2 bg-blue-600 text-white font-semibold py-3 px-6 rounded-lg hover:bg-blue-700 transition-colors">
                                了解这项事工的更多细节
                                <ArrowRight className="w-5 h-5" />
                            </Link>
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
