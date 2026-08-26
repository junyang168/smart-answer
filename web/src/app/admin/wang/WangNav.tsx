"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, DatabaseZap, FileCheck2, FileText, HeartPulse, Highlighter, LayoutDashboard, Network, ShieldCheck } from "lucide-react";

const sections = [
  { href: "/admin/wang", label: "總覽", icon: LayoutDashboard, exact: true },
  { href: "/admin/wang/health", label: "健康視圖", icon: HeartPulse },
  { href: "/admin/wang/library-audit", label: "獨立審計", icon: ShieldCheck },
  { href: "/admin/wang/operations/articles", label: "寫文章", icon: FileText },
  { href: "/admin/wang/matthew-progress", label: "馬太進度", icon: Activity },
  { href: "/admin/wang/argument-layer", label: "論證層", icon: Network },
  { href: "/admin/wang/source-coverage", label: "來源覆蓋", icon: Highlighter },
  { href: "/admin/wang/viewpoints", label: "观点主数据", icon: DatabaseZap },
  { href: "/admin/wang/viewpoint-structures", label: "中心结构", icon: Network },
  { href: "/admin/canonical-repository", label: "出版單元", icon: FileCheck2 },
];

export function WangNav() {
  const pathname = usePathname();
  return (
    <div className="overflow-x-auto border-b border-slate-200 bg-white">
      <div className="flex min-w-max items-center gap-1 py-2" aria-label="王教授文庫後台導覽">
        <Link href="/admin/wang" className="mr-4 px-2 text-sm font-black tracking-wide text-slate-950">王教授文庫</Link>
        {sections.map((item) => {
          const Icon = item.icon;
          const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} className={`inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition ${active ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-950"}`}>
              <Icon className="h-4 w-4" aria-hidden="true" />{item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
