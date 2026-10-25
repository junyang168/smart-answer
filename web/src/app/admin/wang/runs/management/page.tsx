import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { backendJson } from "../../backend";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "管理运行 | 王教授文库后台",
  robots: { index: false, follow: false },
};

type Management = {
  cvp_actions: {
    run_id: string; action_id: string | null; started_at: string | null; status: string | null;
    units: Record<string, number>; open: { unit: string; status: string; detail: string }[];
  }[];
  claim_actions: {
    run_id: string; action_id: string | null; operation: string | null; claims: number;
    started_at: string | null; status: string | null; reason: string | null; continued_by: string | null;
  }[];
  human_decisions: {
    decision_id: string; source_id: string; source_title: string; kind: string | null;
    text: string; created_at: string | null; superseded: boolean;
  }[];
  calibrations: {
    calibrated_at: string | null; model: string | null; prompt_sha256: string | null;
    key_id: string | null; agrees: boolean | null; missed: string[];
  }[];
};

function day(at: string | null): string {
  return at ? at.slice(0, 16).replace("T", " ") : "";
}

function tone(status: string | null): string {
  if (status === "completed") return "bg-emerald-50 text-emerald-800";
  if (status === "superseded" || status === "cancelled") return "bg-slate-100 text-slate-500";
  return "bg-amber-50 text-amber-900";
}

// Engineering's view of the Claim and CVP management runs (#453). Ids are
// shown here on purpose: this page is for tracing a run, not for colleagues.
export default async function ManagementRunsPage() {
  const data = await backendJson<Management>("/admin/wang/runs/management");
  if (!data) return null;
  return (
    <main className="space-y-6 pb-16">
      <Link href="/admin/wang/runs" className="inline-flex items-center gap-2 text-sm font-semibold text-indigo-700 hover:underline">
        <ArrowLeft className="h-4 w-4" />返回运行
      </Link>
      <header>
        <h1 className="text-2xl font-black text-slate-950">Claim 与 CVP 管理运行</h1>
        <p className="mt-1 max-w-3xl text-sm leading-7 text-slate-600">
          最近 25 次。暂停原因写 human_required 的 Claim 运行不一定还在等人：负责人在对话里答过的问题记在下面的「人工来源决定」，运行本身不会自动续上。
        </p>
      </header>

      <section id="cvp" className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <h2 className="px-5 pt-4 text-lg font-black text-slate-950">CVP 管理</h2>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-xs text-slate-500"><tr><th className="px-5 py-2">action</th><th className="px-3">开始</th><th className="px-3">结果</th><th className="px-3">各组</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {data.cvp_actions.map((run) => (
              <tr key={run.run_id} className="align-top">
                <td className="px-5 py-2 font-mono text-xs text-slate-700">{run.action_id ?? run.run_id.slice(0, 16)}
                  {run.open.map((unit) => <p key={unit.unit} className="mt-1 whitespace-pre-wrap font-sans text-xs text-amber-900">{unit.unit} · {unit.status}：{unit.detail}</p>)}
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">{day(run.started_at)}</td>
                <td className="px-3 py-2"><span className={`rounded-full px-2 py-0.5 text-xs font-bold ${tone(run.status)}`}>{run.status}</span></td>
                <td className="px-3 py-2 text-xs text-slate-600">{Object.entries(run.units).map(([status, count]) => `${status} ${count}`).join("，")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <h2 className="px-5 pt-4 text-lg font-black text-slate-950">Claim 管理</h2>
        <table className="mt-2 w-full text-left text-sm">
          <thead className="text-xs text-slate-500"><tr><th className="px-5 py-2">action</th><th className="px-3">操作</th><th className="px-3">Claim</th><th className="px-3">开始</th><th className="px-3">结果</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {data.claim_actions.map((run) => (
              <tr key={run.run_id}>
                <td className="px-5 py-2 font-mono text-xs text-slate-700">{run.action_id ?? run.run_id.slice(0, 16)}</td>
                <td className="px-3 py-2 text-xs">{run.operation}</td>
                <td className="px-3 py-2 text-xs">{run.claims}</td>
                <td className="px-3 py-2 text-xs text-slate-500">{day(run.started_at)}</td>
                <td className="px-3 py-2">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${tone(run.status)}`}>{run.status}{run.reason ? ` · ${run.reason}` : ""}</span>
                  {run.continued_by ? <span className="ml-2 text-xs text-slate-400">已续跑</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-lg font-black text-slate-950">人工来源决定</h2>
        <ul className="mt-3 space-y-3 text-sm leading-6">
          {data.human_decisions.map((decision) => (
            <li key={decision.decision_id} className={decision.superseded ? "text-slate-400" : "text-slate-800"}>
              <span className="text-xs text-slate-500">{day(decision.created_at)} · {decision.source_title} · {decision.kind}{decision.superseded ? " · 已被取代" : ""}</span>
              <p>{decision.text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-lg font-black text-slate-950">写作审稿的校准记录</h2>
        {data.calibrations.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">还没有记录。第一次用新流程审稿前，程序会先校准并记在这里。</p>
        ) : (
          <ul className="mt-3 space-y-2 text-sm">
            {data.calibrations.map((entry, index) => (
              <li key={index}>
                <span className={`mr-2 rounded-full px-2 py-0.5 text-xs font-bold ${entry.agrees ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"}`}>{entry.agrees ? "与判定表一致" : `不一致：${entry.missed.join("、")}`}</span>
                {day(entry.calibrated_at)} · {entry.model} · prompt {entry.prompt_sha256?.slice(0, 12)} · {entry.key_id}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
