// components/common/Breadcrumb.tsx
"use client"; // 因為我們使用 Next.js 的 Link 組件

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

interface BreadcrumbLink {
  name: string;
  href?: string;
}

interface BreadcrumbProps {
  links: BreadcrumbLink[];
}

export const Breadcrumb = ({ links }: BreadcrumbProps) => {
  return (
    <nav aria-label="Breadcrumb" className="mb-6">
      <ol className="flex items-center space-x-1 text-sm text-gray-500">
        {links.map((link, index) => (
          <li key={link.name} className="flex items-center">
            {/* 如果不是第一個元素，則顯示分隔符 */}
            {index > 0 && (
              <ChevronRight className="w-4 h-4 mx-1" />
            )}

            {/* 如果有 href 並且不是最後一個元素，則渲染為連結 */}
            {link.href && index < links.length - 1 ? (
              <Link href={link.href} className="hover:text-[#D4AF37] hover:underline">
                {link.name}
              </Link>
            ) : (
              // 最後一個元素（當前頁面）只顯示文本
              <span className="font-semibold text-gray-700" aria-current="page">
                {link.name}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
};