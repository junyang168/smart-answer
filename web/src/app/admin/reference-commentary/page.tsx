"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { API, chapterHref, getJson, pageHref, type SearchHit, type Volume } from "./types";

/**
 * 參考釋經書的書架：先挑一章讀。
 *
 * Carson's pages arrive from the iCloud scan inbox; this is the one place
 * their text is read (design §6). Chapters come first because reading a
 * chapter is what the owner does most. Below them: pages set aside because
 * their number could not be read, and search.
 */
export default function ReferenceCommentaryShelf() {
  const [volumes, setVolumes] = useState<Volume[] | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    getJson<{ volumes: Volume[] }>(API)
      .then((data) => setVolumes(data.volumes))
      .catch((reason: Error) => setError(reason.message));
  }, [tick]);

  const chapters = useMemo(() => {
    const byChapter = new Map<number, { pages: number; passages: Map<string, { volume: string; page: string }> }>();
    for (const volume of volumes ?? []) {
      for (const page of volume.pages) {
        for (const chapter of page.chapters) {
          const entry = byChapter.get(chapter) ?? { pages: 0, passages: new Map() };
          entry.pages += 1;
          for (const passage of page.passages) {
            if (passage.startsWith(`${chapter}:`) && !entry.passages.has(passage)) {
              entry.passages.set(passage, { volume: volume.volume_id, page: page.printed_page });
            }
          }
          byChapter.set(chapter, entry);
        }
      }
    }
    return [...byChapter.entries()].sort(([a], [b]) => a - b);
  }, [volumes]);

  if (error) return <p className="px-4 py-6 text-sm text-rose-700">{error}</p>;
  if (!volumes) return <p className="px-4 py-6 text-sm text-slate-400">讀取中…</p>;

  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 px-4 pb-16 pt-4">
      <header>
        <h1 className="text-lg font-semibold text-slate-900">參考釋經書</h1>
        <p className="text-sm text-slate-500">D. A. Carson, Matthew (EBC)。用 iPhone「檔案」掃描進 iCloud 的 Carson/Matthew/chNN，幾分鐘後在這裡出現。</p>
      </header>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">按章閱讀</h2>
        {chapters.length === 0 ? (
          <p className="text-sm text-slate-500">還沒有任何一頁。</p>
        ) : (
          <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
            {chapters.map(([chapter, entry]) => (
              <li key={chapter} className="px-4 py-3">
                <Link href={chapterHref(chapter)} className="flex items-baseline justify-between gap-3">
                  <span className="text-base font-medium text-sky-800">馬太福音 {chapter} 章</span>
                  <span className="text-xs text-slate-500">{entry.pages} 頁</span>
                </Link>
                {entry.passages.size > 0 && (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    {[...entry.passages.entries()].map(([passage, where]) => (
                      <Link key={passage} href={pageHref(where.volume, where.page)} className="text-slate-500 hover:text-sky-700">
                        {passage}
                      </Link>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Unassigned volumes={volumes} onAssigned={() => setTick((n) => n + 1)} />

      <Search />

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">書架</h2>
        <ul className="flex flex-col gap-3">
          {volumes.map((volume) => (
            <li key={volume.volume_id} className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm">
              <p className="font-medium text-slate-800">{volume.title}</p>
              <p className="text-slate-500">
                {volume.pages.length} 頁已建檔 · {volume.proofread} 頁已校對
                {volume.isbn ? ` · ISBN ${volume.isbn}` : ""}
                {!volume.confirmed && " · 冊身份待確認（還沒拍版權頁）"}
              </p>
              {volume.missing_pages.length > 0 && (
                <p className="text-amber-700">缺頁：{volume.missing_pages.join("、")}</p>
              )}
              <p className="break-all font-mono text-[0.7rem] text-slate-400">{volume.volume_id}</p>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function Unassigned({ volumes, onAssigned }: { volumes: Volume[]; onAssigned: () => void }) {
  const items = volumes.flatMap((volume) => volume.unassigned.map((item) => ({ volume: volume.volume_id, id: item.id })));
  const [pages, setPages] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  if (items.length === 0) return null;

  const assign = async (volume: string, id: string) => {
    const response = await fetch(`${API}/volumes/${encodeURIComponent(volume)}/unassigned/${id}/assign`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ printed_page: pages[id] ?? "" }),
    });
    const body = await response.json().catch(() => null);
    setMessage(response.ok ? `已存為第 ${body.printed_page} 頁` : body?.detail ?? "無法儲存");
    if (response.ok) onAssigned();
  };

  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold text-slate-700">待確認頁碼</h2>
      <p className="mb-2 text-xs text-slate-500">這些頁讀不出印刷頁碼，沒有猜。看圖填上頁碼就會歸位。</p>
      <ul className="flex flex-col gap-4">
        {items.map(({ volume, id }) => (
          <li key={id} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
            {/* eslint-disable-next-line @next/next/no-img-element -- admin image served by the backend */}
            <img src={`${API}/volumes/${encodeURIComponent(volume)}/unassigned/${id}/image`} alt="待確認頁碼的掃描頁" className="max-h-[28rem] w-auto rounded" />
            <div className="mt-2 flex items-center gap-2">
              <input
                inputMode="numeric"
                placeholder="頁碼"
                value={pages[id] ?? ""}
                onChange={(event) => setPages({ ...pages, [id]: event.target.value })}
                className="w-24 rounded border border-slate-300 px-2 py-1 text-sm"
              />
              <button onClick={() => void assign(volume, id)} className="rounded bg-sky-700 px-3 py-1 text-sm text-white">
                存為這一頁
              </button>
            </div>
          </li>
        ))}
      </ul>
      {message && <p className="mt-2 text-sm text-slate-600">{message}</p>}
    </section>
  );
}

function Search() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState("");

  const run = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const data = await getJson<{ hits: SearchHit[] }>(`${API}/search?q=${encodeURIComponent(query)}`);
      setHits(data.hits);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "搜尋失敗");
    }
  };

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold text-slate-700">搜尋</h2>
      <form onSubmit={(event) => void run(event)} className="flex gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="英文或希臘文，不分大小寫與重音"
          className="min-w-0 flex-1 rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
        <button className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white">搜尋</button>
      </form>
      {error && <p className="mt-2 text-sm text-rose-700">{error}</p>}
      {hits && (
        <ul className="mt-3 flex flex-col gap-2 text-sm">
          {hits.length === 0 && <li className="text-slate-500">沒有找到。</li>}
          {hits.map((hit) => (
            <li key={`${hit.volume_id}-${hit.printed_page}`}>
              <Link href={pageHref(hit.volume_id, hit.printed_page)} className="text-sky-800">
                第 {hit.printed_page} 頁
              </Link>
              <span className="ml-2 text-slate-600">{hit.snippet}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
