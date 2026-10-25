"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpenText, FileText, Inbox, Share2, Wrench } from "lucide-react";

// Colleagues' menu (#453): what waits for a person first, then the places to
// look things up. Engineering's pages sit under 运行, which also owns the
// older pages until their phase moves them.
const sections = [
  { href: "/admin/wang", label: "待处理", icon: Inbox, match: [] as string[], exact: true },
  { href: "/admin/wang/operations/articles", label: "文章", icon: FileText, match: ["/admin/wang/operations/articles", "/admin/wang/matthew-progress"] },
  { href: "/admin/wang/viewpoints", label: "观点图", icon: Share2, match: ["/admin/wang/viewpoints", "/admin/wang/viewpoint-structures", "/admin/wang/viewpoint-exceptions"] },
  { href: "/admin/wang/source-coverage", label: "讲道", icon: BookOpenText, match: ["/admin/wang/source-coverage", "/admin/wang/argument-layer"] },
  { href: "/admin/wang/runs", label: "运行", icon: Wrench, match: ["/admin/wang/runs", "/admin/wang/health", "/admin/wang/library-audit"] },
];

export function WangNav() {
  const pathname = usePathname();
  return (
    <div className="overflow-x-auto border-b border-slate-200 bg-white">
      <div className="flex min-w-max items-center gap-1 py-2" aria-label="王教授文库后台导览">
        <Link href="/admin/wang" className="mr-4 px-2 text-sm font-black tracking-wide text-slate-950">王教授文库</Link>
        {sections.map((item) => {
          const Icon = item.icon;
          const active = item.exact ? pathname === item.href : item.match.some((prefix) => pathname.startsWith(prefix));
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
