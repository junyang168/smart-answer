import { Suspense } from "react";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";
import { SurmonEditor } from "@/app/components/admin/surmons/SurmonEditor";

import { TranscriptReviewEntry } from "@/app/components/admin/surmons/TranscriptProofreader";

type PageProps = {
  params: Promise<{ item: string }>;
  searchParams?: Promise<{ [key: string]: string | string[] | undefined }>;
};

const EditorFallback = () => (
  <div className="py-16 text-center text-gray-500">正在載入講道編輯器...</div>
);

const toBoolean = (value: string | string[] | undefined): boolean => {
  if (Array.isArray(value)) {
    return value.some((entry) => toBoolean(entry));
  }
  if (!value) return false;
  const lowered = value.toLowerCase();
  return lowered === "true" || lowered === "1" || lowered === "changes";
};

export default async function SurmonEditorPage(props: PageProps) {
  const searchParams = await props.searchParams;
  const params = await props.params;
  const item = decodeURIComponent(params.item);
  const query = searchParams ?? {};
  const viewChanges =
    toBoolean(query.view) || toBoolean(query.c) || toBoolean(query.mode);
  const session = await getServerSession(authConfig);
  const beta =
    process.env.NODE_ENV === "development" ||
    session?.user?.email === "junyang168@gmail.com";
  const legacy = <SurmonEditor item={item} viewChanges={viewChanges} />;

  return (
    <Suspense fallback={<EditorFallback />}>
      {beta ? <TranscriptReviewEntry item={item} legacy={legacy} /> : legacy}
    </Suspense>
  );
}
