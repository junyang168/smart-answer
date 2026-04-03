"use client";

import { useEffect, useMemo, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/app/components/popover";
import { cn } from "@/app/utils/utils";
import { SundaySong } from "@/app/types/sundayService";

interface SundaySongPickerProps {
  value: string;
  songs: SundaySong[];
  recentSongs: SundaySong[];
  placeholder?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}

function formatSongLabel(song: SundaySong): string {
  if (song.source === "hymnal" && song.hymnalIndex != null) {
    return `第 ${song.hymnalIndex} 首 | ${song.title}`;
  }
  if (song.source === "hymnal") {
    return `教會聖詩 | ${song.title}`;
  }
  return `自訂 | ${song.title}`;
}

function matchesSongIndex(song: SundaySong, query: string): boolean {
  const trimmed = query.trim();
  if (!trimmed) {
    return true;
  }
  if (song.hymnalIndex == null) {
    return false;
  }
  return String(song.hymnalIndex).includes(trimmed);
}

export function SundaySongPicker({
  value,
  songs,
  recentSongs,
  placeholder = "未指定",
  disabled = false,
  onChange,
}: SundaySongPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) {
      setQuery("");
    }
  }, [open]);

  const selectedSong = useMemo(
    () => songs.find((song) => song.title === value) ?? recentSongs.find((song) => song.title === value) ?? null,
    [recentSongs, songs, value],
  );

  const filteredRecentSongs = useMemo(
    () => recentSongs.filter((song) => matchesSongIndex(song, query)),
    [query, recentSongs],
  );

  const recentSongIds = useMemo(() => new Set(recentSongs.map((song) => song.id)), [recentSongs]);

  const filteredSongs = useMemo(
    () =>
      songs.filter((song) => !recentSongIds.has(song.id) && matchesSongIndex(song, query)),
    [query, recentSongIds, songs],
  );

  const handleSelect = (nextValue: string) => {
    onChange(nextValue);
    setOpen(false);
  };

  const triggerLabel = selectedSong ? formatSongLabel(selectedSong) : value.trim() || placeholder;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex w-full items-center justify-between rounded border border-gray-300 px-3 py-2 text-left text-sm focus:border-blue-400 focus:outline-none",
            disabled && "cursor-not-allowed bg-gray-100 text-gray-400",
          )}
          disabled={disabled}
        >
          <span className={cn("truncate", !selectedSong && !value.trim() && "text-gray-400")}>
            {triggerLabel}
          </span>
          <span className="ml-3 shrink-0 text-xs text-gray-400">搜尋</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[min(32rem,calc(100vw-2rem))] border border-gray-200 bg-white p-0"
      >
        <div className="border-b border-gray-100 px-3 py-3">
          <input
            type="text"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
            placeholder="輸入教會聖詩索引，例如 226"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoFocus
          />
          <p className="mt-2 text-xs text-gray-500">支援以 hymnIndex 搜尋；未輸入時顯示全部詩歌。</p>
        </div>
        <div className="max-h-80 overflow-y-auto p-2">
          <button
            type="button"
            className={cn(
              "mb-2 flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm hover:bg-gray-50",
              !value.trim() && "bg-blue-50 text-blue-700",
            )}
            onClick={() => handleSelect("")}
          >
            <span>未指定</span>
          </button>

          {filteredRecentSongs.length > 0 && (
            <div className="mb-3">
              <div className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-amber-700">
                最近录入
              </div>
              <div className="space-y-1">
                {filteredRecentSongs.map((song) => {
                  const selected = song.title === value;
                  return (
                    <button
                      key={`recent-${song.id}`}
                      type="button"
                      className={cn(
                        "flex w-full flex-col rounded px-3 py-2 text-left hover:bg-amber-50",
                        selected && "bg-blue-50",
                      )}
                      onClick={() => handleSelect(song.title)}
                    >
                      <span className="text-sm font-medium text-gray-900">{song.title}</span>
                      <span className="text-xs text-gray-500">{formatSongLabel(song)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {filteredSongs.length > 0 && (
            <div className="space-y-1">
              <div className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                全部詩歌
              </div>
              {filteredSongs.map((song) => {
                const selected = song.title === value;
                return (
                  <button
                    key={song.id}
                    type="button"
                    className={cn(
                      "flex w-full flex-col rounded px-3 py-2 text-left hover:bg-gray-50",
                      selected && "bg-blue-50",
                    )}
                    onClick={() => handleSelect(song.title)}
                  >
                    <span className="text-sm font-medium text-gray-900">{song.title}</span>
                    <span className="text-xs text-gray-500">{formatSongLabel(song)}</span>
                  </button>
                );
              })}
            </div>
          )}

          {filteredRecentSongs.length === 0 && filteredSongs.length === 0 && (
            <div className="px-3 py-6 text-center text-sm text-gray-500">找不到符合索引的詩歌。</div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
