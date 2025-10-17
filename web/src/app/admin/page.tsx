import Link from "next/link";

const cards = [
  {
    title: "編輯完整文章",
    description: "管理由講道產生的全文內容，查看、編輯並發布最新文章。",
    href: "/admin/full_article",
  },
  {
    title: "編輯問答",
    description: "維護問答資料庫，整理常見問題與對應解答。",
    href: "/admin/qa",
  },
  {
    title: "團契資料管理",
    description: "維護雙週團契聚會的時間、主題與主領資訊 (僅管理員)。",
    href: "/admin/fellowship",
  },
  {
    title: "講道系列管理",
    description: "建立講道系列、編輯摘要與管理歸屬的講道內容。",
    href: "/admin/surmon_series",
  },
];

export default function AdminHomePage() {
  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="mx-auto max-w-4xl px-6">
        <header className="mb-10 space-y-2">
          <h1 className="text-3xl font-bold text-gray-900">管理後台</h1>
          <p className="text-gray-600">選擇下列項目快速進入對應的管理模組。</p>
        </header>

        <div className="grid gap-6 sm:grid-cols-2">
          {cards.map((card) => (
            <Link
              key={card.href}
              href={card.href}
              className="block rounded-xl border border-gray-200 bg-white p-6 shadow-sm transition hover:border-blue-300 hover:shadow-md"
            >
              <h2 className="text-xl font-semibold text-gray-900">{card.title}</h2>
              <p className="mt-3 text-sm text-gray-600">{card.description}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
