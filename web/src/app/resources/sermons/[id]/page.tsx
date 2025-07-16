// app/resources/sermons/[id]/page.tsx
import { SermonDetailView } from "@/app/components/sermons/SermonDetailView";
import { Suspense } from 'react';

const SermonDetailFallback = () => <div className="text-center py-20">正在準備講道頁面...</div>;

export default function SermonDetailPage() {
  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <Suspense fallback={<SermonDetailFallback />}>
          <SermonDetailView />
        </Suspense>
      </div>
    </div>
  );
}