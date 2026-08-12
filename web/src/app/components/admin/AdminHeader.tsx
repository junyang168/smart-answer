"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/admin", label: "控制台", exact: true },
  { href: "/admin/notes-to-sermon/series", label: "講稿整理" },
  { href: "/admin/canonical-repository", label: "釋經文庫" },
  { href: "/admin/thought-review", label: "思想審核" },
  { href: "/", label: "網站首頁", exact: true },
];

export const AdminHeader = () => {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur-sm">
      <div className="container mx-auto flex min-h-14 flex-wrap items-center justify-between gap-3 px-4 py-2 sm:px-6">
        <Link href="/admin" className="flex items-center gap-3">
          <Image
            src="/dhl_logo.jpg"
            alt="Dallas Holy Logos Church"
            width={140}
            height={60}
            className="h-9 w-auto"
          />
          <span className="text-sm font-semibold text-gray-700">Admin</span>
        </Link>
        <nav aria-label="後台主導覽" className="flex max-w-full items-center gap-1 overflow-x-auto text-sm font-semibold">
          {navItems.map((item) => {
            const isActive = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={`whitespace-nowrap rounded-lg px-3 py-2 transition ${
                  isActive
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
};
