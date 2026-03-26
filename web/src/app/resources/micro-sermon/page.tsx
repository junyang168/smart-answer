import type { Metadata } from "next";
import { Breadcrumb } from "@/app/components/common/Breadcrumb";
import { VideoThumbnail } from "@/app/components/resources/micro-sermon/VideoThumbnail";
import {
  MICRO_SERMON_REVALIDATE,
  fetchMicroSermons,
} from "./sermons";
import type { MicroSermonData } from "./sermons";

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

function getPublishedAtTimestamp(sermon: MicroSermonData): number | null {
  if (!sermon.publishedAt) return null;
  const timestamp = Date.parse(sermon.publishedAt);
  return Number.isNaN(timestamp) ? null : timestamp;
}

export default async function MicroSermonPage() {
  const breadcrumbLinks = [
    { name: "首頁", href: "/" },
    { name: "AI 輔助查經", href: "/resources" },
    { name: "微讲道" },
  ];

  const allSermons = await fetchMicroSermons();
  const featuredSermons = allSermons
    .map((sermon, index) => ({ sermon, index }))
    .filter(({ sermon }) => sermon.isFeatured)
    .sort((a, b) => {
      const aTimestamp = getPublishedAtTimestamp(a.sermon);
      const bTimestamp = getPublishedAtTimestamp(b.sermon);

      if (aTimestamp !== null && bTimestamp !== null && aTimestamp !== bTimestamp) {
        return bTimestamp - aTimestamp;
      }
      if (aTimestamp !== null) return -1;
      if (bTimestamp !== null) return 1;
      return a.index - b.index;
    })
    .slice(0, 2)
    .map(({ sermon }) => sermon);
  const featuredIds = new Set(featuredSermons.map((sermon) => sermon.id));
  const otherSermons = allSermons.filter((sermon) => !featuredIds.has(sermon.id));

  return (
    <div className="bg-gray-50 min-h-screen pb-20">
      <Breadcrumb links={breadcrumbLinks} />

      <div className="container mx-auto px-6 pt-12 md:pt-16">
        {/* ── Title Block ──────────────────────────────── */}
        <header className="mx-auto mb-12 max-w-4xl text-center md:mb-16">
          <h1 className="text-4xl md:text-5xl font-bold text-slate-900 tracking-tight">
            微讲道
          </h1>
          <p className="mt-5 text-lg md:text-xl text-slate-500 leading-relaxed">
            用簡短、清晰的內容，幫助你理解神的話語，並在生活中經歷祂的同在
          </p>
        </header>

        {featuredSermons.length > 0 ? (
          <>
            <section>
              <div className="grid gap-8 lg:grid-cols-2">
                {featuredSermons.map((sermon) => {
                  const videoId = extractYoutubeId(sermon.youtubeUrl);

                  return (
                    <article key={sermon.id} className="flex h-full flex-col">
                      <div className="overflow-hidden rounded-2xl">
                        {videoId ? (
                          <VideoThumbnail videoId={videoId} title={sermon.title} />
                        ) : (
                          <div className="w-full rounded-2xl bg-slate-200 flex items-center justify-center text-slate-500 aspect-video">
                            视频准备中
                          </div>
                        )}
                      </div>

                      <div className="flex flex-1 flex-col pt-5">
                        <h3 className="min-h-[5rem] text-xl font-bold leading-tight text-slate-900 md:text-2xl line-clamp-2">
                          {sermon.title}
                        </h3>
                        <div className="mt-2 min-h-[2rem]">
                          {sermon.series && (
                            <p className="text-sm font-medium tracking-wide text-amber-600">
                            {sermon.series}
                            {sermon.seriesNumber
                              ? ` ${String(sermon.seriesNumber).padStart(2, "0")}`
                              : ""}
                            </p>
                          )}
                        </div>
                        <div className="mt-3 min-h-[5.5rem]">
                          {sermon.intro && (
                            <p className="text-base leading-relaxed text-slate-700 line-clamp-3">
                              {sermon.intro}
                            </p>
                          )}
                        </div>
                        {sermon.description && (
                          <p className="mt-2 text-sm leading-relaxed text-slate-500 line-clamp-6">
                            {sermon.description}
                          </p>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          </>
        ) : allSermons.length > 0 ? (
          <div className="rounded-2xl border border-dashed border-amber-200 bg-amber-50 px-6 py-8 text-center text-amber-700">
            目前尚未設定精選微講道。
          </div>
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
