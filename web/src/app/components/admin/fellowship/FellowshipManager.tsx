"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import "react-quill/dist/quill.snow.css";
import {
  createFellowship,
  deleteFellowship,
  fetchFellowshipEmailContent,
  fetchFellowships,
  sendFellowshipEmail,
  updateFellowship,
  updateFellowshipEmailContent,
} from "@/app/admin/fellowship/api";
import {
  FellowshipEmailContent,
  FellowshipEntry,
} from "@/app/types/fellowship";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/components/ui/tabs";

const ReactQuill = dynamic(() => import("react-quill"), { ssr: false });

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

const emptyEmailContent: FellowshipEmailContent = {
  subject: "",
  html: "",
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

function looksLikeHtmlDocument(value: string): boolean {
  return /<!doctype html|<html[\s>]|<head[\s>]|<body[\s>]/i.test(value);
}

export function FellowshipManager() {
  const [state, setState] = useState<FetchState>({ status: "idle", data: [] });
  const [form, setForm] = useState<FormState>(emptyForm);
  const [activeTab, setActiveTab] = useState("details");
  const [editingSequence, setEditingSequence] = useState<number | null>(null);
  const [editingDate, setEditingDate] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [emailContent, setEmailContent] = useState<FellowshipEmailContent>(emptyEmailContent);
  const [emailLoading, setEmailLoading] = useState(false);
  const [emailSaving, setEmailSaving] = useState(false);
  const [emailSending, setEmailSending] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);

  const emailEditorModules = useMemo(
    () => ({
      toolbar: [
        [{ header: [1, 2, false] }],
        ["bold", "italic", "underline", "strike"],
        [{ list: "ordered" }, { list: "bullet" }],
        ["link", "blockquote"],
        ["clean"],
      ],
    }),
    [],
  );

  const emailEditorFormats = useMemo(
    () => [
      "header",
      "bold",
      "italic",
      "underline",
      "strike",
      "list",
      "bullet",
      "link",
      "blockquote",
    ],
    [],
  );

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

    return [...entries].sort((a, b) => parseDate(b.date) - parseDate(a.date));
  }, [entries]);

  const currentEntry = useMemo(
    () => entries.find((entry) => entry.date === editingDate) ?? null,
    [entries, editingDate],
  );

  const syncEntryEmailContent = useCallback((date: string, content: FellowshipEmailContent) => {
    setState((prev) => ({
      ...prev,
      data: prev.data.map((entry) =>
        entry.date === date
          ? {
              ...entry,
              emailSubject: content.subject,
              emailBodyHtml: content.html,
            }
          : entry,
      ),
    }));
  }, []);

  const resetForm = useCallback(() => {
    setForm(emptyForm);
    setEditingSequence(null);
    setEditingDate(null);
    setEmailContent(emptyEmailContent);
    setEmailError(null);
    setEmailLoading(false);
  }, []);

  const handleChange =
    (field: keyof FormState) => (event: React.ChangeEvent<HTMLInputElement>) => {
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

  useEffect(() => {
    if (!editingDate) {
      setEmailContent(emptyEmailContent);
      setEmailError(null);
      setEmailLoading(false);
      return;
    }

    let cancelled = false;
    setEmailLoading(true);
    setEmailError(null);
    setEmailContent({
      subject: currentEntry?.emailSubject ?? "",
      html: currentEntry?.emailBodyHtml ?? "",
    });

    fetchFellowshipEmailContent(editingDate)
      .then((content) => {
        if (cancelled) {
          return;
        }
        setEmailContent(content);
        syncEntryEmailContent(editingDate, content);
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        const message = err instanceof Error ? err.message : "載入團契 email 內容失敗";
        setEmailError(message);
      })
      .finally(() => {
        if (!cancelled) {
          setEmailLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [editingDate, syncEntryEmailContent]);

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

  const handleEmailSubjectChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    setEmailContent((prev) => ({ ...prev, subject: value }));
  };

  const handleEmailEditorChange = useCallback((html: string) => {
    setEmailContent((prev) => ({ ...prev, html }));
  }, []);

  const getCurrentEmailPayload = useCallback((): FellowshipEmailContent => {
    const html = emailContent.html.trim();
    const subject = emailContent.subject.trim();
    return { subject, html };
  }, [emailContent.html, emailContent.subject]);

  const handleEmailSave = async () => {
    if (!editingDate) {
      setEmailError("請先選擇既有團契資料");
      return;
    }
    const payload = getCurrentEmailPayload();
    if (!payload.subject) {
      setEmailError("請輸入 email 主旨");
      return;
    }
    if (!payload.html) {
      setEmailError("請輸入 email 內容");
      return;
    }

    setEmailSaving(true);
    setEmailError(null);
    setFeedback(null);
    try {
      const saved = await updateFellowshipEmailContent(editingDate, payload);
      setEmailContent(saved);
      syncEntryEmailContent(editingDate, saved);
      setFeedback("已儲存團契 email 內容");
    } catch (err) {
      const message = err instanceof Error ? err.message : "儲存團契 email 內容失敗";
      setEmailError(message);
    } finally {
      setEmailSaving(false);
    }
  };

  const handleEmailSend = async () => {
    if (!editingDate) {
      setEmailError("請先選擇既有團契資料");
      return;
    }
    const payload = getCurrentEmailPayload();
    if (!payload.subject) {
      setEmailError("請輸入 email 主旨");
      return;
    }
    if (!payload.html) {
      setEmailError("請輸入 email 內容");
      return;
    }
    if (!window.confirm("確定要發送此團契通知 email 嗎？")) {
      return;
    }

    setEmailSending(true);
    setEmailError(null);
    setFeedback(null);
    try {
      const saved = await updateFellowshipEmailContent(editingDate, payload);
      setEmailContent(saved);
      syncEntryEmailContent(editingDate, saved);
      const result = await sendFellowshipEmail(editingDate);
      const suffix = result.dryRun ? "（測試模式）" : "";
      setFeedback(`已發送團契 email 給 ${result.recipients.length} 位收件人${suffix}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "發送團契 email 失敗";
      setEmailError(message);
    } finally {
      setEmailSending(false);
    }
  };

  const previewInFrame = looksLikeHtmlDocument(emailContent.html);

  return (
    <div className="space-y-8">
      <section className="space-y-6 rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <header>
          <h1 className="text-2xl font-semibold text-gray-900">團契資料管理</h1>
          <p className="mt-1 text-sm text-gray-600">
            維護雙週團契日期、主題、主領與系列資訊，並為既有團契準備通知 email。
          </p>
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

        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList className="grid w-full grid-cols-2 md:inline-flex md:w-auto">
            <TabsTrigger value="details">團契資料管理</TabsTrigger>
            <TabsTrigger value="email">團契 Email 通訊</TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="mt-0">
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
              <div className="flex justify-end gap-2 md:col-span-2">
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
          </TabsContent>

          <TabsContent value="email" className="mt-0">
            <div className="space-y-4 rounded-xl border border-purple-200 bg-purple-50/40 p-6 shadow-sm">
              <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-semibold text-purple-950">團契 Email 通訊</h2>
                  <p className="mt-1 text-sm text-purple-700">
                    {editingDate
                      ? `目前編輯 ${editingDate} 的通知內容，可儲存後直接發送。`
                      : "請先從下方表格選擇一筆既有團契資料，再編輯並發送通知 email。"}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleEmailSave}
                    disabled={!editingDate || emailSaving || emailLoading || emailSending}
                    className="rounded-md border border-purple-300 px-4 py-2 text-sm font-semibold text-purple-700 hover:bg-purple-100 disabled:cursor-not-allowed disabled:border-purple-200 disabled:text-purple-300"
                  >
                    {emailSaving ? "儲存中…" : "儲存 Email"}
                  </button>
                  <button
                    type="button"
                    onClick={handleEmailSend}
                    disabled={!editingDate || emailSaving || emailLoading || emailSending}
                    className="rounded-md bg-purple-600 px-4 py-2 text-sm font-semibold text-white hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-purple-300"
                  >
                    {emailSending ? "發送中…" : "發送 Email"}
                  </button>
                </div>
              </header>

              {emailError && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {emailError}
                </div>
              )}

              <label className="flex flex-col">
                <span className="text-sm font-medium text-purple-900">Email 主旨</span>
                <input
                  type="text"
                  value={emailContent.subject}
                  onChange={handleEmailSubjectChange}
                  disabled={!editingDate || emailLoading}
                  className="mt-1 rounded-md border border-purple-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-300 disabled:bg-gray-100"
                  placeholder="輸入團契通知主旨"
                />
              </label>

              <div className="space-y-3">
                <div className="rounded border border-purple-200 bg-white">
                  {editingDate ? (
                    <ReactQuill
                      theme="snow"
                      value={emailContent.html}
                      onChange={handleEmailEditorChange}
                      modules={emailEditorModules}
                      formats={emailEditorFormats}
                      className="min-h-[260px]"
                    />
                  ) : (
                    <div className="flex min-h-[220px] items-center justify-center px-6 py-10 text-sm text-gray-500">
                      請先從下方表格選擇一筆既有團契資料，再編輯 email 內容。
                    </div>
                  )}
                </div>
                {emailLoading && <p className="text-xs text-purple-500">Email 內容載入中…</p>}
                <p className="text-xs text-gray-500">
                  內容會以 HTML 儲存與寄送，工具列可快速套用基本格式與連結。
                </p>
              </div>

              <div className="space-y-3 rounded-lg border border-purple-100 bg-white p-4">
                <h3 className="text-sm font-semibold text-purple-900">預覽</h3>
                {previewInFrame ? (
                  <iframe
                    title="Fellowship email preview"
                    srcDoc={emailContent.html}
                    className="min-h-[320px] w-full rounded border border-purple-100 bg-white"
                    sandbox=""
                  />
                ) : (
                  <div className="min-h-[220px] rounded border border-purple-100 bg-white p-4">
                    <div
                      className="prose max-w-none text-sm"
                      dangerouslySetInnerHTML={{ __html: emailContent.html }}
                    />
                  </div>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  日期
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  主講
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  主題
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  系列
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  序號
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  Email
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {sortedEntries.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">
                    尚未建立任何團契資訊。
                  </td>
                </tr>
              ) : (
                sortedEntries.map((entry, index) => (
                  <tr
                    key={entry.date || `${entry.title ?? ""}-${index}`}
                    className="transition hover:bg-blue-50"
                  >
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.date}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.host ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.title}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">{entry.series ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-700">{entry.sequence ?? ""}</td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      {entry.emailSubject || entry.emailBodyHtml ? "已自訂" : "使用預設"}
                    </td>
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
