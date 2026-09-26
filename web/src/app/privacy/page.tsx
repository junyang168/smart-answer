// app/privacy/page.tsx
// Public privacy policy. Google requires one before the church's OAuth app can
// leave "Testing" (whose refresh tokens expire after 7 days); see OPS-29 (#403).
import type { Metadata } from 'next';
import { Breadcrumb } from '@/app/components/common/Breadcrumb';

export const metadata: Metadata = {
  title: '隱私權政策 Privacy Policy | 達拉斯聖道教會',
};

const EFFECTIVE_DATE = '2026-09-26';
const CONTACT_EMAIL = 'ContactUs@Dallas-HLC.org';

const sections: { zh: string; zhBody: string[]; en: string; enBody: string[] }[] = [
  {
    zh: '我們收集甚麼',
    zhBody: [
      '瀏覽本網站不需要登入，我們也不要求您提供個人資料。',
      '會友與同工以 Google 帳號登入時，我們取得您的姓名、電子郵件地址和頭像，用來辨認身分並決定您可使用的功能（例如會友專區、編輯工具）。',
      '登入後網站會在您的瀏覽器存放一個登入用的 cookie；不用於廣告或追蹤。',
      '您在「AI 輔助查經」等功能中輸入的問題，會連同必要的經文與講道資料傳送給第三方 AI 服務以產生回答。',
    ],
    en: 'What we collect',
    enBody: [
      'You can browse this site without signing in, and we do not ask for personal information.',
      'When members and staff sign in with Google, we receive your name, email address and profile picture, used to identify you and decide which features you may use (such as member pages and editing tools).',
      'Signing in stores a session cookie in your browser. It is not used for advertising or tracking.',
      'Questions you enter in features such as AI-assisted Bible study are sent, together with the relevant Scripture and sermon material, to third-party AI services to produce an answer.',
    ],
  },
  {
    zh: 'Google 帳號資料的使用',
    zhBody: [
      '本網站只向一般登入者要求基本的 Google 個人資料（姓名、電子郵件、頭像），不存取您的 Google 雲端硬碟、文件或其他資料。',
      '教會同工的帳號另外授權本網站使用 Google 雲端硬碟與 Google 文件，僅用於教會內部工作：把講道稿匯出為 Google 文件，以及取得教會團契聚會的錄影與會議記錄。這些資料屬於教會自己的雲端硬碟。',
      '本網站使用 Google API 所取得的資料，遵守 Google API Services User Data Policy，包括其中的 Limited Use 要求。',
    ],
    en: 'Use of Google account data',
    enBody: [
      'For ordinary sign-in, this site requests only your basic Google profile (name, email, profile picture). It does not access your Google Drive, Docs or other data.',
      'The church staff account separately authorizes this site to use Google Drive and Google Docs, solely for the church\'s own work: exporting sermon drafts as Google Docs and retrieving recordings and meeting notes of church fellowship meetings from the church\'s own Drive.',
      'Use of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements.',
    ],
  },
  {
    zh: '分享與保存',
    zhBody: [
      '我們不出售、出租或交換您的個人資料，也不用於廣告。',
      '資料保存在教會管理的伺服器上，只在提供上述功能所需的範圍內，交給代為處理的服務（Google、AI 服務供應商）。',
      '如需刪除您的登入資料，或對本政策有任何疑問，請寫信與我們聯絡。',
    ],
    en: 'Sharing and retention',
    enBody: [
      'We do not sell, rent or trade your personal information, and we do not use it for advertising.',
      'Data is kept on servers managed by the church and shared with service providers (Google, AI service providers) only as needed to provide the features above.',
      'To have your sign-in information deleted, or with any question about this policy, please email us.',
    ],
  },
];

export default function PrivacyPage() {
  const breadcrumbLinks = [
    { name: '首頁', href: '/' },
    { name: '隱私權政策' },
  ];

  return (
    <div className="container mx-auto px-6 py-12">
      <Breadcrumb links={breadcrumbLinks} />
      <article className="max-w-3xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-900">隱私權政策</h1>
        <p className="text-xl text-gray-600 mt-1">Privacy Policy</p>
        <p className="text-sm text-gray-500 mt-4">
          達拉斯聖道教會 Dallas Holy Logos Church · dallas-hlc.org · 生效日期 Effective {EFFECTIVE_DATE}
        </p>

        {sections.map((section) => (
          <section key={section.en} className="mt-10">
            <h2 className="text-2xl font-semibold text-gray-900">{section.zh}</h2>
            <ul className="mt-3 space-y-2 text-gray-800 leading-relaxed list-disc pl-6">
              {section.zhBody.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <h3 className="text-lg font-semibold text-gray-700 mt-6">{section.en}</h3>
            <ul className="mt-2 space-y-2 text-gray-600 leading-relaxed list-disc pl-6">
              {section.enBody.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </section>
        ))}

        <section className="mt-10">
          <h2 className="text-2xl font-semibold text-gray-900">聯絡我們 Contact</h2>
          <p className="mt-3 text-gray-800">
            <a href={`mailto:${CONTACT_EMAIL}`} className="text-blue-700 hover:underline">
              {CONTACT_EMAIL}
            </a>
          </p>
        </section>
      </article>
    </div>
  );
}
