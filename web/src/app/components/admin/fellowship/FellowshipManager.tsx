"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchFellowships,
  createFellowship,
  updateFellowship,
  deleteFellowship,
} from "@/app/admin/fellowship/api";
import { FellowshipEntry } from "@/app/types/fellowship";

interface FormState {
  sequence: string;
  date: string;
  host: string;
  title: string;
  series: string;
}

type FetchState =
  | { status: "idle" | "loading"; data: FellowshipEntry[] }
  | { status: "ready"; data: FellowshipEntry[] }
  | { status: "error"; data: FellowshipEntry[]; error: string };

const emptyForm: FormState = {
  sequence: "",
  date: "",
  host: "",
  title: "",
  series: "",
};

const toIsoDate = (value: string) => {
  const parts = value.split("/").map((part) => part.trim());
  if (parts.length !== 3) {
    return "";
  }
  const [month, day, year] = parts.map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(month) || Number.isNaN(day) || Number.isNaN(year)) {
    return "";
  }
  return `${year.toString().padStart(4, "0")}-${month
    .toString()
    .padStart(2, "0")}-${day.toString().padStart(2, "0")}`;
};

const toDisplayDate = (value: string) => {
  if (!value) {
    return "";
  }
  const [year, month, day] = value.split("-").map((part) => Number.parseInt(part, 10));
  if (Number.isNaN(year) || Number.isNaN(month) || Number.isNaN(day)) {
    return "";
  }
  return `${month.toString().padStart(2, "0")}/${day
    .toString()
    .padStart(2, "0")}/${year.toString().padStart(4, "0")}`;
};

const isFridayIsoDate = (value: string) => {
  if (!value) {
    return false;
  }
  const [yearStr, monthStr, dayStr] = value.split("-");
  const year = Number.parseInt(yearStr ?? "", 10);
  const month = Number.parseInt(monthStr ?? "", 10);
  const day = Number.parseInt(dayStr ?? "", 10);
  if (Number.isNaN(year) || Number.isNaN(month) || Number.isNaN(day)) {
    return false;
  }
  const selectedDate = new Date(year, month - 1, day);
  return !Number.isNaN(selectedDate.getTime()) && selectedDate.getDay() === 5;
};

export function FellowshipManager() {
  const [state, setState] = useState<FetchState>({ status: "idle", data: [] });
  const [form, setForm] = useState<FormState>(emptyForm);
  const [editingSequence, setEditingSequence] = useState<number | null>(null);
  const [editingDate, setEditingDate] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState((prev) => ({ status: "loading", data: prev.data }));
    try {
      const entries = await fetchFellowships();
      setState({ status: "ready", data: entries });
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入團契資料失敗";
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
    const parseDate = (value: string) => {
      const timestamp = Date.parse(value);
      return Number.isNaN(timestamp) ? 0 : timestamp;
    };

    return [...entries]
      .sort((a, b) => parseDate(b.date) - parseDate(a.date))
  }, [entries]);

  const resetForm = () => {
    setForm(emptyForm);
    setEditingSequence(null);
    setEditingDate(null);
  };

  const handleChange = (field: keyof FormState) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setForm((prev) => ({ ...prev, [field]: value }));
    };

  const handleDateChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    let warning = "";

    if (value && !isFridayIsoDate(value)) {
      warning = "聚會日期須為週五";
    }

    event.target.setCustomValidity(warning);
    if (warning) {
      event.target.reportValidity();
    }

    setForm((prev) => ({ ...prev, date: value }));
  };

  const handleEdit = (entry: FellowshipEntry) => {
    setEditingSequence(entry.sequence ?? null);
    setEditingDate(entry.date);
    setForm({
      sequence: entry.sequence != null ? entry.sequence.toString() : "",
      date: toIsoDate(entry.date),
      host: entry.host ?? "",
      title: entry.title ?? "",
      series: entry.series ?? "",
    });
    setFeedback(null);
    setError(null);
  };

  const handleDelete = async (date: string) => {
    if (!date) {
      window.alert("此團契資訊缺少日期，無法刪除。");
      return;
    }
    if (!window.confirm("確定要刪除此團契資訊嗎？")) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    setError(null);
    try {
      await deleteFellowship(date);
      await load();
      if (editingDate === date) {
        resetForm();
      }
      setFeedback("已刪除團契資訊");
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

    const sequenceInput = form.sequence.trim();
    const parsedSequence = sequenceInput === "" ? null : Number.parseInt(sequenceInput, 10);
    if (sequenceInput !== "" && Number.isNaN(parsedSequence)) {
      setError("序號必須為數字");
      setSaving(false);
      return;
    }
    const trimmedDate = form.date.trim();
    if (!isFridayIsoDate(trimmedDate)) {
      setError("請選擇有效的週五日期");
      setSaving(false);
      return;
    }

    const formattedDate = toDisplayDate(trimmedDate);
    const effectiveSequence =
      parsedSequence != null ? parsedSequence : editingSequence != null ? editingSequence : null;

    const payload: FellowshipEntry = {
      date: formattedDate,
      host: form.host.trim(),
      title: form.title.trim(),
      series: form.series.trim(),
      ...(effectiveSequence != null ? { sequence: effectiveSequence } : {}),
    };

    try {
      if (editingDate != null) {
        await updateFellowship(editingDate, payload);
        setFeedback("已更新團契資訊");
      } else {
        await createFellowship(payload);
        setFeedback("已新增團契資訊");
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
          <h1 className="text-2xl font-semibold text-gray-900">團契資料管理</h1>
          <p className="text-gray-600 text-sm mt-1">維護雙週團契日期、主題、主領與系列資訊。</p>
        </header>

        {feedback && <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">{feedback}</div>}
        {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <form className="grid gap-4 md:grid-cols-2" onSubmit={handleSubmit}>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">聚會日期</span>
            <input
              type="date"
              value={form.date}
              onChange={handleDateChange}
              disabled={editingDate != null}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </label>
          <label className="flex flex-col">
            <span className="text-sm font-medium text-gray-700">主講</span>
            <input
              type="text"
              value={form.host}
              onChange={handleChange("host")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </label>
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">主題</span>
            <input
              type="text"
              value={form.title}
              onChange={handleChange("title")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </label>
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">系列</span>
            <input
              type="text"
              value={form.series}
              onChange={handleChange("series")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </label>
          <label className="flex flex-col md:col-span-2">
            <span className="text-sm font-medium text-gray-700">序號 (顯示順序)</span>
            <input
              type="number"
              min={1}
              value={form.sequence}
              onChange={handleChange("sequence")}
              className="mt-1 rounded-md border border-gray-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <div className="md:col-span-2 flex justify-end gap-2">
            {editingDate != null && (
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
              {saving ? "儲存中..." : editingDate != null ? "更新資料" : "新增團契"}
            </button>
          </div>
        </form>
      </section>

      <section className="bg-white border border-gray-200 rounded-xl shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">日期</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">主講</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">主題</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">系列</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">序號</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {sortedEntries.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-500">
                    尚未建立任何團契資訊。
                  </td>
                </tr>
              ) : (
                sortedEntries.map((entry, index) => (
                  <tr
                    key={entry.date || `${entry.title ?? ""}-${index}`}
                    className="hover:bg-blue-50 transition"
                  >
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.date}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.host ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.title}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.series ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-700">{entry.sequence ?? ""}</td>
                    <td className="px-4 py-3 text-sm">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => handleEdit(entry)}
                          className="rounded-md border border-blue-200 px-3 py-1 text-blue-600 hover:bg-blue-50"
                        >
                          編輯
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(entry.date)}
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
