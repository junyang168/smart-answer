import { Suspense } from "react";
import { SurmonEditor } from "@/app/components/admin/surmons/SurmonEditor";

type PageProps = {
  params: { item: string };
  searchParams?: { [key: string]: string | string[] | undefined };
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

export default function SurmonEditorPage({ params, searchParams }: PageProps) {
  const item = decodeURIComponent(params.item);
  const query = searchParams ?? {};
  const viewChanges = toBoolean(query.view) || toBoolean(query.c) || toBoolean(query.mode);

  return (
    <Suspense fallback={<EditorFallback />}>
      <SurmonEditor item={item} viewChanges={viewChanges} />
    </Suspense>
  );
}
