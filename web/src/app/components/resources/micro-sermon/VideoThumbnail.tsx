"use client";

import { useState } from "react";

interface VideoThumbnailProps {
  videoId: string;
  title: string;
}

export function VideoThumbnail({ videoId, title }: VideoThumbnailProps) {
  const [imgSrc, setImgSrc] = useState(
    `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`
  );

  return (
    <a
      href={`https://www.youtube.com/watch?v=${videoId}`}
      target="_blank"
      rel="noopener noreferrer"
      className="group relative block w-full overflow-hidden rounded-2xl shadow-lg bg-slate-900 aspect-video"
    >
      <img
        src={imgSrc}
        alt={title}
        className="absolute inset-0 w-full h-full object-cover transition-transform duration-500 group-hover:scale-105 opacity-90"
        onError={() => {
          // Fallback to high quality if maxres is not available
          if (imgSrc.includes("maxresdefault")) {
            setImgSrc(`https://img.youtube.com/vi/${videoId}/hqdefault.jpg`);
          }
        }}
      />
      {/* Play Button Overlay */}
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="w-16 h-16 md:w-20 md:h-20 bg-white/90 rounded-full flex items-center justify-center shadow-2xl transition-transform group-hover:scale-110">
          <svg
            className="w-8 h-8 md:w-10 md:h-10 text-slate-900 translate-x-0.5"
            fill="currentColor"
            viewBox="0 0 24 24"
          >
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
      </div>
      {/* Hover Tint */}
      <div className="absolute inset-0 bg-black/10 transition-colors group-hover:bg-black/0" />
    </a>
  );
}
