"use client";

import { useState } from "react";
import {
  createSundayWorker,
  deleteSundayWorker,
  updateSundayWorker,
} from "@/app/admin/sunday-service/api";
import { SundayWorker } from "@/app/types/sundayService";

interface SundayWorkerManagerProps {
  workers: SundayWorker[];
  onWorkersChanged?: () => void;
}

export function SundayWorkerManager({ workers, onWorkersChanged }: SundayWorkerManagerProps) {
  const [newWorkerName, setNewWorkerName] = useState("");
  const [newWorkerEmail, setNewWorkerEmail] = useState("");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resetState = () => {
    setFeedback(null);
    setError(null);
  };

  const handleAdd = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = newWorkerName.trim();
    if (!trimmed) {
      setError("同工姓名不可為空");
      return;
    }
    const email = newWorkerEmail.trim();
    setSaving(true);
    resetState();
    try {
      await createSundayWorker({ name: trimmed, email: email || undefined });
      setNewWorkerName("");
      setNewWorkerEmail("");
      setFeedback("已新增同工");
      onWorkersChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "新增失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (worker: SundayWorker) => {
    setEditingName(worker.name);
    setEditingValue(worker.name);
    setEditingEmail(worker.email ?? "");
    resetState();
  };

  const cancelEdit = () => {
    setEditingName(null);
    setEditingValue("");
    setEditingEmail("");
  };

  const submitEdit = async () => {
    if (!editingName) {
      return;
    }
    const trimmed = editingValue.trim();
    if (!trimmed) {
      setError("同工姓名不可為空");
      return;
    }
    const email = editingEmail.trim();
    setSaving(true);
    resetState();
    try {
      await updateSundayWorker(editingName, { name: trimmed, email: email || undefined });
      setFeedback("已更新同工資訊");
      cancelEdit();
      onWorkersChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "更新失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!window.confirm(`確定要刪除「${name}」嗎？`)) {
      return;
    }
    setSaving(true);
    resetState();
    try {
      await deleteSundayWorker(name);
      setFeedback("已刪除同工");
      onWorkersChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "刪除失敗";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <header className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-gray-800">同工名單</h3>
      </header>
      <p className="mb-4 text-sm text-gray-600">
        編輯同工名單後，現有的主日服事安排會自動更新相同姓名。
      </p>
      <form onSubmit={handleAdd} className="mb-6 grid gap-2 sm:grid-cols-[2fr_2fr_auto]">
        <input
          className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
          placeholder="新增同工姓名"
          value={newWorkerName}
          onChange={(event) => setNewWorkerName(event.target.value)}
          disabled={saving}
        />
        <input
          className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
          placeholder="電子郵件（選填）"
          value={newWorkerEmail}
          onChange={(event) => setNewWorkerEmail(event.target.value)}
          disabled={saving}
          type="email"
        />
        <button
          type="submit"
          className="rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-300"
          disabled={saving}
        >
          新增
        </button>
      </form>
      <ul className="space-y-2">
        {workers.map((worker) => (
          <li
            key={worker.name}
            className="flex items-center justify-between rounded border border-gray-200 px-3 py-2"
          >
            {editingName === worker.name ? (
              <div className="flex w-full flex-col gap-2 lg:flex-row lg:items-center">
                <input
                  className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
                  value={editingValue}
                  onChange={(event) => setEditingValue(event.target.value)}
                  disabled={saving}
                />
                <input
                  className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
                  value={editingEmail}
                  onChange={(event) => setEditingEmail(event.target.value)}
                  disabled={saving}
                  placeholder="電子郵件（選填）"
                  type="email"
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="rounded bg-blue-600 px-3 py-1 text-sm font-semibold text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-300"
                    onClick={submitEdit}
                    disabled={saving}
                  >
                    儲存
                  </button>
                  <button
                    type="button"
                    className="rounded border border-gray-300 px-3 py-1 text-sm font-semibold text-gray-700 hover:bg-gray-100"
                    onClick={cancelEdit}
                    disabled={saving}
                  >
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex w-full items-center justify-between gap-4">
                <div className="flex flex-1 flex-col">
                  <span className="text-sm font-medium text-gray-800">{worker.name}</span>
                  {worker.email && (
                    <span className="text-xs text-gray-500">{worker.email}</span>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="rounded border border-gray-300 px-3 py-1 text-sm font-semibold text-gray-700 hover:bg-gray-100"
                    onClick={() => startEdit(worker)}
                    disabled={saving}
                  >
                    編輯
                  </button>
                  <button
                    type="button"
                    className="rounded border border-red-200 px-3 py-1 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                    onClick={() => handleDelete(worker.name)}
                    disabled={saving}
                  >
                    刪除
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
        {workers.length === 0 && (
          <li className="rounded border border-dashed border-gray-200 px-3 py-4 text-center text-sm text-gray-500">
            尚未建立同工名單，請先新增。
          </li>
        )}
      </ul>
      {(feedback || error) && (
        <p className={`mt-4 text-sm ${feedback ? "text-green-600" : "text-red-600"}`}>
          {feedback ?? error}
        </p>
      )}
    </section>
  );
}
