"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createSundayService,
  deleteSundayService,
  fetchSundayResources,
  fetchSundayServices,
  fetchScriptureBooks,
  generateSundayServicePpt,
  updateSundayService,
} from "@/app/admin/sunday-service/api";
import { SundayWorkerManager } from "@/app/components/admin/sundayService/SundayWorkerManager";
import {
  SundayServiceEntry,
  SundayServiceResources,
  SundaySong,
  ScriptureBook,
} from "@/app/types/sundayService";

interface ScriptureSelection {
  book: string;
  chapter: string;
  start: string;
  end: string;
}

interface FormState {
  date: string;
  presider: string;
  worshipLeader: string;
  pianist: string;
  hymn: string;
  responseHymn: string;
  scriptureBook: string;
  scriptureChapter: string;
  scriptureStart: string;
  scriptureEnd: string;
  scriptureReader1: string;
  scriptureReader2: string;
  scriptureReader3: string;
  sermonSpeaker: string;
  sermonTitle: string;
  announcementsMarkdown: string;
  healthPrayerMarkdown: string;
}

const emptyForm: FormState = {
  date: "",
  presider: "",
  worshipLeader: "",
  pianist: "",
  hymn: "",
  responseHymn: "",
  scriptureBook: "",
  scriptureChapter: "",
  scriptureStart: "",
  scriptureEnd: "",
  scriptureReader1: "",
  scriptureReader2: "",
  scriptureReader3: "",
  sermonSpeaker: "",
  sermonTitle: "",
  announcementsMarkdown: "",
  healthPrayerMarkdown: "",
};

function parseScriptureValue(value: string | null | undefined): ScriptureSelection {
  if (!value) {
    return { book: "", chapter: "", start: "", end: "" };
  }
  const parts = value.split("-");
  if (parts.length < 3) {
    return { book: "", chapter: "", start: "", end: "" };
  }
  const [book, chapter, start, maybeEnd] = parts;
  return {
    book: book ?? "",
    chapter: chapter ?? "",
    start: start ?? "",
    end: maybeEnd ?? start ?? "",
  };
}

function composeScriptureValue(form: FormState): string | null {
  const book = form.scriptureBook.trim();
  const chapter = form.scriptureChapter.trim();
  const start = form.scriptureStart.trim();
  const end = form.scriptureEnd.trim();
  if (!book || !chapter || !start) {
    return null;
  }
  const chapterNum = Number.parseInt(chapter, 10);
  const startNum = Number.parseInt(start, 10);
  const endNum = end ? Number.parseInt(end, 10) : startNum;
  if (Number.isNaN(chapterNum) || Number.isNaN(startNum) || Number.isNaN(endNum)) {
    return null;
  }
  const normalizedEnd = Math.max(startNum, endNum);
  return `${book}-${chapterNum}-${startNum}-${normalizedEnd}`;
}

function toForm(entry: SundayServiceEntry | null): FormState {
  if (!entry) {
    return { ...emptyForm };
  }
  const scripture = parseScriptureValue(entry.scripture);
  return {
    date: entry.date ?? "",
    presider: entry.presider ?? "",
    worshipLeader: entry.worshipLeader ?? "",
    pianist: entry.pianist ?? "",
    hymn: entry.hymn ?? "",
    responseHymn: entry.responseHymn ?? "",
    scriptureBook: scripture.book,
    scriptureChapter: scripture.chapter,
    scriptureStart: scripture.start,
    scriptureEnd: scripture.end,
    scriptureReader1: entry.scriptureReaders?.[0] ?? "",
    scriptureReader2: entry.scriptureReaders?.[1] ?? "",
    scriptureReader3: entry.scriptureReaders?.[2] ?? "",
    sermonSpeaker: entry.sermonSpeaker ?? "",
    sermonTitle: entry.sermonTitle ?? "",
    announcementsMarkdown: entry.announcementsMarkdown ?? "",
    healthPrayerMarkdown: entry.health_prayer_markdown ?? "",
  };
}

