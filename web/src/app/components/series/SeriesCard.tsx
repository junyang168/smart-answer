// components/series/SeriesCard.tsx
import Link from 'next/link';
import Image from 'next/image';
import { SermonSeries } from '@/app/interfaces/article';

export const SeriesCard = ({ series }: { series: SermonSeries }) => {
  return (
    <Link href={`/resources/series/${series.id}`} className="group block bg-white rounded-lg shadow-md hover:shadow-xl transition-shadow duration-300 overflow-hidden">
      <div className="relative">
        <Image src={series.thumbnail} alt={series.title} width={400} height={225} className="w-full h-auto object-cover group-hover:scale-105 transition-transform duration-300"/>
        <div className="absolute bottom-0 right-0 bg-black bg-opacity-60 text-white text-xs font-bold px-2 py-1 m-2 rounded">
          {series.sermons.length} 篇講道
        </div>
      </div>
      <div className="p-4">
        <h3 className="text-lg font-bold font-display text-gray-800 group-hover:text-[#D4AF37]">{series.title}</h3>
        <p className="text-sm text-gray-600 mt-1 line-clamp-2">{series.description}</p>
      </div>
    </Link>
  );
};