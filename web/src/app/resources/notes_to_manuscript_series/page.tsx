import type { Metadata } from "next";
import Link from "next/link";

import { Breadcrumb } from "@/app/components/common/Breadcrumb";

import {
  fetchNotesToManuscriptSeries,
  NOTES_TO_MANUSCRIPT_REVALIDATE,
} from "./data";

export const metadata: Metadata = {
  title: "講義轉逐字稿系列 | AI 輔助查經",
  description: "按系列查看講義轉逐字稿的教學講稿，並直接前往 Google Doc 閱讀完整內容。",
};

export const revalidate = NOTES_TO_MANUSCRIPT_REVALIDATE;

export default async function NotesToManuscriptSeriesPage() {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "講義轉逐字稿系列" },
  ];
  const seriesList = await fetchNotesToManuscriptSeries();

  return (
    <div className="bg-gray-50 min-h-screen pb-16">
      <Breadcrumb links={breadcrumbLinks} />

      <section className="container mx-auto px-6 pt-6">
        <div className="max-w-4xl">
          <p className="uppercase tracking-widest text-sm font-semibold text-sky-700 mb-3">
            Notes To Manuscript
          </p>
          <h1 className="text-4xl md:text-5xl font-bold text-slate-900 leading-tight">
            講義轉逐字稿系列
          </h1>
          <p className="mt-4 text-lg text-slate-600 leading-relaxed">
            依系列查看各講次與已發布的逐字稿專案。進入系列後，您可以按講次瀏覽並直接前往對應的 Google Doc。
          </p>
        </div>
      </section>

      <section className="container mx-auto px-6 mt-10">
        {seriesList.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center text-slate-500">
            目前尚無可瀏覽的講義轉逐字稿系列。
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {seriesList.map((series) => (
              <Link
                key={series.id}
                href={`/resources/notes_to_manuscript_series/${series.id}`}
                className="block rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition hover:shadow-md hover:border-sky-300"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h2 className="text-2xl font-bold text-slate-900">
                      {series.title}
                    </h2>
                    {series.description ? (
                      <p className="mt-3 text-slate-600 leading-relaxed">
                        {series.description}
                      </p>
                    ) : null}
                  </div>
                  <span className="rounded-full bg-sky-50 text-sky-700 text-xs font-semibold px-3 py-1 whitespace-nowrap">
                    {series.project_type === "transcript" ? "逐字稿" : "講義專案"}
                  </span>
                </div>

                <dl className="mt-6 grid grid-cols-3 gap-3 text-sm">
                  <div className="rounded-xl bg-slate-50 px-4 py-3">
                    <dt className="text-slate-500">講次</dt>
                    <dd className="mt-1 text-xl font-semibold text-slate-900">
                      {series.lecture_count}
                    </dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-4 py-3">
                    <dt className="text-slate-500">專案</dt>
                    <dd className="mt-1 text-xl font-semibold text-slate-900">
                      {series.project_count}
                    </dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-4 py-3">
                    <dt className="text-slate-500">可閱讀</dt>
                    <dd className="mt-1 text-xl font-semibold text-slate-900">
                      {series.available_project_count}
                    </dd>
                  </div>
                </dl>

                {series.folder ? (
                  <p className="mt-4 text-xs text-slate-400 font-mono">
                    Source Folder: {series.folder}
                  </p>
                ) : null}
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
