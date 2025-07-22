// app/resources/page.tsx

import ResourceCard from '@/app/components/resources/ResourceCard';
import LatestSermonCard from '@/app/components/resources/LatestSermonCard';
import FeaturedPostItem from '@/app/components/resources/FeaturedPostItem';
import { BookOpen, Mic, MessageCircleQuestion } from 'lucide-react';
import type { NextPage } from 'next';

// --- 模擬數據 (在真實應用中，這些數據應來自後端) ---

const resourceCardsData = [
  {
    icon: Mic,
    title: '講道中心',
    description: '聆聽和觀看王守仁教授及其他講員的深度釋經講道',
    link: '/resources/sermons',
    linkLabel: '進入講道中心',
  },
  {
    icon: BookOpen,
    title: '文章薈萃',
    description: '閱讀團契弟兄姐妹的分享和見證。',
    link: '/resources/articles',
    linkLabel: '瀏覽所有文章',
  },
  {
    icon: MessageCircleQuestion,
    title: '信仰問答',
    description: '解答關於信仰、聖經和生活的常見疑問，',
    link: '/resources/qa',
    linkLabel: '尋找答案',
  },
];

const latestSermonsData = [
  {
    title: '天國的福音與天國的子民',
    speaker: '王守仁 牧師',
    date: '2025年7月13日',
    scripture: '馬太福音 4:17-25',
    summary: '耶穌開始傳道時，宣告了天國的來臨。什麼是天國的福音？我們又該如何成為天國的子民？',
    videoUrl: '#',
    audioUrl: '#',
  },
  {
    title: '在試探中得勝',
    speaker: '王守仁 牧師',
    date: '2025年7月6日',
    scripture: '馬太福音 4:1-11',
    summary: '耶穌在曠野四十天受魔鬼的試探，祂的得勝給了我們在面對誘惑時的寶貴啟示和力量。',
    videoUrl: '#',
    audioUrl: '#',
  },
];

const featuredArticlesData = [
    { title: '如何在忙碌中保持安息？', category: '牧者短講', date: '2025年7月10日', link: '#'},
    { title: '一次意外的禱告蒙應允的經歷', category: '生活見證', date: '2025年7月8日', link: '#'},
];

const featuredQuestionsData = [
    { title: '聖經中有矛盾的地方嗎？', link: '#', isQuestion: true},
    { title: '基督徒可以慶祝農曆新年嗎？', link: '#', isQuestion: true},
]

// --- 頁面組件本身 ---

const ResourcesPage : NextPage = () => {
  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        {/* 頁面標題 */}
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold font-display text-gray-800">資源中心</h1>
          <p className="mt-4 text-lg text-gray-600 max-w-2xl mx-auto">
            在這裡，您可以找到歷次牧者講道的錄音錄影和文字、團契弟兄姐妹們分享的講稿，以及信仰問題的解答。願這些資源能成為您屬靈生命成長的助力。
          </p>
        </div>

        {/* 主要導航卡片 */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8 mb-16">
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

        {/* 最新講道區 */}
        <div className="mb-16">
            <h2 className="text-3xl font-bold font-display text-center text-gray-800 mb-8">最新講道速覽</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                {latestSermonsData.map(sermon => <LatestSermonCard key={sermon.title} sermon={sermon} />)}
            </div>
        </div>

        {/* 精選文章與問答區 */}
        <div>
            <h2 className="text-3xl font-bold font-display text-center text-gray-800 mb-8">精選文章與問答</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-12 max-w-5xl mx-auto">
                {/* 文章欄 */}
                <div className="bg-white p-6 rounded-lg shadow-sm">
                    <h3 className="text-xl font-bold mb-4">最新文章</h3>
                    <ul className="list-none p-0">
                        {featuredArticlesData.map(post => <FeaturedPostItem key={post.title} {...post} />)}
                    </ul>
                </div>
                {/* 問答欄 */}
                <div className="bg-white p-6 rounded-lg shadow-sm">
                    <h3 className="text-xl font-bold mb-4">熱點問答</h3>
                    <ul className="list-none p-0">
                        {featuredQuestionsData.map(post => <FeaturedPostItem key={post.title} {...post} />)}
                    </ul>
                </div>
            </div>
        </div>



      </div>
    </div>
  );
};

export default ResourcesPage;