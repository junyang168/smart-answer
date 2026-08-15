"use client";

export default function ArticleError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="min-h-[65vh] bg-[#f7f4ee] px-5 py-20 text-center">
      <h1 className="text-3xl font-bold text-stone-900">文章暫時無法開啟</h1>
      <p className="mt-4 text-stone-600">請稍後再試；已公開的文章內容不會因此受到影響。</p>
      <button type="button" onClick={reset} className="mt-7 rounded-full bg-stone-900 px-6 py-3 font-semibold text-white">
        重新載入
      </button>
    </main>
  );
}
