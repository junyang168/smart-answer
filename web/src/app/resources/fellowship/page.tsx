import { Suspense } from "react";
import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { FellowshipArchive } from "@/app/components/fellowship/FellowshipArchive";

const FellowshipFallback = () => <div className="py-20 text-center">正在準備團契回顧...</div>;

export default function FellowshipResourcePage() {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "團契查經回顧" },
  ];

  return (
    <div className="bg-gray-50">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <div className="mb-12 text-center">
          <h1 className="text-4xl font-bold font-display text-gray-800">團契查經回顧</h1>
          <p className="mx-auto mt-4 max-w-3xl text-lg text-gray-600">
            回顧雙週團契查經的主題、學習重點與來源連結，也讓想認識我們團契的朋友看見我們如何一起查考聖經。
          </p>
        </div>
        <Suspense fallback={<FellowshipFallback />}>
          <FellowshipArchive />
        </Suspense>
      </div>
    </div>
  );
}
