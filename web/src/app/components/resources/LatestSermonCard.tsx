// components/resources/LatestSermonCard.tsx
import { Sermon } from '@/app/interfaces/article';

interface LatestSermonCardProps {
  sermon: Sermon;
}

const LatestSermonCard = ({ sermon }: LatestSermonCardProps) => {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
      <h4 className="text-lg font-bold font-display text-gray-800">{sermon.title}</h4>
      <p className="text-sm text-gray-500 mt-1 mb-3">
        <b>認領人:</b> {sermon.assigned_to_name} | <b>日期:</b> {sermon.date} 
      </p>
      <p className="text-gray-700 mb-4">{sermon.summary}</p>
      <div className="flex items-center gap-4 text-sm font-semibold">
        <a href={'sermons/' + sermon.id} target="_blank" rel="noopener noreferrer" className="text-[#8B4513] hover:underline">
          ▶️ 觀看
        </a>
      </div>
    </div>
  );
};

export default LatestSermonCard;