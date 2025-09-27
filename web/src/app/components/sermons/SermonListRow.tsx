import Link from 'next/link';
import { Sermon } from '@/app/interfaces/article';
import { getSermonStatusClasses } from '@/app/components/sermons/SermonListItem';

const formatValue = (value?: string | null) => {
  if (!value) return '—';
  return value;
};

const formatDateOnly = (value?: string | null) => {
  if (!value) return '—';
  if (value.length <= 10) return value;
  const match = value.match(/\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : value;
};

export const SermonListRow = ({ sermon }: { sermon: Sermon }) => {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-4 py-3 shadow-sm">
      <div className="grid gap-3 text-sm md:grid-cols-[1.8fr_1fr_1fr_1fr_1fr_1fr_0.8fr] md:items-center">
        <div>
          <Link
            href={`/resources/sermons/${sermon.id}`}
            className="font-semibold text-blue-700 hover:text-blue-800 hover:underline"
          >
            {sermon.title}
          </Link>
        </div>
        <div className="hidden md:block text-gray-700">{formatDateOnly(sermon.published_date)}</div>
        <div className="hidden md:block text-gray-700 truncate">{formatValue(sermon.assigned_to_name)}</div>
        <div className="hidden md:block text-gray-700">{formatValue(sermon.assigned_to_date)}</div>
        <div className="hidden md:block text-gray-700">{formatValue(sermon.completed_date)}</div>
        <div className="hidden md:block text-gray-700">{formatValue(sermon.last_updated)}</div>
        <div className="flex items-center justify-between md:justify-end">
          <span className={`text-xs font-semibold px-2 py-1 rounded-full ${getSermonStatusClasses(sermon.status)}`}>
            {sermon.status}
          </span>
          <Link
            href={`/resources/sermons/${sermon.id}`}
            className="ml-3 text-xs font-medium text-blue-600 hover:text-blue-700 md:hidden"
          >
            查看詳情
          </Link>
        </div>
      </div>
      <div className="mt-2 space-y-1 text-xs text-gray-500 md:hidden">
        <p>認領日期：{formatValue(sermon.assigned_to_date)}</p>
        <p>完成日期：{formatValue(sermon.completed_date)}</p>
        <p>最後更新：{formatValue(sermon.last_updated)}</p>
      </div>
    </div>
  );
};
