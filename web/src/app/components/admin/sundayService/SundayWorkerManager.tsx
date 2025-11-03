"use client";

import { useState } from "react";
import {
  createSundayWorker,
  deleteSundayWorker,
  updateSundayWorker,
} from "@/app/admin/sunday-service/api";
import { SundayWorker, UnavailableDateRange } from "@/app/types/sundayService";

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

interface SundayWorkerManagerProps {
  workers: SundayWorker[];
  onWorkersChanged?: () => void;
}

const sortRanges = (ranges: UnavailableDateRange[]) =>
  [...ranges].sort((a, b) => {
    const startCompare = a.startDate.localeCompare(b.startDate);
    if (startCompare !== 0) {
      return startCompare;
    }
    return a.endDate.localeCompare(b.endDate);
  });

const formatRangeLabel = (range: UnavailableDateRange) =>
  range.startDate === range.endDate ? range.startDate : `${range.startDate} 至 ${range.endDate}`;

const rangesEqual = (a: UnavailableDateRange, b: UnavailableDateRange) =>
  a.startDate === b.startDate && a.endDate === b.endDate;

const resolveWorkerRanges = (worker: SundayWorker): UnavailableDateRange[] => {
  if (worker.unavailableRanges && worker.unavailableRanges.length > 0) {
    return sortRanges(worker.unavailableRanges);
  }
  const legacyDates = (worker as { unavailableDates?: string[] }).unavailableDates;
  if (legacyDates && legacyDates.length > 0) {
    const ranges = legacyDates
      .map((value) => (typeof value === "string" ? value.trim() : ""))
      .filter((value): value is string => value.length > 0)
      .map((value) => ({ startDate: value, endDate: value }));
    return sortRanges(ranges);
  }
  return [];
};

