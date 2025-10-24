"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChangeEvent as ReactChangeEvent,
  FormEvent as ReactFormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from "react";
import { visit } from "unist-util-visit";
import dynamic from "next/dynamic";
import Image from "next/image";
import ReactMarkdown, { type Components as ReactMarkdownComponents } from "react-markdown";
import remarkGfm from "remark-gfm";
import "easymde/dist/easymde.min.css";
import { useSession, signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import { AlertTriangle, BookmarkPlus, Loader2, RotateCcw, Save, Sparkles, UserPlus, Video } from "lucide-react";
import type SimpleMDEEditor from "easymde";
import type { Options as SimpleMDEOptions } from "easymde";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";

import {
  SurmonAssignPayload,
  SurmonPermissions,
  SurmonScriptParagraph,
  SurmonScriptResponse,
  SurmonUpdateScriptPayload,
  SurmonUpdateHeaderPayload,
  SurmonGenerateMetadataPayload,
  SurmonGenerateMetadataResponse,
  SurmonSlideAsset,
  SurmonChatMessage,
  SurmonChatResponse,
  SurmonChatReference,
  SurmonCoreBibleVerse,
} from "@/app/types/surmon-editor";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), { ssr: false });
const SAVE_DELAY = process.env.NODE_ENV === "development" ? 3000 : 10000;
const API_PREFIX = "/api/sc_api";
const SLIDES_PREFIX = "/api/slides";
const MEDIA_PREFIX = "/web/video";
const INDEX_LINK_PREFIX = "index:";

const INDEX_TOKEN_PATTERN = /\[([0-9]+(?:_[0-9]+)?)\]/g;

const remarkSurmonIndexLinks = () =>
  (tree: any) => {
    visit(tree, "text", (node: any, index: number | undefined, parent: any) => {
      if (!parent || typeof node.value !== "string") {
        return;
      }
      if (parent.type === "link" || parent.type === "linkReference" || parent.type === "definition") {
        return;
      }
      if (parent.type === "code" || parent.type === "inlineCode") {
        return;
      }

      const { value } = node;
      const matches = [...value.matchAll(INDEX_TOKEN_PATTERN)];
      if (matches.length === 0) {
        return;
      }

      const newChildren: any[] = [];
      let cursor = 0;

      matches.forEach((match) => {
        const matchIndex = match.index ?? 0;
        if (matchIndex > cursor) {
          newChildren.push({ type: "text", value: value.slice(cursor, matchIndex) });
        }

        const token = match[1];
        newChildren.push({
          type: "link",
          url: `${INDEX_LINK_PREFIX}${token}`,
          data: {
            surmonIndexToken: token,
            hProperties: {
              "data-surmon-index-token": token,
            },
          },
          children: [{ type: "text", value: `[${token}]` }],
        });

        cursor = matchIndex + match[0].length;
      });

      if (cursor < value.length) {
        newChildren.push({ type: "text", value: value.slice(cursor) });
      }

      if (typeof index === "number") {
        parent.children.splice(index, 1, ...newChildren);
        return index + newChildren.length;
      }
      return undefined;
    });
  };

interface SlidePickerModalProps {
  open: boolean;
  slides: SurmonSlideAsset[];
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  onSelect: (slide: SurmonSlideAsset) => void;
  onRetry: () => void;
  onClose: () => void;
  activeIndex: number | null;
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
  activeIndex,
  formatTimestamp,
}: SlidePickerModalProps) => {
  const virtuosoRef = useRef<VirtuosoHandle | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    if (activeIndex == null) {
      return;
    }
    const handle = virtuosoRef.current;
    if (!handle) {
      return;
    }
    handle.scrollToIndex({ index: activeIndex, align: "start", behavior: "auto" });
  }, [open, activeIndex]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-4xl rounded-lg bg-white shadow-xl"
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
        <div className="px-4 py-3">
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
            <Virtuoso
              ref={virtuosoRef}
              style={{ height: "60vh" }}
              data={slides}
              totalCount={slides.length}
              initialTopMostItemIndex={activeIndex ?? 0}
              itemContent={(index, slide) => {
                const timestamp = formatTimestamp(slide.timestamp_seconds ?? null);
                const isActive = activeIndex === index;
                return (
                  <button
                    type="button"
                    onClick={() => onSelect(slide)}
                    className={`w-full rounded-lg border ${
                      isActive ? "border-blue-300 bg-blue-50" : "border-transparent bg-gray-50"
                    } p-3 text-left shadow-sm transition hover:border-blue-200 hover:bg-blue-50`}
                  >
                    <div className="flex gap-4">
                      <div className="w-[500px] overflow-hidden rounded-md border border-gray-200 bg-gray-100">
                        <Image
                          src={slide.image_url}
                          alt={`Slide ${slide.id}`}
                          width={1000}
                          height={1000}
                          unoptimized
                          className="h-auto w-full object-contain"
                        />
                      </div>
                      <div className="flex-1 space-y-1">
                        <p className="text-sm font-semibold text-gray-700">{slide.id}</p>
                        {timestamp ? <p className="text-xs text-gray-500">{timestamp}</p> : null}
                        {slide.extracted_text ? (
                          <p className="text-xs text-gray-600 line-clamp-4">{slide.extracted_text}</p>
                        ) : null}
                      </div>
                    </div>
                  </button>
                );
              }}
            />
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

