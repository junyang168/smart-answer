import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { FellowshipMarkdownDocument } from "@/app/components/fellowship/FellowshipMarkdownDocument";

function safeDecodeURIComponent(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export default function FellowshipMarkdownDocumentPage({
  params,
}: {
  params: { date: string; documentPath: string[] };
}) {
  const date = safeDecodeURIComponent(params.date);
  const documentPath = params.documentPath.map(safeDecodeURIComponent).join("/");
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "團契查經回顧", href: "/resources/fellowship" },
    { name: date, href: `/resources/fellowship/${encodeURIComponent(date)}` },
    { name: documentPath },
  ];

  return (
    <div className="bg-white">
      <div className="container mx-auto px-6 py-12">
        <Breadcrumb links={breadcrumbLinks} />
        <FellowshipMarkdownDocument date={date} documentPath={documentPath} />
      </div>
    </div>
  );
}
