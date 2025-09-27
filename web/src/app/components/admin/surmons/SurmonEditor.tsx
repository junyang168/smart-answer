"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "easymde/dist/easymde.min.css";
import { useSession, signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BookmarkPlus,
  Loader2,
  Lock,
  MessageSquare,
  PlusCircle,
  RotateCcw,
  Save,
  UserPlus,
  Video,
} from "lucide-react";

import {
  SurmonAssignPayload,
  SurmonPermissions,
  SurmonScriptParagraph,
  SurmonScriptResponse,
  SurmonUpdateScriptPayload,
} from "@/app/types/surmon-editor";
import { CopilotChat } from "@/app/components/copilot";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), { ssr: false });
const SAVE_DELAY = process.env.NODE_ENV === "development" ? 3000 : 10000;
const API_PREFIX = "/sc_api";
const MEDIA_PREFIX = "/web/video";

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

export const SurmonEditor = ({ item, viewChanges }: SurmonEditorProps) => {
  const { data: session, status: authStatus } = useSession();
  const router = useRouter();

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [permissions, setPermissions] = useState<SurmonPermissions | null>(null);
  const [state, setState] = useState<SurmonEditorState>({ status: "idle", paragraphs: [] });
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [bookmarkIndex, setBookmarkIndex] = useState<string | null>(null);

  const saveTimerRef = useRef<NodeJS.Timeout | null>(null);

  const userEmail = useMemo(() => {
    if (session?.user?.email) {
      return session.user.email;
    }
    if (process.env.NODE_ENV !== "production") {
      return FALLBACK_USER_ID;
    }
    return null;
  }, [session?.user?.email]);

  const canEdit = Boolean(permissions?.canWrite && !viewChanges);

  const selectedParagraph = useMemo(() => {
    if (selectedIndex == null) return null;
    return state.paragraphs[selectedIndex] ?? null;
  }, [selectedIndex, state.paragraphs]);

  const mediaSource = useMemo(() => {
    if (!state.header) return null;
    const isAudio = state.header.type === "audio";
    const extension = isAudio ? "mp3" : "mp4";
    return `${MEDIA_PREFIX}/${encodeURIComponent(item)}.${extension}`;
  }, [state.header, item]);

  const fetchJSON = useCallback(async <T,>(url: string, init?: RequestInit) => {
    const response = await fetch(url, init);
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`${response.status} ${response.statusText}${text ? `: ${text}` : ""}`);
    }
    return (await response.json()) as T;
  }, []);

  const refreshPermissions = useCallback(async () => {
    if (!userEmail) return;
    const data = await fetchJSON<SurmonPermissions>(
      `${API_PREFIX}/permissions/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}`
    );
    setPermissions(data);
  }, [fetchJSON, item, userEmail]);

  const refreshBookmark = useCallback(async () => {
    if (!userEmail) return;
    try {
      const data = await fetchJSON<{ index: string }>(
        `${API_PREFIX}/bookmark/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}`
      );
      setBookmarkIndex(data.index);
    } catch (error) {
      // Optional feature; ignore errors silently
    }
  }, [fetchJSON, item, userEmail]);

  const loadData = useCallback(async () => {
    if (!userEmail) return;
    setState({ status: "loading", paragraphs: [] });

    try {
      const [userInfo, perms, script] = await Promise.all([
        fetchJSON<UserProfile>(`${API_PREFIX}/user/${encodeURIComponent(userEmail)}`),
        fetchJSON<SurmonPermissions>(
          `${API_PREFIX}/permissions/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}`
        ),
        fetchJSON<SurmonScriptResponse>(
          `${API_PREFIX}/sermon/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}/${
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
      setSelectedIndex(paragraphs.length > 0 ? 0 : null);
      refreshBookmark();
    } catch (error) {
      const message = error instanceof Error ? error.message : "未知錯誤";
      setState({ status: "error", error: message, paragraphs: [] });
    }
  }, [fetchJSON, item, refreshBookmark, userEmail, viewChanges]);

  useEffect(() => {
    if (!userEmail || authStatus === "loading") {
      return;
    }
    if (authStatus === "unauthenticated" && process.env.NODE_ENV === "production") {
      setState({ status: "error", paragraphs: [], error: "請登入後再進入編輯頁面。" });
      return;
    }
    loadData();
  }, [authStatus, loadData, userEmail]);

  const requestSave = useCallback(
    (paragraphs: SurmonScriptParagraph[]) => {
      if (!canEdit || !userEmail) {
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
            user_id: userEmail,
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
    [canEdit, fetchJSON, item, userEmail]
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

  const handleEditorChange = useCallback(
    (value: string) => {
      if (selectedIndex == null) return;
      updateParagraph(selectedIndex, (paragraph) => ({ ...paragraph, text: value }));
    },
    [selectedIndex, updateParagraph]
  );

  const handleSelect = useCallback((index: number) => {
    setSelectedIndex(index);
  }, []);

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
          user_id: userEmail ?? undefined,
          user_name: profile.name,
        };
        next.splice(insertionIndex, 0, newComment);
        const sequenced = ensureSequence(next);
        requestSave(sequenced);
        return { ...prev, paragraphs: sequenced };
      });
      setSelectedIndex(afterIndex + 1);
    },
    [canEdit, profile?.name, requestSave, userEmail]
  );

  const handleAssignToggle = useCallback(async () => {
    if (!permissions || !userEmail) return;
    const action: SurmonAssignPayload["action"] = permissions.canAssign ? "assign" : "unassign";
    try {
      const payload: SurmonAssignPayload = {
        user_id: userEmail,
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
  }, [fetchJSON, item, loadData, permissions, refreshPermissions, userEmail]);

  const handlePublish = useCallback(async () => {
    if (!userEmail) return;
    try {
      await fetchJSON(
        `${API_PREFIX}/publish/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}`,
        { method: "PUT" }
      );
      await loadData();
    } catch (error) {
      const message = error instanceof Error ? error.message : "發布失敗";
      window.alert(`發布失敗：${message}`);
    }
  }, [fetchJSON, item, loadData, userEmail]);

  const handleMarkBookmark = useCallback(
    async (paragraph: SurmonScriptParagraph) => {
      if (!userEmail || !paragraph.index) return;
      try {
        await fetchJSON(
          `${API_PREFIX}/bookmark/${encodeURIComponent(userEmail)}/${encodeURIComponent(item)}/${encodeURIComponent(paragraph.index)}`,
          { method: "PUT" }
        );
        setBookmarkIndex(paragraph.index);
      } catch (error) {
        // ignore bookmark failures
      }
    },
    [fetchJSON, item, userEmail]
  );

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
      }
    };
  }, []);

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

  if (!userEmail && authStatus !== "loading") {
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
    <div className="space-y-6">
      <nav className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => router.push("/admin/surmons")}
          className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-md hover:bg-gray-100"
        >
          <ArrowLeft className="w-4 h-4 mr-2" /> 返回列表
        </button>
        <div className="flex-1" />
        <button
          onClick={() => router.push(`/admin/surmons/${encodeURIComponent(item)}?view=${viewChanges ? "draft" : "changes"}`)}
          className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-md hover:bg-gray-100"
        >
          <RotateCcw className="w-4 h-4 mr-2" /> {viewChanges ? "返回編輯" : "查看差異"}
        </button>
        {permissions?.canAssign || permissions?.canUnassign ? (
          <button
            onClick={handleAssignToggle}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-blue-600 bg-blue-50 border border-blue-200 rounded-md hover:bg-blue-100"
          >
            <UserPlus className="w-4 h-4 mr-2" /> {permissions.canAssign ? "認領" : "取消認領"}
          </button>
        ) : null}
        {permissions?.canPublish ? (
          <button
            onClick={handlePublish}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-green-700 bg-green-100 border border-green-200 rounded-md hover:bg-green-200"
          >
            <Save className="w-4 h-4 mr-2" /> 發布完成版本
          </button>
        ) : null}
        {permissions?.canViewPublished ? (
          <a
            href={`/article?i=${encodeURIComponent(item)}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-md hover:bg-gray-100"
          >
            <Video className="w-4 h-4 mr-2" /> 查看完成版本
          </a>
        ) : null}
      </nav>

      <header className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-gray-900">{header?.title ?? item}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-4 text-sm text-gray-600">
          {header?.deliver_date && <span>日期：{header.deliver_date}</span>}
          {header?.speaker && <span>講員：{header.speaker}</span>}
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

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <aside className="space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            {header?.type === "audio" ? (
              <audio controls className="w-full">
                <source src={mediaSource ?? ""} />
                您的瀏覽器不支援 audio 元素。
              </audio>
            ) : (
              <video controls className="w-full">
                <source src={mediaSource ?? ""} />
                您的瀏覽器不支援 video 元素。
              </video>
            )}
          </div>
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h2 className="text-sm font-semibold text-gray-700">AI 助理</h2>
              <MessageSquare className="w-4 h-4 text-gray-400" />
            </div>
            <div className="p-4 space-y-3 text-sm text-gray-600">
              <p>點擊畫面右側的「Chat with AI」按鈕，即可向 AI 助教提問或生成摘要。</p>
              <CopilotChat item_id={item} />
            </div>
          </div>
        </aside>

        <section className="xl:col-span-2 space-y-4">
          <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
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

            <div className="max-h-[540px] overflow-y-auto divide-y divide-gray-100">
              {state.paragraphs.map((paragraph, index) => {
                const active = index === selectedIndex;
                const isComment = paragraph.type === "comment";
                const showAddComment = canEdit && !isComment;
                const isBookmarked = paragraph.index === bookmarkIndex;
                return (
                  <div
                    key={`${paragraph.index}-${index}`}
                    className={`group px-4 py-3 cursor-pointer transition-colors ${
                      active ? "bg-blue-50" : "hover:bg-gray-50"
                    }`}
                    onClick={() => handleSelect(index)}
                  >
                    <div className="flex items-start gap-3">
                      <div className="pt-1 text-xs font-semibold text-gray-400 w-16">
                        {paragraph.start_timeline ?? "--:--"}
                      </div>
                      <div className="flex-1">
                        {renderParagraphContent(paragraph)}
                        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-gray-400">
                          <span>{isComment ? "評論" : "稿件"}</span>
                          {isBookmarked && <span className="text-amber-600">★ 書籤</span>}
                          {paragraph.user_name && <span>由 {paragraph.user_name}</span>}
                          {showAddComment && (
                            <button
                              onClick={(event) => {
                                event.stopPropagation();
                                handleAddComment(index);
                              }}
                              className="inline-flex items-center text-blue-600 hover:text-blue-700"
                            >
                              <PlusCircle className="w-3 h-3 mr-1" /> 新增評論
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {selectedParagraph ? (
            canEdit ? (
              <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-4">
                <SimpleMDE
                  key={selectedIndex ?? undefined}
                  value={selectedParagraph.text}
                  onChange={handleEditorChange}
                  options={{
                    autofocus: true,
                    spellChecker: false,
                    status: false,
                    placeholder: "在此編輯講道內容...",
                  }}
                />
              </div>
            ) : (
              <div className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 flex items-center justify-center text-gray-500 gap-2">
                <Lock className="w-4 h-4" />
                此視圖為唯讀模式，或您目前沒有編輯權限。
              </div>
            )
          ) : null}
        </section>
      </div>
    </div>
  );
};
