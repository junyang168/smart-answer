"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, ArrowLeftRight, BookOpenText, Loader2, Route } from "lucide-react";
import { RELATION_COLORS, sermonHref, type ViewpointDetailData } from "./graph-types";

const ROLE_LABELS: Record<string, string> = { observation: "观察", bridge: "推论", conclusion: "结论", premise: "前提" };

function Section({ title, count, children, open = false }: { title: string; count?: number; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} className="border-t border-slate-200">
      <summary className="cursor-pointer px-5 py-3 text-sm font-black text-slate-900">
        {title}{count !== undefined ? <span className="ml-2 font-normal text-slate-500">{count}</span> : null}
      </summary>
      <div className="px-5 pb-4">{children}</div>
    </details>
  );
}

// The selected viewpoint, beside the graph: how the professor argues it in each
// sermon, the Claims it is built from, and its recorded relations (#453).
export function ViewpointPanel({ viewpointId, onSelect }: { viewpointId: string; onSelect: (id: string) => void }) {
  const [detail, setDetail] = useState<ViewpointDetailData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`/api/admin/wang/graph/viewpoints/${encodeURIComponent(viewpointId)}`, { cache: "no-store" })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `服务返回 ${response.status}`);
        if (active) { setDetail(payload); setError(""); }
      })
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : "无法读取观点"));
    return () => { active = false; };
  }, [viewpointId]);

  if (error) return <p className="flex items-center gap-2 rounded-xl bg-rose-50 p-4 text-sm text-rose-800"><AlertCircle className="h-4 w-4" />{error}</p>;
  if (!detail || detail.id !== viewpointId) return <p className="flex items-center gap-2 p-6 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" />读取观点…</p>;

  return (
    <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <header className="p-5">
        {detail.scripture.length > 0 ? <p className="text-xs font-bold text-slate-500">{detail.scripture.join("；")}</p> : null}
        <h2 className="mt-1 text-lg font-black leading-7 text-slate-950">{detail.text}</h2>
        {detail.summary ? <p className="mt-2 text-sm leading-7 text-slate-600">{detail.summary}</p> : null}
      </header>

      <Section title="论证路线" count={detail.routes.length} open>
        {detail.routes.length === 0 ? <p className="text-sm text-slate-500">还没有记录论证路线。</p> : (
          <ul className="space-y-4">
            {detail.routes.map((route) => (
              <li key={route.id} className="rounded-xl bg-slate-50 p-3">
                <p className="flex items-start gap-2 text-sm font-bold text-slate-900"><Route className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" />{route.label}</p>
                <ol className="mt-2 space-y-1.5 text-sm leading-6 text-slate-700">
                  {route.steps.map((step) => (
                    <li key={step.key}><span className="mr-1.5 text-xs font-bold text-slate-400">{ROLE_LABELS[step.role] ?? step.role}</span>{step.text}</li>
                  ))}
                </ol>
                <p className="mt-2 text-xs text-slate-500">
                  出自：{route.sermons.map((sermon, index) => (
                    <span key={sermon.source_id}>{index > 0 ? "、" : ""}<Link href={sermonHref(sermon.source_id)} className="text-indigo-700 hover:underline">{sermon.title}</Link></span>
                  ))}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {detail.observations.length > 0 ? (
        <Section title="经文观察" count={detail.observations.length}>
          <ul className="space-y-3 text-sm leading-6 text-slate-700">
            {detail.observations.map((observation, index) => (
              <li key={index}>
                {observation.summary}
                {observation.supports ? <span className="block text-slate-500">{observation.supports}</span> : null}
                <Link href={sermonHref(observation.sermon.source_id)} className="text-xs text-indigo-700 hover:underline">{observation.sermon.title}</Link>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="链接的主张" count={detail.claims.length} open>
        <ul className="space-y-3 text-sm leading-6">
          {detail.claims.map((claim) => (
            <li key={claim.id} className="text-slate-800">
              {claim.text}
              <span className="block text-xs">
                {claim.sermons.map((sermon) => (
                  <Link key={sermon.source_id} href={sermonHref(sermon.source_id, claim.id)} className="mr-2 inline-flex items-center gap-1 text-indigo-700 hover:underline">
                    <BookOpenText className="h-3 w-3" />{sermon.title}
                  </Link>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="相关观点" count={detail.relations.length} open={detail.relations.length > 0}>
        {detail.relations.length === 0 ? <p className="text-sm text-slate-500">还没有记录与其他观点的关系。</p> : (
          <ul className="space-y-3 text-sm leading-6">
            {detail.relations.map((relation, index) => (
              <li key={index}>
                <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold text-white" style={{ backgroundColor: RELATION_COLORS[relation.type] ?? "#94a3b8" }}>
                  <ArrowLeftRight className="h-3 w-3" />{relation.direction === "out" ? `本观点${relation.label}→` : `←${relation.label}本观点`}
                </span>
                <button type="button" onClick={() => onSelect(relation.other_id)} className="mt-1 block text-left text-indigo-800 hover:underline">{relation.other_text}</button>
                {relation.reason ? <span className="block text-xs leading-5 text-slate-500">{relation.reason}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="版本历史" count={detail.history.length}>
        <ol className="space-y-2 text-sm leading-6 text-slate-700">
          {detail.history.map((entry, index) => (
            <li key={index} className={entry.current ? "font-semibold" : "text-slate-500"}>
              {entry.approved_at ? <span className="mr-2 text-xs text-slate-400">{entry.approved_at.slice(0, 10)}</span> : null}
              {entry.text}{entry.current ? "（现行）" : ""}
            </li>
          ))}
        </ol>
      </Section>
    </article>
  );
}
