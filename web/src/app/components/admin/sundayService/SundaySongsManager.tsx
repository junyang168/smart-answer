"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createSundaySong,
  deleteSundaySong,
  fetchHymnMetadata,
  fetchSundaySongs,
  generateHymnLyrics,
  updateSundaySong,
} from "@/app/admin/sunday-service/api";
import {
  GenerateHymnLyricsResponse,
  HymnMetadata,
  SundaySong,
  SundaySongPayload,
  SundaySongSource,
} from "@/app/types/sundayService";

interface SongFormState {
  source: SundaySongSource;
  title: string;
  hymnalIndex: string;
  lyricsMarkdown: string;
  hymnLink: string;
}

const emptyForm: SongFormState = {
  source: "custom",
  title: "",
  hymnalIndex: "",
  lyricsMarkdown: "",
  hymnLink: "",
};

function toPayload(form: SongFormState): SundaySongPayload {
  const lyrics = form.lyricsMarkdown.trim();
  if (form.source === "hymnal") {
    const index = Number.parseInt(form.hymnalIndex, 10);
    return {
      title: form.title,
      source: "hymnal",
      hymnalIndex: Number.isNaN(index) ? undefined : index,
      hymnLink: form.hymnLink || undefined,
      lyricsMarkdown: lyrics || undefined,
    };
  }
  return {
    title: form.title.trim(),
    source: "custom",
    lyricsMarkdown: lyrics || undefined,
  };
}

function formatSongSource(song: SundaySong): string {
  if (song.source === "hymnal") {
    const index = song.hymnalIndex != null ? `第 ${song.hymnalIndex} 首` : "教會聖詩";
    return `教會聖詩 ${index}`;
  }
  return "自訂";
}

