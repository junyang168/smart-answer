import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

import {
  fetchNotesToManuscriptManuscript,
  fetchNotesToManuscriptSeriesDetail,
  NOTES_TO_MANUSCRIPT_REVALIDATE,
} from "../../data";

type PageProps = {
  params: Promise<{ seriesId: string; projectId: string }>;
};

export const revalidate = NOTES_TO_MANUSCRIPT_REVALIDATE;

function decodeRouteSegment(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { projectId: rawProjectId } = await params;
  const projectId = decodeRouteSegment(rawProjectId);
  try {
    const manuscript = await fetchNotesToManuscriptManuscript(projectId);
    return {
      title: `${manuscript.title} | 逐字稿`,
      description: `閱讀 ${manuscript.title} 的逐字稿。`,
    };
  } catch {
    return {
      title: "逐字稿",
    };
  }
}

export default async function NotesToManuscriptReaderPage({ params }: PageProps) {
  const { seriesId: rawSeriesId, projectId: rawProjectId } = await params;
  const seriesId = decodeRouteSegment(rawSeriesId);
  const projectId = decodeRouteSegment(rawProjectId);

  const [seriesResult, manuscriptResult] = await Promise.allSettled([
    fetchNotesToManuscriptSeriesDetail(seriesId),
    fetchNotesToManuscriptManuscript(projectId),
  ]);

  if (seriesResult.status !== "fulfilled" || manuscriptResult.status !== "fulfilled") {
    notFound();
  }

  const series = seriesResult.value;
  const manuscript = manuscriptResult.value;

  const belongsToSeries = series.lectures.some((lecture) =>
    lecture.projects.some((project) => project.id === projectId),
  );
  if (!belongsToSeries) {
    notFound();
  }

  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "講義轉逐字稿系列", href: "/resources/notes_to_manuscript_series" },
    {
      name: series.title,
      href: `/resources/notes_to_manuscript_series/${series.id}`,
    },
    { name: manuscript.title },
  ];

  return (
    <div className="min-h-screen bg-gray-50 pb-16">
      <Breadcrumb links={breadcrumbLinks} />

      <main className="container mx-auto px-6 pt-6">
        <Link
          href={`/resources/notes_to_manuscript_series/${series.id}`}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          ← 返回{series.title}
        </Link>

        <article className="mt-5 max-w-4xl rounded-2xl border border-slate-200 bg-white px-6 py-7 shadow-sm md:px-10 md:py-9">
          <header className="mb-8 border-b border-slate-100 pb-6">
            <p className="text-sm font-semibold text-sky-700">{series.title}</p>
            <h1 className="mt-2 text-3xl font-bold leading-tight text-slate-950 md:text-4xl">
              {manuscript.title}
            </h1>
          </header>

          <div className="prose prose-slate max-w-none prose-headings:scroll-mt-24 prose-p:leading-8 prose-li:leading-8 prose-a:text-sky-700">
            <ScriptureMarkdown markdown={manuscript.markdown} />
          </div>
        </article>
      </main>
    </div>
  );
}
