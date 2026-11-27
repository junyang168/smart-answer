"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Item = {
  item: string;
  title: string;
  assigned_to_name?: string;
  status: string;
};
export default function BetaLectures() {
  const [items, setItems] = useState<Item[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/sc_api/beta/review_items", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("無法載入 beta 講道清單");
        setItems(await response.json());
      })
      .catch((error) => {
        if (error.name !== "AbortError") setError(error.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="mx-auto max-w-4xl p-4">
      <h1 className="mb-4 text-2xl font-semibold">逐字稿審核 Beta</h1>
      {error && <p role="alert">{error}</p>}
      {!items && !error && <p>正在載入…</p>}
      <ul className="space-y-3">
        {items?.map((item) => (
          <li key={item.item} className="rounded-lg border bg-white p-4">
            <Link
              className="font-semibold text-blue-700 underline"
              href={`/admin/transcript-beta/${encodeURIComponent(item.item)}`}
            >
              {item.title || item.item}
            </Link>
            <p className="mt-1 text-sm text-slate-600">
              {item.item} ·{" "}
              {item.assigned_to_name
                ? `認領人：${item.assigned_to_name}`
                : "尚未認領"}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
