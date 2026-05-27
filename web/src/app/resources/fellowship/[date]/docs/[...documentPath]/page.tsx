import { Suspense } from "react";
import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { FellowshipMarkdownDocument } from "@/app/components/fellowship/FellowshipMarkdownDocument";

const MarkdownDocumentFallback = () => <div className="py-20 text-center">正在準備團契文件...</div>;

export default function FellowshipMarkdownDocumentPage({
  params,
}: {
  params: { date: string; documentPath: string[] };
}) {
  const documentPath = params.documentPath.join("/");
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "團契查經回顧", href: "/resources/fellowship" },
    { name: params.date, href: `/resources/fellowship/${encodeURIComponent(params.date)}` },
    { name: documentPath },
  ];

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <Suspense fallback={<MarkdownDocumentFallback />}>
          <FellowshipMarkdownDocument date={params.date} documentPath={documentPath} />
        </Suspense>
      </div>
    </div>
  );
}
