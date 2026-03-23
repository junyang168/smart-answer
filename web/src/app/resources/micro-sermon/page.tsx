import type { Metadata } from "next";
import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { VideoThumbnail } from "@/app/components/resources/micro-sermon/VideoThumbnail";
import {
  MICRO_SERMON_REVALIDATE,
  fetchFeaturedMicroSermon,
  fetchMicroSermons,
} from "./sermons";

export const metadata: Metadata = {
  title: "微讲道 | 達拉斯聖道教會",
  description:
    "用简短、清晰的内容，帮助你理解神的话语，并在生活中经历祂的同在。",
};

export const revalidate = MICRO_SERMON_REVALIDATE;

/**
 * Extract YouTube video ID from various URL formats.
 */
function extractYoutubeId(url: string): string | null {
  if (!url) return null;
  const patterns = [
    /(?:youtube\.com\/watch\?v=)([^&\s]+)/,
    /(?:youtu\.be\/)([^?\s]+)/,
    /(?:youtube\.com\/embed\/)([^?\s]+)/,
  ];
  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match) return match[1];
  }
  return null;
}

export default async function MicroSermonPage() {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "微讲道" },
  ];

  const featured = await fetchFeaturedMicroSermon();
  const allSermons = await fetchMicroSermons();
  const otherSermons = allSermons.filter((s) => s.id !== featured?.id);
  const videoId = featured ? extractYoutubeId(featured.youtubeUrl) : null;

  return (
    <div className="bg-gray-50 min-h-screen pb-20">
      <Breadcrumb links={breadcrumbLinks} />

      {/* Main content area: calm, centered, max-width ~800px */}
      <div className="mx-auto max-w-[800px] px-6 pt-12 md:pt-16">
        {/* ── Title Block ──────────────────────────────── */}
        <header className="text-center mb-12 md:mb-16">
          <h1 className="text-4xl md:text-5xl font-bold text-slate-900 tracking-tight">
            微讲道
          </h1>
          <p className="mt-5 text-lg md:text-xl text-slate-500 leading-relaxed">
            用簡短、清晰的內容，幫助你理解神的話語，並在生活中經歷祂的同在
          </p>
        </header>

        {featured ? (
          <>
            {/* ── Video Title ──────────────────────────── */}
            <div className="mb-6">
              <h2 className="text-2xl md:text-3xl font-bold text-slate-900 leading-snug">
                {featured.title}
              </h2>
              {featured.series && (
                <p className="mt-2 text-sm font-medium text-amber-600 tracking-wide">
                  {featured.series}
                  {featured.seriesNumber
                    ? ` ${String(featured.seriesNumber).padStart(2, "0")}`
                    : ""}
                </p>
              )}
            </div>

            {/* ── YouTube Thumbnail Link ────────────────── */}
            {videoId ? (
              <VideoThumbnail videoId={videoId} title={featured.title} />
            ) : (
              <div className="w-full rounded-2xl bg-slate-200 flex items-center justify-center text-slate-500 aspect-video">
                视频准备中
              </div>
            )}

            {/* ── Intro + Description ─────────────────── */}
            <div className="mt-8 md:mt-10 space-y-4">
              {featured.intro && (
                <p className="text-lg md:text-xl text-slate-700 font-medium leading-relaxed">
                  {featured.intro}
                </p>
              )}
              {featured.description && (
                <p className="text-base text-slate-500 leading-relaxed">
                  {featured.description}
                </p>
              )}
            </div>
          </>
        ) : (
          <div className="rounded-2xl border border-dashed border-gray-300 bg-white px-6 py-16 text-center text-gray-500">
            微講道內容建置中，敬請期待。
          </div>
        )}

        {/* ── Divider ─────────────────────────────────── */}
        {otherSermons.length > 0 && (
          <>
            <hr className="my-12 md:my-16 border-slate-200" />

            {/* ── More Content Section ────────────────── */}
            <section>
              <h3 className="text-xl font-semibold text-slate-800 mb-6">
                更多内容
              </h3>
              <div className="space-y-4">
                {otherSermons.map((sermon) => (
                  <div
                    key={sermon.id}
                    className="flex items-start gap-4 rounded-xl border border-slate-200 bg-white p-5 transition hover:shadow-sm"
                  >
                    <div className="flex-1 min-w-0">
                      <h4 className="text-base font-semibold text-slate-900 leading-snug">
                        {sermon.title}
                      </h4>
                      {sermon.intro && (
                        <p className="mt-1.5 text-sm text-slate-500 line-clamp-2">
                          {sermon.intro}
                        </p>
                      )}
                      {sermon.series && (
                        <p className="mt-2 text-xs text-slate-400">
                          {sermon.series}
                          {sermon.seriesNumber
                            ? ` ${String(sermon.seriesNumber).padStart(2, "0")}`
                            : ""}
                        </p>
                      )}
                    </div>
                    {sermon.tag && (
                      <span className="shrink-0 inline-flex rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                        {sermon.tag}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
