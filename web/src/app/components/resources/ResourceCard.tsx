import Link from 'next/link';
import { ChevronRight, type LucideIcon } from 'lucide-react';

interface ResourceCardProps {
  icon: LucideIcon;
  title: string;
  description: string;
  link: string;
  linkLabel: string;
}

export default function ResourceCard({ icon: Icon, title, description, link, linkLabel }: ResourceCardProps) {
  return (
    <Link
      href={link}
      aria-label={`${title}：${linkLabel}`}
      className="group block rounded-lg border border-gray-200 bg-white p-4 shadow-sm hover:border-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-800 md:p-6"
    >
      <div className="flex items-center gap-2">
        <Icon aria-hidden="true" className="h-5 w-5 shrink-0 text-[#8B4513]" />
        <h3 className="flex-1 text-xl font-bold font-display text-gray-800">{title}</h3>
        <ChevronRight aria-hidden="true" className="h-5 w-5 shrink-0 text-amber-900" />
      </div>
      <p className="mt-2 leading-relaxed text-gray-600">{description}</p>
    </Link>
  );
}
