// app/ministries/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import Link from 'next/link';
import { BrainCircuit, Mic, FileSignature, Users, MessageCircleQuestion, ArrowRight,ChevronRight,Search } from 'lucide-react';
import ReactMarkdown from 'react-markdown'; // ✅ 引入 ReactMarkdown
import remarkGfm from 'remark-gfm';          // ✅ 引入 GFM 插件

const cardDescriptions = {
  sermonLibrary: `所有講道都配備了由 AI 生成並經同工校對的**簡介、要點和完整文字稿**。您可以像搜索文章一樣， 精準定位任何講道內容。`,
  communityWisdom: `我們的文章源自**團契查經的講稿**。在經過弟兄姐妹們的熱烈討論後，其精華內容再由 AI 輔助潤色，最終形成一篇篇*充滿生命力*的深度文章。`,
  realLifeQA: `這裡的每一個問題，都源自**弟兄姐妹在團契查經中的真實探討**。我們利用 AI 技術將這些寶貴的討論整理、潤色，形成了一份真實、貼近生活的問答集。`
};

const metrics = {
  sermons: "351",
  storage: "831GB+",
  words: "1千萬"
};


export default function MinistriesPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '教會事工' },
  ];

  return (
    <div className="bg-white">
      {/* 1. 引言 */}
      <section className="relative bg-gray-900 text-white py-20 md:py-32 text-center overflow-hidden">
        <div className="absolute inset-0 opacity-50">
            {/* 可以使用一張抽象的、有科技感的背景圖 */}
            <Image src="/images/ai-background.jpeg" alt="AI Technology" layout="fill" objectFit="cover" />
        </div>
        <div className="container mx-auto px-6 relative z-10">
          <Breadcrumb links={breadcrumbLinks} />
          <BrainCircuit className="w-16 h-16 mx-auto mb-4 text-[#D4AF37]" />
          <h1 className="text-4xl md:text-5xl font-bold font-serif">當科技遇見神學</h1>
          <p className="mt-4 text-lg md:text-xl max-w-3xl mx-auto">
            我們致力於使用 AI 人工智能技術，將王守仁教授歷年忠於聖經的深度教導，轉化為造就門徒的寶貴屬靈資源。
          </p>
        </div>
      </section>

      {/* 流程介紹 */}
      <div className="bg-gray-50 py-16 md:py-24">
        <div className="container mx-auto px-6 max-w-5xl">
          
          {/* 2. 第一步：源頭 */}
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold font-display text-gray-800">第一步：知識的源頭</h2>
            <p className="mt-2 text-lg text-gray-600">一切都始於王教授數十年來，對神話語深度準確的釋經講道。</p>
        {/* ✅ 新增：數據指標展示牆 */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-8 text-center mt-10 pt-10 border-t">
            <div className="flex flex-col">
                <span className="text-4xl md:text-4xl font-bold text-blue-600">{ metrics.sermons }</span>
                <span className="text-sm font-semibold text-gray-500 mt-2">篇講道</span>
            </div>
            <div className="flex flex-col">
                <span className="text-4xl md:text-4xl font-bold text-blue-600">{ metrics.storage }</span>
                <span className="text-sm font-semibold text-gray-500 mt-2">音視頻資料</span>
            </div>
            <div className="flex flex-col">
                <span className="text-4xl md:text-4xl font-bold text-blue-600">{ metrics.words }</span>
                <span className="text-sm font-semibold text-gray-500 mt-2">轉錄文字</span>
            </div>
        </div>
          </div>

          {/* 3. 第二步：AI 賦能 */}
          <div className="text-center mb-16">
              <h2 className="text-3xl font-bold font-display text-gray-800">第二步：AI 賦能</h2>
              <p className="mt-2 text-lg text-gray-600">我們利用 AI 將音視頻轉錄為文字，再由教會同工嚴謹校對，確保準確性。</p>
              <div className="flex justify-center items-center gap-4 md:gap-8 my-6 text-gray-500">
                  <div className="text-center">
                      <Mic size={40} className="mx-auto"/>
                      <p className="text-sm mt-2">錄音/錄像</p>
                  </div>
                  <ArrowRight size={30} className="flex-shrink-0"/>
                  <div className="text-center">
                      <BrainCircuit size={40} className="mx-auto"/>
                      <p className="text-sm mt-2">AI 轉錄</p>
                  </div>
                   <ArrowRight size={30} className="flex-shrink-0"/>
                  <div className="text-center">
                      <FileSignature size={40} className="mx-auto"/>
                      <p className="text-sm mt-2">同工校對</p>
                  </div>
              </div>
          </div>

          {/* 4. 第三步：資源應用 */}
          <div className="text-center mb-16">
              <h2 className="text-3xl font-bold font-display text-gray-800">第三步：資源的應用</h2>
              <p className="mt-2 text-lg text-gray-600">校對後的文稿被轉化為多種形式，服務於個人靈修和團契生活。</p>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-8 text-left">
                <div className="bg-white p-6 rounded-lg border col-span-1 md:col-span-1">
                    <div className="flex items-center gap-3 mb-3">
                        <Search className="w-6 h-6 text-blue-500"/>
                        <h3 className="font-bold text-xl text-gray-800">講道中心</h3>
                    </div>
                      <div className="prose prose-sm text-gray-600 flex-grow">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cardDescriptions.sermonLibrary}</ReactMarkdown>
                      </div>
                      <Link href="/resources/sermons" className="font-semibold text-blue-600 hover:underline text-sm">
                          前往講道中心體驗 →
                      </Link>
                </div>
                <div className="bg-white p-6 rounded-lg border">
                    <div className="flex items-center gap-3 mb-3">
                        <Users className="w-6 h-6 text-blue-500"/>
                        <h3 className="font-bold text-xl text-gray-800">團契智慧結晶</h3>
                    </div>
                      <div className="prose prose-sm text-gray-600 flex-grow">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cardDescriptions.communityWisdom}</ReactMarkdown>
                      </div>
                      <Link href="/resources/articles" className="font-semibold text-blue-600 hover:underline text-sm">
                          閱讀團契結晶 →
                      </Link>
                </div>
                <div className="bg-white p-6 rounded-lg border">
                    <div className="flex items-center gap-3 mb-3">
                        <MessageCircleQuestion className="w-6 h-6 text-blue-500"/>
                        <h3 className="font-bold text-xl text-gray-800">真實信仰問答</h3>
                    </div>
                      <div className="prose prose-sm text-gray-600 flex-grow">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cardDescriptions.realLifeQA}</ReactMarkdown>
                      </div>
                    <Link href="/resources/qa" className="font-semibold text-blue-600 hover:underline text-sm">
                        探索真實問答 →
                    </Link>
                </div>
              </div>
          </div>
          
          {/* 5. 第四步：互動與探索 */}
          <div className="bg-blue-600 text-white p-8 md:p-12 rounded-2xl text-center">
              <MessageCircleQuestion className="w-12 h-12 mx-auto mb-4"/>
              <h2 className="text-3xl font-bold">第四步：您的 AI 信仰助教</h2>
              <p className="mt-4 max-w-2xl mx-auto">
                  我們將所有這些經過審核的知識，用來訓練一個專屬的 AI 問答模型。它能基於王教授的教導，回答您在信仰上的問題。
              </p>
              <Link href="/resources/qa" className="mt-8 inline-block bg-white text-blue-700 font-bold py-3 px-8 rounded-full hover:bg-gray-200 transition-transform hover:scale-105">
                  立即開始提問
              </Link>
          </div>

        </div>
      </div>
      
      {/* 6. 參與我們 */}
      <section className="py-16 md:py-20">
          <div className="container mx-auto px-6 text-center max-w-3xl">
              <Users className="w-12 h-12 mx-auto text-gray-400 mb-4"/>
              <h2 className="text-3xl font-bold font-display text-gray-800">參與這項事工</h2>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                  這項事工需要弟兄姐妹們的共同參與。如果您有文字校對的恩賜，或願意在團契中積極提問、分享，歡迎您聯繫我們，成為這個事工的一份子。
              </p>
              <Link href="/contact" className="mt-6 inline-flex items-center gap-2 font-semibold text-blue-600 hover:text-blue-800">
                  我想參與 <ChevronRight className="w-4 h-4"/>
              </Link>
          </div>
      </section>

    </div>
  );
}