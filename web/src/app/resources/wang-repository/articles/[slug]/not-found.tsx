import Link from "next/link";

export default function ArticleNotFound() {
  return (
    <main className="min-h-[65vh] bg-[#f7f4ee] px-5 py-20 text-center">
      <h1 className="text-3xl font-bold text-stone-900">找不到這篇文章</h1>
      <p className="mt-4 text-stone-600">文章可能尚未通過發布，或網址有誤。</p>
      <Link href="/resources/wang-repository" className="mt-7 inline-flex rounded-full bg-stone-900 px-6 py-3 font-semibold text-white">
        返回聖經講論文庫
      </Link>
    </main>
  );
}
