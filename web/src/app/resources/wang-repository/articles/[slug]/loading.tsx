export default function LoadingArticle() {
  return (
    <main className="min-h-[70vh] bg-[#f7f4ee] px-5 py-16" aria-busy="true">
      <div className="mx-auto max-w-3xl animate-pulse">
        <div className="h-4 w-36 rounded bg-stone-200" />
        <div className="mt-8 h-12 w-full rounded bg-stone-200" />
        <div className="mt-4 h-6 w-2/3 rounded bg-stone-200" />
        <div className="mt-12 space-y-4">
          <div className="h-4 rounded bg-stone-200" />
          <div className="h-4 rounded bg-stone-200" />
          <div className="h-4 w-5/6 rounded bg-stone-200" />
        </div>
        <span className="sr-only">正在準備文章…</span>
      </div>
    </main>
  );
}
