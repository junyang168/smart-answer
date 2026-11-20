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
    <div className="bg-white border border-gray-200 rounded-lg p-4 md:p-6 shadow-sm hover:shadow-lg transition-shadow duration-300 grid grid-cols-[auto_1fr] items-start gap-x-3 md:flex md:flex-col">
      <div className="row-span-3 bg-gray-100 p-2 rounded-full md:mb-3 md:p-3">
        <Icon className="w-5 h-5 md:w-7 md:h-7 text-[#8B4513]" />
      </div>
      <h3 className="text-xl font-bold font-display text-gray-800 mb-2">{title}</h3>
      <div className="col-start-2 text-gray-600 flex-grow mb-3">
        <div className="prose prose-slate max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{description}</ReactMarkdown>
        </div>
      </div>
      <Link href={link} className="col-start-2 inline-flex min-h-11 items-center justify-self-start mt-auto bg-[#8B4513] text-white font-bold py-2 px-6 rounded-full hover:bg-opacity-90 transition-all md:self-start">
        {linkLabel}
      </Link>
    </div>



  );
};

export default ResourceCard;