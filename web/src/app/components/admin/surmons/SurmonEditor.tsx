"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent as ReactChangeEvent, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import dynamic from "next/dynamic";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "easymde/dist/easymde.min.css";
import { useSession, signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import { AlertTriangle, BookmarkPlus, Loader2, RotateCcw, Save, UserPlus, Video } from "lucide-react";
import type SimpleMDEEditor from "easymde";
import type { Options as SimpleMDEOptions } from "easymde";

import {
  SurmonAssignPayload,
  SurmonPermissions,
  SurmonScriptParagraph,
  SurmonScriptResponse,
  SurmonUpdateScriptPayload,
  SurmonUpdateHeaderPayload,
  SurmonSlideAsset,
} from "@/app/types/surmon-editor";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), { ssr: false });
const SAVE_DELAY = process.env.NODE_ENV === "development" ? 3000 : 10000;
const API_PREFIX = "/sc_api";
const SLIDES_PREFIX = "/api/slides";
const MEDIA_PREFIX = "/web/video";

interface SlidePickerModalProps {
  open: boolean;
  slides: SurmonSlideAsset[];
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  onSelect: (slide: SurmonSlideAsset) => void;
  onRetry: () => void;
  onClose: () => void;
  formatTimestamp: (value: number | null | undefined) => string | null;
}

