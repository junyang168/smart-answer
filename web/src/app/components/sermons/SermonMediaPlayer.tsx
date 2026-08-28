import React, { useEffect, useRef, useState } from 'react';
import { Sermon} from '@/app/interfaces/article';
import Link from 'next/link';
import { SermonPptSlide, type SermonSlideDeck } from './SermonPptSlide';

const mediaSrc = (url: string | undefined | null) => {
  if (!url) return "";
  return process.env.NODE_ENV === "production"
    ? url
    : url.replace("/web/video/", "/dev-media/");
};

// 在文件頂部或合適位置添加 SermonMediaPlayer 組件
export const SermonMediaPlayer: React.FC<{
  sermon: Sermon;
  authenticated: boolean;
  startTime?: number;
  slideDeck?: SermonSlideDeck | null;
}> = ({ sermon, authenticated, startTime, slideDeck }) => {
    const mediaRef = useRef<HTMLMediaElement | null>(null);
    const [seconds, setSeconds] = useState(startTime ?? 0);

    useEffect(() => {
      if (mediaRef.current && typeof startTime === "number" && startTime >= 0) {
        mediaRef.current.currentTime = startTime;
        setSeconds(startTime);
      }
    }, [startTime, sermon.id]);

    const seek = (at: number) => {
      const media = mediaRef.current;
      if (!media) return;
      media.currentTime = at;
      setSeconds(at);
      void media.play();
    };

    return ( authenticated ? (
        <div className="mb-8 flex flex-col gap-4 rounded-xl border bg-gray-100 p-4 shadow-lg">
          {slideDeck?.slides?.length ? (
            <SermonPptSlide
              deck={slideDeck}
              seconds={seconds}
              onSeek={seek}
              coverTitle={sermon.title}
            />
          ) : null}
          {sermon.videoUrl ? (
            // --- 如果有視頻，渲染視頻播放器 ---
            <video
              ref={(element) => { mediaRef.current = element; }}
              key={`${sermon.id}-video`} // 使用唯一的 key
              controls
              onTimeUpdate={(event) => setSeconds(event.currentTarget.currentTime)}
              className="w-full h-auto bg-black"
            >
              <source src={mediaSrc(sermon.videoUrl)} type="video/mp4" />
              您的瀏覽器不支持 video 標籤。
            </video>
          ) : (
            // --- 如果沒有視頻，渲染音頻播放器作為主播放器 ---
            <div className="flex flex-col items-center justify-center rounded-lg bg-gray-50 p-5 text-center">
                <h2 className="text-lg font-semibold text-gray-700 mb-4">本篇講道僅提供音頻格式</h2>
                <audio
                  ref={(element) => { mediaRef.current = element; }}
                  key={`${sermon.id}-audio-main`} // 使用唯一的 key
                  controls
                  onTimeUpdate={(event) => setSeconds(event.currentTarget.currentTime)}
                  className="w-full max-w-md"
                >
                  <source src={mediaSrc(sermon.audioUrl)} type="audio/mpeg" />
                  您的瀏覽器不支持 audio 標籤。
                </audio>
            </div>
          )}
        </div>
    ) : (
    <div className="p-8 flex flex-col items-left justify-center text-left ">
      {sermon.source ? (
        <Link
          href={sermon.source}
          target="_blank"
          className="text-[#FBBF24] font-semibold underline decoration-yellow-400/70 underline-offset-2 hover:decoration-yellow-400 transition-all mx-1"
        >
          錄音，錄影來源
        </Link>
      ) : (
        <span className="text-lg text-gray-700 mb-4">講道錄音，錄影僅對教會成員和同工開放。如果您是教會成員，請登入以查看內容。如果您對教會有興趣，歡迎聯繫我們。</span>
        )}
    </div>
    )
)
};

    
