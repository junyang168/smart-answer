// components/resources/LatestSermonCard.tsx

interface Sermon {
  title: string;
  speaker: string;
  date: string;
  scripture: string;
  summary: string;
  videoUrl: string;
  audioUrl: string;
}

interface LatestSermonCardProps {
  sermon: Sermon;
}

const LatestSermonCard = ({ sermon }: LatestSermonCardProps) => {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 shadow-sm">
      <h4 className="text-lg font-bold font-display text-gray-800">{sermon.title}</h4>
      <p className="text-sm text-gray-500 mt-1 mb-3">
        <b>講員:</b> {sermon.speaker} | <b>日期:</b> {sermon.date} | <b>經文:</b> {sermon.scripture}
      </p>
      <p className="text-gray-700 mb-4">{sermon.summary}</p>
      <div className="flex items-center gap-4 text-sm font-semibold">
        <a href={sermon.videoUrl} target="_blank" rel="noopener noreferrer" className="text-[#8B4513] hover:underline">
          ▶️ 觀看錄影
        </a>
        <a href={sermon.audioUrl} target="_blank" rel="noopener noreferrer" className="text-[#8B4513] hover:underline">
          🎵 聆聽錄音
        </a>
      </div>
    </div>
  );
};

export default LatestSermonCard;