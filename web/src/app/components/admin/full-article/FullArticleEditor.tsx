"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import {
  regenerateFullArticle,
  saveFullArticle,
  updatePrompt,
} from "@/app/admin/full_article/api";
import { FullArticleDetail, FullArticleStatus } from "@/app/types/full-article";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/components/ui/tabs";

import "easymde/dist/easymde.min.css";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
  ssr: false,
});

interface FullArticleEditorProps {
  initialArticle: FullArticleDetail;
}

type Feedback = { type: "success" | "error"; message: string } | null;

const STATUS_LABEL: Record<FullArticleStatus, string> = {
  draft: "草稿",
  generated: "已產生",
  final: "已定稿",
};

export function FullArticleEditor({ initialArticle }: FullArticleEditorProps) {
  const router = useRouter();
  const [articleId, setArticleId] = useState(initialArticle.id);
  const [name, setName] = useState(initialArticle.name ?? "");
  const [status, setStatus] = useState<FullArticleStatus>(initialArticle.status ?? "draft");
  const [scriptMarkdown, setScriptMarkdown] = useState(initialArticle.scriptMarkdown ?? "");
  const [articleMarkdown, setArticleMarkdown] = useState(initialArticle.articleMarkdown ?? "");
  const [promptMarkdown, setPromptMarkdown] = useState(initialArticle.promptMarkdown ?? "");
  const [promptDirty, setPromptDirty] = useState(false);

  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [updatingPrompt, setUpdatingPrompt] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [activeTab, setActiveTab] = useState("article");

  const scriptEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "360px",
    }),
    [],
  );

  const promptEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "240px",
    }),
    [],
  );

  const articleEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "480px",
    }),
    [],
  );

  const handlePromptChange = useCallback((value: string) => {
    setPromptMarkdown((prev) => {
      if (prev === value) {
        return prev;
      }
      setPromptDirty(true);
      return value;
    });
  }, []);

  const resetFeedback = () => setFeedback(null);

  const handleSave = async () => {
    resetFeedback();
    setSaving(true);
    try {
      const payload = {
        id: articleId || undefined,
        name,
        status,
        scriptMarkdown,
        articleMarkdown,
        promptMarkdown: promptDirty ? promptMarkdown : undefined,
      };
      const saved = await saveFullArticle(payload);
      setArticleId(saved.id);
      setName(saved.name);
      setStatus(saved.status);
      setScriptMarkdown(saved.scriptMarkdown);
      setArticleMarkdown(saved.articleMarkdown);
      setPromptMarkdown(saved.promptMarkdown);
      setPromptDirty(false);
      setFeedback({ type: "success", message: "已儲存文章內容" });
      if (!articleId && saved.id) {
        router.replace(`/admin/full_article/${saved.id}`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "儲存失敗";
      setFeedback({ type: "error", message });
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    resetFeedback();
    if (!articleId) {
      setFeedback({ type: "error", message: "請先儲存文章以取得 ID" });
      return;
    }
    setGenerating(true);
    try {
      const result = await regenerateFullArticle(articleId, scriptMarkdown, promptDirty ? promptMarkdown : undefined);
      setArticleMarkdown(result.articleMarkdown);
      setStatus(result.status);
      setFeedback({ type: "success", message: "已透過 Gemini 產生文章內容" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成失敗";
      setFeedback({ type: "error", message });
    } finally {
      setGenerating(false);
    }
  };

  const handleUpdatePrompt = async () => {
    resetFeedback();
    setUpdatingPrompt(true);
    try {
      const savedPrompt = await updatePrompt(promptMarkdown);
      setPromptMarkdown(savedPrompt);
      setPromptDirty(false);
      setFeedback({ type: "success", message: "已更新共享提示" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新提示失敗";
      setFeedback({ type: "error", message });
    } finally {
      setUpdatingPrompt(false);
    }
  };

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold text-gray-900">{articleId ? "編輯全文文章" : "建立全文文章"}</h1>
        <p className="text-gray-600">調整講稿、提示，並維護最終產出的 Markdown 文章。</p>
      </header>

      {feedback && (
        <div
          className={`px-4 py-3 rounded-md border ${
            feedback.type === "success"
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-red-200 bg-red-50 text-red-700"
          }`}
        >
          {feedback.message}
        </div>
      )}

      <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
        <div className="grid gap-4 md:grid-cols-[1fr,200px]">
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">文章標題</span>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="輸入文章名稱"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">狀態</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value as FullArticleStatus)}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {(Object.keys(STATUS_LABEL) as FullArticleStatus[]).map((value) => (
                <option key={value} value={value}>
                  {STATUS_LABEL[value]}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center px-4 py-2 rounded-md bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? "儲存中..." : "儲存"}
          </button>
        </div>
      </section>

      <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="mb-4">
            <TabsTrigger value="script">講稿</TabsTrigger>
            <TabsTrigger value="article">文章</TabsTrigger>
            <TabsTrigger value="prompt">Prompt</TabsTrigger>
          </TabsList>

          <TabsContent value="script">
            <header className="mb-3">
              <h2 className="text-xl font-semibold text-gray-900">講稿</h2>
              <p className="text-gray-600 text-sm mt-1">提供給AI的原始講稿內容。</p>
            </header>
            <SimpleMDE
              key="script-editor"
              value={scriptMarkdown}
              onChange={(value) => setScriptMarkdown(value)}
              options={scriptEditorOptions}
            />
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={handleGenerate}
                disabled={generating}
                className="inline-flex items-center px-4 py-2 rounded-md bg-purple-600 text-white font-medium hover:bg-purple-700 disabled:opacity-60"
              >
                {generating ? "生成中..." : "生成文章"}
              </button>
            </div>
          </TabsContent>

          <TabsContent value="article">
            <header className="mb-3">
              <h2 className="text-xl font-semibold text-gray-900">文章</h2>
              <p className="text-gray-600 text-sm mt-1">AI生成的文章，可於此處進一步人工编辑。</p>
            </header>
            <SimpleMDE
              key="article-editor"
              value={articleMarkdown}
              onChange={(value) => setArticleMarkdown(value)}
              options={articleEditorOptions}
            />
          </TabsContent>

          <TabsContent value="prompt">
            <header className="mb-3">
              <h2 className="text-xl font-semibold text-gray-900">Prompt</h2>
              <p className="text-gray-600 text-sm mt-1">
                系統所有文章共用此Prompt。更新後將影響後續生成的文章。
              </p>
            </header>
            <SimpleMDE
              key="prompt-editor"
              value={promptMarkdown}
              onChange={handlePromptChange}
              options={promptEditorOptions}
            />
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                onClick={handleUpdatePrompt}
                disabled={updatingPrompt || !promptDirty}
                className="inline-flex items-center px-4 py-2 rounded-md bg-emerald-600 text-white font-medium hover:bg-emerald-700 disabled:opacity-60"
              >
                {updatingPrompt ? "更新中..." : "更新prompt"}
              </button>
            </div>
          </TabsContent>
        </Tabs>
      </section>
    </div>
  );
}
