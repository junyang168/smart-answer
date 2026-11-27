import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';

interface ResourceCardProps {
  icon: LucideIcon;
  title: string;
  description: string;
  link: string;
  linkLabel: string;
}

export default function ResourceCard({ icon: Icon, title, description, link, linkLabel }: ResourceCardProps) {
  return (
    <div className="rounded-[10px] border border-stone-200 bg-white p-3 sm:p-4">
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-1.5 sm:gap-2">
        <Icon aria-hidden="true" strokeWidth={1.7} className="h-5 w-5 text-amber-900" />
        <h3 className="font-display text-base font-bold leading-snug text-gray-800 min-[346px]:text-lg">{title}</h3>
        <Link
          href={link}
          className="inline-flex min-h-11 items-center whitespace-nowrap rounded-lg border border-amber-900/25 bg-orange-50/60 px-2 text-sm font-semibold text-amber-900 hover:bg-orange-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-900 sm:px-3"
        >
          {linkLabel}
        </Link>
      </div>
      <p className="mt-1 text-[15px] leading-relaxed text-gray-600">{description}</p>
    </div>
  );
}