export function SundayWorkerManager({ workers, onWorkersChanged }: SundayWorkerManagerProps) {
  const [newWorkerName, setNewWorkerName] = useState("");
  const [newWorkerEmail, setNewWorkerEmail] = useState("");
  const [newWorkerUnavailableRanges, setNewWorkerUnavailableRanges] = useState<
    UnavailableDateRange[]
  >([]);
  const [newRangeStart, setNewRangeStart] = useState("");
  const [newRangeEnd, setNewRangeEnd] = useState("");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [editingUnavailableRanges, setEditingUnavailableRanges] = useState<
    UnavailableDateRange[]
  >([]);
  const [editingRangeStart, setEditingRangeStart] = useState("");
  const [editingRangeEnd, setEditingRangeEnd] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resetState = () => {
    setFeedback(null);
    setError(null);
  };

  const parseDateInput = (value: string): string | null => {
    const trimmed = value.trim();
    if (!trimmed) {
      setError("請輸入日期");
      return null;
    }
    if (!DATE_PATTERN.test(trimmed)) {
      setError("日期格式需為 YYYY-MM-DD");
      return null;
    }
    return trimmed;
  };

  const normalizeRangeInputs = (startValue: string, endValue: string): UnavailableDateRange | null => {
    const start = parseDateInput(startValue);
    if (!start) {
      return null;
    }
    const end = parseDateInput(endValue);
    if (!end) {
      return null;
    }
    if (end < start) {
      setError("結束日期不可早於開始日期");
      return null;
    }
    return { startDate: start, endDate: end };
  };

  const handleAddNewUnavailableRange = () => {
    resetState();
    const range = normalizeRangeInputs(newRangeStart, newRangeEnd);
    if (!range) {
      return;
    }
    if (newWorkerUnavailableRanges.some((existing) => rangesEqual(existing, range))) {
      setError("日期區間已在清單中");
      return;
    }
    setNewWorkerUnavailableRanges(sortRanges([...newWorkerUnavailableRanges, range]));
    setNewRangeStart("");
    setNewRangeEnd("");
  };

  const handleRemoveNewUnavailableRange = (range: UnavailableDateRange) => {
    resetState();
    setNewWorkerUnavailableRanges((prev) =>
      prev.filter((value) => !rangesEqual(value, range)),
    );
  };

  const handleAddEditingUnavailableRange = () => {
    resetState();
    const range = normalizeRangeInputs(editingRangeStart, editingRangeEnd);
    if (!range) {
      return;
    }
    if (editingUnavailableRanges.some((existing) => rangesEqual(existing, range))) {
      setError("日期區間已在清單中");
      return;
    }
    setEditingUnavailableRanges((prev) => sortRanges([...prev, range]));
    setEditingRangeStart("");
    setEditingRangeEnd("");
  };

  const handleRemoveEditingUnavailableRange = (range: UnavailableDateRange) => {
    resetState();
    setEditingUnavailableRanges((prev) => prev.filter((value) => !rangesEqual(value, range)));
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
      await createSundayWorker({
        name: trimmed,
        email: email || undefined,
        unavailableRanges: newWorkerUnavailableRanges,
      });
      setNewWorkerName("");
      setNewWorkerEmail("");
      setNewWorkerUnavailableRanges([]);
      setNewRangeStart("");
      setNewRangeEnd("");
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
    setEditingUnavailableRanges(resolveWorkerRanges(worker));
    setEditingRangeStart("");
    setEditingRangeEnd("");
    resetState();
  };

  const cancelEdit = () => {
    setEditingName(null);
    setEditingValue("");
    setEditingEmail("");
    setEditingUnavailableRanges([]);
    setEditingRangeStart("");
    setEditingRangeEnd("");
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
      await updateSundayWorker(editingName, {
        name: trimmed,
        email: email || undefined,
        unavailableRanges: editingUnavailableRanges,
      });
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
        <div className="sm:col-span-3 rounded border border-dashed border-gray-300 px-3 py-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">不可服事日期區間</span>
            <span className="text-xs text-gray-400">點擊 × 移除</span>
          </div>
          <div className="mb-3 flex flex-wrap gap-2">
            {newWorkerUnavailableRanges.length === 0 ? (
              <span className="text-xs text-gray-500">尚未設定</span>
            ) : (
              newWorkerUnavailableRanges.map((range, index) => (
                <span
                  key={`new-unavailable-${range.startDate}-${range.endDate}-${index}`}
                  className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-1 text-xs text-gray-700"
                >
                  {formatRangeLabel(range)}
                  <button
                    type="button"
                    className="text-gray-500 hover:text-red-500"
                    onClick={() => handleRemoveNewUnavailableRange(range)}
                    disabled={saving}
                    aria-label={`移除 ${formatRangeLabel(range)}`}
                  >
                    ×
                  </button>
                </span>
              ))
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="date"
              className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
              value={newRangeStart}
              onChange={(event) => setNewRangeStart(event.target.value)}
              disabled={saving}
              aria-label="不可服事開始日期"
            />
            <span className="text-xs text-gray-500">至</span>
            <input
              type="date"
              className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
              value={newRangeEnd}
              onChange={(event) => setNewRangeEnd(event.target.value)}
              disabled={saving}
              aria-label="不可服事結束日期"
            />
            <button
              type="button"
              className="rounded border border-blue-300 px-3 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
              onClick={handleAddNewUnavailableRange}
              disabled={saving}
            >
              加入日期區間
            </button>
          </div>
        </div>
      </form>
      <ul className="space-y-2">
        {workers.map((worker) => {
          const unavailableRanges = resolveWorkerRanges(worker);
          return (
            <li
              key={worker.name}
              className="rounded border border-gray-200 px-3 py-2"
            >
              {editingName === worker.name ? (
                <div className="flex w-full flex-col gap-3">
                  <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
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
                  <div className="rounded border border-dashed border-gray-300 px-3 py-3">
                    <div className="mb-2 flex items-center justify-between">
                      <span className="text-xs font-semibold text-gray-600">不可服事日期區間</span>
                      <span className="text-xs text-gray-400">點擊 × 移除</span>
                    </div>
                    <div className="mb-3 flex flex-wrap gap-2">
                      {editingUnavailableRanges.length === 0 ? (
                        <span className="text-xs text-gray-500">尚未設定</span>
                      ) : (
                        editingUnavailableRanges.map((range, index) => (
                          <span
                            key={`edit-unavailable-${range.startDate}-${range.endDate}-${index}`}
                            className="inline-flex items-center gap-1 rounded bg-gray-100 px-2 py-1 text-xs text-gray-700"
                          >
                            {formatRangeLabel(range)}
                            <button
                              type="button"
                              className="text-gray-500 hover:text-red-500"
                              onClick={() => handleRemoveEditingUnavailableRange(range)}
                              disabled={saving}
                              aria-label={`移除 ${formatRangeLabel(range)}`}
                            >
                              ×
                            </button>
                          </span>
                        ))
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        type="date"
                        className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
                        value={editingRangeStart}
                        onChange={(event) => setEditingRangeStart(event.target.value)}
                        disabled={saving}
                        aria-label="不可服事開始日期"
                      />
                      <span className="text-xs text-gray-500">至</span>
                      <input
                        type="date"
                        className="rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
                        value={editingRangeEnd}
                        onChange={(event) => setEditingRangeEnd(event.target.value)}
                        disabled={saving}
                        aria-label="不可服事結束日期"
                      />
                      <button
                        type="button"
                        className="rounded border border-blue-300 px-3 py-1 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                        onClick={handleAddEditingUnavailableRange}
                        disabled={saving}
                      >
                        加入日期區間
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex w-full flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex flex-1 flex-col gap-1">
                    <span className="text-sm font-medium text-gray-800">{worker.name}</span>
                    {worker.email && (
                      <span className="text-xs text-gray-500">{worker.email}</span>
                    )}
                    {unavailableRanges.length > 0 && (
                      <span className="text-xs text-amber-600">
                        不可服事：
                        {unavailableRanges.map((range) => formatRangeLabel(range)).join("、")}
                      </span>
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
          );
        })}
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
