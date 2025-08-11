// components/sermons/SermonDetailSidebar.tsx
import { Sermon } from '@/app/interfaces/article';
import ReactMarkdown from 'react-markdown'; // ✅ 步驟 1: 引入庫
import remarkGfm from 'remark-gfm';         // ✅ 引入 GFM 插件
import { ScriptureHover } from './ScriptureHover';

interface SermonDetailSidebarProps {
  sermon: Sermon;
}

const InfoRow = ({ label, value }: { label: string, value: string }) => (
  <div className="flex justify-between py-2 border-b border-gray-100">
    <dt className="text-sm font-medium text-gray-500">{label}</dt>
    <dd className="text-sm text-gray-900 text-right">{value}</dd>
  </div>
);

const MultiValueRow = ({ label, values }: { label: string, values: string[] }) => (
    <div className="flex justify-between items-baseline py-3 border-b border-gray-200">
        <dt className="text-sm font-medium text-gray-500 whitespace-nowrap">{label}</dt>
        <dd className="flex flex-col items-end ml-4 text-sm text-gray-900">
            {values.map((value, index) => (
                <span key={index} className={index < values.length - 1 ? 'mb-1.5' : ''}>{value}</span>
            ))}
        </dd>
    </div>
);

export const SermonDetailSidebar = ({ sermon }: SermonDetailSidebarProps) => {
  return (
    <aside className="lg:col-span-1 mt-12 lg:mt-0 lg:sticky lg:top-24 self-start">
      <div className="bg-gray-50 p-6 rounded-lg">
        <h3 className="text-xl font-bold font-display mb-4">講道信息</h3>
        <dl>
          <InfoRow label="主題" value={sermon.theme} />

          {sermon.scripture && sermon.scripture.length > 0 && (
            <div className="flex justify-between items-baseline py-3 border-b border-gray-200">
              
              {/* 左側標籤 */}
              <dt className="text-sm font-medium text-gray-500 whitespace-nowrap">
                主要經文
              </dt>
              
              {/* 右側值列表 */}
              <dd className="flex flex-col items-end ml-4">
                  {sermon.scripture.map((line, index) => (
                    <div key={index} className={index < sermon.scripture.length - 1 ? 'mb-1.5' : ''}>
                      <ScriptureHover key={line} reference={line} text={sermon.core_bible_verses![line]} />
                    </div>
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