"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, Loader2, Network } from "lucide-react";
import { AsOfStrip } from "../viewpoints/ViewpointChrome";
import type { Envelope } from "../viewpoints/types";

type Focal = {
  structure_role: string;
  viewpoint_id: string | null;
  viewpoint_revision_id: string;
  core_proposition: string | null;
  counts: { members: number; sources: number };
};

type Structure = {
  structure_id: string;
  structure_revision_id: string;
  central_synthesis: string;
  wording_label: string;
  focal: Focal[];
  unresolved_items: string[];
  review_status: string;
};

// Ordered so a reader meets the centre first and the supporting material after.
const ROLE_ORDER = [
  "central_claim",
  "negative_boundary",
  "positive_identification",
  "supporting_conclusion",
  "qualification",
  "tension_side",
  "application",
  "methodological_boundary",
];

const ROLE_LABELS: Record<string, string> = {
  central_claim: "中心主张",
  negative_boundary: "否定面",
  positive_identification: "正面识别",
  supporting_conclusion: "支持性结论",
  qualification: "限定",
  tension_side: "张力一方",
  application: "应用",
  methodological_boundary: "方法边界",
};

const ROLE_STYLES: Record<string, string> = {
  central_claim: "bg-slate-900 text-white",
  negative_boundary: "bg-rose-100 text-rose-800",
  positive_identification: "bg-emerald-100 text-emerald-800",
  supporting_conclusion: "bg-emerald-50 text-emerald-700",
  qualification: "bg-amber-100 text-amber-800",
  tension_side: "bg-orange-100 text-orange-800",
  application: "bg-indigo-100 text-indigo-800",
  methodological_boundary: "bg-sky-100 text-sky-800",
};

async function read<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `服务返回 ${response.status}`);
  return data as T;
}

export function StructureExplorer() {
  const [envelope, setEnvelope] = useState<Envelope<{ items: Structure[]; total: number }> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    read<Envelope<{ items: Structure[]; total: number }>>("/api/admin/wang/viewpoint-structures")
      .then(setEnvelope)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取中心结构"));
  }, []);

  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 p-5">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-rose-600" />
        <div>
          <p className="font-bold text-rose-900">读取失败</p>
          <p className="mt-1 text-sm text-rose-800">{error}</p>
        </div>
      </div>
    );
  }
  if (!envelope) {
    return (
      <p className="flex items-center gap-2 py-10 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />正在编译中心结构…
      </p>
    );
  }

  const { items, total } = envelope.data;
  return (
    <div className="space-y-5">
      <Link href="/admin/wang/viewpoints" className="inline-flex items-center gap-2 text-sm font-bold text-slate-600 hover:text-indigo-700">
        <ArrowLeft className="h-4 w-4" />返回观点主数据
      </Link>

      <div className="rounded-2xl bg-slate-950 p-6 text-white sm:p-8">
        <p className="text-sm font-bold uppercase tracking-wide text-indigo-300">Viewpoint structures</p>
        <h1 className="mt-1 text-3xl font-black">中心结构</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
          哪几个观点合起来构成一个完整论述，各自扮演什么角色。中心综合只组织已批准的观点，不新增主张，也不是观点的父节点。
        </p>
      </div>

      <AsOfStrip asOf={envelope.as_of} projectionSha={envelope.projection_sha256} />

      {total === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white px-5 py-14 text-center">
          <Network className="mx-auto h-7 w-7 text-slate-300" />
          <p className="mt-3 font-bold text-slate-800">这个快照还没有经审核的中心结构</p>
          <p className="mt-1 text-sm text-slate-500">观点可以先各自成立；只有解析出中心组织时才会建立 structure。</p>
        </div>
      ) : (
        items.map((structure) => {
          const focal = [...structure.focal].sort(
            (a, b) => ROLE_ORDER.indexOf(a.structure_role) - ROLE_ORDER.indexOf(b.structure_role),
          );
          return (
            <section key={structure.structure_id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
              <div className="border-b border-slate-200 bg-slate-50 p-5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-slate-200 px-2 py-1 text-xs font-bold text-slate-700">中心综合</span>
                  <span className="rounded bg-white px-2 py-1 text-xs text-slate-500">{structure.wording_label}</span>
                  <span className="ml-auto font-mono text-[11px] text-slate-400">{structure.structure_id}</span>
                </div>
                <p className="mt-3 text-base font-bold leading-7 text-slate-950">{structure.central_synthesis}</p>
              </div>

              <ul className="divide-y divide-slate-100">
                {focal.map((item) => (
                  <li key={item.viewpoint_revision_id} className="flex flex-wrap items-center gap-3 p-4 sm:px-5">
                    <span className={`rounded px-2 py-1 text-xs font-bold ${ROLE_STYLES[item.structure_role] ?? "bg-slate-100 text-slate-700"}`}>
                      {ROLE_LABELS[item.structure_role] ?? item.structure_role}
                    </span>
                    <span className="min-w-0 flex-1 text-sm font-semibold leading-6 text-slate-900">
                      {item.viewpoint_id ? (
                        <Link href={`/admin/wang/viewpoints/${item.viewpoint_id}`} className="hover:text-indigo-700 hover:underline">
                          {item.core_proposition ?? item.viewpoint_revision_id}
                        </Link>
                      ) : (
                        item.core_proposition ?? item.viewpoint_revision_id
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-slate-500">
                      <strong className="text-slate-900">{item.counts.members}</strong> 句 ·{" "}
                      <strong className="text-slate-900">{item.counts.sources}</strong> 篇讲道
                    </span>
                  </li>
                ))}
              </ul>

              {structure.unresolved_items.length > 0 && (
                <div className="border-t border-amber-200 bg-amber-50 p-4 sm:px-5">
                  <p className="text-xs font-bold text-amber-900">未决（讲员自己没有统一，不强行调和）</p>
                  <ul className="mt-2 space-y-1.5">
                    {structure.unresolved_items.map((text) => (
                      <li key={text} className="text-sm leading-6 text-amber-900">· {text}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}
