// app/about/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import Link from 'next/link';
import { readMissionStatement } from '@/app/utils/siteContent';
import { Landmark, Users, ScrollText, ChevronRight } from 'lucide-react';

// 我們可以把牧師和團隊的數據定義在這裡或從一個單獨的文件導入
const pastorInfo = {
    name: "王守仁 榮譽牧師/博士/教授",
    avatar: "/images/JosephSWang-546w.webp", // 假設的路徑
    bioExcerpt: "王守仁牧師／教授在原文釋經上很有造詣。在他教學，講道中，他時常根據聖經原文深度的釋經，說明經文的本意。他也經常從自然科學，心理學，歷史，考古學等提出證據，驗證聖經的真理性，基督福音的確實性。藉此他幫助基督徒和慕道朋友除去對聖經的誤解，疑惑而正確地明白聖經的真理。",
    link: "/about/pastor-profile"
};


export const revalidate = 0;

export default async function AboutPage() {
  const missionStatement = await readMissionStatement();
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '關於我們' },
  ];

  return (
    <div className="bg-white">
      <Breadcrumb links={breadcrumbLinks} />
      {/* 1. 引言與核心價值 */}
      <section className="relative bg-gray-800 text-white py-20 md:py-32 text-center">
        <Image src="/images/church-community.jpeg" alt="教會社群" layout="fill" objectFit="cover" className="opacity-30" />
        <div className="container mx-auto px-6 relative z-10">
          <h1 className="text-4xl md:text-5xl font-bold">關於達拉斯聖道教會</h1>
          <p className="mt-4 text-lg md:text-xl max-w-3xl mx-auto">
            我們是一個相信聖經、傳揚福音、彼此相愛的屬靈大家庭。
          </p>
        </div>
      </section>

      {/* 3. 教會使命 */}
      <section className="bg-gray-50 py-16 md:py-20">
          <div className="container mx-auto px-6 max-w-4xl text-center">
              <ScrollText className="w-12 h-12 mx-auto text-gray-400 mb-4"/>
              <h2 className="text-3xl font-bold font-display">教會使命</h2>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                  {missionStatement.zhHant}
              </p>
           </div>
      </section>

      {/* 3. 我們的信仰 */}
      <section className="bg-gray-50 py-16 md:py-20">
          <div className="container mx-auto px-6 max-w-4xl text-center">
              <ScrollText className="w-12 h-12 mx-auto text-gray-400 mb-4"/>
              <h2 className="text-3xl font-bold font-display">我們的信仰</h2>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                  我們相信聖經是神所默示，是信仰與生活的最高權威；相信獨一的三一真神，以及耶穌基督的受死、復活與再來。人因罪與神隔絕，唯有藉著神的恩典、悔改並信靠基督，才能得著救恩。我們以基督為教會的元首，在真理與愛中彼此造就，遵守洗禮與聖餐，傳揚福音、培育門徒，持守神所設立的婚姻與家庭，並盼望主再來與永遠同在。
              </p>
              <Link href="/about/our-faith" className="mt-6 inline-flex items-center gap-2 font-semibold text-blue-600 hover:text-blue-800">
                閱讀完整信仰宣告 <ChevronRight className="w-4 h-4" />
              </Link>
           </div>
      </section>

      {/* 4. 認識我們的牧師 */}
      <section className="py-16 md:py-20">
        <div className="container mx-auto px-6 max-w-5xl">
          <h2 className="text-3xl font-bold font-display text-center mb-12">創會牧師</h2>
          <div className="bg-white rounded-lg shadow-xl overflow-hidden md:flex">
             <div className="md:w-1/3">
                <Image src={pastorInfo.avatar} alt={pastorInfo.name} width={400} height={400} className="w-full h-full object-cover"/>
             </div>
             <div className="md:w-2/3 p-8 md:p-10 flex flex-col justify-center">
                <h3 className="text-2xl font-bold">{pastorInfo.name}</h3>
                <p className="mt-4 text-gray-700 leading-relaxed">{pastorInfo.bioExcerpt}</p>
                <Link href={pastorInfo.link} className="mt-6 self-start inline-flex items-center gap-2 font-semibold text-blue-600 hover:text-blue-800">
                  查看完整介紹 <ChevronRight className="w-4 h-4"/>
                </Link>
             </div>
          </div>
        </div>
      </section>

      {/* 5. (可選) 同工團隊 - 這裡先不實現，但可以預留位置 */}


    </div>
  );
}