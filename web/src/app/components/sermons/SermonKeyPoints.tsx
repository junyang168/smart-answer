import { Sermon} from '@/app/interfaces/article';
import ReactMarkdown from 'react-markdown'; // ✅ 步驟 1: 引入庫
import remarkGfm from 'remark-gfm';         // ✅ 引入 GFM 插件


export const SermonKeyPoints: React.FC<{ sermon: Sermon }> = ({ sermon  }) => {
    return (
      <div className="mt-6">

        <h3 className="text-xl font-bold font-display mb-4">主要觀點</h3>
        <div className="prose prose-sm max-w-none text-gray-700 bg-gray-50 p-6 rounded-lg">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {sermon.keypoints}
          </ReactMarkdown>
        </div>
      </div>
    )
};