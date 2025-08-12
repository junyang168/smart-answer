// components/articles/SidebarDownload.tsx
"use client";

import { Presentation, Download } from 'lucide-react';

interface SidebarDownloadProps {
  downloadUrl: string;
}

export const SidebarDownload = ({ downloadUrl }: SidebarDownloadProps) => {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 mb-6 shadow-sm">
      <div className="flex items-center mb-3">
        <Presentation className="w-5 h-5 text-gray-600 mr-3" />
        <h3 className="font-bold text-gray-800">配套資源</h3>
      </div>
      <a
        href={downloadUrl}
        download
        className="flex items-center justify-center w-full bg-blue-600 text-white font-semibold py-2 px-4 rounded-lg hover:bg-blue-700 transition-colors text-sm"
      >
        <Download className="w-4 h-4 mr-2" />
        <span>下載團契查經PPT</span>
      </a>
    </div>
  );
};