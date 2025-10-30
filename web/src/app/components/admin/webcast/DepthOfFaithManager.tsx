"use client";

import { useEffect, useMemo, useState } from "react";
import {
  createDepthOfFaithEpisode,
  deleteDepthOfFaithEpisode,
  listDepthOfFaithEpisodes,
  updateDepthOfFaithEpisode,
  uploadDepthOfFaithAudio,
} from "@/app/admin/webcast/api";
import type { DepthOfFaithEpisode } from "@/app/types/depthOfFaith";
import { resolveWebcastAudioUrl } from "@/app/utils/webcast";

interface FormState {
  id?: string;
  title: string;
  description: string;
  audioFilename: string;
  scripture: string;
  duration: string;
  publishedAt: string;
}

type StatusType = "success" | "error" | "info";

const emptyForm: FormState = {
  id: "",
  title: "",
  description: "",
  audioFilename: "",
  scripture: "",
  duration: "",
  publishedAt: "",
};

export function DepthOfFaithManager() {
  const [episodes, setEpisodes] = useState<DepthOfFaithEpisode[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [formState, setFormState] = useState<FormState>({ ...emptyForm });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [status, setStatus] = useState<{ type: StatusType; message: string } | null>(null);

  useEffect(() => {
    setStatus(null);
    void loadEpisodes();
  }, []);

  async function loadEpisodes() {
    setLoading(true);
    try {
      const data = await listDepthOfFaithEpisodes();
      setEpisodes(data);
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "無法載入節目列表",
      });
    } finally {
      setLoading(false);
    }
  }

  function resetForm() {
    setFormState({ ...emptyForm });
    setEditingId(null);
  }

  function handleEdit(episode: DepthOfFaithEpisode) {
    setEditingId(episode.id);
    setFormState({
      id: episode.id,
      title: episode.title,
      description: episode.description,
      audioFilename: episode.audioFilename ?? "",
      scripture: episode.scripture ?? "",
      duration: episode.duration ?? "",
      publishedAt: episode.publishedAt ?? "",
    });
    setStatus({ type: "info", message: `正在編輯：${episode.title}` });
  }

  async function handleDelete(episode: DepthOfFaithEpisode) {
    if (!confirm(`確定要刪除「${episode.title}」？`)) {
      return;
    }
    setStatus(null);
    try {
      await deleteDepthOfFaithEpisode(episode.id);
      setStatus({ type: "success", message: "節目已刪除" });
      if (editingId === episode.id) {
        resetForm();
      }
      await loadEpisodes();
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "刪除節目時發生錯誤",
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
        description: formState.description.trim(),
        audioFilename: formState.audioFilename.trim() || undefined,
        scripture: formState.scripture.trim() || undefined,
        duration: formState.duration.trim() || undefined,
        publishedAt: formState.publishedAt.trim() || undefined,
      };

      if (!payload.title) {
        throw new Error("請輸入節目標題");
      }
      if (!payload.description) {
        throw new Error("請輸入節目摘要");
      }
      if (editingId) {
        const { id: _omit, ...updatePayload } = payload;
        await updateDepthOfFaithEpisode(editingId, updatePayload);
        setStatus({ type: "success", message: "節目已更新" });
      } else {
        await createDepthOfFaithEpisode(payload);
        setStatus({ type: "success", message: "節目已新增" });
      }

      await loadEpisodes();
      resetForm();
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "儲存節目時發生錯誤",
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleAudioUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    setUploading(true);
    setStatus(null);
    try {
      const response = await uploadDepthOfFaithAudio(file);
      setFormState((previous) => ({
        ...previous,
        audioFilename: response.filename,
      }));
      setStatus({ type: "success", message: "音訊檔案上傳完成" });
    } catch (error) {
      setStatus({
        type: "error",
        message: error instanceof Error ? error.message : "上傳音訊時發生錯誤",
      });
    } finally {
      setUploading(false);
    }
  }

  const currentAudioPreview = useMemo(() => {
    if (!formState.audioFilename) {
      return null;
    }
    return resolveWebcastAudioUrl(formState.audioFilename) ?? null;
  }, [formState.audioFilename]);

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold text-gray-900">信仰的深度｜節目管理</h1>
        <p className="text-gray-600">
          上傳 MP3 音訊並維護節目摘要、經文與發佈日期，內容會同步更新到公開資源頁面。
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
          {editingId ? "編輯節目" : "新增節目"}
        </h2>
        <form className="mt-4 space-y-5" onSubmit={handleSubmit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                節目標題<span className="ml-1 text-red-500">*</span>
              </label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.title}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, title: event.target.value }))
                }
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">
                自訂節目代號 (可選)
              </label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring disabled:cursor-not-allowed disabled:bg-gray-100"
                value={formState.id ?? ""}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, id: event.target.value }))
                }
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
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, publishedAt: event.target.value }))
                }
              />
            </div>
            <div>
              <label className="block text-sm font-semibold text-gray-700">長度 (例如 28:45)</label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.duration}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, duration: event.target.value }))
                }
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">經文焦點</label>
              <input
                type="text"
                className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.scripture}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, scripture: event.target.value }))
                }
                placeholder="例如：羅馬書 10:17"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                節目摘要 / 簡介<span className="ml-1 text-red-500">*</span>
              </label>
              <textarea
                className="mt-2 h-32 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                value={formState.description}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, description: event.target.value }))
                }
              />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-semibold text-gray-700">
                音訊檔案 (MP3，可稍後補上)
              </label>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center">
                <input
                  type="file"
                  accept="audio/mpeg"
                  onChange={handleAudioUpload}
                  disabled={uploading}
                  className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring sm:w-auto"
                />
              </div>
              <input
                type="text"
                className="mt-3 w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring"
                placeholder="可手動輸入檔名，例如 faith-depth-001.mp3"
                value={formState.audioFilename}
                onChange={(event) =>
                  setFormState((previous) => ({ ...previous, audioFilename: event.target.value }))
                }
              />
              {currentAudioPreview && (
                <audio
                  controls
                  src={currentAudioPreview}
                  className="mt-3 w-full rounded-md border border-gray-200"
                >
                  您的瀏覽器不支援 audio 元素。
                </audio>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="submit"
              disabled={saving || uploading}
              className="inline-flex items-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? "儲存中…" : editingId ? "更新節目" : "新增節目"}
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
          <h2 className="text-xl font-semibold text-gray-900">現有節目</h2>
          <button
            type="button"
            onClick={resetForm}
            className="text-sm font-semibold text-blue-600 hover:text-blue-700"
          >
            新增新的節目
          </button>
        </div>
        {loading ? (
          <div className="mt-6 rounded-md border border-dashed border-gray-300 px-4 py-12 text-center text-gray-500">
            正在載入節目列表…
          </div>
        ) : episodes.length === 0 ? (
          <div className="mt-6 rounded-md border border-dashed border-gray-300 px-4 py-12 text-center text-gray-500">
            目前尚未建立任何節目。
          </div>
        ) : (
          <div className="mt-6 overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm text-gray-700">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 font-semibold text-gray-600">標題</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">發佈日期</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">長度</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">經文</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">音訊預覽</th>
                  <th className="px-4 py-2 font-semibold text-gray-600">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white">
                {episodes.map((episode) => (
                  <tr key={episode.id} className={editingId === episode.id ? "bg-blue-50/40" : ""}>
                    <td className="px-4 py-3">
                      <div className="font-semibold text-gray-900">{episode.title}</div>
                      <div className="mt-1 text-xs text-gray-500">ID：{episode.id}</div>
                    </td>
                    <td className="px-4 py-3">
                      {episode.publishedAt
                        ? new Date(episode.publishedAt).toLocaleDateString("zh-TW")
                        : "—"}
                    </td>
                    <td className="px-4 py-3">{episode.duration || "—"}</td>
                    <td className="px-4 py-3">{episode.scripture || "—"}</td>
                    <td className="px-4 py-3">
                      {resolveWebcastAudioUrl(episode.audioFilename) ? (
                        <audio
                          controls
                          src={resolveWebcastAudioUrl(episode.audioFilename) ?? undefined}
                          className="w-48"
                        >
                          您的瀏覽器不支援 audio 元素。
                        </audio>
                      ) : (
                        <span className="text-xs text-gray-400">尚未提供音訊</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-2 sm:flex-row">
                        <button
                          type="button"
                          onClick={() => handleEdit(episode)}
                          className="inline-flex items-center justify-center rounded-md border border-blue-200 px-3 py-1 text-sm font-semibold text-blue-600 transition hover:bg-blue-50"
                        >
                          編輯
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDelete(episode)}
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
