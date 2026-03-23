"use client";

import { useEffect, useState } from "react";
import {
  createMicroSermon,
  deleteMicroSermon,
  listMicroSermons,
  updateMicroSermon,
} from "@/app/admin/micro-sermon/api";
import type { MicroSermon } from "@/app/types/microSermon";

interface FormState {
  id?: string;
  title: string;
  series: string;
  seriesNumber: string;
  youtubeUrl: string;
  intro: string;
  description: string;
  tag: string;
  isFeatured: boolean;
  publishedAt: string;
}

type StatusType = "success" | "error" | "info";

const emptyForm: FormState = {
  id: "",
  title: "",
  series: "",
  seriesNumber: "",
  youtubeUrl: "",
  intro: "",
  description: "",
  tag: "",
  isFeatured: false,
  publishedAt: "",
};

export function MicroSermonManager() {
  const [sermons, setSermons] = useState<MicroSermon[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formState, setFormState] = useState<FormState>({ ...emptyForm });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [status, setStatus] = useState<{ type: StatusType; message: string } | null>(null);

  useEffect(() => {
    setStatus(null);
    void loadSermons();
  }, []);

  async function loadSermons() {
    setLoading(true);
    try {
      const data = await listMicroSermons();
      setSermons(data);
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "無法載入微講道列表",
      });
    } finally {
      setLoading(false);
    }
  }

  function resetForm() {
    setFormState({ ...emptyForm });
    setEditingId(null);
  }

  function handleEdit(sermon: MicroSermon) {
    setEditingId(sermon.id);
    setFormState({
      id: sermon.id,
      title: sermon.title,
      series: sermon.series ?? "",
      seriesNumber: sermon.seriesNumber?.toString() ?? "",
      youtubeUrl: sermon.youtubeUrl ?? "",
      intro: sermon.intro ?? "",
      description: sermon.description ?? "",
      tag: sermon.tag ?? "",
      isFeatured: sermon.isFeatured ?? false,
      publishedAt: sermon.publishedAt ?? "",
    });
    setStatus({ type: "info", message: `正在編輯：${sermon.title}` });
  }

  async function handleDelete(sermon: MicroSermon) {
    if (!confirm(`確定要刪除「${sermon.title}」？`)) {
      return;
    }
    setStatus(null);
    try {
      await deleteMicroSermon(sermon.id);
      setStatus({ type: "success", message: "視頻已刪除" });
      if (editingId === sermon.id) {
        resetForm();
      }
      await loadSermons();
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "刪除視頻時發生錯誤",
      });
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setStatus(null);

    try {
      const payload = {
        id: formState.id?.trim() || undefined,
        title: formState.title.trim(),
        series: formState.series.trim() || undefined,
        seriesNumber: formState.seriesNumber ? parseInt(formState.seriesNumber, 10) : undefined,
        youtubeUrl: formState.youtubeUrl.trim() || undefined,
        intro: formState.intro.trim() || undefined,
        description: formState.description.trim() || undefined,
        tag: formState.tag.trim() || undefined,
        isFeatured: formState.isFeatured,
        publishedAt: formState.publishedAt.trim() || undefined,
      };

      if (!payload.title) {
        throw new Error("請輸入視頻標題");
      }

      if (editingId) {
        const { id: _omit, ...updatePayload } = payload;
        await updateMicroSermon(editingId, updatePayload);
        setStatus({ type: "success", message: "視頻已更新" });
      } else {
        await createMicroSermon(payload);
        setStatus({ type: "success", message: "視頻已新增" });
      }

      await loadSermons();
      resetForm();
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "儲存視頻時發生錯誤",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold text-gray-900">微講道管理</h1>
        <p className="text-gray-600">
          管理微講道視頻內容，包括標題、系列、YouTube 連結和描述文字。
        </p>
      </header>

      {status && (
        <div
          className={`rounded-md border px-4 py-3 text-sm ${
            status.type === "success"
              ? "border-green-200 bg-green-50 text-green-700"
              : status.type === "error"
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-blue-200 bg-blue-50 text-blue-700"
          }`}
        >
          {status.message}
        </div>
      )}

      <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-gray-900">
          {editingId ? "編輯視頻" : "新增視頻"}
        </h2>
        <form className="mt-4 space-y-5" onSubmit={handleSubmit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                視頻標題<span className="ml-1 text-red-500">*</span>
              </label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.title}
                onChange={(e) => setFormState((p) => ({ ...p, title: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">
                自訂代號 (可選)
              </label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring disabled:cursor-not-allowed disabled:bg-gray-100"
                value={formState.id ?? ""}
                onChange={(e) => setFormState((p) => ({ ...p, id: e.target.value }))}
                disabled={Boolean(editingId)}
                placeholder="未填寫則依標題自動產生"
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">發佈日期</label>
              <input
                type="date"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.publishedAt}
                onChange={(e) => setFormState((p) => ({ ...p, publishedAt: e.target.value }))}
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">系列名稱</label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.series}
                onChange={(e) => setFormState((p) => ({ ...p, series: e.target.value }))}
                placeholder="例如：祷告系列"
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">系列集數</label>
              <input
                type="number"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.seriesNumber}
                onChange={(e) => setFormState((p) => ({ ...p, seriesNumber: e.target.value }))}
                placeholder="例如：1"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                YouTube 網址
              </label>
              <input
                type="url"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.youtubeUrl}
                onChange={(e) => setFormState((p) => ({ ...p, youtubeUrl: e.target.value }))}
                placeholder="https://www.youtube.com/watch?v=..."
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">標籤</label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.tag}
                onChange={(e) => setFormState((p) => ({ ...p, tag: e.target.value }))}
                placeholder="例如：讲道、释经"
              />
            </div>
            <div className="flex items-center gap-3 pt-6">
              <input
                id="isFeatured"
                type="checkbox"
                className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                checked={formState.isFeatured}
                onChange={(e) => setFormState((p) => ({ ...p, isFeatured: e.target.checked }))}
              />
              <label htmlFor="isFeatured" className="text-sm font-semibold text-gray-700">
                設為精選（在首頁顯示）
              </label>
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                簡短引言
              </label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.intro}
                onChange={(e) => setFormState((p) => ({ ...p, intro: e.target.value }))}
                placeholder="1-2 句簡述"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                描述 / 簡介
              </label>
              <textarea
                className="mt-2 h-32 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.description}
                onChange={(e) => setFormState((p) => ({ ...p, description: e.target.value }))}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? "儲存中…" : editingId ? "更新視頻" : "新增視頻"}
            </button>
            {editingId && (
              <button
                type="button"
                onClick={resetForm}
                className="inline-flex items-center rounded-md border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 shadow-sm transition hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1"
              >
                取消編輯
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-gray-900">現有視頻</h2>
          <button
            type="button"
            onClick={resetForm}
            className="text-sm font-semibold text-blue-600 hover:text-blue-700"
          >
            新增新的視頻
          </button>
        </div>
        {loading ? (
          <div className="mt-6 rounded-md border border-dashed border-gray-300 px-4 py-12 text-center text-gray-500">
            正在載入視頻列表…
          </div>
        ) : sermons.length === 0 ? (
          <div className="mt-6 rounded-md border border-dashed border-gray-300 px-4 py-12 text-center text-gray-500">
            目前尚未建立任何微講道視頻。
          </div>
        ) : (
          <div className="mt-6 overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm text-gray-700">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 font-semibold text-gray-600">標題</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">系列</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">標籤</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">精選</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">發佈日期</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {sermons.map((sermon) => (
                  <tr key={sermon.id} className={editingId === sermon.id ? "bg-blue-50/40" : ""}>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-gray-900">{sermon.title}</div>
                      <div className="mt-1 text-xs text-gray-500">ID：{sermon.id}</div>
                    </td>
                    <td className="px-4 py-3">
                      {sermon.series
                        ? `${sermon.series}${sermon.seriesNumber ? ` ${String(sermon.seriesNumber).padStart(2, "0")}` : ""}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {sermon.tag ? (
                        <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-700">
                          {sermon.tag}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {sermon.isFeatured ? (
                        <span className="inline-flex rounded-full bg-green-100 px-2 py-0.5 text-xs font-semibold text-green-700">
                          ✓ 精選
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {sermon.publishedAt
                        ? new Date(sermon.publishedAt).toLocaleDateString("zh-TW")
                        : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <button
                          type="button"
                          onClick={() => handleEdit(sermon)}
                          className="inline-flex items-center justify-center rounded-md border border-blue-200 px-3 py-1 text-sm font-semibold text-blue-600 transition hover:bg-blue-50"
                        >
                          編輯
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDelete(sermon)}
                          className="inline-flex items-center justify-center rounded-md border border-red-200 px-3 py-1 text-sm font-semibold text-red-600 transition hover:bg-red-50"
                        >
                          刪除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
