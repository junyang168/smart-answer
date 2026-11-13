"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArgumentUpdateStatus } from "@/app/components/series/ArgumentUpdateStatus";
import { useRouter } from "next/navigation";
import { AlertCircle, Loader2 } from "lucide-react";
import { CatalogView } from "../source-coverage/CatalogView";
import type { Overview } from "../source-coverage/types";

// The sermons by book and chapter, or by topic; opening one goes to its text
// and argument graph (#453). The catalog is the source-coverage one.
export function SermonCatalog() {
  const router = useRouter();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<"all" | "grouped">("all");
  const [filter, setFilter] = useState<"all" | "completed" | "pending">("all");

  useEffect(() => {
    let active = true;
    fetch("/api/admin/source-coverage/sources", { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `服务返回 ${response.status}`);
        if (active) setOverview(payload);
      })
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "无法读取讲道目录"));
    return () => { active = false; };
  }, []);

  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!overview) return <p className="flex items-center gap-2 py-10 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取讲道目录…</p>;
  const rows = overview.catalog.filter((row) => filter === "all" ||
    (row.argument_update_status === "completed" ? filter === "completed" : filter === "pending"));

  return (
    <>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-2">
          檢視
          <select className="rounded border border-slate-300 bg-white p-2" value={view}
            onChange={(event) => setView(event.target.value === "grouped" ? "grouped" : "all")}>
            <option value="all">全部講道與母本</option>
            <option value="grouped">按經卷或主題</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          論證層更新
          <select className="rounded border border-slate-300 bg-white p-2" value={filter}
            onChange={(event) => {
              const value = event.target.value;
              setFilter(value === "completed" || value === "pending" ? value : "all");
            }}>
            <option value="all">全部狀態</option>
            <option value="completed">已完成</option>
            <option value="pending">待更新或未有完成紀錄</option>
          </select>
        </label>
        <span className="text-slate-500">{rows.length} 筆來源</span>
      </div>
      {view === "grouped" ? (
        <CatalogView catalog={rows} sources={overview.sources}
          onOpen={(sourceId) => router.push(`/admin/wang/sermons/${encodeURIComponent(sourceId)}`)} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th scope="col" className="px-4 py-3">來源</th>
                <th scope="col" className="whitespace-nowrap px-4 py-3">類型</th>
                <th scope="col" className="whitespace-nowrap px-4 py-3">論證層更新</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.kind}:${row.item ?? row.file}`} className="border-t border-slate-100">
                  <td className="px-4 py-2">
                    {row.source_id ? (
                      <Link className="font-medium text-indigo-700 hover:underline"
                        href={`/admin/wang/sermons/${encodeURIComponent(row.source_id)}`}>
                        {row.title}
                      </Link>
                    ) : row.title}
                    {row.file && row.file !== row.title ? <div className="text-xs text-slate-500">{row.file}</div> : null}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2">{row.kind === "notes_manuscript" ? "母本" : "講道"}</td>
                  <td className="whitespace-nowrap px-4 py-2"><ArgumentUpdateStatus status={row.argument_update_status} /></td>
                </tr>
              ))}
              {!rows.length ? <tr><td colSpan={3} className="px-4 py-8 text-center text-slate-500">沒有符合條件的來源</td></tr> : null}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
