"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchSermonSeries,
  createSermonSeries,
  updateSermonSeries,
  deleteSermonSeries,
} from "@/app/admin/surmon_series/api";
import { SermonSeries } from "@/app/types/sermon-series";

interface FormState {
  id: string;
  title: string;
  summary: string;
  topics: string;
  keypoints: string;
  sermons: string[];
}

type FetchState =
  | { status: "idle" | "loading"; data: SermonSeries[] }
  | { status: "ready"; data: SermonSeries[] }
  | { status: "error"; data: SermonSeries[]; error: string };

const emptyForm: FormState = {
  id: "",
  title: "",
  summary: "",
  topics: "",
  keypoints: "",
  sermons: [],
};

const sanitizeSermons = (sermons: string[]) =>
  sermons.map((item) => item.trim()).filter((item, index, array) => item && array.indexOf(item) === index);

export function SermonSeriesManager() {
  const [state, setState] = useState<FetchState>({ status: "idle", data: [] });
  const [form, setForm] = useState<FormState>(emptyForm);
  const [newSermon, setNewSermon] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState((prev) => ({ status: "loading", data: prev.data }));
    try {
      const series = await fetchSermonSeries();
      setState({ status: "ready", data: series });
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入系列資料失敗";
      setState((prev) => ({ status: "error", data: prev.data, error: message }));
    }
  }, []);

  useEffect(() => {
    if (state.status === "idle") {
      load().catch(() => {});
    }
  }, [state.status, load]);

  const entries = state.data;

  const sortedEntries = useMemo(() => {
    return [...entries].sort((a, b) => a.id.localeCompare(b.id, "zh-Hant"));
  }, [entries]);

  const resetForm = () => {
    setForm(emptyForm);
    setNewSermon("");
    setEditingId(null);
    setFeedback(null);
    setError(null);
  };

  const handleChange = (field: keyof Omit<FormState, "sermons">) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const value = event.target.value;
      setForm((prev) => ({ ...prev, [field]: value }));
    };

  const handleAddSermon = () => {
    const value = newSermon.trim();
    if (!value) {
      return;
    }
    setForm((prev) => ({ ...prev, sermons: sanitizeSermons([...prev.sermons, value]) }));
    setNewSermon("");
  };

  const handleRemoveSermon = (value: string) => {
    setForm((prev) => ({ ...prev, sermons: prev.sermons.filter((item) => item !== value) }));
  };

  const handleEdit = (entry: SermonSeries) => {
    const topicsValue = Array.isArray(entry.topics)
      ? entry.topics.join(", ")
      : entry.topics ?? "";

    setForm({
      id: entry.id,
      title: entry.title ?? "",
      summary: entry.summary ?? "",
      topics: topicsValue,
      keypoints: entry.keypoints ?? "",
      sermons: sanitizeSermons(entry.sermons ?? []),
    });
    setNewSermon("");
    setEditingId(entry.id);
    setFeedback(null);
    setError(null);
  };

  const handleDelete = async (seriesId: string) => {
    if (!window.confirm("確定要刪除此系列嗎？")) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    setError(null);
    try {
      await deleteSermonSeries(seriesId);
      await load();
      if (editingId === seriesId) {
        resetForm();
      }
      setFeedback("已刪除系列");
    } catch (err) {
      const message = err instanceof Error ? err.message : "刪除失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setFeedback(null);
    setError(null);

    const trimmedId = form.id.trim();
    if (!trimmedId) {
      setError("系列代號不可為空");
      setSaving(false);
      return;
    }

    const payload: SermonSeries = {
      id: trimmedId,
      title: form.title.trim() || null,
      summary: form.summary.trim() || null,
      topics: form.topics.trim() || null,
      keypoints: form.keypoints.trim() || null,
      sermons: sanitizeSermons(form.sermons),
    };

    try {
      if (editingId != null) {
        await updateSermonSeries(editingId, payload);
        setFeedback("已更新系列");
      } else {
        await createSermonSeries(payload);
        setFeedback("已新增系列");
      }
      await load();
      resetForm();
    } catch (err) {
      const message = err instanceof Error ? err.message : "儲存失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-8">
      <section className="bg-white border border-gray-200 rounded-xl shadow-sm p-6 space-y-4">
        <header>
          <h1 className="text-2xl font-semibold text-gray-900">系列管理</h1>
          <p className="text-gray-600 text-sm mt-1">建立與維護講道系列與所屬講道編號。</p>
        </header>

        {feedback && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">
            {feedback}
          </div>
        )}
        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <form className="grid gap-4" onSubmit={handleSubmit}>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="flex flex-col">
              <span className="text-sm font-medium text-gray-700">系列代號</span>
              <input
                type="text"
                value={form.id}
                onChange={handleChange("id")}
                className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </label>
            <label className="flex flex-col">
              <span className="text-sm font-medium text-gray-700">標題</span>
              <input
                type="text"
                value={form.title}
                onChange={handleChange("title")}
                className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
          </div>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">主題標籤 (以逗號分隔)</span>
            <input
              type="text"
              value={form.topics}
              onChange={handleChange("topics")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">摘要</span>
            <textarea
              value={form.summary}
              onChange={handleChange("summary")}
              className="mt-1 min-h-[120px] rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">重點 (支援多行)</span>
            <textarea
              value={form.keypoints}
              onChange={handleChange("keypoints")}
              className="mt-1 min-h-[120px] rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>

          <div className="space-y-3">
            <span className="text-sm font-medium text-gray-700">講道編號</span>
            <div className="flex flex-col gap-2 md:flex-row md:items-center">
              <input
                type="text"
                value={newSermon}
                onChange={(event) => setNewSermon(event.target.value)}
                placeholder="例如：S 200426"
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    handleAddSermon();
                  }
                }}
                className="rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                type="button"
                onClick={handleAddSermon}
                className="inline-flex items-center rounded-md border border-blue-200 bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
              >
                新增講道
              </button>
            </div>
            {form.sermons.length > 0 ? (
              <ul className="flex flex-wrap gap-2">
                {form.sermons.map((sermon) => (
                  <li key={sermon} className="group inline-flex items-center gap-2 rounded-full border border-gray-300 px-3 py-1 text-sm text-gray-700">
                    <span>{sermon}</span>
                    <button
                      type="button"
                      onClick={() => handleRemoveSermon(sermon)}
                      className="text-xs text-gray-400 transition group-hover:text-red-600"
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-500">尚未加入任何講道。</p>
            )}
          </div>

          <div className="flex justify-end gap-2">
            {editingId != null && (
              <button
                type="button"
                onClick={resetForm}
                className="inline-flex items-center rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-100"
              >
                取消編輯
              </button>
            )}
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {saving ? "儲存中..." : editingId != null ? "更新系列" : "新增系列"}
            </button>
          </div>
        </form>
      </section>

      <section className="bg-white border border-gray-200 rounded-xl shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">系列代號</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">標題</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">講道數量</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {state.status === "loading" ? (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-sm text-gray-500">
                    載入中...
                  </td>
                </tr>
              ) : state.status === "error" ? (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-sm text-red-600">
                    {state.error ?? "載入資料時發生錯誤"}
                  </td>
                </tr>
              ) : sortedEntries.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-sm text-gray-500">
                    目前尚未建立講道系列。
                  </td>
                </tr>
              ) : (
                sortedEntries.map((entry) => (
                  <tr key={entry.id} className="hover:bg-blue-50 transition">
                    <td className="px-4 py-3 text-sm text-gray-700">{entry.id}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.title ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.sermons?.length ?? 0}</td>
                    <td className="px-4 py-3 text-sm">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => handleEdit(entry)}
                          className="rounded-md border border-blue-200 px-3 py-1 text-blue-600 hover:bg-blue-50"
                        >
                          編輯
                        </button>
                        <a
                          href={`/resources/series/${encodeURIComponent(entry.id)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center rounded-md border border-emerald-200 px-3 py-1 text-emerald-600 hover:bg-emerald-50"
                        >
                          觀看
                        </a>
                        <button
                          type="button"
                          onClick={() => handleDelete(entry.id)}
                          className="rounded-md border border-red-200 px-3 py-1 text-red-600 hover:bg-red-50"
                        >
                          刪除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
