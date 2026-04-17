import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumb } from "@/app/components/common/Breadcrumb";

import {
  fetchNotesToManuscriptSeriesDetail,
  NOTES_TO_MANUSCRIPT_REVALIDATE,
} from "../data";

type PageProps = {
  params: Promise<{ seriesId: string }>;
};

export const revalidate = NOTES_TO_MANUSCRIPT_REVALIDATE;

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { seriesId } = await params;
  try {
    const series = await fetchNotesToManuscriptSeriesDetail(seriesId);
    return {
      title: `${series.title} | 講義轉逐字稿系列`,
      description: series.description || `查看 ${series.title} 的各講次與逐字稿專案。`,
    };
  } catch {
    return {
      title: "講義轉逐字稿系列",
    };
  }
}

export default async function NotesToManuscriptSeriesDetailPage({
  params,
}: PageProps) {
  const { seriesId } = await params;

  let series;
  try {
    series = await fetchNotesToManuscriptSeriesDetail(seriesId);
  } catch {
    notFound();
  }

  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "講義轉逐字稿系列", href: "/resources/notes_to_manuscript_series" },
    { name: series.title },
  ];

  return (
    <div className="bg-gray-50 min-h-screen pb-16">
      <Breadcrumb links={breadcrumbLinks} />

      <section className="container mx-auto px-6 pt-6">
        <Link
          href="/resources/notes_to_manuscript_series"
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          ← 返回系列列表
        </Link>

        <div className="mt-4 max-w-5xl">
          <h1 className="text-4xl md:text-5xl font-bold text-slate-900">
            {series.title}
          </h1>
          {series.description ? (
            <p className="mt-4 text-lg text-slate-600 leading-relaxed">
              {series.description}
            </p>
          ) : null}
          {series.folder ? (
            <p className="mt-3 text-xs text-slate-400 font-mono">
              Source Folder: {series.folder}
            </p>
          ) : null}
        </div>
      </section>

      <section className="container mx-auto px-6 mt-10 space-y-8">
        {series.lectures.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-slate-500">
            此系列目前尚未整理任何講次。
          </div>
        ) : (
          series.lectures.map((lecture, lectureIndex) => (
            <article
              key={lecture.id}
              className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden"
            >
              <div className="px-6 py-5 border-b border-slate-200 bg-slate-50">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold text-slate-400">
                      第 {lectureIndex + 1} 講
                    </p>
                    <h2 className="mt-1 text-2xl font-bold text-slate-900">
                      {lecture.title}
                    </h2>
                    {lecture.description ? (
                      <p className="mt-2 text-slate-600">{lecture.description}</p>
                    ) : null}
                    {lecture.folder ? (
                      <p className="mt-2 text-xs text-slate-400 font-mono">
                        /{lecture.folder}
                      </p>
                    ) : null}
                  </div>
                  <span className="rounded-full bg-sky-50 text-sky-700 text-xs font-semibold px-3 py-1 whitespace-nowrap">
                    {lecture.projects.filter((project) => project.available).length}/
                    {lecture.projects.length} 可閱讀
                  </span>
                </div>
              </div>

              <div className="p-6">
                {lecture.projects.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-sm text-slate-500">
                    此講次目前尚無專案。
                  </div>
                ) : (
                  <div className="space-y-3">
                    {lecture.projects.map((project) =>
                      project.available && project.google_doc_url ? (
                        <a
                          key={project.id}
                          href={project.google_doc_url}
                          className="block rounded-xl border border-slate-200 px-4 py-4 transition hover:border-sky-300 hover:bg-sky-50/40"
                        >
                          <div className="flex items-center justify-between gap-4">
                            <div>
                              <h3 className="text-lg font-semibold text-slate-900">
                                {project.title}
                              </h3>
                              <p className="mt-1 text-sm text-slate-500">
                                前往 Google Doc 閱讀逐字稿
                              </p>
                            </div>
                            <span className="text-sm font-semibold text-sky-700 whitespace-nowrap">
                              查看稿件 →
                            </span>
                          </div>
                        </a>
                      ) : (
                        <div
                          key={project.id}
                          className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-4"
                        >
                          <div className="flex items-center justify-between gap-4">
                            <div>
                              <h3 className="text-lg font-semibold text-slate-700">
                                {project.title}
                              </h3>
                              <p className="mt-1 text-sm text-slate-500">
                                尚未發布 Google Doc
                              </p>
                            </div>
                            <span className="text-sm font-semibold text-slate-400 whitespace-nowrap">
                              未就緒
                            </span>
                          </div>
                        </div>
                      ),
                    )}
                  </div>
                )}
              </div>
            </article>
          ))
        )}
      </section>
    </div>
  );
}
