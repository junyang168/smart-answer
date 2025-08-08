// components/resources/FeaturedPostItem.tsx
import Link from 'next/link';

interface FeaturedPostItemProps {
  title: string;
  category?: string;
  date?: string;
  link: string;
  isQuestion?: boolean;
  author?: string;
}

const FeaturedPostItem = ({ title, author,  category, date, link, isQuestion = false }: FeaturedPostItemProps) => {
  return (
    <li className="border-b border-gray-200 py-3 flex items-start gap-2">
      {isQuestion && <span className="text-[#8B4513] font-bold mt-1">❓</span>}
      <div>
        <Link href={link} className="text-gray-800 hover:text-[#D4AF37] font-semibold transition-colors">
          {category && <b className="text-gray-600">[{category}]</b>} {title}
        </Link>
        <p className="text-sm text-gray-500 mt-1 mb-3">
        <b>作者:</b> {author} | <b>日期:</b> {date} 
        </p>
      </div>
    </li>
  );
};

export default FeaturedPostItem;