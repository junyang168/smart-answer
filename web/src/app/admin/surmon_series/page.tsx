import { SermonSeriesManager } from "@/app/components/admin/surmon-series/SermonSeriesManager";

export const dynamic = "force-dynamic";

export default function SurmonSeriesAdminPage() {
  return (
    <div className="min-h-screen bg-gray-50 py-10">
      <div className="mx-auto max-w-6xl px-6">
        <SermonSeriesManager />
      </div>
    </div>
  );
}
