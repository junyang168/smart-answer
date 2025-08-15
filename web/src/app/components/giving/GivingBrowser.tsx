"use client";

// app/giving/page.tsx
import { Breadcrumb } from '@/app/components/common/Breadcrumb';
import { GivingOptionCard } from '@/app/components/giving/GivingOptionCard'; // 我們將創建這個客戶端組件
import { HandHeart, Landmark, Mail } from 'lucide-react';
import { useSession, signIn } from "next-auth/react"; // ✅ 引入 useSession 和 signIn
import { Lock } from 'lucide-react';

// 將奉獻信息結構化
const givingOptions = [
    {
        method: '網路銀行匯款 (Zelle)',
        icon: Landmark,
        details: [
            { label: '收款人郵箱', value: 'HLCofDallas@gmail.com', isCopiable: true },
        ],
        instructions: '大部分美國銀行 App 都內置 Zelle 功能。在轉賬時，請使用上面的郵箱地址作為收款人。您可以在備註中註明您的姓名和奉獻用途（例如：十一奉獻、宣教奉獻等）。'
    },
    {
        method: '郵寄支票',
        icon: Mail,
        details: [
            { label: '抬頭 (Payable to)', value: 'HLC', isCopiable: true },
            { label: 'attn', value: 'John Wang', isCopiable: false },
            { label: '郵寄地址', value: '4205 Brooktree Ln., Dallas, TX 75287', isCopiable: true },
        ],
        instructions: '請將支票郵寄至以上地址。為安全起見，請不要在信封中郵寄現金。'
    }
];

export const GivingBrowser = () => {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '奉獻支持' },
  ];

  const { data: session, status } = useSession(); // ✅ 獲取 session 狀態
  if (status === "unauthenticated") {
      // 如果用戶未登錄，顯示一個登錄提示界面
      return (
      <div className="text-center py-20 bg-gray-50 rounded-lg max-w-lg mx-auto">
          <Lock className="w-12 h-12 mx-auto text-gray-400 mb-4" />
          <h2 className="text-2xl font-bold mb-2">需要登錄</h2>
          <p className="text-gray-600 mb-6">此內容僅對已登錄用戶開放，請先登錄以繼續訪問。</p>
          <button
          onClick={() => signIn("google")}
          className="bg-blue-500 text-white font-semibold py-3 px-6 rounded-full hover:bg-blue-600 text-lg"
          >
          使用 Google 登錄
          </button>
      </div>
      );
  }


  return (
    <div className="bg-gray-50">
      {/* 1. 引言 */}
      <section className="bg-white py-16 text-center border-b">
        <div className="container mx-auto px-6 max-w-3xl">
          <HandHeart className="w-16 h-16 mx-auto text-yellow-500 mb-4" />
          <h1 className="text-4xl md:text-5xl font-bold font-display text-gray-800">同心建造神的家</h1>
          <p className="mt-6 text-lg text-gray-600 leading-relaxed">
            您的奉獻不僅支持著教會的日常運作和各項事工的發展，更是您在神國度中的一份投資。我們感謝您甘心樂意的擺上，願神親自紀念和祝福您的奉獻。
          </p>
          <blockquote className="mt-8 border-l-4 border-gray-300 pl-6 italic text-gray-700">
            “各人要隨本心所酌定的，不要作難，不要勉強，因為捐得樂意的人是神所喜愛的。”
            <cite className="block mt-2 not-italic font-semibold">— 哥林多後書 9:7</cite>
          </blockquote>
        </div>
      </section>

      {/* 2. 奉獻方式詳解 */}
      <section className="py-16 md:py-20">
        <div className="container mx-auto px-6 max-w-4xl">
          <h2 className="text-3xl font-bold font-display text-center mb-12">奉獻方式</h2>
          <div className="space-y-8">
            {givingOptions.map(option => (
              <GivingOptionCard 
                key={option.method}
                method={option.method}
                icon={option.icon}
                details={option.details}
                instructions={option.instructions}
              />
            ))}
          </div>
        </div>
      </section>

      {/* 3. 財務透明度 */}
      <section className="bg-white py-16 border-t">
        <div className="container mx-auto px-6 max-w-3xl text-center">
            <h3 className="text-2xl font-bold">忠心的管家</h3>
            <p className="mt-4 text-gray-600 leading-relaxed">
                達拉斯聖道教會致力於成為神百般恩賜的好管家。我們承諾將以最高標準的誠信和透明度來管理教會的財務。教會的年度財務報告會向所有正式會友公開。
            </p>
        </div>
      </section>

    </div>
  );
}