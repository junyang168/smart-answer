"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API, chapterHref, getJson, pageHref, type PageDetail } from "../../../types";

/**
 * 單頁：掃描圖對照轉寫文字，在這裡校對。
 *
 * Side by side on a wide screen, stacked on a phone. Saving never overwrites:
 * the backend stores the corrected text as a new version and marks the page
 * proofread, and it refuses a save based on text that changed meanwhile.
 */
export default function PageView() {
  const params = useParams();
  const one = (value: string | string[] | undefined) => decodeURIComponent(Array.isArray(value) ? value[0] : String(value));
  const volumeId = one(params.volumeId);
  const printedPage = one(params.page);
  const base = `${API}/volumes/${encodeURIComponent(volumeId)}/pages/${encodeURIComponent(printedPage)}`;

  const [page, setPage] = useState<PageDetail | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [message, setMessage] = useState("");
  const [version, setVersion] = useState<{ sha: string; text: string } | null>(null);

  useEffect(() => {
    setPage(null);
    setEditing(false);
    setVersion(null);
    getJson<PageDetail>(base)
      .then((value) => {
        setPage(value);
        setError("");
      })
      .catch((reason: Error) => setError(reason.message));
  }, [base]);

  const save = async () => {
    if (!page) return;
    setMessage("儲存中…");
    const response = await fetch(`${base}/proofread`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: draft, expected_sha256: page.text_sha256 }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      setMessage(typeof body?.detail === "string" ? body.detail : "無法儲存");
      return;
    }
    setPage(body as PageDetail);
    setEditing(false);
    setMessage("已存為新版本，標記為已校對。");
  };

  const showVersion = async (sha: string) => {
    const value = await getJson<{ sha256: string; text: string }>(`${base}/versions/${sha}`);
    setVersion({ sha, text: value.text });
  };

  if (error) return <p className="px-4 py-6 text-sm text-rose-700">{error}</p>;
  if (!page) return <p className="px-4 py-6 text-sm text-slate-400">讀取中…</p>;

  const chapter = page.chapters[0];

  return (
    <main className="mx-auto max-w-6xl px-4 pb-16 pt-4">
      <nav className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <Link href="/admin/reference-commentary" className="text-sky-800">書架</Link>
        {chapter && <Link href={chapterHref(chapter)} className="text-sky-800">馬太福音 {chapter} 章</Link>}
        <span className="font-medium text-slate-800">第 {page.printed_page} 頁</span>
        <span className={page.status === "proofread" ? "text-emerald-700" : "text-slate-500"}>
          {page.status === "proofread" ? "已校對" : "機器轉寫，未校對"}
        </span>
        <span className="ml-auto flex gap-3">
          {page.previous_page && <Link href={pageHref(page.volume_id, page.previous_page)} className="text-sky-800">← {page.previous_page}</Link>}
          {page.next_page && <Link href={pageHref(page.volume_id, page.next_page)} className="text-sky-800">{page.next_page} →</Link>}
        </span>
      </nav>

      <div className="grid gap-4 lg:grid-cols-2">
        <figure className="lg:sticky lg:top-4 lg:self-start">
          <a href={`${base}/image`} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element -- admin image served by the backend */}
            <img src={`${base}/image`} alt={`第 ${page.printed_page} 頁掃描`} className="w-full rounded border border-slate-200" />
          </a>
          <figcaption className="mt-1 text-xs text-slate-400">點圖看原尺寸{page.image_history.length > 0 && ` · 另有 ${page.image_history.length} 張舊掃描`}</figcaption>
        </figure>

        <section className="min-w-0">
          {page.gaps.length > 0 && (
            <p className="mb-2 rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
              {page.gaps.length} 處看不清，文字裡標為 […]：第 {page.gaps.map((gap) => gap.line).join("、")} 行
            </p>
          )}
          {page.pending_ocr_text && (
            <details className="mb-2 rounded bg-sky-50 px-3 py-2 text-sm">
              <summary className="cursor-pointer text-sky-800">這一頁重掃過，新的機器轉寫沒有蓋掉你的校對，點開比較</summary>
              <pre className="mt-2 whitespace-pre-wrap font-serif text-slate-700">{page.pending_ocr_text}</pre>
            </details>
          )}

          {editing ? (
            <div className="flex flex-col gap-2">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                className="min-h-[60vh] w-full rounded border border-slate-300 p-3 font-mono text-sm leading-relaxed"
                spellCheck={false}
              />
              <div className="flex gap-2">
                <button onClick={() => void save()} className="rounded bg-sky-700 px-3 py-1.5 text-sm text-white">存為校對版</button>
                <button onClick={() => setEditing(false)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">取消</button>
              </div>
            </div>
          ) : (
            <>
              <article className="prose max-w-none font-serif prose-headings:font-sans">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.text}</ReactMarkdown>
              </article>
              <button
                onClick={() => {
                  setDraft(page.text);
                  setEditing(true);
                  setMessage("");
                }}
                className="mt-4 rounded border border-slate-300 px-3 py-1.5 text-sm"
              >
                校對這一頁
              </button>
            </>
          )}
          {message && <p className="mt-2 text-sm text-slate-600">{message}</p>}

          <details className="mt-6 text-sm">
            <summary className="cursor-pointer text-slate-600">版本（{page.revisions.length}）</summary>
            <ol className="mt-2 flex flex-col gap-1">
              {[...page.revisions].reverse().map((revision) => (
                <li key={`${revision.at}-${revision.to}`} className="flex flex-wrap gap-2 text-slate-600">
                  <span>{new Date(revision.at).toLocaleString()}</span>
                  <span>{revision.source === "proofread" ? "校對" : "機器轉寫"}</span>
                  {revision.to !== page.text_sha256 && (
                    <button onClick={() => void showVersion(revision.to)} className="text-sky-800">看這一版</button>
                  )}
                </li>
              ))}
            </ol>
            {version && (
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 font-serif text-slate-700">{version.text}</pre>
            )}
          </details>
        </section>
      </div>
    </main>
  );
}