interface SurmonBibleVerseDraft {
  book: string;
  chapter_verse: string;
  text: string;
}

interface SurmonMetadataDraft {
  title: string;
  summary: string;
  keypoints: string;
  coreBibleVerses: SurmonBibleVerseDraft[];
}

const FALLBACK_USER_ID = "junyang168@gmail.com";

interface SurmonConversationMessage extends SurmonChatMessage {
  id: string;
  quotes?: SurmonChatReference[] | null;
}

const ensureSequence = (paragraphs: SurmonScriptParagraph[]) =>
  paragraphs.map((para, index) => ({ ...para, s_index: index }));

const mapHeaderVersesToDrafts = (
  verses: SurmonCoreBibleVerse[] | undefined
): SurmonBibleVerseDraft[] =>
  (verses ?? []).map((verse) => ({
    book: verse.book ?? "",
    chapter_verse: verse.chapter_verse ?? "",
    text: verse.text ?? "",
  }));

const normalizeVerseDrafts = (verses: SurmonBibleVerseDraft[]): SurmonBibleVerseDraft[] =>
  verses
    .map((verse) => ({
      book: verse.book.trim(),
      chapter_verse: verse.chapter_verse.trim(),
      text: verse.text.trim(),
    }))
    .filter((verse) => verse.book || verse.chapter_verse || verse.text);

