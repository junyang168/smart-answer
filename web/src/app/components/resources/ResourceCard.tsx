// components/resources/ResourceCard.tsx
import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';
import ReactMarkdown from 'react-markdown'; // ✅ 步驟 1: 引入庫
import remarkGfm from 'remark-gfm';         // ✅ 引入 GFM 插件

interface ResourceCardProps {
  icon: LucideIcon;
  title: string;
  description: string;
  link: string;
  linkLabel: string;
}

const ResourceCard = ({ icon: Icon, title, description, link, linkLabel }: ResourceCardProps) => {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 text-center shadow-sm hover:shadow-lg transition-shadow duration-300 flex flex-col">
      <div className="mx-auto bg-gray-100 p-4 rounded-full mb-4">
        <Icon className="w-10 h-10 text-[#8B4513]" />
      </div>
      <h3 className="text-xl font-bold font-display text-gray-800 mb-2">{title}</h3>
      <p className="text-gray-600 flex-grow mb-4">
        <div className="prose prose-slate max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{description}</ReactMarkdown>
        </div>
      </p>
      <Link href={link} className="mt-auto bg-[#8B4513] text-white font-bold py-2 px-6 rounded-full hover:bg-opacity-90 transition-all self-center">
        {linkLabel}
      </Link>
    </div>



  );
};

export default ResourceCard;