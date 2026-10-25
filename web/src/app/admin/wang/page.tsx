import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, CheckCircle2, Inbox } from "lucide-react";
import { backendJson } from "./backend";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "待处理 | 王教授文库后台",
  robots: { index: false, follow: false },
};

type InboxItem = {
  kind: string;
  line: string;
  title: string;
  summary: string;
  waiting_for: string;
  at: string | null;
  href: string;
};

function when(at: string | null): string {
  if (!at) return "";
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
}

// People are needed in two places only: a ruling the models and the rules
// cannot settle, and the final review before publishing (solution
// architecture §5). This page lists those and nothing else.
export default async function WangInboxPage() {
  const payload = await backendJson<{ items: InboxItem[] }>("/admin/wang/inbox");
  const items = payload?.items ?? [];
  return (
    <main>
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-black tracking-tight text-slate-950">
            <Inbox className="h-7 w-7 text-indigo-600" aria-hidden="true" />待处理
          </h1>
          <p className="mt-2 max-w-3xl text-base leading-7 text-slate-600">
            等人决定的事：模型和规则判不了的裁决，以及发布前的终审。其余的事程序自己往下做，进度在「文章」和「运行」里。
          </p>
        </div>
        <p className="text-sm font-semibold text-slate-500">{items.length} 件</p>
      </header>

      {items.length === 0 ? (
        <div className="mt-8 flex items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-emerald-900">
          <CheckCircle2 className="h-5 w-5" aria-hidden="true" />现在没有等人决定的事。
        </div>
      ) : (
        <ul className="mt-6 divide-y divide-slate-200 rounded-2xl border border-slate-200 bg-white">
          {items.map((item) => (
            <li key={`${item.kind}-${item.href}`}>
              <Link href={item.href} className="group flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-4 transition hover:bg-slate-50">
                <span className="w-12 shrink-0 rounded-full bg-slate-100 px-2 py-1 text-center text-xs font-bold text-slate-600">{item.line}</span>
                <span className="min-w-0 flex-1">
                  <span className="block font-bold text-slate-950">{item.title}</span>
                  <span className="mt-1 block text-sm leading-6 text-slate-600">{item.summary}</span>
                </span>
                <span className="text-sm text-slate-500">等{item.waiting_for}{when(item.at) && ` · ${when(item.at)}`}</span>
                <ArrowRight className="h-4 w-4 text-slate-400 group-hover:text-indigo-600" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
