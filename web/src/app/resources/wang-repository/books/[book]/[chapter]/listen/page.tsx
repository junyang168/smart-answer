"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import ChapterListener from "../../../../audio/ChapterListener";

/**
 * 一章的聆聽模式：投影片、逐字稿、播放控制同一個畫面。
 *
 * 網址 `?source=<來源>&section=<經文段>`：從總覽某一段的「聽這段」進來，就從那
 * 一段開始；換段時網址跟著換，複製下來的連結打開就是那一段。
 */

type Source = { name: string; page?: { kind: string; passage?: string } };
type ChapterData = { name: string; chapter: number; sources: Source[] };

export default function ListenPage() {
  return (
    <Suspense fallback={null}>
      <Listen />
    </Suspense>
  );
}

function Listen() {
  const routeParams = useParams();
  const slug = String(routeParams?.book ?? "");
  const number = String(routeParams?.chapter ?? "");
  const params = useSearchParams();
  const [data, setData] = useState<ChapterData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`/api/public/repository/books/${slug}/${number}`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then(setData)
      .catch(() => setError("這一章還沒有講道。"));
  }, [slug, number]);

  const wanted = params.get("source") ?? "";
  const source =
    data?.sources.find((item) => item.name === wanted && item.page?.kind === "chapter") ??
    data?.sources.find((item) => item.page?.kind === "chapter");
  const back = `/resources/wang-repository/books/${slug}/${number}${
    source ? `?source=${encodeURIComponent(source.name)}` : ""
  }`;

  if (error || (data && !source?.page?.passage)) {
    return <p className="mx-auto max-w-4xl px-5 py-16 text-rose-800">{error || "這一章還沒有按章的錄音頁面。"}</p>;
  }
  if (!data || !source?.page?.passage) {
    return (
      <p className="mx-auto max-w-4xl px-5 py-16 text-stone-600" role="status">
        正在讀取…
      </p>
    );
  }
  return (
    <ChapterListener
      passage={source.page.passage}
      title={`${data.name} 第 ${data.chapter} 章`}
      source={source.name}
      backHref={back}
      initialSection={params.get("section") ?? undefined}
      initialRecording={params.get("recording") ?? undefined}
      initialTime={params.has("at") ? Number(params.get("at")) : undefined}
    />
  );
}
