"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  regenerateFullArticle,
  regenerateSummary,
  saveFullArticle,
  updatePrompt,
} from "@/app/admin/full_article/api";
import { FullArticleDetail, FullArticleStatus, FullArticleType } from "@/app/types/full-article";
import { ArrowDown, ArrowUp, Plus, Trash2, LayoutList, FileText, Settings, FileText as FileIcon, AlignLeft, ScrollText, MessageSquare } from "lucide-react";
import ReactDOMServer from "react-dom/server";
import { ScriptureMarkdown } from "@/app/components/full-article/ScriptureMarkdown";

import "easymde/dist/easymde.min.css";

const SimpleMDE = dynamic(() => import("react-simplemde-editor"), {
  ssr: false,
});

interface Section {
  id: string;
  title: string;
  content: string;
}

function generateId() {
  return Math.random().toString(36).substring(2, 9);
}

function parseSections(markdown: string): Section[] {
  const lines = markdown.split("\n");
  const sections: Section[] = [];
  let currentTitle = "";
  let currentContent: string[] = [];
  let isFirst = true;

  // Handle case where file doesn't start with ##
  if (lines.length > 0 && !lines[0].startsWith("## ")) {
    // It's the intro/preamble
    // We will treat it as a section with empty title
  }

  lines.forEach((line) => {
    if (line.startsWith("## ")) {
      if (!isFirst || currentContent.length > 0 || currentTitle) {
        sections.push({
          id: generateId(),
          title: currentTitle,
          content: currentContent.join("\n").trim(),
        });
      }
      currentTitle = line.substring(3).trim();
      currentContent = [];
      isFirst = false;
    } else {
      currentContent.push(line);
    }
  });

  // Push the last section
  if (currentContent.length > 0 || currentTitle) {
    sections.push({
      id: generateId(),
      title: currentTitle,
      content: currentContent.join("\n").trim(),
    });
  }

  // If empty, ensure at least one section
  if (sections.length === 0) {
    sections.push({ id: generateId(), title: "", content: "" });
  }

  return sections;
}

function assembleSections(sections: Section[]): string {
  return sections
    .map((section) => {
      const titlePart = section.title ? `## ${section.title}\n` : "";
      return `${titlePart}${section.content}`;
    })
    .join("\n\n");
}

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

