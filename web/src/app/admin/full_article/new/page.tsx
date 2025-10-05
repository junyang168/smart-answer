"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { FullArticleEditor } from "@/app/components/admin/full-article/FullArticleEditor";
import { fetchNewArticleTemplate } from "@/app/admin/full_article/api";
import { FullArticleDetail } from "@/app/types/full-article";

export default function NewFullArticlePage() {
  const router = useRouter();
  const [template, setTemplate] = useState<FullArticleDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let aborted = false;
    const load = async () => {
      try {
        const data = await fetchNewArticleTemplate();
        if (!aborted) {
          setTemplate(data);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "載入失敗";
        setError(message);
        setTimeout(() => router.push("/admin/full_article"), 1500);
      }
    };
    load();
    return () => {
      aborted = true;
    };
  }, [router]);

  if (error) {
    return <div className="p-6 text-red-600">{error}</div>;
  }

  if (!template) {
    return <div className="p-6 text-gray-600">正在載入...</div>;
  }

  return <FullArticleEditor initialArticle={template} />;
}