function fromForm(form: FormState): SundayServiceEntry {
  const optional = (value: string): string | null => {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  };
  return {
    date: form.date,
    presider: optional(form.presider),
    worshipLeader: optional(form.worshipLeader),
    pianist: optional(form.pianist),
    hymn: optional(form.hymn),
    responseHymn: optional(form.responseHymn),
    scripture: composeScriptureValue(form),
    scriptureReaders: [form.scriptureReader1, form.scriptureReader2, form.scriptureReader3]
      .map((value) => value.trim())
      .filter((value) => value.length > 0),
    sermonSpeaker: optional(form.sermonSpeaker),
    sermonTitle: optional(form.sermonTitle),
    announcementsMarkdown: form.announcementsMarkdown.trim(),
    health_prayer_markdown: form.healthPrayerMarkdown.trim(),
  };
}

function renderSongLabel(song: SundaySong): string {
  if (song.source === "hymnal") {
    const indexText = song.hymnalIndex != null ? `第 ${song.hymnalIndex} 首` : "";
    const prefix = indexText ? `教會聖詩${indexText}` : "教會聖詩";
    return `${prefix}：${song.title}`;
  }
  return song.title;
}

function validateScripture(form: FormState): string | null {
  const hasAny =
    form.scriptureBook.trim() ||
    form.scriptureChapter.trim() ||
    form.scriptureStart.trim() ||
    form.scriptureEnd.trim();
  if (!hasAny) {
    return "請完整輸入讀經經文";
  }
  if (!form.scriptureBook.trim()) {
    return "請選擇經文書卷";
  }
  const chapter = Number.parseInt(form.scriptureChapter.trim(), 10);
  const start = Number.parseInt(form.scriptureStart.trim(), 10);
  const end = form.scriptureEnd.trim()
    ? Number.parseInt(form.scriptureEnd.trim(), 10)
    : start;
  if (Number.isNaN(chapter) || chapter <= 0) {
    return "章節須為正整數";
  }
  if (Number.isNaN(start) || start <= 0) {
    return "起始經文須為正整數";
  }
  if (Number.isNaN(end) || end <= 0) {
    return "結束經文須為正整數";
  }
  if (end < start) {
    return "結束經文須大於或等於起始經文";
  }
  const readers = [form.scriptureReader1, form.scriptureReader2, form.scriptureReader3].map((value) =>
    value.trim(),
  );
  if (readers.some((value) => value.length === 0)) {
    return "請輸入三位讀經人員姓名";
  }
  return null;
}

function formatScriptureDisplay(
  value: string | null | undefined,
  lookup: Map<string, string>,
): string {
  const { book, chapter, start, end } = parseScriptureValue(value);
  if (!book || !chapter || !start) {
    return value ?? "-";
  }
  const name = lookup.get(book) ?? book.toUpperCase();
  if (!end || end === start) {
    return `${name} ${chapter}:${start}`;
  }
  return `${name} ${chapter}:${start}-${end}`;
}

