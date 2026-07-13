"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import "react-quill/dist/quill.snow.css";
import {
  createFellowship,
  deleteFellowship,
  fetchFellowshipAnalysisAssets,
  fetchFellowshipAnalysisJob,
  fetchFellowshipDocumentText,
  fetchFellowshipDocuments,
  fetchFellowshipEmailContent,
  fetchFellowships,
  generateFellowshipAnalysis,
  generateFellowshipLearning,
  sendFellowshipEmail,
  updateFellowship,
  updateFellowshipEmailContent,
  updateFellowshipLearning,
} from "@/app/admin/fellowship/api";
import {
  FellowshipDocument,
  FellowshipEmailContent,
  FellowshipEntry,
  FellowshipAnalysisAssets,
  FellowshipAnalysisContent,
  FellowshipAnalysisJob,
  FellowshipLearningContent,
  FellowshipSourceLink,
} from "@/app/types/fellowship";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/components/ui/tabs";
import { toFellowshipDocumentHref } from "@/app/utils/fellowshipDocuments";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const ReactQuill = dynamic(() => import("react-quill"), { ssr: false });

interface FormState {
  sequence: string;
  date: string;
  host: string;
  title: string;
  series: string;
  sourceLinks: FellowshipSourceLink[];
}

type FetchState =
  | { status: "idle" | "loading"; data: FellowshipEntry[] }
  | { status: "ready"; data: FellowshipEntry[] }
  | { status: "error"; data: FellowshipEntry[]; error: string };

type DocumentsByDate = Record<string, FellowshipDocument[]>;
type LearningListField =
  | "keyLearnings"
  | "audienceQuestions"
  | "audienceSharings"
  | "leaderResponses";

const emptyForm: FormState = {
  sequence: "",
  date: "",
  host: "",
  title: "",
  series: "",
  sourceLinks: [],
};

const emptyEmailContent: FellowshipEmailContent = {
  subject: "",
  html: "",
};

