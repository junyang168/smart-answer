// components/sermons/SermonDetailSidebar.tsx
import { Sermon } from '@/app/interfaces/article';
import { Download, Share2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown'; // ✅ 步驟 1: 引入庫
import remarkGfm from 'remark-gfm';         // ✅ 引入 GFM 插件


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
          <InfoRow label="认领人" value={sermon.assigned_to_name} />
          <InfoRow label="主題" value={sermon.theme} />
          {sermon.scripture && sermon.scripture.length > 0 && (
            <div className="flex justify-between items-baseline py-3 border-b border-gray-200">
              {/* 
                - flex: 啟用 Flexbox 佈局
                - justify-between: 將標籤和值推向兩側
                - items-baseline: 這是實現對齊的關鍵，它會讓flex子項的基線對齊。
                                  對於單行文本和多行文本的第一行，效果非常好。
              */}
              
              {/* 左側標籤 */}
              <dt className="text-sm font-medium text-gray-500 whitespace-nowrap">
                主要經文
              </dt>
              
              {/* 右側值列表 */}
              <dd className="text-sm text-gray-900 text-right ml-4">
                {/* 
                  - ml-4: 增加一個左邊距，防止標籤和值在空間不足時擠在一起
                */}
                {sermon.scripture.map((line, index) => (
                  <span key={index} className="block">
                    {line}
                  </span>
                ))}
              </dd>
            </div>
          )}          
    
        </dl>
      </div>

      <div className="mt-6">
        <h3 className="text-xl font-bold font-display mb-4">主要觀點</h3>
        
        {/* 
          使用 'prose' 類來自動應用樣式到 Markdown 渲染的 HTML 上。
          'prose-sm' 是一個較小的尺寸，非常適合側邊欄。
          'max-w-none' 用來移除 prose 默認的寬度限制，讓它填滿容器。
        */}
        <div className="prose prose-sm max-w-none text-gray-700 bg-gray-50 p-6 rounded-lg">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {sermon.keypoints}
          </ReactMarkdown>
        </div>
      </div>    
    </aside>
  );
};