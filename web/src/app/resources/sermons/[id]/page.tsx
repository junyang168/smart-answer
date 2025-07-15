// app/resources/sermons/[id]/page.tsx

import { notFound } from 'next/navigation';
import { Sermon } from '@/app/interfaces/article'; // 假設 Sermon 接口已經定義在這個路徑
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { SermonDetailSidebar } from '@/app/components/sermons/SermonDetailSidebar';
import Link from 'next/link';

// --- 模擬數據和獲取邏輯 (通常放在 page.tsx 或一個單獨的 data 文件中) ---
const allSermons: Sermon[] = [
  { id: '1', title: '福音基礎 (一): 什麼是福音?', speaker: '王守仁 牧師', date: '2025年01月05日', scripture: '羅馬書 1:16-17', book: '羅馬書', topic: '福音基礎', status: '已發佈', assigned_to_name: '張三', videoUrl: 'https://www.youtube.com/embed/dQw4w9WgXcQ', audioUrl: '#', summary: '摘要...', source:'',
    markdownContent: "## 一、福音的大能\n\n羅馬書 1:16 說：「我不以福音為恥；這福音本是神的大能，要救一切相信的。」這句話揭示了福音的核心本質——它不是一套哲學理論，不是一種道德勸說，而是上帝改變生命的大能。\n\n### 為什麼是「大能」？\n\n1.  **勝過罪惡**：它能將人從罪的捆綁中釋放出來。\n2.  **勝過死亡**：它賜予永生的盼望，讓人不懼怕死亡。\n3.  **帶來和好**：它使我們與神和好，也使我們能與人和好。\n\n> 這是一段引用的經文或名言，用來強調重點。\n\n## 二、福音的對象\n\n這大能是給「一切相信的」。這意味著福音是普世的，不分種族、背景、性別或社會地位。唯一的條件就是「相信」。\n\n- **猶太人**：首先臨到他們。\n- **希臘人**：也臨到外邦人。" },
  // ... 其他講道數據 ...
];

async function fetchSermonById(id: string): Promise<Sermon | undefined> {
  return allSermons.find(sermon => sermon.id === id);
}
// --- ------------------------------------------------------------- ---

export default async function SermonDetailPage({ params }: { params: { id: string } }) {
  const sermon = await fetchSermonById(params.id);

  if (!sermon) {
    notFound(); // 如果找不到講道，顯示 404 頁面
  }

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <div className="grid grid-cols-1 lg:grid-cols-3 lg:gap-12">
          
          {/* 左側主內容區 (占 2/3) */}
          <main className="lg:col-span-2">
            {/* 導航路徑 (Breadcrumbs) */}
            <nav className="text-sm mb-4 text-gray-500">
              <Link href="/resources/sermons" className="hover:underline">講道中心</Link>
              <span className="mx-2">/</span>
              <span>{sermon.title}</span>
            </nav>

            {/* 標題和元數據 */}
            <h1 className="text-3xl lg:text-4xl font-bold font-display text-gray-900 mb-2">{sermon.title}</h1>
            <p className="text-gray-600 mb-6">{sermon.speaker} • {sermon.date} • {sermon.scripture}</p>

            {/* 視頻播放器 */}
            <div className="aspect-w-16 aspect-h-9 mb-8 shadow-lg rounded-lg overflow-hidden">
                <iframe 
                    src={sermon.videoUrl} 
                    title={sermon.title}
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" 
                    allowFullScreen
                    className="w-full h-full"
                ></iframe>
            </div>

            {/* Markdown 轉錄內容 */}
            <article className="prose lg:prose-lg max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {sermon.markdownContent}
              </ReactMarkdown>
            </article>
          </main>

          {/* 右側邊欄 (占 1/3) */}
          <SermonDetailSidebar sermon={sermon} />

        </div>
      </div>
    </div>
  );
}