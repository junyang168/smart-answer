"use client";

import { useRef } from "react";
import { BookOpenText, ExternalLink, FileAudio, NotebookText } from "lucide-react";
import type { ReviewSourceFragment, ReviewSourceMedia } from "./types";

function formatTime(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  const value = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function mediaUrl(url: string): string {
  return process.env.NODE_ENV === "production" ? url : url.replace("/web/video/", "/dev-media/");
}

function SourceMediaPlayer({ media }: { media: ReviewSourceMedia }) {
  const positioned = useRef(false);
  const start = media.start_seconds;
  const end = media.end_seconds;
  const position = (element: HTMLMediaElement) => {
    if (!positioned.current && start !== null && start >= 0) {
      element.currentTime = start;
      positioned.current = true;
    }
  };
  const stopAtEnd = (element: HTMLMediaElement) => {
    if (end !== null && end > 0 && element.currentTime >= end) element.pause();
  };
  const label = start === null ? "原讲道录音" : `从 ${formatTime(start)} 播放`;
  return (
    <span className="mt-4 block rounded-xl border border-amber-200 bg-amber-50/70 p-3">
      <span className="mb-2 flex items-center gap-2 text-xs font-bold text-amber-950">
        <FileAudio className="h-4 w-4" />{label}
      </span>
      {media.kind === "audio" ? (
        <audio
          controls
          preload="none"
          src={mediaUrl(media.url)}
          className="h-10 w-full"
          onLoadedMetadata={(event) => position(event.currentTarget)}
          onPlay={(event) => position(event.currentTarget)}
          onTimeUpdate={(event) => stopAtEnd(event.currentTarget)}
        />
      ) : (
        <video
          controls
          preload="none"
          src={mediaUrl(media.url)}
          className="max-h-72 w-full rounded-lg bg-black"
          onLoadedMetadata={(event) => position(event.currentTarget)}
          onPlay={(event) => position(event.currentTarget)}
          onTimeUpdate={(event) => stopAtEnd(event.currentTarget)}
        />
      )}
    </span>
  );
}

function SourceCard({ source }: { source: ReviewSourceFragment }) {
  const transcript = source.source_type === "sermon_transcript";
  const Icon = transcript ? BookOpenText : NotebookText;
  return (
    <article className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
      <p className="flex items-center gap-2 text-xs font-black tracking-[0.08em] text-stone-500">
        <Icon className="h-4 w-4 text-amber-800" />
        {transcript ? "教授原文逐字稿" : "母本片段"}
      </p>
      <p className="mt-2 text-sm font-bold leading-6 text-stone-900">{source.title}</p>
      <blockquote className="mt-3 border-l-2 border-amber-700 pl-4 text-sm leading-7 text-stone-700">
        {source.excerpts.map((excerpt) => <p key={excerpt} className="mt-2 first:mt-0">{excerpt}</p>)}
      </blockquote>
      {source.media ? <SourceMediaPlayer media={source.media} /> : null}
      {source.full_source_url ? (
        <a
          href={source.full_source_url}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex items-center gap-1.5 text-xs font-bold text-indigo-700 hover:underline"
        >
          {transcript ? "查看完整逐字稿" : "查看完整母本"}<ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : null}
    </article>
  );
}

export function SourceEvidenceDisclosure({ sources }: { sources: ReviewSourceFragment[] }) {
  return (
    <details className="not-prose my-5 rounded-2xl border border-dashed border-amber-300 bg-amber-50/30 open:border-solid open:bg-[#fffaf0]">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-bold text-amber-950 marker:hidden">
        <span className="inline-flex items-center gap-2">
          <BookOpenText className="h-4 w-4" />查看本段来源
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">{sources.length}</span>
        </span>
      </summary>
      <div className="grid gap-3 border-t border-amber-200 p-4">
        {sources.map((source) => <SourceCard key={source.fragment_ids.join(":")} source={source} />)}
      </div>
    </details>
  );
}
