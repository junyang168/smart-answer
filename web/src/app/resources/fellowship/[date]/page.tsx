import { Suspense } from "react";
import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { FellowshipDetail } from "@/app/components/fellowship/FellowshipDetail";

const FellowshipDetailFallback = () => <div className="py-20 text-center">正在準備團契回顧...</div>;

export default function FellowshipDetailPage({ params }: { params: { date: string } }) {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "團契查經回顧", href: "/resources/fellowship" },
    { name: params.date },
  ];

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <Suspense fallback={<FellowshipDetailFallback />}>
          <FellowshipDetail date={params.date} />
        </Suspense>
      </div>
    </div>
  );
}
