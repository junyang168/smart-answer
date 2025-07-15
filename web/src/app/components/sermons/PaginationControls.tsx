// components/sermons/PaginationControls.tsx
"use client";

import { useSearchParams, useRouter, usePathname } from 'next/navigation';

interface PaginationControlsProps {
  hasNextPage: boolean;
  hasPrevPage: boolean;
}

export const PaginationControls = ({ hasNextPage, hasPrevPage }: PaginationControlsProps) => {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const page = searchParams.get('page') ?? '1';

  const handlePageChange = (direction: 'prev' | 'next') => {
    const params = new URLSearchParams(searchParams);
    const newPage = direction === 'prev' ? Number(page) - 1 : Number(page) + 1;
    params.set('page', newPage.toString());
    router.push(`${pathname}?${params.toString()}`);
  }

  return (
    <div className="flex justify-center items-center gap-4 mt-8">
      <button 
        disabled={!hasPrevPage} 
        onClick={() => handlePageChange('prev')}
        className="bg-[#8B4513] text-white py-2 px-6 rounded-full disabled:bg-gray-300 disabled:cursor-not-allowed"
      >
        上一頁
      </button>
      <span className="font-bold">第 {page} 頁</span>
      <button 
        disabled={!hasNextPage} 
        onClick={() => handlePageChange('next')}
        className="bg-[#8B4513] text-white py-2 px-6 rounded-full disabled:bg-gray-300 disabled:cursor-not-allowed"
      >
        下一頁
      </button>
    </div>
  );
};