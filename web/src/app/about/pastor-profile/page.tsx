// app/about/pastor-profile/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import { Mail, Phone } from 'lucide-react';
import ReactMarkdown from 'react-markdown'; // 我們將用它來渲染帶有列表格式的內容
import remarkGfm from 'remark-gfm';

// ... 將上面的 pastorProfileData 對象粘貼到這裡 ...
// 假設的數據結構
const pastorProfileData = {
    name: "王守仁 Joseph Wang ",
    title: {
        zh: "榮譽牧師／博士／教授",
        en: "The Rev. Dr. Prof. "
    },
    avatar: "/images/JosephSWang-546w.webp", // 一張更高質量的照片
    contact: [
        { type: "email-no", value: "pastor.wang@dallas-hlc.org" },
        // { type: "phone", value: "123-456-7890" },
    ],
    sections: [
        {
            title: { zh: "學術背景", en: "Academic Background" },
            content: {
                zh: `王守仁牧師／博士／教授自台灣大學電機系畢業後就來美國專攻神學。他從阿斯伯利神學院得道學碩士（M. Div., Asbury Theological Seminary）, 從普林斯頓神學院得神學碩士（Th. M., Princeton Theological Seminary）, 從愛慕理大學得專攻新約研究的哲學博士學位(Ph. D. in New Testament Studies, Emory University)。`,
                en: `After graduation from the Department of Electrical Engineering, National Taiwan Unversity, the Reverend Dr. Prof. Joseph Wang came to the United States to pursue theological studies. He earned Master of Divinity degree，Master of Theology degree and Ph. D. degree in New Testament studies.`
            }
        },
        {
            title: { zh: "事奉經歷", en: "Ministry Experience" },
            content: {
                zh: `畢業後王博士立刻被母校阿斯伯利神學院（Asbury Theological Seminary）聘為新約教授。 他在該神學院全時間服務三十多年，其中數年也任新約系主任。現在他是該神學院的榮譽新約教授。他曾被選為美國傑出教育家。目前他常是華人大型聚會的主要講員，他也是普世神學院，大學的客座教授。這些學術機構包括北京大學，清華大學等著名學校。\n\n 王守仁牧師／教授在原文釋經上很有造詣。在他教學，講道中，他時常根據聖經原文深度的釋經，說明經文的本意。他也經常從自然科學，心理學，歷史，考古學等提出證據，驗證聖經的真理性，基督福音的確實性。藉此他幫助基督徒和慕道朋友除去對聖經的誤解，疑惑而正確地明白聖經的真理。\n\n ## Publications\n\n1. **《從新約聖經看靈恩運動》** 王守仁 — 中台神學院出版社, 1993 — *Baptism in the Holy Spirit*\n2. *On This Rock: A Study of Peter's Life and Ministry* — Joseph Wang and Anne B. Crumpler\n3. **《21世纪基督徒装备100课》圣经篇**（共五課）王守仁  \n   - 第8课 圣经的形成  \n   - 第9课 圣经的权威  \n   - 第10课 释经的步骤  \n   - 第11课 圣经的要旨  \n   - 第12课 圣经与基督徒\n4. *Pauline Doctrine of Law* — Dr. Joseph S. Wang, 1970\n5. *Romans in The Wesley Bible NKJ (A personal Study Bible for Holy Living)* — Thomas Nelson, Inc., 1990\n6. *Asbury Bible Commentary – Romans* — Joseph S. Wang\n`,
                en: `He served full time on the faculty of Asbury Theology Seminary for more than three decades. He served as Professor of New Testament, and for many years as Chair of the New Testament Department. He, now, is Professor Emeritus of New Testament at that seminary. He was elected Outstanding Educator of America. Now he serves as speaker of large Chinese Christian conferences, as well as guest professor in many seminaries and universities throughout the world, including Peking University, Tsinghua University and many other academic institutes.\n\nThe Reverend Professor Wang is an expert in Biblical exegesis and exposition. In his teachigs and sermons, he often explains the Biblical truths in depth on the basis of the original laguages of the Bible. He also presents relevant evidences from nartural sciences, Psychology, History, Archaeology to support the reliability of the Bible. In this way he helps Christians and seekers to dissolve Biblical difficulties and accurately understand the Biblical messages.`
            }
        }
    ]
};
// 一個用於渲染信息區塊的內部組件
const ProfileSection = ({ title, content }: { 
    title: { zh: string, en: string }, 
    content: { zh: string, en: string } 
}) => {
    return (
        <section className="mb-10">
            {/* 桌面端雙欄佈局 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
                {/* 中文內容 */}
                <div>
                    <h2 className="text-2xl font-bold font-display border-b-2 border-blue-600 pb-2 mb-4">{title.zh}</h2>
                    <div className="prose prose-slate max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.zh}</ReactMarkdown>
                    </div>
                </div>
                {/* 英文內容 */}
                <div>
                    <h2 className="text-2xl font-bold font-display border-b-2 border-gray-400 pb-2 mb-4 text-gray-700">{title.en}</h2>
                    <div className="prose prose-slate max-w-none">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.en}</ReactMarkdown>
                    </div>
                </div>
            </div>
        </section>
    );
};


export default function PastorProfilePage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '關於我們', href: '/about' },
    { name: '牧師簡介' },
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        
        <div className="lg:flex lg:gap-12">

          {/* 左側邊欄 (桌面端) */}
          <aside className="lg:w-1/3 xl:w-1/4 mb-10 lg:mb-0">
            <div className="sticky top-24">
              <Image 
                src={pastorProfileData.avatar}
                alt={pastorProfileData.name}
                width={400}
                height={400}
                className="rounded-lg shadow-lg w-full"
              />
              <div className="mt-6 bg-white p-6 rounded-lg shadow-md">
                <h1 className="text-2xl font-bold">{pastorProfileData.name}</h1>
                <p className="text-md text-gray-600 mt-1">{pastorProfileData.title.zh} / {pastorProfileData.title.en}</p>
              </div>
            </div>
          </aside>

          {/* 右側主內容區 (桌面端) */}
          <main className="lg:w-2/3 xl:w-3/4">
            {pastorProfileData.sections.map(section => (
                <ProfileSection key={section.title.en} title={section.title} content={section.content} />
            ))}
          </main>

        </div>
      </div>
    </div>
  );
}