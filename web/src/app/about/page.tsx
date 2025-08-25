// app/about/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import Image from 'next/image';
import Link from 'next/link';
import { Landmark, Users, ScrollText, ChevronRight } from 'lucide-react';

// 我們可以把牧師和團隊的數據定義在這裡或從一個單獨的文件導入
const pastorInfo = {
    name: "王守仁 榮譽牧師/博士/教授",
    title: "The Rev. Dr. Prof., Pastor Emeritus Joseph Wang ",
    avatar: "/images/JosephSWang-546w.webp", // 假設的路徑
    bioExcerpt: "王守仁牧師／教授在原文釋經上很有造詣。在他教學，講道中，他時常根據聖經原文深度的釋經，說明經文的本意。他也經常從自然科學，心理學，歷史，考古學等提出證據，驗證聖經的真理性，基督福音的確實性。藉此他幫助基督徒和慕道朋友除去對聖經的誤解，疑惑而正確地明白聖經的真理。",
    bioExcerpt_En:"The Reverend Professor Wang is an expert in Biblical exegesis and exposition. In his teachings and sermons, he often explains the Biblical truths in depth on the basis of the original languages of the Bible. He also presents relevant evidences from natural sciences, Psychology, History, Archaeology to support the reliability of the Bible. In this way he helps Christians and seekers to dissolve Biblical difficulties and accurately understand the Biblical messages.",
    link: "/about/pastor-profile"
};


export default function AboutPage() {
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
                  依靠神的恩典, 達拉斯聖道教會追求藉著深度, 準確釋經, 幫助弟兄姐妹們真明白, 遵行, 持守聖經真理, 並在愛的環境中訓練，造就他們成為主的門徒，以完成主的命令， 使萬民做祂的門徒.
              </p>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                Mission Statement: <br/> By the grace of God Dallas Holy Logos Church (HLC) seeks, through in-depth and accurate exegesis, exposition to help the brothers and sisters to truly understand, obey and defend the biblical truths, and in the environment of love to train and equip them to be the disciples of the Lord in order to fulfill His commission to make disciples of all nations.
            </p>
           </div>
      </section>

      {/* 3. 我們的信仰 */}
      <section className="bg-gray-50 py-16 md:py-20">
          <div className="container mx-auto px-6 max-w-4xl text-center">
              <ScrollText className="w-12 h-12 mx-auto text-gray-400 mb-4"/>
              <h2 className="text-3xl font-bold font-display">我們的信仰</h2>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                  我信上帝, 全能的父, 創造天地的主。我信我主耶穌基督, 上帝的獨生子;  因聖靈感孕, 由童貞女馬利亞所生, 在本丟彼拉多手下受難, 被釘在十字架上, 受死,埋葬; 降在陰間; 第三天從死人中復活; 升天, 坐在全能父上帝的右邊; 將來必從那裡降臨, 審判活人死人。我信聖靈;我信聖而公之教會; 我信聖徒相通; 我信罪得赦免;我信身體復活; 我信永生。
              </p>
              <p className="mt-4 text-lg text-gray-600 leading-relaxed">
                Apostles Creed: <br/>
I believe in God the Father Almighty, maker of heaven and earth. And in Jesus Christ, His only Son, our Lord; Who was conceived by the Holy Spirit, Born of the virgin Mary, Suffered under Pontius Pilate, Was crucified, dead and buried. On the third day, He rose again from the dead. He ascended to heaven, And sits at the right hand of God the Father Almighty; From thence He will come to judge the living and the dead. I believe in the Holy Spirit, The holy universal church, The communion of saints, The forgiveness of sins, The resurrection of the body, And the life everlasting.
              </p>
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
                <p className="text-gray-500 font-semibold mt-1">{pastorInfo.title}</p>
                <p className="mt-4 text-gray-700 leading-relaxed">{pastorInfo.bioExcerpt}</p>
                <p className="mt-4 text-gray-700 leading-relaxed">{pastorInfo.bioExcerpt_En}</p>
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