export function SundaySongsManager() {
  const [songs, setSongs] = useState<SundaySong[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [form, setForm] = useState<SongFormState>({ ...emptyForm });
  const [editing, setEditing] = useState<SundaySong | null>(null);
  const [hymnMetadata, setHymnMetadata] = useState<HymnMetadata | null>(null);
  const [saving, setSaving] = useState(false);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSongs = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const data = await fetchSundaySongs();
      setSongs(data);
      setStatus("ready");
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入詩歌資料失敗";
      setError(message);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    if (status === "idle") {
      loadSongs().catch(() => {});
    }
  }, [status, loadSongs]);

  const sortedSongs = useMemo(() => {
    return [...songs].sort((a, b) => a.title.localeCompare(b.title, "zh-Hant"));
  }, [songs]);

  const resetForm = () => {
    setForm({ ...emptyForm });
    setEditing(null);
    setHymnMetadata(null);
  };

  const setFormField = <K extends keyof SongFormState>(field: K, value: SongFormState[K]) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSourceChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value as SundaySongSource;
    setForm((prev) => ({
      ...prev,
      source: value,
      title: value === "custom" ? prev.title : hymnMetadata?.title ?? "",
      hymnalIndex: value === "hymnal" ? prev.hymnalIndex : "",
      hymnLink: value === "hymnal" ? prev.hymnLink : "",
    }));
    if (value !== "hymnal") {
      setHymnMetadata(null);
    }
  };

  const handleLookupHymn = async () => {
    const trimmed = form.hymnalIndex.trim();
    const index = Number.parseInt(trimmed, 10);
    if (!trimmed || Number.isNaN(index) || index <= 0) {
      setError("請輸入有效的詩歌索引");
      return;
    }
    setLookupLoading(true);
    setError(null);
    try {
      const metadata = await fetchHymnMetadata(index);
      setHymnMetadata(metadata);
      setForm((prev) => ({
        ...prev,
        title: metadata.title,
        hymnLink: metadata.link ?? "",
      }));
      setFeedback(`已載入教會聖詩第 ${metadata.index} 首：${metadata.title}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "查詢詩歌索引失敗";
      setError(message);
      setHymnMetadata(null);
    } finally {
      setLookupLoading(false);
    }
  };

  const handleGenerateLyrics = async () => {
    if (!hymnMetadata) {
      setError("請先查詢詩歌索引");
      return;
    }
    setGenerating(true);
    setError(null);
    setFeedback(null);
    try {
      const response: GenerateHymnLyricsResponse = await generateHymnLyrics(
        hymnMetadata.index,
        hymnMetadata.title,
      );
      setForm((prev) => ({ ...prev, lyricsMarkdown: response.lyricsMarkdown }));
      setFeedback("已取得歌詞，請確認內容後儲存。");
    } catch (err) {
      const message = err instanceof Error ? err.message : "取得歌詞失敗";
      setError(message);
    } finally {
      setGenerating(false);
    }
  };

  const submitSong = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setFeedback(null);
    setError(null);

    if (form.source === "custom") {
      const title = form.title.trim();
      if (!title) {
        setError("請輸入詩歌名稱");
        setSaving(false);
        return;
      }
    } else {
      if (!hymnMetadata) {
        setError("請先確認教會聖詩索引");
        setSaving(false);
        return;
      }
    }

    const payload = toPayload(form);
    try {
      if (editing) {
        await updateSundaySong(editing.id, payload);
        setFeedback("已更新詩歌");
      } else {
        await createSundaySong(payload);
        setFeedback("已新增詩歌");
      }
      resetForm();
      await loadSongs();
    } catch (err) {
      const message = err instanceof Error ? err.message : "儲存詩歌失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (song: SundaySong) => {
    setEditing(song);
    setFeedback(null);
    setError(null);
    if (song.source === "hymnal") {
      setForm({
        source: "hymnal",
        title: song.title,
        hymnalIndex: song.hymnalIndex != null ? String(song.hymnalIndex) : "",
        lyricsMarkdown: song.lyricsMarkdown ?? "",
        hymnLink: song.hymnLink ?? "",
      });
      if (song.hymnalIndex != null) {
        setLookupLoading(true);
        fetchHymnMetadata(song.hymnalIndex)
          .then((metadata) => {
            setHymnMetadata(metadata);
            setForm((prev) => ({
              ...prev,
              title: metadata.title,
              hymnLink: metadata.link ?? "",
            }));
          })
          .catch((err: unknown) => {
            const message = err instanceof Error ? err.message : "查詢詩歌索引失敗";
            setError(message);
            setHymnMetadata({
              index: song.hymnalIndex ?? 0,
              title: song.title,
              link: song.hymnLink ?? undefined,
            });
          })
          .finally(() => {
            setLookupLoading(false);
          });
      } else {
        setHymnMetadata(null);
      }
    } else {
      setHymnMetadata(null);
      setForm({
        source: "custom",
        title: song.title,
        hymnalIndex: "",
        lyricsMarkdown: song.lyricsMarkdown ?? "",
        hymnLink: "",
      });
    }
  };

  const handleDelete = async (song: SundaySong) => {
    if (!window.confirm(`確定要刪除「${song.title}」嗎？`)) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    setError(null);
    try {
      await deleteSundaySong(song.id);
      setFeedback("已刪除詩歌");
      if (editing?.id === song.id) {
        resetForm();
      }
      await loadSongs();
    } catch (err) {
      const message = err instanceof Error ? err.message : "刪除詩歌失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-8">
      <header className="rounded-lg border border-blue-100 bg-blue-50 p-6">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">詩歌管理</h2>
            <p className="mt-1 text-sm text-gray-600">
              維護主日所使用的詩歌資料，支援自訂與教會聖詩索引。
            </p>
          </div>
          <Link
            href="/admin/sunday-service"
            className="inline-flex items-center rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100"
          >
            返回主日服事管理
          </Link>
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

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-lg font-semibold text-gray-800">
          {editing ? `編輯詩歌：${editing.title}` : "新增詩歌"}
        </h3>
        <form className="space-y-5" onSubmit={submitSong}>
          <div>
            <span className="mb-2 block text-sm font-semibold text-gray-700">來源</span>
            <div className="flex flex-wrap gap-4 text-sm text-gray-700">
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name="song-source"
                  value="custom"
                  checked={form.source === "custom"}
                  onChange={handleSourceChange}
                  disabled={saving || lookupLoading || generating}
                />
                自訂
              </label>
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name="song-source"
                  value="hymnal"
                  checked={form.source === "hymnal"}
                  onChange={handleSourceChange}
                  disabled={saving || lookupLoading || generating}
                />
                教會聖詩
              </label>
            </div>
          </div>

          {form.source === "custom" && (
            <label className="flex flex-col gap-1 text-sm font-semibold text-gray-700">
              詩歌名稱
              <input
                className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                value={form.title}
                onChange={(event) => setFormField("title", event.target.value)}
                disabled={saving}
                placeholder="例如：奇異恩典"
              />
            </label>
          )}

          {form.source === "hymnal" && (
            <div className="space-y-3">
              <div className="flex flex-col gap-2 md:flex-row md:items-end">
                <label className="flex-1 text-sm font-semibold text-gray-700">
                  教會聖詩索引
                  <input
                    type="number"
                    min={1}
                    className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                    value={form.hymnalIndex}
                    onChange={(event) => setFormField("hymnalIndex", event.target.value)}
                    disabled={saving || lookupLoading}
                    placeholder="請輸入教會聖詩索引，例如 1"
                  />
                </label>
                <button
                  type="button"
                  className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                  onClick={handleLookupHymn}
                  disabled={saving || lookupLoading}
                >
                  {lookupLoading ? "查詢中…" : "查詢詩歌"}
                </button>
              </div>
              <div className="rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
                {hymnMetadata ? (
                  <div className="space-y-1">
                    <p>
                      教會聖詩第 {hymnMetadata.index} 首：《{hymnMetadata.title}》
                    </p>
                    {hymnMetadata.link && (
                      <p>
                        <a
                          href={hymnMetadata.link}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 underline"
                        >
                          線上樂譜 / 樂譜連結
                        </a>
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-gray-500">請輸入索引後按「查詢詩歌」取得標題。</p>
                )}
              </div>
            </div>
          )}

          <label className="flex flex-col gap-1 text-sm font-semibold text-gray-700">
            歌詞 (Markdown)
            <textarea
              className="h-48 rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              value={form.lyricsMarkdown}
              onChange={(event) => setFormField("lyricsMarkdown", event.target.value)}
              placeholder="可貼上或編輯歌詞內容，支援 Markdown 格式。"
              disabled={saving}
            />
          </label>

          {form.source === "hymnal" && (
            <button
              type="button"
              className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
              onClick={handleGenerateLyrics}
              disabled={generating || saving || !hymnMetadata}
            >
              {generating ? "取得歌詞中…" : "以 LLM 取得歌詞"}
            </button>
          )}

          <div className="flex gap-3">
            {editing && (
              <button
                type="button"
                className="rounded border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-100"
                onClick={resetForm}
                disabled={saving || generating}
              >
                取消
              </button>
            )}
            <button
              type="submit"
              className="rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-300"
              disabled={saving || generating}
            >
              {editing ? "更新" : "新增"}
            </button>
          </div>
        </form>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <header className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-800">詩歌清單</h3>
          <span className="text-sm text-gray-500">共 {sortedSongs.length} 首</span>
        </header>
        <div className="overflow-auto">
          <table className="min-w-full table-fixed text-left text-sm">
            <thead>
              <tr className="bg-gray-50 text-xs uppercase text-gray-500">
                <th className="w-1/3 px-3 py-2">名稱</th>
                <th className="w-1/6 px-3 py-2">來源</th>
                <th className="w-1/6 px-3 py-2">教會聖詩索引</th>
                <th className="px-3 py-2">歌詞摘要</th>
                <th className="w-32 px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {sortedSongs.map((song) => (
                <tr key={song.id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-900">{song.title}</td>
                  <td className="px-3 py-2 text-gray-700">{formatSongSource(song)}</td>
                  <td className="px-3 py-2 text-gray-700">
                    {song.hymnalIndex != null ? song.hymnalIndex : "-"}
                  </td>
                  <td className="px-3 py-2 text-gray-600">
                    {song.lyricsMarkdown ? `${song.lyricsMarkdown.slice(0, 40)}…` : "-"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="rounded border border-gray-300 px-3 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-100"
                        onClick={() => startEdit(song)}
                        disabled={saving || generating}
                      >
                        編輯
                      </button>
                      <button
                        type="button"
                        className="rounded border border-red-200 px-3 py-1 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                        onClick={() => handleDelete(song)}
                        disabled={saving || generating}
                      >
                        刪除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {sortedSongs.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-sm text-gray-500">
                    尚未建立任何詩歌資料。
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
