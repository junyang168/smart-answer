import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { Breadcrumb } from "@/app/components/common/Breadcrumb";

import { TopicNavigator } from "../TopicNavigator";
import {
  fetchNotesToManuscriptSeriesDetail,
  fetchSeriesTopics,
  NotesToManuscriptSeriesDetail,
  NOTES_TO_MANUSCRIPT_REVALIDATE,
  TopicListResponse,
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

  let series: NotesToManuscriptSeriesDetail;
  try {
    series = await fetchNotesToManuscriptSeriesDetail(seriesId);
  } catch {
    notFound();
  }

  let topicList: TopicListResponse = { available: false, count: 0, topics: [] };
  try {
    topicList = await fetchSeriesTopics(series.id);
  } catch {
    // non-fatal: degrade to manuscript-only navigation
  }

  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "講義轉逐字稿系列", href: "/resources/notes_to_manuscript_series" },
    { name: series.title },
  ];
  const projectLinks = Object.fromEntries(
    series.lectures.flatMap((lecture) =>
      lecture.projects.map((project) => [
        project.id,
        {
          title: project.title,
          href: `/resources/notes_to_manuscript_series/${series.id}/${project.id}`,
          available: project.available,
        },
      ]),
    ),
  );

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
        </div>
      </section>

      <Suspense fallback={null}>
        <TopicNavigator
          seriesId={series.id}
          seriesTitle={series.title}
          topics={topicList.topics}
          topicsAvailable={topicList.available}
          projectLinks={projectLinks}
          lectures={series.lectures}
        />
      </Suspense>
    </div>
  );
}
