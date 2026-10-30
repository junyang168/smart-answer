"use client";
import { use, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
type Publication = {
  metadata: { title: string; review_revision: number };
  script: { index: string; text: string }[];
  groups: {
    id: string;
    slide_id: string;
    hidden: boolean;
    before_paragraph_id: string;
  }[];
  occurrences: Record<string, { asset_id: string }>;
  visual_assets: Record<string, { visible_text: string }>;
  paragraph_bindings: Record<string, { start_ms: number }>;
};
export default function TranscriptPublication({
  params,
  apiBase = "/api/sc_api",
}: {
  params: Promise<{ item: string }>;
  apiBase?: string;
}) {
  const { item } = use(params);
  const [publication, setPublication] = useState<Publication>();
  const [error, setError] = useState("");
  const base = `${apiBase}/review_public/${encodeURIComponent(item)}`;
  useEffect(() => {
    let alive = true;
    fetch(base, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw Error((await r.json()).detail || "無法載入已發佈版本");
        return r.json();
      })
      .then((v) => {
        if (alive) setPublication(v);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [base]);
  if (!publication) return <p className="p-8">{error || "正在載入逐字稿…"}</p>;
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-2 text-3xl font-semibold">
        {publication.metadata.title}
      </h1>
      <p className="mb-8 text-sm text-slate-500">
        已發佈第 {publication.metadata.review_revision} 版
      </p>
      {publication.script.map((p) => (
        <section key={p.index} className="mb-7">
          {publication.groups
            .filter((g) => !g.hidden && g.before_paragraph_id === p.index)
            .map((g) => {
              const aid = publication.occurrences[g.slide_id].asset_id;
              return (
                <figure key={g.id} className="my-6 rounded-lg border p-3">
                  <img
                    className="w-full"
                    src={`${base}/assets/${aid}`}
                    alt="教授原始投影片"
                  />
                  <figcaption className="mt-2 text-sm text-slate-500">
                    投影片（非口述）
                  </figcaption>
                  <details className="mt-2 text-sm">
                    <summary>投影片文字（機器辨識，非口述）</summary>
                    <p className="whitespace-pre-wrap leading-7">
                      {publication.visual_assets[aid].visible_text}
                    </p>
                  </details>
                </figure>
              );
            })}
          <div className="prose max-w-none leading-8">
            <ReactMarkdown>{p.text}</ReactMarkdown>
          </div>
        </section>
      ))}
    </main>
  );
}