export function SundayServiceManager() {
  const [services, setServices] = useState<SundayServiceEntry[]>([]);
  const [resources, setResources] = useState<SundayServiceResources>({ workers: [], songs: [] });
  const [scriptureBooks, setScriptureBooks] = useState<ScriptureBook[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [form, setForm] = useState<FormState>({ ...emptyForm });
  const [editingDate, setEditingDate] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pptGenerating, setPptGenerating] = useState<string | null>(null);

  const bookNameMap = useMemo(
    () => new Map(scriptureBooks.map((book) => [book.slug, book.name])),
    [scriptureBooks],
  );

  const scriptureBookOptions = useMemo(() => {
    if (!form.scriptureBook) {
      return scriptureBooks;
    }
    if (scriptureBooks.some((book) => book.slug === form.scriptureBook)) {
      return scriptureBooks;
    }
    return [...scriptureBooks, { slug: form.scriptureBook, name: form.scriptureBook }];
  }, [scriptureBooks, form.scriptureBook]);

  const loadResources = useCallback(async () => {
    try {
      const data = await fetchSundayResources();
      setResources(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入同工與詩歌清單失敗";
      setError(message);
    }
  }, []);

  useEffect(() => {
    if (!editingDate && scriptureBooks.length > 0 && !form.scriptureBook) {
      setForm((prev) => ({ ...prev, scriptureBook: scriptureBooks[0].slug }));
    }
  }, [editingDate, scriptureBooks, form.scriptureBook]);

  useEffect(() => {
    if (editingDate) {
      return;
    }
    if (services.length === 0) {
      return;
    }
    const latest = services[0];
    const previousAnnouncements = latest.announcementsMarkdown ?? "";
    const previousHealthPrayer = latest.health_prayer_markdown ?? "";
    setForm((prev) => {
      const next: Partial<FormState> = {};
      let changed = false;

      if (!prev.announcementsMarkdown.trim()) {
        if (prev.announcementsMarkdown !== previousAnnouncements) {
          next.announcementsMarkdown = previousAnnouncements;
          changed = true;
        }
      }

      if (!prev.healthPrayerMarkdown.trim()) {
        if (prev.healthPrayerMarkdown !== previousHealthPrayer) {
          next.healthPrayerMarkdown = previousHealthPrayer;
          changed = true;
        }
      }

      return changed ? { ...prev, ...next } : prev;
    });
  }, [editingDate, services]);

  const loadServices = useCallback(
    async (opts?: { preserveForm?: boolean }) => {
      try {
        const data = await fetchSundayServices();
        setServices(data);
        if (opts?.preserveForm && editingDate) {
          const latest = data.find((entry) => entry.date === editingDate);
          if (latest) {
            setForm(toForm(latest));
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "載入主日服事資訊失敗";
        setError(message);
        setStatus((prev) => (prev === "idle" ? "error" : prev));
      }
    },
    [editingDate],
  );

  const initialize = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const [serviceData, resourceData, bookData] = await Promise.all([
        fetchSundayServices(),
        fetchSundayResources(),
        fetchScriptureBooks(),
      ]);
      setServices(serviceData);
      setResources(resourceData);
      setScriptureBooks(bookData);
      setStatus("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入主日服事資料失敗";
      setError(message);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    if (status === "idle") {
      initialize().catch(() => {});
    }
  }, [status, initialize]);

  const sortedSongs = useMemo(() => {
    return [...resources.songs].sort((a, b) => a.title.localeCompare(b.title, "zh-Hant"));
  }, [resources.songs]);

  const workerNames = useMemo(
    () => resources.workers.map((worker) => worker.name).filter((name) => name.trim().length > 0),
    [resources.workers],
  );

  const readerOptions1 = useMemo(() => {
    const set = new Set(workerNames);
    if (form.scriptureReader1.trim() && !set.has(form.scriptureReader1)) {
      set.add(form.scriptureReader1);
    }
    return Array.from(set);
  }, [workerNames, form.scriptureReader1]);

  const readerOptions2 = useMemo(() => {
    const set = new Set(workerNames);
    if (form.scriptureReader2.trim() && !set.has(form.scriptureReader2)) {
      set.add(form.scriptureReader2);
    }
    return Array.from(set);
  }, [workerNames, form.scriptureReader2]);

  const readerOptions3 = useMemo(() => {
    const set = new Set(workerNames);
    if (form.scriptureReader3.trim() && !set.has(form.scriptureReader3)) {
      set.add(form.scriptureReader3);
    }
    return Array.from(set);
  }, [workerNames, form.scriptureReader3]);

  const handleInputChange = (field: keyof FormState) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
      const value = event.target.value;
      setForm((prev) => ({ ...prev, [field]: value }));
    };

  const resetForm = () => {
    setForm((prev) => ({
      ...emptyForm,
      scriptureBook: scriptureBooks[0]?.slug ?? prev.scriptureBook ?? "",
    }));
    setEditingDate(null);
  };

  const handleEdit = (entry: SundayServiceEntry) => {
    setEditingDate(entry.date);
    const next = toForm(entry);
    if (!next.scriptureBook && scriptureBooks.length > 0) {
      next.scriptureBook = scriptureBooks[0].slug;
    }
    setForm(next);
    setFeedback(null);
    setError(null);
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.date) {
      setError("請選擇主日日期");
      return;
    }
    const scriptureError = validateScripture(form);
    if (scriptureError) {
      setError(scriptureError);
      return;
    }
    setSaving(true);
    setFeedback(null);
    setError(null);
    const payload = fromForm(form);
    try {
      if (editingDate) {
        await updateSundayService(editingDate, payload);
        setFeedback("已更新主日服事資訊");
      } else {
        await createSundayService(payload);
        setFeedback("已新增主日服事資訊");
        setEditingDate(payload.date);
      }
      await loadServices({ preserveForm: true });
      await loadResources();
    } catch (err) {
      const message = err instanceof Error ? err.message : "儲存失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (date: string) => {
    if (!window.confirm(`確定要刪除 ${date} 的主日服事資訊嗎？`)) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    setError(null);
    try {
      await deleteSundayService(date);
      setFeedback("已刪除主日服事資訊");
      if (editingDate === date) {
        resetForm();
      }
      await loadServices();
    } catch (err) {
      const message = err instanceof Error ? err.message : "刪除失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleGeneratePpt = async (date: string) => {
    setPptGenerating(date);
    setFeedback(null);
    setError(null);
    try {
      const blob = await generateSundayServicePpt(date);
      if (blob.size === 0) {
        throw new Error("產生的檔案內容為空");
      }
      const safeDate = date.replace(/[\\/:*?"<>|]/g, "-");
      const fileName = `${safeDate || "主日服事"}_主日敬拜.pptx`;
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      window.URL.revokeObjectURL(url);
      setFeedback(`已生成並下載 ${fileName}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "生成 PPT 失敗";
      setError(message);
    } finally {
      setPptGenerating(null);
    }
  };

  const handleRefresh = async () => {
    setFeedback(null);
    setError(null);
    setStatus("loading");
    try {
      const [serviceData, resourceData, bookData] = await Promise.all([
        fetchSundayServices(),
        fetchSundayResources(),
        fetchScriptureBooks(),
      ]);
      setServices(serviceData);
      setResources(resourceData);
      setScriptureBooks(bookData);
      if (editingDate) {
        const latest = serviceData.find((entry) => entry.date === editingDate);
        if (latest) {
          setForm(toForm(latest));
        }
      }
      setStatus("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "重新載入失敗";
      setError(message);
      setStatus("error");
    }
  };

  const formatDisplayDate = (value: string) => {
    if (!value) {
      return "";
    }
    const timestamp = Date.parse(value);
    if (!Number.isNaN(timestamp)) {
      const date = new Date(timestamp);
      return `${date.getFullYear()}-${`${date.getMonth() + 1}`.padStart(2, "0")}-${`${date.getDate()}`.padStart(2, "0")}`;
    }
    return value;
  };

  return (
    <div className="space-y-8">
      <header className="rounded-lg border border-blue-100 bg-blue-50 p-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">主日服事管理</h2>
            <p className="mt-1 text-sm text-gray-600">
              管理主日服事同工、詩歌、讀經經文與家事報告。
            </p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/admin/sunday-service/songs"
              className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100"
            >
              詩歌管理
            </Link>
            <button
              type="button"
              className="rounded border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-100"
              onClick={handleRefresh}
              disabled={status === "loading"}
            >
              重新載入
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}
      {feedback && (
        <div className="rounded border border-green-200 bg-green-50 p-4 text-sm text-green-700">
          {feedback}
        </div>
      )}

      <section className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h3 className="mb-4 text-lg font-semibold text-gray-800">
            {editingDate ? `編輯 ${formatDisplayDate(editingDate)} 主日` : "新增主日服事"}
          </h3>
          {editingDate && (
            <div className="mb-4 flex justify-end">
              <button
                type="button"
                className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                onClick={() => handleGeneratePpt(editingDate)}
                disabled={pptGenerating === editingDate || saving}
              >
                {pptGenerating === editingDate ? "生成中…" : "生成 PPT"}
              </button>
            </div>
          )}
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">主日日期</label>
              <input
                type="date"
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                value={form.date}
                onChange={handleInputChange("date")}
                disabled={saving}
                required
              />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">司會</label>
                <select
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.presider}
                  onChange={handleInputChange("presider")}
                  disabled={saving}
                >
                  <option value="">未指定</option>
                  {resources.workers.map((worker) => (
                    <option key={worker.name} value={worker.name}>
                      {worker.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">領詩</label>
                <select
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.worshipLeader}
                  onChange={handleInputChange("worshipLeader")}
                  disabled={saving}
                >
                  <option value="">未指定</option>
                  {resources.workers.map((worker) => (
                    <option key={worker.name} value={worker.name}>
                      {worker.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">司琴</label>
                <select
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.pianist}
                  onChange={handleInputChange("pianist")}
                  disabled={saving}
                >
                  <option value="">未指定</option>
                  {resources.workers.map((worker) => (
                    <option key={worker.name} value={worker.name}>
                      {worker.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">證道講員</label>
                <input
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  placeholder="講員姓名"
                  value={form.sermonSpeaker}
                  onChange={handleInputChange("sermonSpeaker")}
                  disabled={saving}
                  list="worker-options"
                />
                <datalist id="worker-options">
                  {resources.workers.map((worker) => (
                    <option key={`speaker-${worker.name}`} value={worker.name} />
                  ))}
                </datalist>
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">詩歌</label>
                <select
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.hymn}
                  onChange={handleInputChange("hymn")}
                  disabled={saving}
                >
                  <option value="">未指定</option>
                  {sortedSongs.map((song) => (
                    <option key={song.id} value={song.title}>
                      {renderSongLabel(song)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-700">回應詩歌</label>
                <select
                  className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.responseHymn}
                  onChange={handleInputChange("responseHymn")}
                  disabled={saving}
                >
                  <option value="">未指定</option>
                  {sortedSongs.map((song) => (
                    <option key={`${song.id}-response`} value={song.title}>
                      {renderSongLabel(song)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">讀經經文</label>
              <div className="grid gap-3 md:grid-cols-4">
                <select
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.scriptureBook}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, scriptureBook: event.target.value }))
                  }
                  disabled={saving || scriptureBookOptions.length === 0}
                >
                  {scriptureBookOptions.map((book) => (
                    <option key={book.slug} value={book.slug}>
                      {book.name}
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  min={1}
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  placeholder="章"
                  value={form.scriptureChapter}
                  onChange={handleInputChange("scriptureChapter")}
                  disabled={saving}
                />
                <input
                  type="number"
                  min={1}
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  placeholder="起始經文"
                  value={form.scriptureStart}
                  onChange={handleInputChange("scriptureStart")}
                  disabled={saving}
                />
                <input
                  type="number"
                  min={1}
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  placeholder="結束經文 (可留空)"
                  value={form.scriptureEnd}
                  onChange={handleInputChange("scriptureEnd")}
                  disabled={saving}
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">讀經同工</label>
              <div className="grid gap-3 md:grid-cols-3">
                <select
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.scriptureReader1}
                  onChange={handleInputChange("scriptureReader1")}
                  disabled={saving || readerOptions1.length === 0}
                >
                  <option value="">選擇同工 1</option>
                  {readerOptions1.map((name) => (
                    <option key={`reader1-${name}`} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                <select
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.scriptureReader2}
                  onChange={handleInputChange("scriptureReader2")}
                  disabled={saving || readerOptions2.length === 0}
                >
                  <option value="">選擇同工 2</option>
                  {readerOptions2.map((name) => (
                    <option key={`reader2-${name}`} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                <select
                  className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                  value={form.scriptureReader3}
                  onChange={handleInputChange("scriptureReader3")}
                  disabled={saving || readerOptions3.length === 0}
                >
                  <option value="">選擇同工 3</option>
                  {readerOptions3.map((name) => (
                    <option key={`reader3-${name}`} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">證道題目</label>
              <input
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                placeholder="證道主題"
                value={form.sermonTitle}
                onChange={handleInputChange("sermonTitle")}
                disabled={saving}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">家事與報告</label>
              <textarea
                className="h-40 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                value={form.announcementsMarkdown}
                onChange={handleInputChange("announcementsMarkdown")}
                disabled={saving}
                placeholder="例：\n- 本週小組聚會時間為……"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-semibold text-gray-700">身體健康代祷</label>
              <textarea
                className="h-32 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                value={form.healthPrayerMarkdown}
                onChange={handleInputChange("healthPrayerMarkdown")}
                disabled={saving}
                placeholder="例：\n- 請為弟兄姊妹的身體健康代祷……"
              />
            </div>
            <div className="flex justify-end gap-3">
              {editingDate && (
                <button
                  type="button"
                  className="rounded border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-100"
                  onClick={resetForm}
                  disabled={saving}
                >
                  取消編輯
                </button>
              )}
              <button
                type="submit"
                className="rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-300"
                disabled={saving}
              >
                {editingDate ? "更新" : "新增"}
              </button>
            </div>
          </form>
        </div>
        <SundayWorkerManager workers={resources.workers} onWorkersChanged={loadResources} />
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <header className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-800">主日服事清單</h3>
          <span className="text-sm text-gray-500">共 {services.length} 筆</span>
        </header>
        <div className="overflow-auto">
          <table className="min-w-full table-fixed text-left text-sm">
            <thead>
              <tr className="bg-gray-50 text-xs uppercase text-gray-500">
                <th className="w-32 px-3 py-2">日期</th>
                <th className="w-24 px-3 py-2">司會</th>
                <th className="w-24 px-3 py-2">領詩</th>
                <th className="w-24 px-3 py-2">司琴</th>
                <th className="w-40 px-3 py-2">詩歌 / 回應</th>
                <th className="w-40 px-3 py-2">講員 / 題目</th>
                <th className="w-56 px-3 py-2">讀經</th>
                <th className="w-40 px-3 py-2">讀經同工</th>
                <th className="w-32 px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {services.map((entry) => (
                <tr key={entry.date} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-900">{formatDisplayDate(entry.date)}</td>
                  <td className="px-3 py-2 text-gray-700">{entry.presider ?? "-"}</td>
                  <td className="px-3 py-2 text-gray-700">{entry.worshipLeader ?? "-"}</td>
                  <td className="px-3 py-2 text-gray-700">{entry.pianist ?? "-"}</td>
                  <td className="px-3 py-2 text-gray-700">
                    <div className="flex flex-col">
                      <span>{entry.hymn ?? "-"}</span>
                      <span className="text-xs text-gray-500">回應：{entry.responseHymn ?? "-"}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-gray-700">
                    <div className="flex flex-col">
                      <span>{entry.sermonSpeaker ?? "-"}</span>
                      <span className="text-xs text-gray-500">{entry.sermonTitle ?? ""}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-gray-700">
                    {formatScriptureDisplay(entry.scripture, bookNameMap)}
                  </td>
                  <td className="px-3 py-2 text-gray-700">
                    {(entry.scriptureReaders && entry.scriptureReaders.length > 0
                      ? entry.scriptureReaders.join("、")
                      : "-")}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="rounded border border-gray-300 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-100"
                        onClick={() => handleEdit(entry)}
                        disabled={saving}
                      >
                        編輯
                      </button>
                      <button
                        type="button"
                        className="rounded border border-blue-300 px-3 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                        onClick={() => handleGeneratePpt(entry.date)}
                        disabled={pptGenerating === entry.date || saving}
                      >
                        {pptGenerating === entry.date ? "生成中…" : "生成 PPT"}
                      </button>
                      <button
                        type="button"
                        className="rounded border border-red-200 px-3 py-1 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                        onClick={() => handleDelete(entry.date)}
                        disabled={saving}
                      >
                        刪除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {services.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-3 py-6 text-center text-sm text-gray-500"
                  >
                    尚未建立主日服事資訊。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
