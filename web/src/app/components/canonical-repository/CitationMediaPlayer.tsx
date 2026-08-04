"use client";

import Link from "next/link";
import { signIn, useSession } from "next-auth/react";
import { useCallback, useEffect, useRef } from "react";

type CitationMediaPlayerProps = {
  source: {
    source_type: string;
    public_url: string;
    media?: { kind?: "audio" | "video" | "unknown"; url?: string | null };
  };
  startTime?: number | null;
};

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function CitationMediaPlayer({ source, startTime }: CitationMediaPlayerProps) {
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const { status } = useSession();
  const authenticated = process.env.NODE_ENV !== "production" || status === "authenticated";
  const seekToCitation = useCallback(() => {
    if (mediaRef.current && typeof startTime === "number" && startTime >= 0) {
      mediaRef.current.currentTime = startTime;
    }
  }, [startTime]);

  useEffect(() => {
    seekToCitation();
  }, [seekToCitation, source.media?.url]);

  if (source.source_type !== "sermon_transcript") return null;

  if (!authenticated) {
    return (
      <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-700">
        <p>讲道录音与录像仅向教会成员及同工开放。</p>
        <button onClick={() => signIn("google")} className="mt-3 rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white">
          使用 Google 登录后播放
        </button>
      </div>
    );
  }

  if (!source.media?.url || source.media.kind === "unknown") {
    return <p className="mt-4 text-sm text-amber-700">尚未找到媒体文件；请打开原讲道页面查看。</p>;
  }

  return (
    <div className="mt-4 overflow-hidden rounded-lg border bg-slate-50">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 text-sm">
        <span className="font-semibold text-slate-700">
          {source.media.kind === "audio" ? "讲道录音" : "讲道录像"}
          {typeof startTime === "number" ? ` · 已定位至 ${formatTime(startTime)}` : ""}
        </span>
        <Link href={source.public_url} className="font-semibold text-indigo-700 hover:underline">
          打开完整讲道与逐字稿
        </Link>
      </div>
      {source.media.kind === "video" ? (
        <video
          ref={(element) => { mediaRef.current = element; }}
          controls
          preload="metadata"
          onLoadedMetadata={seekToCitation}
          className="aspect-video w-full bg-black"
        >
          <source src={source.media.url} type="video/mp4" />
          您的浏览器不支持视频播放。
        </video>
      ) : (
        <div className="p-6">
          <audio
            ref={(element) => { mediaRef.current = element; }}
            controls
            preload="metadata"
            onLoadedMetadata={seekToCitation}
            className="w-full"
          >
            <source src={source.media.url} type="audio/mpeg" />
            您的浏览器不支持音频播放。
          </audio>
        </div>
      )}
    </div>
  );
}
