import { Suspense } from "react";
import { SurmonAdminList } from "@/app/components/admin/surmons/SurmonAdminList";

const SurmonListFallback = () => (
  <div className="py-16 text-center text-gray-500">正在準備講道列表...</div>
);

export default function AdminSurmonsPage() {
  return (
    <div className="container mx-auto px-6 py-12">
      <Suspense fallback={<SurmonListFallback />}>
        <SurmonAdminList />
      </Suspense>
    </div>
  );
}