type SidebarItem = "metadata" | "summary" | "script" | "prompt" | string; // string is section ID

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

  // 2-Column Layout State
  const [activeSidebarItem, setActiveSidebarItem] = useState<SidebarItem>("metadata");
  const [sections, setSections] = useState<Section[]>([]);

  // Initialize sections from articleMarkdown on load
  useEffect(() => {
    if (initialArticle.articleMarkdown) {
      setSections(parseSections(initialArticle.articleMarkdown));
    }
  }, [initialArticle.articleMarkdown]);

  const handleSectionChange = (id: string, field: "title" | "content", value: string) => {
    setSections((prev) =>
      prev.map((section) =>
        section.id === id ? { ...section, [field]: value } : section
      )
    );
  };

  const handleAddSection = (index: number) => {
    const newSection: Section = { id: generateId(), title: "New Section", content: "" };
    setSections((prev) => {
      const newSections = [...prev];
      newSections.splice(index + 1, 0, newSection);
      return newSections;
    });
    setActiveSidebarItem(newSection.id);
  };

  const handleDeleteSection = (index: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm("Are you sure you want to delete this section?")) {
      const sectionIdToDelete = sections[index].id;
      setSections((prev) => prev.filter((_, i) => i !== index));
      if (activeSidebarItem === sectionIdToDelete) {
        setActiveSidebarItem("metadata");
      }
    }
  };

  const handleMoveSection = (index: number, direction: "up" | "down", e: React.MouseEvent) => {
    e.stopPropagation();
    setSections((prev) => {
      const newSections = [...prev];
      if (direction === "up" && index > 0) {
        [newSections[index], newSections[index - 1]] = [newSections[index - 1], newSections[index]];
      } else if (direction === "down" && index < newSections.length - 1) {
        [newSections[index], newSections[index + 1]] = [newSections[index + 1], newSections[index]];
      }
      return newSections;
    });
  };

  // Sync sections to markdown when saving
  const getCurrentMarkdown = () => {
    return assembleSections(sections);
  };

  const scriptEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "calc(100vh - 250px)",
    }),
    [],
  );

  const promptEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "calc(100vh - 250px)",
    }),
    [],
  );

  const articleEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "calc(100vh - 250px)",
      previewRender: (plainText: string) => {
        return ReactDOMServer.renderToStaticMarkup(<ScriptureMarkdown markdown={plainText} />);
      },
    }),
    [],
  );

  const summaryEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "calc(100vh - 250px)",
    }),
    [],
  );

  const sectionEditorOptions = useMemo(
    () => ({
      spellChecker: false,
      status: false,
      minHeight: "300px",
      maxHeight: "calc(100vh - 350px)",
      previewRender: (plainText: string) => {
        return ReactDOMServer.renderToStaticMarkup(<ScriptureMarkdown markdown={plainText} />);
      },
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

      const currentArticleMarkdown = getCurrentMarkdown();

      const payload = {
        id: articleId || undefined,
        name,
        subtitle,
        status,
        scriptMarkdown,
        articleMarkdown: currentArticleMarkdown,
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
      // Update sections from saved markdown to ensure sync
      const newSections = parseSections(saved.articleMarkdown);
      setSections(newSections);

      // Restore active section if possible
      if (activeSidebarItem !== "metadata" && activeSidebarItem !== "summary" && activeSidebarItem !== "script" && activeSidebarItem !== "prompt") {
        // Find which index was active
        const activeIndex = sections.findIndex(s => s.id === activeSidebarItem);
        if (activeIndex !== -1 && activeIndex < newSections.length) {
          setActiveSidebarItem(newSections[activeIndex].id);
        } else {
          setActiveSidebarItem("metadata");
        }
      }

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

    // Capture current active index before generation
    let activeIndex = -1;
    if (activeSidebarItem !== "metadata" && activeSidebarItem !== "summary" && activeSidebarItem !== "script" && activeSidebarItem !== "prompt") {
      activeIndex = sections.findIndex(s => s.id === activeSidebarItem);
    }

    try {
      const result = await regenerateFullArticle(articleId, scriptMarkdown, promptDirty ? promptMarkdown : undefined);
      setArticleMarkdown(result.articleMarkdown);
      const newSections = parseSections(result.articleMarkdown);
      setSections(newSections);

      // Restore active section if possible
      if (activeIndex !== -1 && activeIndex < newSections.length) {
        setActiveSidebarItem(newSections[activeIndex].id);
      } else if (activeIndex !== -1) {
        setActiveSidebarItem("metadata");
      }

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

  // Render Helpers
  const renderSidebar = () => (
    <div className="w-64 flex-shrink-0 border-r border-gray-200 bg-gray-50 h-full overflow-y-auto flex flex-col">
      <div className="p-2 space-y-1">
        <button
          onClick={() => setActiveSidebarItem("metadata")}
          className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium flex items-center gap-2 ${activeSidebarItem === "metadata" ? "bg-white shadow-sm text-blue-600" : "text-gray-700 hover:bg-gray-100"
            }`}
        >
          <Settings className="w-4 h-4" />
          基本資訊
        </button>
        <button
          onClick={() => setActiveSidebarItem("summary")}
          className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium flex items-center gap-2 ${activeSidebarItem === "summary" ? "bg-white shadow-sm text-blue-600" : "text-gray-700 hover:bg-gray-100"
            }`}
        >
          <AlignLeft className="w-4 h-4" />
          摘要
        </button>
        <button
          onClick={() => setActiveSidebarItem("script")}
          className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium flex items-center gap-2 ${activeSidebarItem === "script" ? "bg-white shadow-sm text-blue-600" : "text-gray-700 hover:bg-gray-100"
            }`}
        >
          <ScrollText className="w-4 h-4" />
          原始講稿
        </button>
        <button
          onClick={() => setActiveSidebarItem("prompt")}
          className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium flex items-center gap-2 ${activeSidebarItem === "prompt" ? "bg-white shadow-sm text-blue-600" : "text-gray-700 hover:bg-gray-100"
            }`}
        >
          <MessageSquare className="w-4 h-4" />
          Prompt
        </button>
      </div>

      <div className="px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider flex justify-between items-center border-t border-gray-200 mt-2 pt-4">
        <span>文章章節</span>
        <button onClick={() => handleAddSection(sections.length - 1)} className="hover:bg-gray-200 p-1 rounded">
          <Plus className="w-3 h-3" />
        </button>
      </div>

      <div className="flex-1 p-2 space-y-1 overflow-y-auto">
        {sections.map((section, index) => (
          <div
            key={section.id}
            className={`group flex items-center justify-between px-3 py-2 rounded-md text-sm cursor-pointer ${activeSidebarItem === section.id ? "bg-white shadow-sm text-blue-600" : "text-gray-700 hover:bg-gray-100"
              }`}
            onClick={() => setActiveSidebarItem(section.id)}
          >
            <div className="flex items-center gap-2 truncate">
              <FileIcon className="w-4 h-4 flex-shrink-0" />
              <span className="truncate">{section.title || "(無標題)"}</span>
            </div>
            <div className="hidden group-hover:flex items-center gap-1">
              <button
                onClick={(e) => handleMoveSection(index, "up", e)}
                disabled={index === 0}
                className="p-0.5 hover:bg-gray-200 rounded disabled:opacity-30"
              >
                <ArrowUp className="w-3 h-3" />
              </button>
              <button
                onClick={(e) => handleMoveSection(index, "down", e)}
                disabled={index === sections.length - 1}
                className="p-0.5 hover:bg-gray-200 rounded disabled:opacity-30"
              >
                <ArrowDown className="w-3 h-3" />
              </button>
              <button
                onClick={(e) => handleDeleteSection(index, e)}
                className="p-0.5 hover:bg-red-100 text-red-500 rounded"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  const renderMetadataForm = () => (
    <div className="space-y-6 max-w-2xl">
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
          <span className="text-sm font-medium text-gray-700">講道來源 ID（每行一個）</span>
          <textarea
            value={sourceSermonIdsInput}
            onChange={(event) => setSourceSermonIdsInput(event.target.value)}
            rows={3}
            className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="例如：\nsermon-2024-05-12"
          />
        </label>
      </div>
    </div>
  );

  const renderSummaryEditor = () => (
    <div className="space-y-3 h-full flex flex-col">
      <div className="flex justify-between items-center">
        <span className="text-sm font-medium text-gray-700">摘要</span>
        <button
          type="button"
          onClick={handleGenerateSummary}
          disabled={generatingSummary || !articleId}
          className="inline-flex items-center px-3 py-1.5 rounded-md bg-purple-600 text-white text-sm font-medium hover:bg-purple-700 disabled:opacity-60"
        >
          {generatingSummary ? "生成中..." : "生成摘要"}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        <SimpleMDE
          key="summary-editor"
          value={summaryMarkdown}
          onChange={(value) => setSummaryMarkdown(value)}
          options={summaryEditorOptions}
        />
      </div>
    </div>
  );

  const renderScriptEditor = () => (
    <div className="space-y-3 h-full flex flex-col">
      <header className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">講稿</h2>
          <p className="text-gray-600 text-sm mt-1">提供給AI的原始講稿內容。</p>
        </div>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={generating}
          className="inline-flex items-center px-4 py-2 rounded-md bg-purple-600 text-white font-medium hover:bg-purple-700 disabled:opacity-60"
        >
          {generating ? "生成中..." : "生成文章"}
        </button>
      </header>
      <div className="flex-1 overflow-y-auto">
        <SimpleMDE
          key="script-editor"
          value={scriptMarkdown}
          onChange={(value) => setScriptMarkdown(value)}
          options={scriptEditorOptions}
        />
      </div>
    </div>
  );

  const renderPromptEditor = () => (
    <div className="space-y-3 h-full flex flex-col">
      <header className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Prompt</h2>
          <p className="text-gray-600 text-sm mt-1">
            系統所有文章共用此Prompt。
          </p>
        </div>
        <button
          type="button"
          onClick={handleUpdatePrompt}
          disabled={updatingPrompt || !promptDirty}
          className="inline-flex items-center px-4 py-2 rounded-md bg-emerald-600 text-white font-medium hover:bg-emerald-700 disabled:opacity-60"
        >
          {updatingPrompt ? "更新中..." : "更新prompt"}
        </button>
      </header>
      <div className="flex-1 overflow-y-auto">
        <SimpleMDE
          key="prompt-editor"
          value={promptMarkdown}
          onChange={handlePromptChange}
          options={promptEditorOptions}
        />
      </div>
    </div>
  );

  const renderSectionEditor = (sectionId: string) => {
    const section = sections.find(s => s.id === sectionId);
    if (!section) return <div>Section not found</div>;

    const index = sections.findIndex(s => s.id === sectionId);

    return (
      <div className="space-y-4 h-full flex flex-col">
        <div className="flex items-center gap-2 border-b border-gray-200 pb-2">
          <input
            type="text"
            value={section.title}
            onChange={(e) => handleSectionChange(section.id, "title", e.target.value)}
            className="text-xl font-bold flex-1 focus:outline-none focus:border-blue-500 bg-transparent"
            placeholder="章節標題"
          />
          <div className="flex items-center gap-1">
            <button
              onClick={() => handleAddSection(index)}
              className="p-1.5 text-gray-500 hover:bg-gray-100 hover:text-blue-600 rounded-md transition-colors"
              title="在下方新增章節"
            >
              <Plus className="w-5 h-5" />
            </button>
            <button
              onClick={(e) => handleDeleteSection(index, e)}
              className="p-1.5 text-gray-500 hover:bg-red-50 hover:text-red-600 rounded-md transition-colors"
              title="刪除此章節"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <SimpleMDE
            value={section.content}
            onChange={(value) => handleSectionChange(section.id, "content", value)}
            options={sectionEditorOptions}
          />
        </div>
      </div>
    );
  };

  return (
    <div className="h-[calc(100vh-2rem)] flex flex-col overflow-hidden">
      <header className="flex-shrink-0 mb-4 space-y-2">
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <Link
              href="/admin/full_article"
              className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
            >
              ← 回到列表
            </Link>
            <h1 className="text-xl font-bold text-gray-900 truncate">
              {articleId ? (name || "未命名文章") : "從講稿生成文章"}
            </h1>
          </div>
          <div className="flex items-center gap-3">
            {articleId ? (
              <Link
                href={`/resources/full_article/${articleId}?nocache=1`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center rounded-md border border-blue-200 px-3 py-1 text-sm text-blue-600 hover:bg-blue-50"
              >
                預覽
              </Link>
            ) : null}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center px-4 py-1.5 rounded-md bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-60"
            >
              {saving ? "儲存中..." : "儲存"}
            </button>
          </div>
        </div>
      </header>

      {feedback && (
        <div
          className={`flex-shrink-0 mb-4 px-4 py-2 rounded-md border text-sm ${feedback.type === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
            : "border-red-200 bg-red-50 text-red-700"
            }`}
        >
          {feedback.message}
        </div>
      )}

      <div className="flex-1 min-h-0 bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden flex">
        {/* Sidebar */}
        {renderSidebar()}

        {/* Main Content */}
        <div className="flex-1 p-6 overflow-y-auto h-full">
          {activeSidebarItem === "metadata" && renderMetadataForm()}
          {activeSidebarItem === "summary" && renderSummaryEditor()}
          {activeSidebarItem === "script" && renderScriptEditor()}
          {activeSidebarItem === "prompt" && renderPromptEditor()}
          {activeSidebarItem !== "metadata" && activeSidebarItem !== "summary" && activeSidebarItem !== "script" && activeSidebarItem !== "prompt" && renderSectionEditor(activeSidebarItem)}
        </div>
      </div>
    </div>
  );
}
