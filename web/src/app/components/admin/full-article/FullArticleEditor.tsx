"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import {
  regenerateFullArticle,
  regenerateSummary,
  saveFullArticle,
  updatePrompt,
} from "@/app/admin/full_article/api";
import { FullArticleDetail, FullArticleStatus, FullArticleType } from "@/app/types/full-article";
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

const ARTICLE_TYPES: FullArticleType[] = ["釋經", "神學觀點", "短文"];

export function FullArticleEditor({ initialArticle }: FullArticleEditorProps) {
  const router = useRouter();
  const [articleId, setArticleId] = useState(initialArticle.id);
  const [name, setName] = useState(initialArticle.name ?? "");
  const [subtitle, setSubtitle] = useState(initialArticle.subtitle ?? "");
  const [status, setStatus] = useState<FullArticleStatus>(initialArticle.status ?? "draft");
  const [scriptMarkdown, setScriptMarkdown] = useState(initialArticle.scriptMarkdown ?? "");
  const [articleMarkdown, setArticleMarkdown] = useState(initialArticle.articleMarkdown ?? "");
  const [promptMarkdown, setPromptMarkdown] = useState(initialArticle.promptMarkdown ?? "");
  const [promptDirty, setPromptDirty] = useState(false);
  const [summaryMarkdown, setSummaryMarkdown] = useState(initialArticle.summaryMarkdown ?? "");
  const [articleType, setArticleType] = useState<FullArticleType | "">(initialArticle.articleType ?? "");
  const [coreBibleVersesInput, setCoreBibleVersesInput] = useState(
    (initialArticle.coreBibleVerses ?? []).join("\n"),
  );
  const [sourceSermonIdsInput, setSourceSermonIdsInput] = useState(
    (initialArticle.sourceSermonIds ?? []).join("\n"),
  );

  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingSummary, setGeneratingSummary] = useState(false);
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

  const summaryEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "240px",
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
      const coreBibleVerses = coreBibleVersesInput
        .split(/\r?\n/)
        .map((verse) => verse.trim())
        .filter((verse) => verse.length > 0);
      const sourceSermonIds = sourceSermonIdsInput
        .split(/\r?\n/)
        .map((id) => id.trim())
        .filter((id) => id.length > 0);
      const payload = {
        id: articleId || undefined,
        name,
        subtitle,
        status,
        scriptMarkdown,
        articleMarkdown,
        promptMarkdown: promptDirty ? promptMarkdown : undefined,
        summaryMarkdown,
        articleType: articleType || undefined,
        coreBibleVerses,
        sourceSermonIds,
      };
      const saved = await saveFullArticle(payload);
      setArticleId(saved.id);
      setName(saved.name);
      setSubtitle(saved.subtitle ?? "");
      setStatus(saved.status);
      setScriptMarkdown(saved.scriptMarkdown);
      setArticleMarkdown(saved.articleMarkdown);
      setPromptMarkdown(saved.promptMarkdown);
      setSummaryMarkdown(saved.summaryMarkdown ?? "");
      setArticleType(saved.articleType ?? "");
      setCoreBibleVersesInput((saved.coreBibleVerses ?? []).join("\n"));
      setSourceSermonIdsInput((saved.sourceSermonIds ?? []).join("\n"));
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

  const handleGenerateSummary = async () => {
    resetFeedback();
    if (!articleId) {
      setFeedback({ type: "error", message: "請先儲存文章以取得 ID" });
      return;
    }
    setGeneratingSummary(true);
    try {
      const result = await regenerateSummary(articleId);
      setSummaryMarkdown(result.summaryMarkdown);
      setFeedback({ type: "success", message: "已透過 Gemini 產生摘要" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成摘要失敗";
      setFeedback({ type: "error", message });
    } finally {
      setGeneratingSummary(false);
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
        <h1 className="text-3xl font-bold text-gray-900">{articleId ? "编辑讲稿和文章" : "从讲稿生成文章"}</h1>
        <p className="text-gray-600">講稿、提示，並維護最終生成文章。</p>
        <Link
          href="/admin/full_article"
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          ← 回到文章列表
        </Link>
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
        <div className="flex justify-end">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center px-4 py-2 rounded-md bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? "儲存中..." : "儲存"}
          </button>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
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
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">副標題</span>
            <input
              type="text"
              value={subtitle}
              onChange={(event) => setSubtitle(event.target.value)}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="輸入副標題（選填）"
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
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">文章類型</span>
            <select
              value={articleType}
              onChange={(event) => setArticleType(event.target.value as FullArticleType | "")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">未指定</option>
              {ARTICLE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">核心經文 (每行一條)</span>
            <textarea
              value={coreBibleVersesInput}
              onChange={(event) => setCoreBibleVersesInput(event.target.value)}
              rows={4}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="例如：\n約翰福音 3:16"
            />
          </label>
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">來源講道 ID（每行一個）</span>
            <textarea
              value={sourceSermonIdsInput}
              onChange={(event) => setSourceSermonIdsInput(event.target.value)}
              rows={3}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="例如：\nsermon-2024-05-12"
            />
          </label>
        </div>

        <div className="space-y-3">
          <span className="text-sm font-medium text-gray-700">摘要</span>
          <SimpleMDE
            key="summary-editor"
            value={summaryMarkdown}
            onChange={(value) => setSummaryMarkdown(value)}
            options={summaryEditorOptions}
          />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleGenerateSummary}
              disabled={generatingSummary || !articleId}
              className="inline-flex items-center px-3 py-1.5 rounded-md bg-purple-600 text-white text-sm font-medium hover:bg-purple-700 disabled:opacity-60"
            >
              {generatingSummary ? "生成中..." : "生成摘要"}
            </button>
          </div>
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
