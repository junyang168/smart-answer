// components/sermons/SermonListItem.tsx
import Link from 'next/link';
import { Sermon } from '@/app/interfaces/article';

// 一個幫助函數，根據狀態返回不同的顏色
const getStatusColor = (status: Sermon['status']) => {
    switch (status) {
        case '已發佈': return 'bg-green-100 text-green-800';
        case '編輯中': return 'bg-yellow-100 text-yellow-800';
        case '草稿': return 'bg-gray-100 text-gray-800';
        default: return 'bg-gray-100 text-gray-800';
    }
}

export const SermonListItem = ({ sermon }: { sermon: Sermon }) => {
  return (
    <Link href={`/resources/sermons/${sermon.id}`} className="block bg-white p-6 rounded-lg shadow-sm hover:shadow-lg transition-shadow duration-300 border border-gray-200">
        <div className="flex justify-between items-start mb-2">
            <p className="text-xs text-gray-500">{sermon.date} • {sermon.assigned_to_name}</p>
            {/* 新增的標籤 */}
            <div className="flex gap-2">
                <span className={`text-xs font-semibold px-2 py-1 rounded-full ${getStatusColor(sermon.status)}`}>{sermon.status}</span>
            </div>
        </div>
        <h3 className="text-xl font-bold font-display text-gray-800 mb-1">{sermon.title}</h3>
        <p className="text-sm font-semibold text-blue-600 mb-2">{sermon.topic}</p>
        <p className="font-semibold text-gray-600 mb-4">{sermon.scripture}</p>
        <p className="text-gray-700 text-sm line-clamp-2">{sermon.summary}</p>
        <div className="mt-4 flex items-center gap-4 text-sm font-bold text-[#8B4513]">
            <span>觀看錄影 →</span>
            <span>聆聽錄音 →</span>
        </div>
    </Link>
  );
};