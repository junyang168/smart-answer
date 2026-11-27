import { BookOpen, FilePenLine, Library, MessagesSquare } from 'lucide-react';
import { getServerSession } from 'next-auth';
import ResourceCard from '@/app/components/resources/ResourceCard';
import { authConfig } from '@/app/utils/auth';
import { canUseSermonEditing, canViewStudyDemo } from '@/app/utils/roles';

const resourceCardsData = [
  {
    icon: BookOpen,
    title: '查經資料',
    description: '查找經文的研讀資料',
    link: '/demos/scripture-study.html',
    linkLabel: '選擇經文',
    visibleTo: canViewStudyDemo,
  },
  {
    icon: Library,
    title: '釋經講道系列',
    description: `聆聽王守仁教授的原聲講道，字幕同步，投影片列出要點與經文。`,
    link: '/resources/wang-repository',
    linkLabel: '瀏覽系列',
  },
  {
    icon: MessagesSquare,
    title: '團契查經回顧',
    description: `回顧雙週團契查經的主題、學習重點與來源連結，也讓新朋友認識我們如何一起查考聖經。`,
    link: '/resources/fellowship',
    linkLabel: '查看回顧',
  },
  {
    icon: FilePenLine,
    title: '講道校對與編輯',
    description: `認領逐字稿，校對講道內容，查看編輯與發布進度。`,
    link: '/resources/series',
    linkLabel: '校對編輯',
    visibleTo: canUseSermonEditing,
  },
];

export async function ResourceCenter() {
  const session = await getServerSession(authConfig);
  const cards = resourceCardsData.filter(card => !card.visibleTo || card.visibleTo(session?.user?.role));
  const columns = cards.length === 3 ? "md:grid-cols-2 lg:grid-cols-3 max-w-6xl" : "md:grid-cols-2 max-w-4xl";
  return (
    <section className="bg-gray-50 px-4 py-5 sm:px-6 sm:py-8">
      <h1 className="sr-only">聖經學習資源</h1>
      <div className={`mx-auto grid grid-cols-1 gap-3 text-left sm:gap-5 ${columns}`}>
        {cards.map(card => <ResourceCard key={card.link} {...card} />)}
      </div>
    </section>
  );
}
