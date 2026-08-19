import Link from "next/link";
import {
  ArrowUpRight,
  BookOpen,
  CalendarDays,
  FileText,
  Library,
  Radio,
  Settings,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

type AdminItem = {
  title: string;
  description: string;
  href: string;
};

type AdminGroup = {
  title: string;
  description: string;
  icon: LucideIcon;
  accent: string;
  items: AdminItem[];
};

const groups: AdminGroup[] = [
  {
    title: "講道與文庫",
    description: "從原始講道、筆記和逐字稿，整理可審閱、可發布的內容。",
    icon: BookOpen,
    accent: "bg-sky-100 text-sky-700",
    items: [
      { title: "講道稿件", description: "管理講道逐字稿、認領狀態與發布流程。", href: "/admin/surmons" },
      { title: "講道系列", description: "建立系列並整理系列內的講道內容。", href: "/admin/surmon_series" },
      { title: "筆記與逐字稿生成講稿", description: "執行 AI 整理、審閱、合併與發布流程。", href: "/admin/notes-to-sermon/series" },
      { title: "Wang 釋經與思想文庫", description: "統一查看文章進度、內容候選、知識審核與出版單元。", href: "/admin/wang" },
      { title: "完整文章", description: "編輯並發布由講道產生的全文內容。", href: "/admin/full_article" },
    ],
  },
  {
    title: "內容出版",
    description: "維護網站上的問答、節目和短影音內容。",
    icon: Library,
    accent: "bg-violet-100 text-violet-700",
    items: [
      { title: "信仰問答", description: "整理常見問題與對應解答。", href: "/admin/qa" },
      { title: "信仰的深度", description: "維護網播音訊、摘要、經文與發布資訊。", href: "/admin/webcast/depth-of-faith" },
      { title: "微講道", description: "維護短影音標題、系列、連結與說明。", href: "/admin/micro-sermon" },
    ],
  },
  {
    title: "教會事工",
    description: "安排聚會、主日服事與同工資料。",
    icon: CalendarDays,
    accent: "bg-emerald-100 text-emerald-700",
    items: [
      { title: "主日服事", description: "安排同工、詩歌、讀經與家事報告。", href: "/admin/sunday-service" },
      { title: "團契資料", description: "維護團契時間、主題與主領資訊。", href: "/admin/fellowship" },
    ],
  },
  {
    title: "溝通與系統",
    description: "處理會眾聯絡、郵件與後台權限。",
    icon: Settings,
    accent: "bg-amber-100 text-amber-700",
    items: [
      { title: "新朋友資訊", description: "查看聯絡表單提交的資料與留言。", href: "/admin/contacts" },
      { title: "發送 Email", description: "向會眾發送自訂郵件。", href: "/admin/email" },
      { title: "Email 收件人", description: "維護各類提醒郵件的收件人名單。", href: "/admin/email/recipients" },
      { title: "使用者與權限", description: "管理後台使用者及其角色。", href: "/admin/users" },
    ],
  },
];

const quickLinks = [
  { title: "王教授文庫", href: "/admin/wang", icon: BookOpen },
  { title: "講稿整理", href: "/admin/notes-to-sermon/series", icon: FileText },
  { title: "講道稿件", href: "/admin/surmons", icon: Radio },
];

export default function AdminHomePage() {
  return (
    <div className="min-h-screen bg-slate-50 py-8 sm:py-12">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <header className="rounded-2xl bg-slate-900 px-6 py-8 text-white shadow-sm sm:px-8">
          <p className="text-sm font-semibold text-sky-300">Dallas Holy Logos Church</p>
          <h1 className="mt-2 text-3xl font-bold sm:text-4xl">管理後台</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300 sm:text-base">
            按工作性質選擇管理區域；常用功能可從下方直接進入。
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {quickLinks.map((item) => {
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} className="flex items-center gap-3 rounded-xl bg-white/10 px-4 py-3 font-semibold transition hover:bg-white/20">
                  <Icon className="h-5 w-5 text-sky-300" aria-hidden="true" />
                  <span>{item.title}</span>
                </Link>
              );
            })}
          </div>
        </header>

        <div className="mt-8 grid items-start gap-6 lg:grid-cols-2">
          {groups.map((group) => {
            const GroupIcon = group.icon;
            return (
              <section key={group.title} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex gap-4 border-b border-slate-100 p-5 sm:p-6">
                  <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${group.accent}`}>
                    <GroupIcon className="h-5 w-5" aria-hidden="true" />
                  </div>
                  <div>
                    <h2 className="text-xl font-bold text-slate-950">{group.title}</h2>
                    <p className="mt-1 text-sm leading-6 text-slate-600">{group.description}</p>
                  </div>
                </div>
                <div className="divide-y divide-slate-100">
                  {group.items.map((item) => (
                    <Link key={item.href} href={item.href} className="group flex items-center justify-between gap-4 px-5 py-4 transition hover:bg-slate-50 sm:px-6">
                      <div>
                        <h3 className="font-semibold text-slate-900 group-hover:text-indigo-700">{item.title}</h3>
                        <p className="mt-1 text-sm leading-5 text-slate-500">{item.description}</p>
                      </div>
                      <ArrowUpRight className="h-5 w-5 shrink-0 text-slate-300 transition group-hover:text-indigo-600" aria-hidden="true" />
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
