// components/sermons/SermonDetailSidebar.tsx
import { Sermon } from '@/app/interfaces/article';
import { Download, Share2 } from 'lucide-react';

interface SermonDetailSidebarProps {
  sermon: Sermon;
}

const InfoRow = ({ label, value }: { label: string, value: string }) => (
  <div className="flex justify-between py-2 border-b border-gray-100">
    <dt className="text-sm font-medium text-gray-500">{label}</dt>
    <dd className="text-sm text-gray-900 text-right">{value}</dd>
  </div>
);

export const SermonDetailSidebar = ({ sermon }: SermonDetailSidebarProps) => {
  return (
    <aside className="lg:col-span-1 mt-12 lg:mt-0 lg:sticky lg:top-24 self-start">
      <div className="bg-gray-50 p-6 rounded-lg">
        <h3 className="text-xl font-bold font-display mb-4">講道信息</h3>
        <dl>
          <InfoRow label="講員" value={sermon.speaker} />
          <InfoRow label="日期" value={sermon.date} />
          <InfoRow label="主題系列" value={sermon.topic} />
          <InfoRow label="主要經文" value={sermon.scripture} />
          <InfoRow label="聖經書卷" value={sermon.book} />
        </dl>
      </div>

      <div className="mt-6">
        <h3 className="text-xl font-bold font-display mb-4">資源</h3>
        <div className="space-y-3">
          <a href={sermon.audioUrl} download className="flex items-center w-full bg-blue-500 text-white p-3 rounded-lg hover:bg-blue-600 transition-colors">
            <Download className="w-5 h-5 mr-3" />
            <span>下載音頻 (MP3)</span>
          </a>
          <button className="flex items-center w-full bg-gray-600 text-white p-3 rounded-lg hover:bg-gray-700 transition-colors">
            <Share2 className="w-5 h-5 mr-3" />
            <span>分享此講道</span>
          </button>
        </div>
      </div>
    </aside>
  );
};