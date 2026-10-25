"use client";

import { useEffect, useState } from "react";
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
  return (
    <CatalogView catalog={overview.catalog} sources={overview.sources}
      onOpen={(sourceId) => router.push(`/admin/wang/sermons/${encodeURIComponent(sourceId)}`)} />
  );
}
