"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { BookOpen, ExternalLink, Search, Users } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PublicFellowshipEntry } from "@/app/types/publicFellowship";

async function fetchFellowships(): Promise<PublicFellowshipEntry[]> {
  const response = await fetch("/api/sc_api/fellowships", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load fellowship entries");
  }
  return response.json();
}

export function FellowshipArchive() {
  const [entries, setEntries] = useState<PublicFellowshipEntry[]>([]);
  const [query, setQuery] = useState("");
  const [series, setSeries] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFellowships()
      .then((data) => {
        setEntries(data);
        setError(null);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "載入團契資料失敗");
      })
      .finally(() => setLoading(false));
  }, []);

  const seriesOptions = useMemo(
    () =>
      Array.from(new Set(entries.map((entry) => entry.series).filter(Boolean) as string[])).sort(),
    [entries],
  );

  const filteredEntries = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return entries.filter((entry) => {
      const matchesSeries = !series || entry.series === series;
      const searchable = [entry.title, entry.series, entry.host, entry.date]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return matchesSeries && (!normalizedQuery || searchable.includes(normalizedQuery));
    });
  }, [entries, query, series]);

  const latest = filteredEntries[0];
  const rest = latest ? filteredEntries.slice(1) : filteredEntries;

  if (loading) {
    return <div className="py-16 text-center text-gray-500">正在載入團契回顧...</div>;
  }

  if (error) {
    return <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>;
  }

  return (
    <div className="space-y-10">
      <section className="grid gap-4 md:grid-cols-[1fr_240px]">
        <label className="relative block">
          <Search className="pointer-events-none absolute left-3 top-3 h-5 w-5 text-gray-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="w-full rounded-md border border-gray-300 bg-white py-3 pl-10 pr-3 text-base focus:outline-none focus:ring-2 focus:ring-[#8B4513]/30"
            placeholder="搜尋主題、系列、主講..."
          />
        </label>
        <select
          value={series}
          onChange={(event) => setSeries(event.target.value)}
          className="rounded-md border border-gray-300 bg-white px-3 py-3 text-base focus:outline-none focus:ring-2 focus:ring-[#8B4513]/30"
        >
          <option value="">全部系列</option>
          {seriesOptions.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </section>

      {latest && (
        <section className="rounded-lg border border-amber-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2 text-base font-semibold text-[#8B4513]">
            <Users className="h-5 w-5" />
            最新團契回顧
          </div>
          <FellowshipCard entry={latest} featured />
        </section>
      )}

      <section className="grid gap-5 lg:grid-cols-2">
        {rest.map((entry) => (
          <FellowshipCard key={entry.isoDate} entry={entry} />
        ))}
      </section>

      {filteredEntries.length === 0 && (
        <div className="rounded-md border border-gray-200 bg-white p-8 text-center text-gray-500">
          沒有符合條件的團契回顧。
        </div>
      )}
    </div>
  );
}

function FellowshipCard({
  entry,
  featured = false,
}: {
  entry: PublicFellowshipEntry;
  featured?: boolean;
}) {
  const previewLearnings = entry.keyLearnings.slice(0, featured ? 4 : 2);

  return (
    <article className={featured ? "" : "rounded-lg border border-gray-200 bg-white p-5 shadow-sm"}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-base font-semibold text-gray-500">{entry.date}</p>
          <h2 className="mt-1 text-2xl font-bold font-display text-gray-900">{entry.title || "團契查經"}</h2>
          <p className="mt-2 text-base text-gray-600">
            {[entry.series, entry.sequence ? `第 ${entry.sequence} 講` : null, entry.host]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <Link
          href={`/resources/fellowship/${entry.isoDate}`}
          className="inline-flex items-center gap-1 rounded-md bg-[#8B4513] px-4 py-2 text-base font-semibold text-white hover:bg-[#6f3710]"
        >
          查看回顧
          <ExternalLink className="h-4 w-4" />
        </Link>
      </div>

      {entry.summary && (
        <div className="prose prose-slate mt-4 max-w-none text-gray-700 prose-p:leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.summary}</ReactMarkdown>
        </div>
      )}

      {previewLearnings.length > 0 && (
        <ul className="mt-4 space-y-2 text-base text-gray-700">
          {previewLearnings.map((learning, index) => (
            <li key={index} className="flex gap-2">
              <BookOpen className="mt-0.5 h-4 w-4 flex-none text-[#8B4513]" />
              <div className="prose prose-slate max-w-none text-gray-700 prose-p:my-0">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{learning}</ReactMarkdown>
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap gap-2 text-xs text-gray-500">
        <span className="rounded-full bg-gray-100 px-3 py-1">來源 {entry.sourceLinks.length}</span>
        {entry.hasDocuments && (
          <span className="rounded-full bg-gray-100 px-3 py-1">登入可看文件 {entry.documentCount}</span>
        )}
      </div>
    </article>
  );
}