const emptyLearningContent: FellowshipLearningContent = {
  summary: "",
  keyLearnings: [],
  audienceQuestions: [],
  audienceSharings: [],
  leaderResponses: [],
  generatedAt: null,
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

function formatFileSize(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const ANALYSIS_POLL_INTERVAL_MS = 3000;
const ANALYSIS_POLL_TIMEOUT_MS = 90 * 60 * 1000;
const ANALYSIS_DOCUMENT_NAME = "主題與查經重點.md";

export function FellowshipManager() {
  const [state, setState] = useState<FetchState>({ status: "idle", data: [] });
  const [documentsByDate, setDocumentsByDate] = useState<DocumentsByDate>({});
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
  const [learningContent, setLearningContent] =
    useState<FellowshipLearningContent>(emptyLearningContent);
  const [learningSaving, setLearningSaving] = useState(false);
  const [learningGenerating, setLearningGenerating] = useState(false);
  const [learningError, setLearningError] = useState<string | null>(null);
  const [analysisAssets, setAnalysisAssets] = useState<FellowshipAnalysisAssets | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisGenerating, setAnalysisGenerating] = useState(false);
  const [analysisJob, setAnalysisJob] = useState<FellowshipAnalysisJob | null>(null);
  const [analysisContent, setAnalysisContent] = useState<FellowshipAnalysisContent | null>(null);
  const [analysisMarkdown, setAnalysisMarkdown] = useState("");
  const [analysisMarkdownLoading, setAnalysisMarkdownLoading] = useState(false);
  const [analysisMarkdownError, setAnalysisMarkdownError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

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
      const documentPairs = await Promise.all(
        entries.map(async (entry): Promise<[string, FellowshipDocument[]]> => {
          try {
            return [entry.date, await fetchFellowshipDocuments(entry.date)];
          } catch {
            return [entry.date, []];
          }
        }),
      );
      setDocumentsByDate(Object.fromEntries(documentPairs));
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入團契資料失敗";
      setState((prev) => ({ status: "error", data: prev.data, error: message }));
    }
  }, []);

  const loadAnalysisAssets = useCallback(async (date: string) => {
    setAnalysisLoading(true);
    setAnalysisError(null);
    try {
      const assets = await fetchFellowshipAnalysisAssets(date);
      setAnalysisAssets(assets);
      return assets;
    } catch (err) {
      const message = err instanceof Error ? err.message : "載入分析資料來源失敗";
      setAnalysisError(message);
      setAnalysisAssets(null);
      return null;
    } finally {
      setAnalysisLoading(false);
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

  const currentDocuments = useMemo(
    () => (editingDate ? documentsByDate[editingDate] ?? [] : []),
    [documentsByDate, editingDate],
  );

  const currentAnalysisDocument = useMemo(
    () => currentDocuments.find((document) => document.name === ANALYSIS_DOCUMENT_NAME) ?? null,
    [currentDocuments],
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

  const syncEntryLearningContent = useCallback((date: string, content: FellowshipLearningContent) => {
    setState((prev) => ({
      ...prev,
      data: prev.data.map((entry) =>
        entry.date === date
          ? {
              ...entry,
              summary: content.summary,
              keyLearnings: content.keyLearnings,
              audienceQuestions: content.audienceQuestions,
              audienceSharings: content.audienceSharings,
              leaderResponses: content.leaderResponses,
              keyLearningsGeneratedAt: content.generatedAt ?? entry.keyLearningsGeneratedAt ?? null,
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
    setLearningContent(emptyLearningContent);
    setLearningError(null);
    setAnalysisAssets(null);
    setAnalysisError(null);
    setAnalysisJob(null);
    setAnalysisContent(null);
    setAnalysisMarkdown("");
    setAnalysisMarkdownLoading(false);
    setAnalysisMarkdownError(null);
    setAnalysisLoading(false);
    setAnalysisGenerating(false);
  }, []);

  const handleChange =
    (field: Exclude<keyof FormState, "sourceLinks">) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setForm((prev) => ({ ...prev, [field]: value }));
    };

  const handleSourceLinkChange =
    (index: number, field: keyof FellowshipSourceLink) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const value = event.target.value;
      setForm((prev) => ({
        ...prev,
        sourceLinks: prev.sourceLinks.map((link, linkIndex) =>
          linkIndex === index ? { ...link, [field]: value } : link,
        ),
      }));
    };

  const addSourceLink = () => {
    setForm((prev) => ({
      ...prev,
      sourceLinks: [...prev.sourceLinks, { label: "", url: "" }],
    }));
  };

  const removeSourceLink = (index: number) => {
    setForm((prev) => ({
      ...prev,
      sourceLinks: prev.sourceLinks.filter((_link, linkIndex) => linkIndex !== index),
    }));
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

  const handleLearningSummaryChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setLearningContent((prev) => ({ ...prev, summary: value }));
  };

  const handleKeyLearningChange =
    (index: number) => (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = event.target.value;
      setLearningContent((prev) => ({
        ...prev,
        keyLearnings: prev.keyLearnings.map((item, itemIndex) =>
          itemIndex === index ? value : item,
        ),
      }));
    };

  const addKeyLearning = () => {
    setLearningContent((prev) => ({
      ...prev,
      keyLearnings: [...prev.keyLearnings, ""],
    }));
  };

  const removeKeyLearning = (index: number) => {
    setLearningContent((prev) => ({
      ...prev,
      keyLearnings: prev.keyLearnings.filter((_item, itemIndex) => itemIndex !== index),
    }));
  };

  const handleLearningListChange =
    (field: LearningListField, index: number) =>
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      const value = event.target.value;
      setLearningContent((prev) => ({
        ...prev,
        [field]: prev[field].map((item, itemIndex) => (itemIndex === index ? value : item)),
      }));
    };

  const addLearningListItem = (field: LearningListField) => {
    setLearningContent((prev) => ({
      ...prev,
      [field]: [...prev[field], ""],
    }));
  };

  const removeLearningListItem = (field: LearningListField, index: number) => {
    setLearningContent((prev) => ({
      ...prev,
      [field]: prev[field].filter((_item, itemIndex) => itemIndex !== index),
    }));
  };

  const handleEdit = (entry: FellowshipEntry) => {
    const isSameDate = editingDate === entry.date;
    setEditingSequence(entry.sequence ?? null);
    setEditingDate(entry.date);
    setForm({
      sequence: entry.sequence != null ? entry.sequence.toString() : "",
      date: toIsoDate(entry.date),
      host: entry.host ?? "",
      title: entry.title ?? "",
      series: entry.series ?? "",
      sourceLinks: entry.sourceLinks?.length
        ? entry.sourceLinks.map((link) => ({ label: link.label ?? "", url: link.url ?? "" }))
        : [],
    });
    setFeedback(null);
    setError(null);
    setLearningError(null);
    setLearningContent({
      summary: entry.summary ?? "",
      keyLearnings: entry.keyLearnings?.length ? [...entry.keyLearnings] : [],
      audienceQuestions: entry.audienceQuestions?.length ? [...entry.audienceQuestions] : [],
      audienceSharings: entry.audienceSharings?.length ? [...entry.audienceSharings] : [],
      leaderResponses: entry.leaderResponses?.length ? [...entry.leaderResponses] : [],
      generatedAt: entry.keyLearningsGeneratedAt ?? null,
    });
    setAnalysisAssets(null);
    setAnalysisError(null);
    setAnalysisJob(null);
    setAnalysisContent(null);
    setAnalysisMarkdown("");
    setAnalysisMarkdownLoading(false);
    setAnalysisMarkdownError(null);
    if (isSameDate) {
      void loadAnalysisAssets(entry.date);
    }
  };

  useEffect(() => {
    if (!editingDate) {
      setAnalysisAssets(null);
      setAnalysisError(null);
      setAnalysisJob(null);
      setAnalysisLoading(false);
      return;
    }

    let cancelled = false;
    setAnalysisLoading(true);
    setAnalysisError(null);
    fetchFellowshipAnalysisAssets(editingDate)
      .then((assets) => {
        if (!cancelled) {
          setAnalysisAssets(assets);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "載入分析資料來源失敗";
          setAnalysisError(message);
          setAnalysisAssets(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAnalysisLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [editingDate]);

  useEffect(() => {
    if (!editingDate || !currentAnalysisDocument) {
      setAnalysisMarkdown("");
      setAnalysisMarkdownLoading(false);
      setAnalysisMarkdownError(null);
      return;
    }

    let cancelled = false;
    setAnalysisMarkdownLoading(true);
    setAnalysisMarkdownError(null);
    fetchFellowshipDocumentText(editingDate, currentAnalysisDocument.name)
      .then((markdown) => {
        if (!cancelled) {
          setAnalysisMarkdown(markdown);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "載入完整分析報告失敗";
          setAnalysisMarkdownError(message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAnalysisMarkdownLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [currentAnalysisDocument, editingDate]);

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
    const sourceLinks = form.sourceLinks
      .map((link) => ({
        label: link.label.trim(),
        url: link.url.trim(),
      }))
      .filter((link) => link.label || link.url);
    const incompleteSourceLink = sourceLinks.find((link) => !link.label || !link.url);
    if (incompleteSourceLink) {
      setError("來源連結需同時填寫名稱與 URL");
      setSaving(false);
      return;
    }

    const payload: FellowshipEntry = {
      date: formattedDate,
      host: form.host.trim(),
      title: form.title.trim(),
      series: form.series.trim(),
      sourceLinks,
      summary: currentEntry?.summary ?? "",
      keyLearnings: currentEntry?.keyLearnings ?? [],
      audienceQuestions: currentEntry?.audienceQuestions ?? [],
      audienceSharings: currentEntry?.audienceSharings ?? [],
      leaderResponses: currentEntry?.leaderResponses ?? [],
      keyLearningsGeneratedAt: currentEntry?.keyLearningsGeneratedAt ?? null,
      emailSubject: currentEntry?.emailSubject ?? null,
      emailBodyHtml: currentEntry?.emailBodyHtml ?? null,
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

  const handleLearningSave = async () => {
    if (!editingDate) {
      setLearningError("請先選擇既有團契資料");
      return;
    }
    const payload: FellowshipLearningContent = {
      summary: learningContent.summary.trim(),
      keyLearnings: learningContent.keyLearnings.map((item) => item.trim()).filter(Boolean),
      audienceQuestions: learningContent.audienceQuestions.map((item) => item.trim()).filter(Boolean),
      audienceSharings: learningContent.audienceSharings.map((item) => item.trim()).filter(Boolean),
      leaderResponses: learningContent.leaderResponses.map((item) => item.trim()).filter(Boolean),
      generatedAt: learningContent.generatedAt ?? null,
    };
    setLearningSaving(true);
    setLearningError(null);
    setFeedback(null);
    try {
      const saved = await updateFellowshipLearning(editingDate, payload);
      setLearningContent(saved);
      syncEntryLearningContent(editingDate, saved);
      setFeedback("已儲存團契學習回顧");
    } catch (err) {
      const message = err instanceof Error ? err.message : "儲存團契學習回顧失敗";
      setLearningError(message);
    } finally {
      setLearningSaving(false);
    }
  };

  const handleLearningGenerate = async () => {
    if (!editingDate) {
      setLearningError("請先選擇既有團契資料");
      return;
    }
    setLearningGenerating(true);
    setLearningError(null);
    setFeedback(null);
    try {
      const generated = await generateFellowshipLearning(editingDate);
      setLearningContent(generated);
      syncEntryLearningContent(editingDate, generated);
      setFeedback("已從團契文件產生學習回顧，請確認後可再手動調整");
    } catch (err) {
      const message = err instanceof Error ? err.message : "產生團契學習回顧失敗";
      setLearningError(message);
    } finally {
      setLearningGenerating(false);
    }
  };

  const handleAnalysisGenerate = async () => {
    if (!editingDate) {
      setAnalysisError("請先選擇既有團契資料");
      return;
    }
    setAnalysisGenerating(true);
    setAnalysisError(null);
    setAnalysisMarkdownError(null);
    setFeedback(null);
    try {
      const started = await generateFellowshipAnalysis(editingDate);
      setAnalysisJob(started);
      let latest = started;
      const deadline = Date.now() + ANALYSIS_POLL_TIMEOUT_MS;
      while (Date.now() < deadline) {
        if (latest.status === "completed" || latest.status === "failed") {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, ANALYSIS_POLL_INTERVAL_MS));
        latest = await fetchFellowshipAnalysisJob(editingDate, started.jobId);
        setAnalysisJob(latest);
      }
      if (latest.status !== "completed" && latest.status !== "failed") {
        setFeedback("分析仍在後台執行，可稍後重新整理此頁查看結果。");
        return;
      }
      if (latest.status !== "completed") {
        throw new Error(latest.error || latest.message || "產生主題與查經重點失敗");
      }
      if (latest.content) {
        setAnalysisContent(latest.content);
        setAnalysisMarkdown(latest.content.markdown || "");
        setAnalysisMarkdownError(null);
        const interactionSummaries = (kind: string) =>
          latest.content?.interactions
            .filter((interaction) => interaction.kind === kind)
            .map((interaction) => {
              const text = (interaction.summary || interaction.text).trim();
              return interaction.speaker && text ? `${interaction.speaker}：${text}` : text;
            })
            .filter(Boolean) ?? [];
        const generated: FellowshipLearningContent = {
          summary: latest.content.centralMessage || latest.content.theme,
          keyLearnings: latest.content.keyPoints,
          audienceQuestions: interactionSummaries("question"),
          audienceSharings: interactionSummaries("sharing"),
          leaderResponses: interactionSummaries("response"),
          generatedAt: latest.content.generatedAt ?? null,
        };
        setLearningContent(generated);
        syncEntryLearningContent(editingDate, generated);
      }
      const [documents, assets] = await Promise.all([
        fetchFellowshipDocuments(editingDate),
        fetchFellowshipAnalysisAssets(editingDate),
      ]);
      setDocumentsByDate((prev) => ({ ...prev, [editingDate]: documents }));
      setAnalysisAssets(assets);
      setFeedback("已產生主題與查經重點文件");
    } catch (err) {
      const message = err instanceof Error ? err.message : "產生主題與查經重點失敗";
      setAnalysisError(message);
    } finally {
      setAnalysisGenerating(false);
    }
  };

  const renderAssetSummary = (
    label: string,
    asset: FellowshipAnalysisAssets["pptx"] | FellowshipAnalysisAssets["transcript"] | FellowshipAnalysisAssets["recording"],
    loaded = analysisAssets !== null,
  ) => (
    <div className="rounded-md border border-sky-100 bg-white p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-sky-700">{label}</p>
      {asset ? (
        <div className="mt-1 space-y-1 text-sm text-gray-700">
          <p className="break-all font-medium">{asset.name}</p>
          <p className="text-xs text-gray-500">
            {asset.source === "drive" ? "Google Drive" : "本地文件"}
            {asset.size != null ? ` · ${formatFileSize(asset.size)}` : ""}
          </p>
        </div>
      ) : (
        <p className="mt-1 text-sm text-gray-500">{loaded ? "未找到" : "尚未載入"}</p>
      )}
    </div>
  );

  const displayedAnalysisMarkdown = analysisContent?.markdown || analysisMarkdown;

  const renderLearningListEditor = (
    field: LearningListField,
    title: string,
    addLabel: string,
    emptyText: string,
    placeholder: string,
  ) => {
    const items = learningContent[field];
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium text-emerald-950">{title}</span>
          <button
            type="button"
            onClick={() => addLearningListItem(field)}
            disabled={!editingDate || learningGenerating || learningSaving}
            className="rounded-md border border-emerald-300 px-3 py-1 text-sm text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:border-emerald-200 disabled:text-emerald-300"
          >
            {addLabel}
          </button>
        </div>
        {items.length > 0 ? (
          <div className="space-y-2">
            {items.map((item, index) => (
              <div key={`${field}-${index}`} className="grid gap-2 md:grid-cols-[1fr_auto]">
                <textarea
                  value={item}
                  onChange={handleLearningListChange(field, index)}
                  disabled={!editingDate || learningGenerating || learningSaving}
                  rows={2}
                  className="min-h-20 rounded-md border border-emerald-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300 disabled:bg-gray-100"
                  placeholder={placeholder}
                />
                <button
                  type="button"
                  onClick={() => removeLearningListItem(field, index)}
                  disabled={!editingDate || learningGenerating || learningSaving}
                  className="rounded-md border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                >
                  移除
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">{emptyText}</p>
        )}
      </div>
    );
  };

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
          <TabsList className="grid w-full grid-cols-3 md:inline-flex md:w-auto">
            <TabsTrigger value="details">團契資料管理</TabsTrigger>
            <TabsTrigger value="learning">學習回顧</TabsTrigger>
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
              <div className="space-y-3 md:col-span-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium text-gray-700">來源連結</span>
                  <button
                    type="button"
                    onClick={addSourceLink}
                    className="rounded-md border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-100"
                  >
                    新增來源
                  </button>
                </div>
                {form.sourceLinks.length > 0 ? (
                  <div className="space-y-2">
                    {form.sourceLinks.map((link, index) => (
                      <div key={index} className="grid gap-2 md:grid-cols-[1fr_1.5fr_auto]">
                        <input
                          type="text"
                          value={link.label}
                          onChange={handleSourceLinkChange(index, "label")}
                          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="來源名稱，例如王守仁牧師講道"
                        />
                        <input
                          type="url"
                          value={link.url}
                          onChange={handleSourceLinkChange(index, "url")}
                          className="rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="https://..."
                        />
                        <button
                          type="button"
                          onClick={() => removeSourceLink(index)}
                          className="rounded-md border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50"
                        >
                          移除
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500">尚未加入來源連結。</p>
                )}
              </div>
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

          <TabsContent value="learning" className="mt-0">
            <div className="space-y-4 rounded-xl border border-emerald-200 bg-emerald-50/40 p-6 shadow-sm">
              <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-xl font-semibold text-emerald-950">團契學習回顧</h2>
                  <p className="mt-1 text-sm text-emerald-700">
                    {editingDate
                      ? `目前編輯 ${editingDate} 的公開學習重點。`
                      : "請先從下方表格選擇一筆既有團契資料，再編輯學習回顧。"}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleLearningGenerate}
                    disabled={!editingDate || learningGenerating || learningSaving}
                    className="rounded-md border border-emerald-300 px-4 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:border-emerald-200 disabled:text-emerald-300"
                  >
                    {learningGenerating ? "產生中…" : "從文件產生"}
                  </button>
                  <button
                    type="button"
                    onClick={handleLearningSave}
                    disabled={!editingDate || learningGenerating || learningSaving}
                    className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-emerald-300"
                  >
                    {learningSaving ? "儲存中…" : "儲存回顧"}
                  </button>
                </div>
              </header>

              {learningError && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {learningError}
                </div>
              )}

              <div className="space-y-3 rounded-lg border border-sky-200 bg-sky-50/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-sky-950">分析資料來源</h3>
                    <p className="mt-1 text-xs text-sky-700">
                      從團契 metadata、文件資料夾與 Google Meet Recordings 解析 PPT、逐字稿與錄音。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={handleAnalysisGenerate}
                    disabled={!editingDate || analysisLoading || analysisGenerating}
                    className="rounded-md bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-sky-300"
                  >
                    {analysisGenerating ? "產生中…" : "產生主題與查經重點"}
                  </button>
                </div>

                {analysisError && (
                  <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    {analysisError}
                  </div>
                )}

                {analysisLoading ? (
                  <p className="text-sm text-sky-700">分析資料來源載入中…</p>
                ) : (
                  <div className="grid gap-3 md:grid-cols-3">
                    {renderAssetSummary("PPT", analysisAssets?.pptx)}
                    {renderAssetSummary("逐字稿 / 講稿", analysisAssets?.transcript)}
                    {renderAssetSummary("錄音", analysisAssets?.recording)}
                  </div>
                )}

                {analysisAssets?.emptyChat && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                    找到 chat 檔案「{analysisAssets.emptyChat.name}」，但檔案太小，已視為空檔案並忽略。
                  </div>
                )}

                {analysisAssets?.messages.length ? (
                  <ul className="space-y-1 text-xs text-sky-700">
                    {analysisAssets.messages.map((message, index) => (
                      <li key={`${message}-${index}`}>{message}</li>
                    ))}
                  </ul>
                ) : null}

                {analysisJob && (
                  <div className="rounded-md border border-sky-100 bg-white p-3 text-sm text-gray-700">
                    <p>
                      Job 狀態：<span className="font-semibold">{analysisJob.status}</span>
                      {analysisJob.message ? ` · ${analysisJob.message}` : ""}
                    </p>
                    {analysisJob.resultDocumentName && editingDate && (
                      <a
                        href={toFellowshipDocumentHref(toIsoDate(editingDate), {
                          name: analysisJob.resultDocumentName,
                          url: "",
                          size: 0,
                          modifiedAt: "",
                        })}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-block text-sky-700 hover:underline"
                      >
                        開啟 {analysisJob.resultDocumentName}
                      </a>
                    )}
                  </div>
                )}
              </div>

              <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-950">完整分析報告</h3>
                    <p className="mt-1 text-xs text-slate-500">
                      顯示已產生的 `主題與查經重點.md`，包含主題、中心信息、經文結構、查經重點、會眾互動與生活應用。
                    </p>
                  </div>
                  {currentAnalysisDocument && editingDate && (
                    <a
                      href={toFellowshipDocumentHref(toIsoDate(editingDate), currentAnalysisDocument)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-md border border-slate-300 px-3 py-1 text-sm font-medium text-slate-700 hover:bg-slate-50"
                    >
                      開啟 Markdown
                    </a>
                  )}
                </div>

                {analysisMarkdownError && (
                  <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                    {analysisMarkdownError}
                  </div>
                )}

                {analysisMarkdownLoading ? (
                  <p className="text-sm text-slate-500">完整分析報告載入中…</p>
                ) : displayedAnalysisMarkdown ? (
                  <div className="max-h-[520px] overflow-auto rounded-md border border-slate-100 bg-slate-50 p-4">
                    <div className="prose prose-slate max-w-none text-sm">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {displayedAnalysisMarkdown}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">
                    尚未產生完整分析報告。點選「產生主題與查經重點」後會在這裡顯示所有 section。
                  </p>
                )}
              </div>

              <label className="flex flex-col">
                <span className="text-sm font-medium text-emerald-950">公開摘要（Markdown）</span>
                <textarea
                  value={learningContent.summary}
                  onChange={handleLearningSummaryChange}
                  disabled={!editingDate || learningGenerating || learningSaving}
                  rows={4}
                  className="mt-1 rounded-md border border-emerald-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300 disabled:bg-gray-100"
                  placeholder="可使用 Markdown 簡短介紹本次團契查經的主題與屬靈焦點，例如 **信心的回應** 或 [來源](https://...)"
                />
              </label>

              {renderLearningListEditor(
                "keyLearnings",
                "學習重點（Markdown）",
                "新增重點",
                "尚未建立學習重點。",
                "輸入一項學習重點，可使用 Markdown，例如 **基督是教會的根基**",
              )}

              {renderLearningListEditor(
                "audienceQuestions",
                "會眾問題（Markdown）",
                "新增問題",
                "尚未整理會眾問題。",
                "輸入一項會眾問題，例如 彼得出於好意，為何耶穌卻稱他為撒但？",
              )}

              {renderLearningListEditor(
                "audienceSharings",
                "會眾分享（Markdown）",
                "新增分享",
                "尚未整理會眾分享。",
                "輸入一項會眾分享，可包含分享者與內容摘要。",
              )}

              {renderLearningListEditor(
                "leaderResponses",
                "帶領者回應（Markdown）",
                "新增回應",
                "尚未整理帶領者回應。",
                "輸入一項帶領者回應，聚焦對問題或分享的查經回應。",
              )}

              <div>
                {learningContent.generatedAt && (
                  <p className="text-xs text-emerald-700">
                    最近產生時間：{new Date(learningContent.generatedAt).toLocaleString()}
                  </p>
                )}
              </div>
            </div>
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
                  來源
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  文件
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {sortedEntries.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-sm text-gray-500">
                    尚未建立任何團契資訊。
                  </td>
                </tr>
              ) : (
                sortedEntries.map((entry, index) => {
                  const documents = documentsByDate[entry.date] ?? [];
                  return (
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
                      <td className="min-w-48 px-4 py-3 text-sm text-gray-600">
                        {entry.sourceLinks?.length ? (
                          <div className="space-y-1">
                            {entry.sourceLinks.map((link, linkIndex) => (
                              <a
                                key={`${link.url}-${linkIndex}`}
                                href={link.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block max-w-56 truncate text-blue-600 hover:underline"
                                title={link.url}
                              >
                                {link.label || link.url}
                              </a>
                            ))}
                          </div>
                        ) : (
                          <span className="text-gray-400">無</span>
                        )}
                      </td>
                      <td className="min-w-48 px-4 py-3 text-sm text-gray-600">
                        {documents.length > 0 ? (
                          <div className="space-y-1">
                            {documents.map((document) => (
                              <a
                                key={document.name}
                                href={toFellowshipDocumentHref(toIsoDate(entry.date), document)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="block max-w-56 truncate text-blue-600 hover:underline"
                                title={`${document.name} (${formatFileSize(document.size)})`}
                              >
                                {document.name}
                              </a>
                            ))}
                          </div>
                        ) : (
                          <span className="text-gray-400">無</span>
                        )}
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
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