const areVerseDraftsEqual = (a: SurmonBibleVerseDraft[], b: SurmonBibleVerseDraft[]) => {
  if (a.length !== b.length) {
    return false;
  }
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].book !== b[i].book || a[i].chapter_verse !== b[i].chapter_verse || a[i].text !== b[i].text) {
      return false;
    }
  }
  return true;
};

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
  const [metadataDraft, setMetadataDraft] = useState<SurmonMetadataDraft>({
    title: "",
    summary: "",
    keypoints: "",
    coreBibleVerses: [],
  });
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [metadataSaveError, setMetadataSaveError] = useState<string | null>(null);
  const [isGeneratingMetadata, setIsGeneratingMetadata] = useState(false);
  const [metadataGenerateError, setMetadataGenerateError] = useState<string | null>(null);
  const [slides, setSlides] = useState<SurmonSlideAsset[]>([]);
  const [slidesStatus, setSlidesStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [slidesError, setSlidesError] = useState<string | null>(null);
  const [openSlidePickerIndex, setOpenSlidePickerIndex] = useState<number | null>(null);
  const [chatMessages, setChatMessages] = useState<SurmonConversationMessage[]>([]);
  const [chatInput, setChatInput] = useState<string>("");
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [highlightedParagraphs, setHighlightedParagraphs] = useState<Set<number>>(() => new Set());
  const [activeHighlightToken, setActiveHighlightToken] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"script" | "metadata">("script");

  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const saveTimerRef = useRef<NodeJS.Timeout | null>(null);
  const activeEditorRef = useRef<SimpleMDEEditor | null>(null);
  const activeEditorIndexRef = useRef<number | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
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
    if (chatMessages.length === 0 && !isChatLoading) {
      return;
    }
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages, isChatLoading]);

  useEffect(() => {
    setChatMessages([]);
    setChatInput("");
    setChatError(null);
    setIsChatLoading(false);
    setHighlightedParagraphs(new Set());
    setActiveHighlightToken(null);
  }, [item]);

  useEffect(() => {
    const header = state.header;
    const nextTitle = header?.title ?? item;
    const nextSummary = header?.summary ?? "";
    const nextKeypoints = header?.keypoints ?? "";
    const nextVerses = mapHeaderVersesToDrafts(header?.core_bible_verse);

    setMetadataDraft((prev) => {
      if (
        prev.title === nextTitle &&
        prev.summary === nextSummary &&
        prev.keypoints === nextKeypoints &&
        areVerseDraftsEqual(prev.coreBibleVerses, nextVerses)
      ) {
        return prev;
      }
      return {
        title: nextTitle,
        summary: nextSummary,
        keypoints: nextKeypoints,
        coreBibleVerses: nextVerses,
      };
    });
  }, [item, state.header]);

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

  const sendChatRequest = useCallback(
    async (history: SurmonChatMessage[]) => {
      if (!resolvedUserEmail) {
        throw new Error("缺少使用者資訊，請重新登入後再試。");
      }
      return fetchJSON<SurmonChatResponse>(
        `${API_PREFIX}/surmon_chat/${encodeURIComponent(resolvedUserEmail)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item, history }),
        }
      );
    },
    [fetchJSON, item, resolvedUserEmail]
  );

  const computeHighlightSet = useCallback(
    (token: string) => {
      const normalized = token.trim();
      const indices = new Set<number>();
      let firstMatch: number | null = null;

      if (!normalized) {
        return { indices, firstMatch };
      }

      const addIndex = (candidate: number) => {
        if (!indices.has(candidate)) {
          indices.add(candidate);
          if (firstMatch == null || candidate < firstMatch) {
            firstMatch = candidate;
          }
        }
      };

      state.paragraphs.forEach((paragraph, arrayIndex) => {
        const rawIndex = paragraph.index;
        const indexValue = typeof rawIndex === "string" ? rawIndex : rawIndex != null ? String(rawIndex) : "";
        if (indexValue === normalized) {
          addIndex(arrayIndex);
        }
      });

      const rangeMatch = normalized.match(/^(\d+)[_\-](\d+)$/);
      if (rangeMatch) {
        const start = Number(rangeMatch[1]);
        const end = Number(rangeMatch[2]);
        if (!Number.isNaN(start) && !Number.isNaN(end)) {
          const [min, max] = start <= end ? [start, end] : [end, start];
          state.paragraphs.forEach((paragraph, arrayIndex) => {
            const prefixRaw = paragraph.index?.split("_")[0] ?? "";
            const prefixNumber = Number(prefixRaw);
            if (!Number.isNaN(prefixNumber) && prefixNumber >= min && prefixNumber <= max) {
              addIndex(arrayIndex);
            }
          });
        }
      } else {
        state.paragraphs.forEach((paragraph, arrayIndex) => {
          const rawIndex = paragraph.index;
          const indexValue = typeof rawIndex === "string" ? rawIndex : rawIndex != null ? String(rawIndex) : "";
          if (indexValue === normalized || indexValue.startsWith(`${normalized}_`)) {
            addIndex(arrayIndex);
          }
        });
      }

      return { indices, firstMatch };
    },
    [state.paragraphs]
  );

  const highlightParagraphsByToken = useCallback(
    (token: string) => {
      const normalized = token.trim();
      if (!normalized) {
        return;
      }

      if (activeHighlightToken === normalized && highlightedParagraphs.size > 0) {
        setHighlightedParagraphs(new Set());
        setActiveHighlightToken(null);
        return;
      }

      const { indices, firstMatch } = computeHighlightSet(normalized);
      setHighlightedParagraphs(indices);
      setActiveHighlightToken(indices.size > 0 ? normalized : null);

      if (firstMatch != null && typeof window !== "undefined") {
        window.requestAnimationFrame(() => {
          const target = document.getElementById(`surmon-paragraph-${firstMatch}`);
          target?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
      }
    },
    [activeHighlightToken, highlightedParagraphs, computeHighlightSet]
  );

  const assistantRemarkPlugins = useMemo(() => [remarkGfm, remarkSurmonIndexLinks], []);

  const assistantMarkdownComponents = useMemo<ReactMarkdownComponents>(
    () => ({
      a: ({ node, children, href, ...props }) => {
        const token = node?.properties['data-surmon-index-token']
        if (typeof token === "string" && token.length > 0) {
          return (
            <span
              role="button"
              tabIndex={0}
              onClick={(event) => {
                event.preventDefault?.();
                event.stopPropagation?.();
                highlightParagraphsByToken(token);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  highlightParagraphsByToken(token);
                }
              }}
              className="cursor-pointer rounded px-1 text-blue-600 underline decoration-dotted underline-offset-4 transition-colors hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-300"
            >
              {children}
            </span>
          );
        }
        return (
          <a
            {...props}
            href={href ?? undefined}
            className="text-blue-600 underline"
            rel="noreferrer"
            target="_blank"
          >
            {children}
          </a>
        );
      },
    }),
    [highlightParagraphsByToken]
  );

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
      setMetadataSaveError(null);
      setIsSavingMetadata(false);
      setMetadataGenerateError(null);
      setIsGeneratingMetadata(false);
      setActiveTab("script");
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

  const handleChatInputChange = useCallback((event: ReactChangeEvent<HTMLTextAreaElement>) => {
    setChatInput(event.target.value);
  }, []);

  const handleChatSubmit = useCallback(
    async (event?: ReactFormEvent<HTMLFormElement>) => {
      event?.preventDefault();
      const question = chatInput.trim();
      if (!question || isChatLoading) {
        return;
      }

      const messageId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const userMessage: SurmonConversationMessage = {
        id: messageId,
        role: "user",
        content: question,
      };

      const nextMessages = [...chatMessages, userMessage];
      setChatMessages(nextMessages);
      setChatInput("");
      setIsChatLoading(true);
      setChatError(null);

      const historyPayload: SurmonChatMessage[] = nextMessages.map(({ role, content }) => ({ role, content }));

      try {
        const response = await sendChatRequest(historyPayload);
        if (!response?.answer) {
          throw new Error("未取得助理回應，請稍後再試。");
        }
        const assistantMessage: SurmonConversationMessage = {
          id: `${messageId}-reply`,
          role: "assistant",
          content: response.answer,
          quotes: response.quotes ?? null,
        };
        setChatMessages((prev) => [...prev, assistantMessage]);
      } catch (error) {
        const message = error instanceof Error ? error.message : "無法取得助理回應，請稍後再試。";
        setChatError(message);
        setChatMessages((prev) => prev.filter((entry) => entry.id !== messageId));
        setChatInput(question);
      } finally {
        setIsChatLoading(false);
      }
    },
    [chatInput, chatMessages, isChatLoading, sendChatRequest]
  );

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

  const closestSlideIndex = useMemo(() => {
    if (openSlidePickerIndex == null || slides.length === 0) {
      return null;
    }
    const targetStart = paragraphTimingData[openSlidePickerIndex]?.start;
    if (typeof targetStart !== "number" || Number.isNaN(targetStart)) {
      return null;
    }

    let bestIndex: number | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    let bestTime = Number.POSITIVE_INFINITY;

    slides.forEach((slide, index) => {
      const time = typeof slide.timestamp_seconds === "number" ? slide.timestamp_seconds : Number.POSITIVE_INFINITY;
      const distance = Math.abs(time - targetStart);
      if (distance < bestDistance || (distance === bestDistance && time < bestTime)) {
        bestDistance = distance;
        bestIndex = index;
        bestTime = time;
      }
    });

    return bestIndex;
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

//  useEffect(() => {
//    if (state.status === "ready" && canEdit) {
//      loadSlides().catch(() => {});
//   }
//  }, [canEdit, loadSlides, state.status]);

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

  useEffect(() => {
    const media = mediaRef.current;
    if (!media) {
      return;
    }

    if (editingIndex == null || editingIndex !== selectedIndex) {
      return;
    }

    if (selectedEnd == null || Number.isNaN(selectedEnd)) {
      return;
    }

    const boundary = selectedEnd;
    const clampToBoundary = () => {
      if (media.currentTime >= boundary) {
        media.pause();
        media.currentTime = boundary;
      }
    };

    const handleTimeUpdate = () => {
      clampToBoundary();
    };

    const handleSeeked = () => {
      clampToBoundary();
    };

    clampToBoundary();

    media.addEventListener("timeupdate", handleTimeUpdate);
    media.addEventListener("seeked", handleSeeked);

    return () => {
      media.removeEventListener("timeupdate", handleTimeUpdate);
      media.removeEventListener("seeked", handleSeeked);
    };
  }, [editingIndex, selectedEnd, selectedIndex]);

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
      const wasSelected = selectedIndex === index;
      setSelectedIndex(index);
      setEditingIndex((current) => {
        if (!canEdit || current == null) {
          return current;
        }
        return index;
      });

      if (wasSelected) {
        return;
      }

      const start = paragraphTimingData[index]?.start;
      if (typeof start === "number" && !Number.isNaN(start)) {
        seekMedia(start);
      }
    },
    [canEdit, paragraphTimingData, seekMedia, selectedIndex]
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
    setMetadataSaveError(null);
    setMetadataGenerateError(null);
    const { value } = event.target;
    setMetadataDraft((prev) => ({ ...prev, title: value }));
  }, []);

  const handleSummaryChange = useCallback((event: ReactChangeEvent<HTMLTextAreaElement>) => {
    setMetadataSaveError(null);
    setMetadataGenerateError(null);
    const { value } = event.target;
    setMetadataDraft((prev) => ({ ...prev, summary: value }));
  }, []);

  const handleKeypointsChange = useCallback((event: ReactChangeEvent<HTMLTextAreaElement>) => {
    setMetadataSaveError(null);
    setMetadataGenerateError(null);
    const { value } = event.target;
    setMetadataDraft((prev) => ({ ...prev, keypoints: value }));
  }, []);

  const handleVerseFieldChange = useCallback(
    (index: number, field: keyof SurmonBibleVerseDraft) =>
      (event: ReactChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const { value } = event.target;
        setMetadataSaveError(null);
        setMetadataGenerateError(null);
        setMetadataDraft((prev) => {
          const nextVerses = prev.coreBibleVerses.map((verse, verseIndex) =>
            verseIndex === index ? { ...verse, [field]: value } : verse
          );
          return { ...prev, coreBibleVerses: nextVerses };
        });
      },
    []
  );

  const handleAddVerse = useCallback(() => {
    setMetadataSaveError(null);
    setMetadataGenerateError(null);
    setMetadataDraft((prev) => ({
      ...prev,
      coreBibleVerses: [...prev.coreBibleVerses, { book: "", chapter_verse: "", text: "" }],
    }));
  }, []);

  const handleRemoveVerse = useCallback((index: number) => {
    setMetadataSaveError(null);
    setMetadataGenerateError(null);
    setMetadataDraft((prev) => {
      const nextVerses = prev.coreBibleVerses.filter((_, verseIndex) => verseIndex !== index);
      return { ...prev, coreBibleVerses: nextVerses };
    });
  }, []);

  const saveMetadata = useCallback(async () => {
    if (!canEdit || !resolvedUserEmail) {
      return;
    }
    const header = state.header;
    const fallbackTitle = header?.title ?? item;
    const sanitizedTitle = metadataDraft.title.trim();
    const titleToPersist = sanitizedTitle || fallbackTitle;
    if (!sanitizedTitle) {
      setMetadataDraft((prev) => ({ ...prev, title: fallbackTitle }));
    }

    const sanitizedSummary = metadataDraft.summary.trim();
    const sanitizedKeypoints = metadataDraft.keypoints.trim();
    const sanitizedVerses = normalizeVerseDrafts(metadataDraft.coreBibleVerses);

    const currentTitle = (header?.title ?? item).trim();
    const currentSummary = (header?.summary ?? "").trim();
    const currentKeypoints = (header?.keypoints ?? "").trim();
    const currentVerses = normalizeVerseDrafts(mapHeaderVersesToDrafts(header?.core_bible_verse));

    if (
      titleToPersist === currentTitle &&
      sanitizedSummary === currentSummary &&
      sanitizedKeypoints === currentKeypoints &&
      areVerseDraftsEqual(sanitizedVerses, currentVerses)
    ) {
      return;
    }

    setIsSavingMetadata(true);
    setMetadataSaveError(null);

    const payload: SurmonUpdateHeaderPayload = {
      user_id: resolvedUserEmail,
      item,
      title: titleToPersist,
      summary: sanitizedSummary,
      keypoints: sanitizedKeypoints,
      core_bible_verse: sanitizedVerses.length ? sanitizedVerses : [],
    };

    try {
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
          header: {
            ...prev.header,
            title: titleToPersist,
            summary: sanitizedSummary,
            keypoints: sanitizedKeypoints,
            core_bible_verse: sanitizedVerses,
          },
        };
      });
      setMetadataDraft({
        title: titleToPersist,
        summary: sanitizedSummary,
        keypoints: sanitizedKeypoints,
        coreBibleVerses: sanitizedVerses,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新講道資訊失敗";
      setMetadataSaveError(message);
      setMetadataDraft((prev) => ({ ...prev, title: fallbackTitle }));
    } finally {
      setIsSavingMetadata(false);
    }
  }, [canEdit, fetchJSON, item, metadataDraft, resolvedUserEmail, state.header]);

  const metadataHasChanges = useMemo(() => {
    const header = state.header;
    if (!header) {
      return false;
    }
    const fallbackTitle = header.title ?? item;
    const sanitizedTitle = metadataDraft.title.trim() || fallbackTitle;
    const sanitizedSummary = metadataDraft.summary.trim();
    const sanitizedKeypoints = metadataDraft.keypoints.trim();
    const sanitizedVerses = normalizeVerseDrafts(metadataDraft.coreBibleVerses);

    const currentTitle = (header.title ?? item).trim();
    const currentSummary = (header.summary ?? "").trim();
    const currentKeypoints = (header.keypoints ?? "").trim();
    const currentVerses = normalizeVerseDrafts(mapHeaderVersesToDrafts(header.core_bible_verse));

    if (sanitizedTitle !== currentTitle) {
      return true;
    }
    if (sanitizedSummary !== currentSummary) {
      return true;
    }
    if (sanitizedKeypoints !== currentKeypoints) {
      return true;
    }
    if (!areVerseDraftsEqual(sanitizedVerses, currentVerses)) {
      return true;
    }
    return false;
  }, [item, metadataDraft, state.header]);

  const handleTitleBlur = useCallback(() => {
    saveMetadata().catch(() => {});
  }, [saveMetadata]);

  const handleTitleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        saveMetadata().catch(() => {});
      } else if (event.key === "Escape") {
        const currentTitle = state.header?.title ?? item;
        setMetadataDraft((prev) => ({ ...prev, title: currentTitle }));
        setMetadataSaveError(null);
        event.currentTarget.blur();
      }
    },
    [item, saveMetadata, state.header?.title]
  );

  const handleMetadataSubmit = useCallback(() => {
    saveMetadata().catch(() => {});
  }, [saveMetadata]);

  const handleGenerateMetadata = useCallback(() => {
    if (!canEdit || !resolvedUserEmail) {
      return;
    }
    setActiveTab("metadata");
    setMetadataGenerateError(null);
    setIsGeneratingMetadata(true);

    const candidateParagraphs = state.paragraphs.filter(
      (paragraph) => (paragraph.type ?? "content") !== "comment"
    );
    const payload: SurmonGenerateMetadataPayload = {
      user_id: resolvedUserEmail,
      item,
      paragraphs: (candidateParagraphs.length > 0 ? candidateParagraphs : state.paragraphs).map((paragraph) => ({
        index: paragraph.index,
        text: paragraph.text,
        type: paragraph.type,
        start_time: paragraph.start_time,
        end_time: paragraph.end_time,
        start_timeline: paragraph.start_timeline,
        end_index: paragraph.end_index,
      })),
    };

    fetchJSON<SurmonGenerateMetadataResponse>(`${API_PREFIX}/generate_metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((response) => {
        const generatedVerses = mapHeaderVersesToDrafts(response.core_bible_verse);
        const nextSummary = (response.summary ?? "").trim();
        const nextKeypoints = (response.keypoints ?? "").trim();
        const nextTitle = response.title?.trim();
        setMetadataDraft((prev) => ({
          title: nextTitle || prev.title || state.header?.title || item,
          summary: nextSummary,
          keypoints: nextKeypoints,
          coreBibleVerses: generatedVerses,
        }));
        setMetadataSaveError(null);
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : "AI 生成失敗，請稍後再試。";
        setMetadataGenerateError(message);
      })
      .finally(() => {
        setIsGeneratingMetadata(false);
      });
  }, [canEdit, fetchJSON, item, resolvedUserEmail, state.header?.title, state.paragraphs]);

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

  const editorOptions = useMemo<SimpleMDEOptions>(
    () => ({
      autofocus: true,
      spellChecker: false,
      status: false,
      placeholder: "在此編輯講道內容...",
      toolbar: editorToolbar,
    }),
    [editorToolbar]
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
          <span className="text-lg text-gray-600">{metadataDraft.title}</span>
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
      {metadataSaveError && <p className="text-xs text-red-600">{metadataSaveError}</p>}



      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <aside className="flex flex-col gap-4 self-stretch min-h-0">
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
          <section className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm flex flex-col flex-1 min-h-0 max-h-[70vh] overflow-hidden">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-700">講道 AI 助理</h3>
              <span className="text-xs text-gray-400">Beta</span>
            </div>
            <div className="mt-3 flex-1 min-h-0 overflow-y-auto space-y-3 pr-1">
              {chatMessages.length === 0 ? (
                <p className="text-sm leading-relaxed text-gray-500">
                  歡迎提問講道相關問題，助理會根據讲道內容提供回答。
                </p>
              ) : (
                chatMessages.map((message) => (
                  <div
                    key={message.id}
                    className={`rounded-lg border px-3 py-2 text-sm leading-relaxed shadow-sm ${
                      message.role === "user"
                        ? "border-blue-200 bg-blue-50 text-blue-800"
                        : "border-gray-200 bg-gray-50 text-gray-700"
                    }`}
                  >
                    {message.role === "assistant" ? (
                      <ReactMarkdown
                        remarkPlugins={assistantRemarkPlugins}
                        components={assistantMarkdownComponents}
                        className="prose prose-sm max-w-none text-gray-700 [&>*:last-child]:mb-0"
                      >
                        {message.content}
                      </ReactMarkdown>
                    ) : (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    )}
                    {message.quotes?.length ? (
                      <ul className="mt-2 space-y-1 border-l border-gray-200 pl-3 text-xs text-gray-500">
                        {message.quotes.map((quote) => (
                          <li key={`${message.id}-${quote.Id}`}>
                            <span className="font-semibold text-gray-600">[{quote.Index}]</span>{" "}
                            {quote.Title ?? "相關引文"}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))
              )}
              {isChatLoading ? (
                <div className="flex items-center gap-2 text-sm text-blue-600">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  助理正在整理回應...
                </div>
              ) : null}
              <div ref={chatEndRef} />
            </div>
            <form onSubmit={handleChatSubmit} className="mt-3 space-y-2">
              <textarea
                value={chatInput}
                onChange={handleChatInputChange}
                disabled={isChatLoading}
                rows={3}
                className="w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm shadow-inner focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                placeholder="輸入想詢問的內容，例如：這段講道的重點是什麼？"
              />
              <div className="flex items-center justify-between">
                {chatError ? (
                  <p className="text-xs text-red-600">{chatError}</p>
                ) : (
                  <span className="text-xs text-gray-400">Shift + Enter 換行</span>
                )}
                <button
                  type="submit"
                  disabled={isChatLoading || chatInput.trim() === ""}
                  className="inline-flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-blue-700 disabled:bg-blue-300"
                >
                  發送
                </button>
              </div>
            </form>
          </section>
        </aside>

        <section className="xl:col-span-2 space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setActiveTab("script")}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md transition ${
                    activeTab === "script"
                      ? "border border-blue-200 bg-blue-100 text-blue-700"
                      : "border border-transparent text-gray-600 hover:text-blue-700"
                  }`}
                >
                  講道稿內容
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("metadata")}
                  className={`px-3 py-1.5 text-sm font-medium rounded-md transition ${
                    activeTab === "metadata"
                      ? "border border-blue-200 bg-blue-100 text-blue-700"
                      : "border border-transparent text-gray-600 hover:text-blue-700"
                  }`}
                >
                  标题和简介
                </button>
              </div>
              {activeTab === "script" && selectedParagraph && (
                <button
                  onClick={() => handleMarkBookmark(selectedParagraph)}
                  className="inline-flex items-center text-xs px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100"
                >
                  <BookmarkPlus className="w-3 h-3 mr-1" /> 設為書籤
                </button>
              )}
            </div>

            {activeTab === "script" ? (
              <div className="max-h-[70vh] overflow-y-auto">
                <div className="divide-y divide-gray-100">
                  {state.paragraphs.map((paragraph, index) => {
                    const isEditing = canEdit && editingIndex === index;
                    const isSelected = index === selectedIndex;
                    const isHighlighted = highlightedParagraphs.has(index);
                    const isBookmarked = paragraph.index === bookmarkIndex;
                    return (
                      <div
                        id={`surmon-paragraph-${index}`}
                        key={`${paragraph.index}-${index}`}
                        className={`group px-4 py-3 transition-colors ${
                          isSelected
                            ? "bg-blue-50"
                            : isHighlighted
                            ? "bg-amber-50"
                            : "hover:bg-gray-50"
                        } ${isEditing ? "border-l-2 border-blue-300" : ""} ${
                          isHighlighted ? "ring-1 ring-amber-300" : ""
                        } ${isBookmarked ? "border border-amber-200" : ""}`}
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
                                  options={editorOptions}
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
            ) : (
              <div className="max-h-[70vh] overflow-y-auto px-4 py-4">
                <div className="space-y-5">
                  {metadataGenerateError && (
                    <p className="text-xs text-red-600">{metadataGenerateError}</p>
                  )}
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">講道標題</label>
                    <input
                      type="text"
                      value={metadataDraft.title}
                      onChange={handleTitleChange}
                      onBlur={handleTitleBlur}
                      onKeyDown={handleTitleKeyDown}
                      disabled={!canEdit}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                      placeholder="輸入講道標題"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">講道摘要</label>
                    <textarea
                      value={metadataDraft.summary}
                      onChange={handleSummaryChange}
                      rows={4}
                      disabled={!canEdit}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm resize-y focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                      placeholder="輸入講道摘要或簡介"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">要點整理</label>
                    <textarea
                      value={metadataDraft.keypoints}
                      onChange={handleKeypointsChange}
                      rows={6}
                      disabled={!canEdit}
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm resize-y focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                      placeholder="輸入要點條列或重點整理"
                    />
                  </div>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <label className="block text-sm font-semibold text-gray-700">核心經文</label>
                      {canEdit && (
                        <button
                          type="button"
                          onClick={handleAddVerse}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800"
                        >
                          新增經文
                        </button>
                      )}
                    </div>
                    {metadataDraft.coreBibleVerses.length === 0 ? (
                      <p className="text-xs text-gray-500">目前尚未設定核心經文。</p>
                    ) : (
                      metadataDraft.coreBibleVerses.map((verse, index) => (
                        <div
                          key={`core-verse-${index}`}
                          className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-3"
                        >
                          <div className="flex flex-col gap-3 md:flex-row">
                            <div className="flex-1">
                              <label className="block text-xs font-medium text-gray-600 mb-1">書卷</label>
                              <input
                                type="text"
                                value={verse.book}
                                onChange={handleVerseFieldChange(index, "book")}
                                disabled={!canEdit}
                                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                                placeholder="如：創世記"
                              />
                            </div>
                            <div className="flex-1">
                              <label className="block text-xs font-medium text-gray-600 mb-1">章節</label>
                              <input
                                type="text"
                                value={verse.chapter_verse}
                                onChange={handleVerseFieldChange(index, "chapter_verse")}
                                disabled={!canEdit}
                                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                                placeholder="例如：3:16-18"
                              />
                            </div>
                            {canEdit && (
                              <button
                                type="button"
                                onClick={() => handleRemoveVerse(index)}
                                className="self-start rounded-md border border-red-200 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                              >
                                移除
                              </button>
                            )}
                          </div>
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">經文內容</label>
                            <textarea
                              value={verse.text}
                              onChange={handleVerseFieldChange(index, "text")}
                              disabled={!canEdit}
                              rows={3}
                              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm resize-y focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                              placeholder="輸入經文內容"
                            />
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                  {metadataSaveError && (
                    <p className="text-sm text-red-600">{metadataSaveError}</p>
                  )}
                  <div className="flex items-center justify-end gap-2 pt-2">
                    <button
                      type="button"
                      onClick={handleGenerateMetadata}
                      disabled={!canEdit || isGeneratingMetadata || state.paragraphs.length === 0}
                      className="inline-flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 transition hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-100 disabled:bg-blue-50 disabled:text-blue-300"
                    >
                      {isGeneratingMetadata ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 產生中...
                        </>
                      ) : (
                        <>
                          <Sparkles className="h-3.5 w-3.5" /> AI 生成
                        </>
                      )}
                    </button>
                    {metadataHasChanges && !isSavingMetadata ? (
                      <span className="text-xs text-gray-500">尚有未儲存的變更</span>
                    ) : null}
                    <button
                      type="button"
                      onClick={handleMetadataSubmit}
                      disabled={!canEdit || !metadataHasChanges || isSavingMetadata}
                      className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:bg-blue-300"
                    >
                      {isSavingMetadata ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" /> 儲存中...
                        </>
                      ) : (
                        <>
                          <Save className="h-4 w-4" /> 儲存標題與簡介
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
    <SlidePickerModal
      open={openSlidePickerIndex != null}
      slides={slides}
      status={slidesStatus}
      error={slidesError}
      onSelect={(slide) => {
        if (openSlidePickerIndex != null) {
          handleInsertSlide(openSlidePickerIndex, slide);
        }
      }}
      onRetry={handleSlideRetry}
      onClose={() => setOpenSlidePickerIndex(null)}
      activeIndex={closestSlideIndex}
      formatTimestamp={formatTimestamp}
    />
    </>
  );
};