const SlidePickerModal = ({
  open,
  slides,
  status,
  error,
  onSelect,
  onRetry,
  onClose,
  formatTimestamp,
}: SlidePickerModalProps) => {
  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <p className="text-sm font-semibold text-gray-700">選擇投影片</p>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-gray-500 transition hover:text-gray-700"
          >
            關閉
          </button>
        </div>
        <div className="max-h-80 overflow-y-auto px-4 py-3">
          {status === "loading" ? (
            <p className="text-xs text-gray-500">正在載入投影片...</p>
          ) : status === "error" ? (
            <div className="space-y-2 text-xs text-red-600">
              <p>{error ?? "投影片載入失敗"}</p>
              <button
                type="button"
                onClick={onRetry}
                className="rounded border border-red-200 px-2 py-1 text-red-600 hover:bg-red-50"
              >
                重新嘗試
              </button>
            </div>
          ) : slides.length === 0 ? (
            <p className="text-xs text-gray-500">目前沒有投影片資料。</p>
          ) : (
            <div className="grid gap-2">
              {slides.map((slide) => {
                const timestamp = formatTimestamp(slide.timestamp_seconds ?? null);
                return (
                  <button
                    key={slide.id}
                    type="button"
                    onClick={() => onSelect(slide)}
                    className="flex items-center gap-3 rounded-lg border border-transparent bg-gray-50 p-2 text-left shadow-sm transition hover:border-blue-200 hover:bg-blue-50"
                  >
                    <div className="h-16 w-28 overflow-hidden rounded-md border border-gray-200 bg-gray-100">
                      <Image
                        src={slide.image_url}
                        alt={`Slide ${slide.id}`}
                        width={112}
                        height={64}
                        unoptimized
                        className="h-full w-full object-cover"
                      />
                    </div>
                    <div className="flex-1">
                      <p className="text-xs font-medium text-gray-700">{slide.id}</p>
                      {timestamp ? <p className="text-[11px] text-gray-500">{timestamp}</p> : null}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div className="flex justify-end border-t border-gray-200 px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
};

interface SurmonEditorProps {
  item: string;
  viewChanges: boolean;
}

interface UserProfile {
  name: string;
  email: string;
}

interface SurmonEditorState {
  status: "idle" | "loading" | "ready" | "error";
  error?: string;
  header?: SurmonScriptResponse["header"];
  paragraphs: SurmonScriptParagraph[];
}

const FALLBACK_USER_ID = "junyang168@gmail.com";

const ensureSequence = (paragraphs: SurmonScriptParagraph[]) =>
  paragraphs.map((para, index) => ({ ...para, s_index: index }));

const parseTimelineToSeconds = (timeline?: string): number | null => {
  if (!timeline) return null;
  const parts = timeline
    .trim()
    .split(":")
    .map((value) => Number(value));
  if (parts.some((value) => Number.isNaN(value))) {
    return null;
  }
  return parts.reduce((total, segment) => total * 60 + segment, 0);
};

const getParagraphStartTime = (paragraph: SurmonScriptParagraph): number | null => {
  if (typeof paragraph.start_time === "number" && !Number.isNaN(paragraph.start_time)) {
    return paragraph.start_time;
  }
  return parseTimelineToSeconds(paragraph.start_timeline);
};

export const SurmonEditor = ({ item, viewChanges }: SurmonEditorProps) => {
  const { data: session, status: authStatus } = useSession();
  const router = useRouter();

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [permissions, setPermissions] = useState<SurmonPermissions | null>(null);
  const [state, setState] = useState<SurmonEditorState>({ status: "idle", paragraphs: [] });
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [bookmarkIndex, setBookmarkIndex] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState<string>("");
  const [isSavingTitle, setIsSavingTitle] = useState(false);
  const [titleSaveError, setTitleSaveError] = useState<string | null>(null);
  const [slides, setSlides] = useState<SurmonSlideAsset[]>([]);
  const [slidesStatus, setSlidesStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [slidesError, setSlidesError] = useState<string | null>(null);
  const [openSlidePickerIndex, setOpenSlidePickerIndex] = useState<number | null>(null);

  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const saveTimerRef = useRef<NodeJS.Timeout | null>(null);
  const activeEditorRef = useRef<SimpleMDEEditor | null>(null);
  const activeEditorIndexRef = useRef<number | null>(null);
  const slidesLoadedRef = useRef(false);

  const sessionEmail = session?.user?.email ?? null;

  const resolvedUserEmail = useMemo(() => sessionEmail ?? FALLBACK_USER_ID, [sessionEmail]);

  const canEdit = Boolean(permissions?.canWrite && !viewChanges);

  const handleMediaRef = useCallback((element: HTMLVideoElement | HTMLAudioElement | null) => {
    mediaRef.current = element;
  }, []);

  useEffect(() => {
    if (!canEdit) {
      setEditingIndex(null);
    }
  }, [canEdit]);

  useEffect(() => {
    const nextTitle = state.header?.title ?? item;
    setTitleDraft((prev) => (prev === nextTitle ? prev : nextTitle));
  }, [item, state.header?.title]);

  const selectedParagraph = useMemo(() => {
    if (selectedIndex == null) return null;
    return state.paragraphs[selectedIndex] ?? null;
  }, [selectedIndex, state.paragraphs]);

  const paragraphTimingData = useMemo(
    () =>
      state.paragraphs.map((paragraph, index) => ({
        index,
        start: getParagraphStartTime(paragraph),
        end:
          typeof paragraph.end_time === "number" && !Number.isNaN(paragraph.end_time)
            ? paragraph.end_time
            : null,
      })),
    [state.paragraphs]
  );

  const timedParagraphs = useMemo(() => {
    const sorted = paragraphTimingData
      .filter((entry): entry is { index: number; start: number; end: number | null } =>
        typeof entry.start === "number" && !Number.isNaN(entry.start)
      )
      .sort((a, b) => a.start - b.start)
      .map((entry) => ({ ...entry }));

    for (let i = 0; i < sorted.length; i += 1) {
      const current = sorted[i];
      if (current.end == null) {
        const next = sorted[i + 1];
        if (next?.start != null) {
          current.end = next.start;
        }
      }
    }

    return sorted;
  }, [paragraphTimingData]);

  const selectedTiming = useMemo(() => {
    if (selectedIndex == null) return null;
    return timedParagraphs.find((entry) => entry.index === selectedIndex) ?? null;
  }, [selectedIndex, timedParagraphs]);

  const mediaSource = useMemo(() => {
    if (!state.header) return null;
    const isAudio = state.header.type === "audio";
    const extension = isAudio ? "mp3" : "mp4";
    return `${MEDIA_PREFIX}/${encodeURIComponent(item)}.${extension}`;
  }, [state.header, item]);

  const formatTimestamp = useCallback((value: number | null | undefined) => {
    if (value == null || Number.isNaN(value)) {
      return null;
    }
    const totalSeconds = Math.max(0, Math.floor(value));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    const minutePart = hours > 0 ? String(minutes).padStart(2, "0") : String(minutes);
    const secondPart = String(seconds).padStart(2, "0");
    return hours > 0 ? `${hours}:${minutePart}:${secondPart}` : `${minutePart}:${secondPart}`;
  }, []);

  const seekMedia = useCallback((time: number) => {
    const media = mediaRef.current;
    if (!media) return;
    const wasPaused = media.paused;
    media.currentTime = Math.max(time, 0);
    if (!wasPaused) {
      media.play().catch(() => {
        // Ignore autoplay errors.
      });
    }
  }, []);

  const selectedStart = useMemo(() => {
    if (selectedIndex == null) return null;
    const entry = paragraphTimingData[selectedIndex];
    if (entry && typeof entry.start === "number" && !Number.isNaN(entry.start)) {
      return entry.start;
    }
    return null;
  }, [paragraphTimingData, selectedIndex]);

  const selectedEnd = useMemo(() => {
    if (!selectedTiming) return null;
    if (selectedTiming.end != null && !Number.isNaN(selectedTiming.end)) {
      return selectedTiming.end;
    }
    return null;
  }, [selectedTiming]);

  const fetchJSON = useCallback(async <T,>(url: string, init?: RequestInit) => {
    const response = await fetch(url, init);
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`${response.status} ${response.statusText}${text ? `: ${text}` : ""}`);
    }
    return (await response.json()) as T;
  }, []);

  const refreshPermissions = useCallback(async () => {
    if (!resolvedUserEmail) return;
    const data = await fetchJSON<SurmonPermissions>(
      `${API_PREFIX}/permissions/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}`
    );
    setPermissions(data);
  }, [fetchJSON, item, resolvedUserEmail]);

  useEffect(() => {
    if (authStatus === "loading") {
      return;
    }
    if (!sessionEmail && process.env.NODE_ENV === "production") {
      setPermissions(null);
      return;
    }
    refreshPermissions().catch(() => {
      // keep the existing permissions snapshot if refresh fails
    });
  }, [authStatus, item, refreshPermissions, resolvedUserEmail, sessionEmail]);

  const refreshBookmark = useCallback(async () => {
    if (!resolvedUserEmail) return;
    try {
      const data = await fetchJSON<{ index: string }>(
        `${API_PREFIX}/bookmark/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}`
      );
      setBookmarkIndex(data.index);
    } catch (error) {
      // Optional feature; ignore errors silently
    }
  }, [fetchJSON, item, resolvedUserEmail]);

  const loadData = useCallback(async () => {
    if (!resolvedUserEmail) return;
    setState({ status: "loading", paragraphs: [] });

    try {
      const [userInfo, perms, script] = await Promise.all([
        fetchJSON<UserProfile>(`${API_PREFIX}/user/${encodeURIComponent(resolvedUserEmail)}`),
        fetchJSON<SurmonPermissions>(
          `${API_PREFIX}/permissions/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}`
        ),
        fetchJSON<SurmonScriptResponse>(
          `${API_PREFIX}/sermon/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}/${
            viewChanges ? "changes" : "no_changes"
          }`
        ),
      ]);


      setProfile(userInfo);
      setPermissions(perms);
      const paragraphs = ensureSequence(script.script ?? []);
      setState({
        status: "ready",
        header: script.header,
        paragraphs,
      });
      setTitleSaveError(null);
      setIsSavingTitle(false);
      setEditingIndex(null);
      setSelectedIndex(paragraphs.length > 0 ? 0 : null);
      refreshBookmark();
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知錯誤";
      setState({ status: "error", error: message, paragraphs: [] });
    }
  }, [fetchJSON, item, refreshBookmark, resolvedUserEmail, viewChanges]);

  const loadSlides = useCallback(async () => {
    if (slidesStatus === "loading" || slidesLoadedRef.current) {
      return;
    }
    setSlidesStatus("loading");
    setSlidesError(null);
    try {
      const data = await fetchJSON<SurmonSlideAsset[]>(
        `${SLIDES_PREFIX}/${encodeURIComponent(item)}`
      );
      setSlides(data);
      slidesLoadedRef.current = true;
      setSlidesStatus("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "投影片載入失敗";
      setSlidesError(message);
      setSlidesStatus("error");
    }
  }, [fetchJSON, item, slidesStatus]);

  useEffect(() => {
    slidesLoadedRef.current = false;
    setSlides([]);
    setSlidesError(null);
    setSlidesStatus("idle");
  }, [item]);

  const createSlideMarkdown = useCallback(
    (slide: SurmonSlideAsset) => {
      const timestampLabel = formatTimestamp(slide.timestamp_seconds ?? null);
      const altText = timestampLabel ? `Slide ${slide.id} @ ${timestampLabel}` : `Slide ${slide.id}`;
      return `![${altText}](${slide.image_url})`;
    },
    [formatTimestamp]
  );

  const handleSlideRetry = useCallback(() => {
    slidesLoadedRef.current = false;
    setSlidesError(null);
    setSlidesStatus("idle");
  }, []);

  const handleOpenSlidePicker = useCallback(() => {
    if (!canEdit) {
      return;
    }
    const index = activeEditorIndexRef.current;
    if (index == null) {
      return;
    }
    setOpenSlidePickerIndex(index);
  }, [canEdit]);

  const sortedSlidesForPicker = useMemo(() => {
    if (openSlidePickerIndex == null || slides.length === 0) {
      return slides;
    }
    const targetStart = paragraphTimingData[openSlidePickerIndex]?.start;
    if (typeof targetStart !== "number" || Number.isNaN(targetStart)) {
      return slides;
    }

    return [...slides].sort((a, b) => {
      const aTime = typeof a.timestamp_seconds === "number" ? a.timestamp_seconds : Number.POSITIVE_INFINITY;
      const bTime = typeof b.timestamp_seconds === "number" ? b.timestamp_seconds : Number.POSITIVE_INFINITY;
      const aDistance = Math.abs(aTime - targetStart);
      const bDistance = Math.abs(bTime - targetStart);
      if (aDistance === bDistance) {
        return aTime - bTime;
      }
      return aDistance - bDistance;
    });
  }, [openSlidePickerIndex, paragraphTimingData, slides]);

  useEffect(() => {
    if (authStatus === "loading") {
      return;
    }
    if (authStatus === "unauthenticated" && process.env.NODE_ENV === "production") {
      setState({ status: "error", paragraphs: [], error: "請登入後再進入編輯頁面。" });
      return;
    }
    loadData().catch(() => {});
  }, [authStatus, loadData, sessionEmail]);

  useEffect(() => {
    if (state.status === "ready" && canEdit) {
      loadSlides().catch(() => {});
    }
  }, [canEdit, loadSlides, state.status]);

  useEffect(() => {
    if (openSlidePickerIndex != null && slidesStatus === "idle") {
      loadSlides().catch(() => {});
    }
  }, [loadSlides, openSlidePickerIndex, slidesStatus]);

  useEffect(() => {
    if (editingIndex == null) {
      activeEditorRef.current = null;
      activeEditorIndexRef.current = null;
    }
  }, [editingIndex]);

  useEffect(() => {
    if (openSlidePickerIndex != null && editingIndex !== openSlidePickerIndex) {
      setOpenSlidePickerIndex(null);
    }
  }, [editingIndex, openSlidePickerIndex]);

  const requestSave = useCallback(
    (paragraphs: SurmonScriptParagraph[]) => {
      if (!canEdit || !resolvedUserEmail) {
        return;
      }
      setSaveError(null);
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
      saveTimerRef.current = setTimeout(async () => {
        setIsSaving(true);
        try {
          const payload: SurmonUpdateScriptPayload = {
            user_id: resolvedUserEmail,
            item,
            type: "scripts",
            data: paragraphs,
          };
          await fetchJSON(`${API_PREFIX}/update_script`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          setLastSavedAt(new Date());
        } catch (error) {
          const message = error instanceof Error ? error.message : "儲存失敗";
          setSaveError(message);
        } finally {
          setIsSaving(false);
        }
      }, SAVE_DELAY);
    },
    [canEdit, fetchJSON, item, resolvedUserEmail]
  );

  const updateParagraph = useCallback(
    (index: number, updater: (paragraph: SurmonScriptParagraph) => SurmonScriptParagraph) => {
      setState((prev) => {
        const current = prev.paragraphs;
        if (!current[index]) {
          return prev;
        }
        const next = ensureSequence(
          current.map((paragraph, idx) => (idx === index ? updater(paragraph) : paragraph))
        );
        requestSave(next);
        return { ...prev, paragraphs: next };
      });
    },
    [requestSave]
  );

  const handleInsertSlide = useCallback(
    (index: number, slide: SurmonSlideAsset) => {
      const editor = activeEditorRef.current;
      const markdown = createSlideMarkdown(slide);
      if (editor && activeEditorIndexRef.current === index) {
        const doc = editor.codemirror.getDoc();
        const selection = doc.getSelection();
        const insertion = selection ? `${selection}\n\n${markdown}\n` : `${markdown}\n`;
        doc.replaceSelection(insertion);
        editor.codemirror.focus();
      } else {
        updateParagraph(index, (paragraph) => {
          const base = paragraph.text ?? "";
          const trimmed = base.trimEnd();
          const prefix = trimmed ? `${trimmed}\n\n` : "";
          return { ...paragraph, text: `${prefix}${markdown}\n` };
        });
      }
      setOpenSlidePickerIndex(null);
    },
    [createSlideMarkdown, updateParagraph]
  );

  const handleEditorChange = useCallback(
    (index: number, value: string) => {
      updateParagraph(index, (paragraph) => ({ ...paragraph, text: value }));
    },
    [updateParagraph]
  );

  const handlePlayMedia = useCallback(() => {
    if (!mediaSource) return;
    const media = mediaRef.current;
    if (!media) return;
    media.play().catch(() => {
      // Ignore autoplay errors triggered by browsers.
    });
  }, [mediaSource]);

  const handlePauseMedia = useCallback(() => {
    mediaRef.current?.pause();
  }, []);

  const handleSkipBackward = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    const target = Math.max((media.currentTime ?? 0) - 5, 0);
    seekMedia(target);
  }, [seekMedia]);

  const handleSkipForward = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    const duration = Number.isFinite(media.duration) ? media.duration : null;
    const current = media.currentTime ?? 0;
    const target = duration != null ? Math.min(current + 5, duration) : current + 5;
    seekMedia(target);
  }, [seekMedia]);

  const handleJumpToStart = useCallback(() => {
    if (selectedStart == null) return;
    seekMedia(selectedStart);
  }, [seekMedia, selectedStart]);

  const handleJumpToEnd = useCallback(() => {
    if (selectedEnd == null) return;
    const baseline = selectedStart != null && selectedEnd <= selectedStart ? selectedStart : selectedEnd;
    const target = baseline > 0.25 ? baseline - 0.25 : baseline;
    seekMedia(target);
  }, [seekMedia, selectedEnd, selectedStart]);

  const handleSelect = useCallback(
    (index: number) => {
      setSelectedIndex(index);
      setEditingIndex((current) => {
        if (!canEdit || current == null) {
          return current;
        }
        return index;
      });

      const start = paragraphTimingData[index]?.start;
      if (typeof start === "number" && !Number.isNaN(start)) {
        seekMedia(start);
      }
    },
    [canEdit, paragraphTimingData, seekMedia]
  );

  const handleParagraphClick = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>, index: number) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".editor-toolbar")) {
        return;
      }
      handleSelect(index);
    },
    [handleSelect]
  );

  const handleTitleChange = useCallback((event: ReactChangeEvent<HTMLInputElement>) => {
    setTitleSaveError(null);
    setTitleDraft(event.target.value);
  }, []);

  const saveTitle = useCallback(async () => {
    if (!canEdit || !resolvedUserEmail) {
      return;
    }
    const currentTitle = state.header?.title ?? item;
    const nextTitle = titleDraft.trim();
    if (!nextTitle) {
      setTitleDraft(currentTitle);
      return;
    }
    if (nextTitle === currentTitle) {
      return;
    }
    setIsSavingTitle(true);
    setTitleSaveError(null);
    try {
      const payload: SurmonUpdateHeaderPayload = {
        user_id: resolvedUserEmail,
        item,
        title: nextTitle 
      };
      await fetchJSON(`${API_PREFIX}/update_header`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setState((prev) => {
        if (!prev.header) {
          return prev;
        }
        return {
          ...prev,
          header: { ...prev.header, title: nextTitle },
        };
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新標題失敗";
      setTitleSaveError(message);
      const fallback = state.header?.title ?? item;
      setTitleDraft(fallback);
    } finally {
      setIsSavingTitle(false);
    }
  }, [canEdit, fetchJSON, item, resolvedUserEmail, state.header?.title, titleDraft]);

  const handleTitleBlur = useCallback(() => {
    saveTitle().catch(() => {});
  }, [saveTitle]);

  const handleTitleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        saveTitle().catch(() => {});
      } else if (event.key === "Escape") {
        const currentTitle = state.header?.title ?? item;
        setTitleDraft(currentTitle);
        setTitleSaveError(null);
        event.currentTarget.blur();
      }
    },
    [item, saveTitle, state.header?.title]
  );

  const editorToolbar = useMemo<NonNullable<SimpleMDEOptions["toolbar"]>>(
    () =>
      [
        "heading",
        "bold",
        "italic",
        "strikethrough",
        "quote",
        "unordered-list",
        "ordered-list",
        "code",
        "table",
        "link",
        {
          name: "insert-slide",
          action: () => handleOpenSlidePicker(),
          className: "fa fa-image",
          title: "插入投影片",
        },
        "horizontal-rule",
        "|",
        "preview",
        "side-by-side",
        "fullscreen",
        "guide",
        "|",
        {
          name: "jump-start",
          action: () => handleJumpToStart(),
          className: "fa fa-step-backward",
          title: "跳至段落開頭",
        },
        {
          name: "play-media",
          action: () => handlePlayMedia(),
          className: "fa fa-play",
          title: "播放媒體",
        },
        {
          name: "pause-media",
          action: () => handlePauseMedia(),
          className: "fa fa-pause",
          title: "暫停媒體",
        },
        {
          name: "skip-backward",
          action: () => handleSkipBackward(),
          className: "fa fa-rotate-left",
          title: "倒退 5 秒",
        },
        {
          name: "skip-forward",
          action: () => handleSkipForward(),
          className: "fa fa-rotate-right",
          title: "快轉 5 秒",
        },
        {
          name: "jump-end",
          action: () => handleJumpToEnd(),
          className: "fa fa-step-forward",
          title: "跳至段落結尾",
        }
      ] as NonNullable<SimpleMDEOptions["toolbar"]>,
    [
      handleJumpToEnd,
      handleJumpToStart,
      handleOpenSlidePicker,
      handlePauseMedia,
      handlePlayMedia,
      handleSkipBackward,
      handleSkipForward,
    ]
  );

  const handleStartEditing = useCallback(
    (index: number) => {
      if (!canEdit) return;
      setSelectedIndex(index);
      setEditingIndex(index);
    },
    [canEdit, setEditingIndex, setSelectedIndex]
  );

  const handleAddComment = useCallback(
    (afterIndex: number) => {
      if (!canEdit || !profile?.name) return;
      setState((prev) => {
        const next = [...prev.paragraphs];
        const reference = next[afterIndex];
        if (!reference) return prev;
        const insertionIndex = afterIndex + 1;
        const newComment: SurmonScriptParagraph = {
          index: reference.index,
          type: "comment",
          text: "",
          user_id: resolvedUserEmail ?? undefined,
          user_name: profile.name,
        };
        next.splice(insertionIndex, 0, newComment);
        const sequenced = ensureSequence(next);
        requestSave(sequenced);
        return { ...prev, paragraphs: sequenced };
      });
      setSelectedIndex(afterIndex + 1);
      setEditingIndex(afterIndex + 1);
    },
    [canEdit, profile?.name, requestSave, resolvedUserEmail, setEditingIndex, setSelectedIndex]
  );

  const handleAssignToggle = useCallback(async () => {
    if (!permissions || !resolvedUserEmail) return;
    const action: SurmonAssignPayload["action"] = permissions.canAssign ? "assign" : "unassign";
    try {
      const payload: SurmonAssignPayload = {
        user_id: resolvedUserEmail,
        item,
        action,
      };
      await fetchJSON(`${API_PREFIX}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      await refreshPermissions();
      await loadData();
    } catch (error) {
      const message = error instanceof Error ? error.message : "認領操作失敗";
      window.alert(`認領操作失敗：${message}`);
    }
  }, [fetchJSON, item, loadData, permissions, refreshPermissions, resolvedUserEmail]);

  const handlePublish = useCallback(async () => {
    if (!resolvedUserEmail) return;
    try {
      await fetchJSON(
        `${API_PREFIX}/publish/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}`,
        { method: "PUT" }
      );
      await loadData();
    } catch (error) {
      const message = error instanceof Error ? error.message : "發布失敗";
      window.alert(`發布失敗：${message}`);
    }
  }, [fetchJSON, item, loadData, resolvedUserEmail]);

  const handleMarkBookmark = useCallback(
    async (paragraph: SurmonScriptParagraph) => {
      if (!resolvedUserEmail || !paragraph.index) return;
      try {
        await fetchJSON(
          `${API_PREFIX}/bookmark/${encodeURIComponent(resolvedUserEmail)}/${encodeURIComponent(item)}/${encodeURIComponent(paragraph.index)}`,
          { method: "PUT" }
        );
        setBookmarkIndex(paragraph.index);
      } catch (error) {
        // ignore bookmark failures
      }
    },
    [fetchJSON, item, resolvedUserEmail]
  );

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media || timedParagraphs.length === 0) {
      return;
    }

    const handleTimeUpdate = () => {
      const currentTime = media.currentTime ?? 0;
      let targetIndex: number | null = null;

      for (let i = 0; i < timedParagraphs.length; i += 1) {
        const current = timedParagraphs[i];
        const endBoundary = current.end ?? timedParagraphs[i + 1]?.start ?? Number.POSITIVE_INFINITY;
        if (currentTime >= current.start && currentTime < endBoundary) {
          targetIndex = current.index;
          break;
        }
      }

      if (targetIndex == null) {
        const last = timedParagraphs[timedParagraphs.length - 1];
        if (last && currentTime >= last.start) {
          targetIndex = last.index;
        }
      }

      if (targetIndex != null) {
        setSelectedIndex((prev) => (prev === targetIndex ? prev : targetIndex));
      }
    };

    media.addEventListener("timeupdate", handleTimeUpdate);

    return () => {
      media.removeEventListener("timeupdate", handleTimeUpdate);
    };
  }, [timedParagraphs]);

  const renderParagraphContent = useCallback(
    (paragraph: SurmonScriptParagraph) => {
      const fallback = paragraph.type === "comment" ? "（評論）" : "（尚未填寫）";
      if (!paragraph.text) {
        return <p className="text-gray-400 italic">{fallback}</p>;
      }

      if (viewChanges) {
        const html = paragraph.text
          .replace(/\n/g, "<br/>")
          .replace(/<->/g, '<span class="bg-red-100 text-red-700 line-through">')
          .replace(/<\+>/g, '<span class="bg-green-100 text-green-700">')
          .replace(/<\/>/g, "</span>");
        return (
          <div
            className="prose prose-sm max-w-none"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        );
      }

      return (
        <ReactMarkdown remarkPlugins={[remarkGfm]} className="prose prose-sm max-w-none">
          {paragraph.text}
        </ReactMarkdown>
      );
    },
    [viewChanges]
  );

  if (!sessionEmail && authStatus !== "loading" && process.env.NODE_ENV === "production") {
    return (
      <div className="bg-white border border-amber-200 rounded-lg p-12 text-center shadow-sm">
        <AlertTriangle className="w-10 h-10 text-amber-500 mx-auto mb-4" />
        <h2 className="text-2xl font-semibold mb-3">需要登入</h2>
        <p className="text-gray-600 mb-8">
          請登入教會 Google 帳號以存取講道編輯器。
        </p>
        <button
          onClick={() => signIn("google")}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white font-medium rounded-md hover:bg-blue-700 transition"
        >
          使用 Google 登入
        </button>
      </div>
    );
  }

  if (state.status === "loading" || authStatus === "loading") {
    return (
      <div className="flex items-center justify-center py-24 text-blue-600">
        <Loader2 className="w-6 h-6 mr-2 animate-spin" />
        正在載入講道資料...
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="max-w-2xl mx-auto bg-white border border-red-200 rounded-lg p-8 text-center text-red-600">
        {state.error ?? "載入資料時發生錯誤"}
      </div>
    );
  }

  const header = state.header;

  return (
    <>
      <div className="space-y-6">
      <nav className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-lg text-gray-600">标题：</span>
          <input
            type="text"
            value={titleDraft}
            onChange={handleTitleChange}
            onBlur={handleTitleBlur}
            onKeyDown={handleTitleKeyDown}
            disabled={!canEdit}
            className={`w-96 max-w-xl rounded-md border px-2 py-1 text-lg ${
              canEdit
                ? "border-gray-300 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                : "border-transparent bg-transparent text-gray-700"
            }`}
            placeholder="輸入講道標題"
            aria-label="講道標題"
          />
          {isSavingTitle && <span className="text-xs text-blue-600">儲存中...</span>}
        </div>
        <div className="flex-1" />
        <button
          onClick={() => router.push(`/admin/surmons/${encodeURIComponent(item)}?view=${viewChanges ? "draft" : "changes"}`)}
          className="inline-flex items-center px-2.5 py-1.5 font-medium text-gray-700 bg-white border border-gray-200 rounded-md hover:bg-gray-100"
        >
          <RotateCcw className="w-4 h-4 mr-2" /> {viewChanges ? "返回編輯" : "查看差異"}
        </button>
        {permissions?.canAssign || permissions?.canUnassign ? (
  
          <button
            onClick={handleAssignToggle}
            className="inline-flex items-center px-2.5 py-1.5 font-medium text-blue-600 bg-blue-50 border border-blue-200 rounded-md hover:bg-blue-100"
          >
            <UserPlus className="w-4 h-4 mr-2" /> {permissions.canAssign ? "認領" : "取消認領"}
          </button>
        ) : null}
        {permissions?.canPublish ? (
          <button
            onClick={handlePublish}
            className="inline-flex items-center px-2.5 py-1.5 font-medium text-green-700 bg-green-100 border border-green-200 rounded-md hover:bg-green-200"
          >
            <Save className="w-4 h-4 mr-2" /> 發布完成版本
          </button>
        ) : null}
        {permissions?.canViewPublished ? (
          <a
            href={`/resources/sermons/${encodeURIComponent(item)}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center px-2.5 py-1.5 font-medium text-gray-700 bg-white border border-gray-200 rounded-md hover:bg-gray-100"
          >
            <Video className="w-4 h-4 mr-2" /> 查看完成版本
          </a>
        ) : null}
      </nav>
      {titleSaveError && <p className="text-xs text-red-600">{titleSaveError}</p>}



      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <aside className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            {header?.type === "audio" ? (
              <audio ref={handleMediaRef} controls className="w-full">
                <source src={mediaSource ?? ""} />
                您的瀏覽器不支援 audio 元素。
              </audio>
            ) : (
              <video ref={handleMediaRef} controls className="w-full">
                <source src={mediaSource ?? ""} />
                您的瀏覽器不支援 video 元素。
              </video>
            )}
          </div>
          <header className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
            <div className="flex flex-wrap items-center gap-4 text-sm text-gray-600">
              {header?.deliver_date && <span>讲道日期：{header.deliver_date}</span>}
              {header?.theme && <span>主題：{header.theme}</span>}
              <span>
                權限：
                {permissions?.canWrite ? "可編輯" : permissions?.canRead ? "僅可讀" : "未授權"}
                {viewChanges && "（差異檢視）"}
              </span>
            </div>
            {saveError && <p className="mt-3 text-sm text-red-600">儲存失敗：{saveError}</p>}
            {isSaving && <p className="mt-3 text-sm text-blue-600">正在自動儲存...</p>}
            {lastSavedAt && !isSaving && (
              <p className="mt-3 text-sm text-gray-500">最後儲存：{lastSavedAt.toLocaleTimeString()}</p>
            )}
          </header>
        </aside>

        <section className="xl:col-span-2 space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h2 className="text-sm font-semibold text-gray-700">講道稿內容</h2>
              {selectedParagraph && (
                <button
                  onClick={() => handleMarkBookmark(selectedParagraph)}
                  className="inline-flex items-center text-xs px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100"
                >
                  <BookmarkPlus className="w-3 h-3 mr-1" /> 設為書籤
                </button>
              )}
            </div>

            <div className="max-h-[70vh] overflow-y-auto">
              <div className="divide-y divide-gray-100">
                {state.paragraphs.map((paragraph, index) => {
                  const isComment = paragraph.type === "comment";
                  const showAddComment = canEdit && !isComment;
                  const isBookmarked = paragraph.index === bookmarkIndex;
                  const isEditing = canEdit && editingIndex === index;
                  const isSelected = index === selectedIndex;
                  return (
                    <div
                      key={`${paragraph.index}-${index}`}
                      className={`group px-4 py-3 transition-colors ${
                        isSelected ? "bg-blue-50" : "hover:bg-gray-50"
                      } ${isEditing ? "border-l-2 border-blue-300" : ""}`}
                      onClick={(event) => handleParagraphClick(event, index)}
                      onDoubleClick={canEdit ? () => handleStartEditing(index) : undefined}
                    >
                      <div className="flex items-start gap-3">
                        <div className="pt-1 text-xs font-semibold text-gray-400 w-16">
                          {paragraph.start_timeline ?? "--:--"}
                        </div>
                        <div className="flex-1">
                          {isEditing ? (
                            <div className="space-y-2">
                              <SimpleMDE
                                key={`${paragraph.index ?? "paragraph"}-${index}`}
                                value={paragraph.text}
                                onChange={(value) => handleEditorChange(index, value)}
                                getMdeInstance={(instance) => {
                                  activeEditorRef.current = instance;
                                  activeEditorIndexRef.current = index;
                                }}
                                options={{
                                  autofocus: true,
                                  spellChecker: false,
                                  status: false,
                                  placeholder: "在此編輯講道內容...",
                                  toolbar: editorToolbar,
                                }}
                              />
                            </div>
                          ) : (
                            renderParagraphContent(paragraph)
                          )}

                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
    <SlidePickerModal
      open={openSlidePickerIndex != null}
      slides={sortedSlidesForPicker}
      status={slidesStatus}
      error={slidesError}
      onSelect={(slide) => {
        if (openSlidePickerIndex != null) {
          handleInsertSlide(openSlidePickerIndex, slide);
        }
      }}
      onRetry={handleSlideRetry}
      onClose={() => setOpenSlidePickerIndex(null)}
      formatTimestamp={formatTimestamp}
    />
    </>
  );
};
