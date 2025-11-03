"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSundayService,
  deleteSundayService,
  fetchSundayResources,
  fetchSundayServices,
  fetchScriptureBooks,
  generateSundayServicePpt,
  downloadFinalSundayServicePpt,
  uploadFinalSundayServicePpt,
  sendSundayServiceEmail,
  updateSundayService,
} from "@/app/admin/sunday-service/api";
import { SundayWorkerManager } from "@/app/components/admin/sundayService/SundayWorkerManager";
import {
  SundayServiceEntry,
  SundayServiceResources,
  SundaySong,
  SundayWorker,
  UnavailableDateRange,
  ScriptureBook,
} from "@/app/types/sundayService";

interface ScriptureSelection {
  book: string;
  chapter: string;
  start: string;
  end: string;
}

const createEmptyScriptureSelection = (): ScriptureSelection => ({
  book: "",
  chapter: "",
  start: "",
  end: "",
});

interface FormState {
  date: string;
  presider: string;
  worshipLeader: string;
  pianist: string;
  hymn: string;
  responseHymn: string;
  scriptures: ScriptureSelection[];
  scriptureReader1: string;
  scriptureReader2: string;
  scriptureReader3: string;
  sermonSpeaker: string;
  sermonTitle: string;
  announcementsMarkdown: string;
  healthPrayerMarkdown: string;
  holdHolyCommunion: boolean;
}

function createEmptyForm(): FormState {
  return {
    date: "",
    presider: "",
    worshipLeader: "",
    pianist: "",
    hymn: "",
    responseHymn: "",
    scriptures: [createEmptyScriptureSelection()],
    scriptureReader1: "",
    scriptureReader2: "",
    scriptureReader3: "",
    sermonSpeaker: "",
    sermonTitle: "",
    announcementsMarkdown: "",
    healthPrayerMarkdown: "",
    holdHolyCommunion: false,
  };
}

function isFirstSundayOfMonth(value: string): boolean {
  if (!value) {
    return false;
  }
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number.parseInt(yearText ?? "", 10);
  const month = Number.parseInt(monthText ?? "", 10);
  const day = Number.parseInt(dayText ?? "", 10);
  if (
    Number.isNaN(year) ||
    Number.isNaN(month) ||
    Number.isNaN(day) ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > 31
  ) {
    return false;
  }
  const date = new Date(Date.UTC(year, month - 1, day));
  const dayOfWeek = date.getUTCDay();
  const dayOfMonth = date.getUTCDate();
  return dayOfWeek === 0 && dayOfMonth <= 7;
}

function parseScriptureSlug(value: string | null | undefined): ScriptureSelection {
  if (!value) {
    return createEmptyScriptureSelection();
  }
  const trimmed = value.trim().replace(/^scripture-/, "");
  if (!trimmed) {
    return createEmptyScriptureSelection();
  }
  const parts = trimmed.split("-");
  if (parts.length < 3) {
    return createEmptyScriptureSelection();
  }
  const [book, chapter, start, maybeEnd] = parts;
  return {
    book: book ?? "",
    chapter: chapter ?? "",
    start: start ?? "",
    end: maybeEnd ?? start ?? "",
  };
}

function parseScriptureValues(values: string[] | null | undefined): ScriptureSelection[] {
  if (!values || values.length === 0) {
    return [createEmptyScriptureSelection()];
  }
  const parsed = values
    .map((value) => parseScriptureSlug(value))
    .filter((selection) => selection.book || selection.chapter || selection.start || selection.end);
  return parsed.length > 0 ? parsed : [createEmptyScriptureSelection()];
}

function composeScriptureSlug(selection: ScriptureSelection): string | null {
  const book = selection.book.trim();
  const chapter = selection.chapter.trim();
  const start = selection.start.trim();
  const end = selection.end.trim();
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

function composeScriptureValues(selections: ScriptureSelection[]): string[] {
  const slugs: string[] = [];
  const seen = new Set<string>();
  selections.forEach((selection) => {
    const slug = composeScriptureSlug(selection);
    if (slug && !seen.has(slug)) {
      slugs.push(slug);
      seen.add(slug);
    }
  });
  return slugs;
}

const getWorkerUnavailableRanges = (worker: SundayWorker): UnavailableDateRange[] => {
  if (worker.unavailableRanges && worker.unavailableRanges.length > 0) {
    return worker.unavailableRanges;
  }
  const legacy = (worker as { unavailableDates?: string[] }).unavailableDates;
  if (legacy && legacy.length > 0) {
    return legacy
      .map((value) => (typeof value === "string" ? value.trim() : ""))
      .filter((value): value is string => value.length > 0)
      .map((value) => ({ startDate: value, endDate: value }));
  }
  return [];
};

const isRangeActiveOnDate = (range: UnavailableDateRange, date: string): boolean => {
  const target = date.trim();
  if (!target) {
    return false;
  }
  return range.startDate <= target && target <= range.endDate;
};

const isWorkerUnavailableOnDate = (worker: SundayWorker, date: string): boolean => {
  return getWorkerUnavailableRanges(worker).some((range) => isRangeActiveOnDate(range, date));
};

const parseServiceDateValue = (value: string | null | undefined): number | null => {
  if (!value) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parts = trimmed.split(/[-/]/);
  if (parts.length !== 3) {
    return null;
  }
  let year: number;
  let month: number;
  let day: number;
  const [first, second, third] = parts;
  if (first.length === 4) {
    year = Number.parseInt(first, 10);
    month = Number.parseInt(second, 10);
    day = Number.parseInt(third, 10);
  } else if (third.length === 4) {
    year = Number.parseInt(third, 10);
    month = Number.parseInt(first, 10);
    day = Number.parseInt(second, 10);
  } else {
    return null;
  }
  if (
    Number.isNaN(year) ||
    Number.isNaN(month) ||
    Number.isNaN(day) ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > 31
  ) {
    return null;
  }
  return Date.UTC(year, month - 1, day);
};

function toForm(entry: SundayServiceEntry | null): FormState {
  if (!entry) {
    return createEmptyForm();
  }
  const base = createEmptyForm();
  const rawHold =
    entry.holdHolyCommunion ??
    (entry as { hold_holy_communion?: boolean | null }).hold_holy_communion ??
    null;
  return {
    ...base,
    date: entry.date ?? "",
    presider: entry.presider ?? "",
    worshipLeader: entry.worshipLeader ?? "",
    pianist: entry.pianist ?? "",
    hymn: entry.hymn ?? "",
    responseHymn: entry.responseHymn ?? "",
    scriptures: parseScriptureValues(entry.scripture ?? null),
    scriptureReader1: entry.scriptureReaders?.[0] ?? "",
    scriptureReader2: entry.scriptureReaders?.[1] ?? "",
    scriptureReader3: entry.scriptureReaders?.[2] ?? "",
    sermonSpeaker: entry.sermonSpeaker ?? "",
    sermonTitle: entry.sermonTitle ?? "",
    announcementsMarkdown: entry.announcementsMarkdown ?? "",
    healthPrayerMarkdown: entry.health_prayer_markdown ?? "",
    holdHolyCommunion:
      rawHold ?? (entry.date ? isFirstSundayOfMonth(entry.date) : base.holdHolyCommunion),
  };
}

function fromForm(form: FormState): SundayServiceEntry {
  const optional = (value: string): string | null => {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  };
  const scriptureSelections = composeScriptureValues(form.scriptures);
  return {
    date: form.date,
    presider: optional(form.presider),
    worshipLeader: optional(form.worshipLeader),
    pianist: optional(form.pianist),
    hymn: optional(form.hymn),
    responseHymn: optional(form.responseHymn),
    scripture: scriptureSelections,
    scriptureReaders: [form.scriptureReader1, form.scriptureReader2, form.scriptureReader3]
      .map((value) => value.trim())
      .filter((value) => value.length > 0),
    sermonSpeaker: optional(form.sermonSpeaker),
    sermonTitle: optional(form.sermonTitle),
    announcementsMarkdown: form.announcementsMarkdown.trim(),
    health_prayer_markdown: form.healthPrayerMarkdown.trim(),
    holdHolyCommunion: form.holdHolyCommunion,
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
  const processed = form.scriptures.map((selection) => ({
    book: selection.book.trim(),
    chapter: selection.chapter.trim(),
    start: selection.start.trim(),
    end: selection.end.trim(),
  }));

  const filled = processed.filter(
    (selection) => selection.book || selection.chapter || selection.start || selection.end,
  );

  if (filled.length === 0) {
    return "請完整輸入讀經經文";
  }

  for (let index = 0; index < filled.length; index += 1) {
    const selection = filled[index];
    const prefix = `第 ${index + 1} 段`;
    if (!selection.book) {
      return `${prefix}：請選擇經文書卷`;
    }
    const chapter = Number.parseInt(selection.chapter, 10);
    if (Number.isNaN(chapter) || chapter <= 0) {
      return `${prefix}：章節須為正整數`;
    }
    const start = Number.parseInt(selection.start, 10);
    if (Number.isNaN(start) || start <= 0) {
      return `${prefix}：起始經文須為正整數`;
    }
    const effectiveEnd = selection.end || selection.start;
    const end = Number.parseInt(effectiveEnd, 10);
    if (Number.isNaN(end) || end <= 0) {
      return `${prefix}：結束經文須為正整數`;
    }
    if (end < start) {
      return `${prefix}：結束經文須大於或等於起始經文`;
    }
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
  value: string[] | null | undefined,
  lookup: Map<string, string>,
): string[] {
  if (!value || value.length === 0) {
    return [];
  }
  return value
    .map((item) => parseScriptureSlug(item))
    .filter((selection) => selection.book && selection.chapter && selection.start)
    .map((selection) => {
      const name = lookup.get(selection.book) ?? selection.book.toUpperCase();
      const hasRange = selection.end && selection.end !== selection.start;
      const range = hasRange ? `${selection.start}-${selection.end}` : selection.start;
      return `${name} ${selection.chapter}:${range}`;
    });
}

export function SundayServiceManager() {
  const [services, setServices] = useState<SundayServiceEntry[]>([]);
  const [resources, setResources] = useState<SundayServiceResources>({ workers: [], songs: [] });
  const [scriptureBooks, setScriptureBooks] = useState<ScriptureBook[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [form, setForm] = useState<FormState>(createEmptyForm());
  const [editingDate, setEditingDate] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pptGenerating, setPptGenerating] = useState<string | null>(null);
  const [finalPptUploading, setFinalPptUploading] = useState(false);
  const [finalPptDownloading, setFinalPptDownloading] = useState<string | null>(null);
  const [emailSending, setEmailSending] = useState<string | null>(null);
  const finalPptInputRef = useRef<HTMLInputElement | null>(null);

  const bookNameMap = useMemo(
    () => new Map(scriptureBooks.map((book) => [book.slug, book.name])),
    [scriptureBooks],
  );

  const resolveBookOptions = useCallback(
    (current: string) => {
      if (!current) {
        return scriptureBooks;
      }
      if (scriptureBooks.some((book) => book.slug === current)) {
        return scriptureBooks;
      }
      return [...scriptureBooks, { slug: current, name: current }];
    },
    [scriptureBooks],
  );

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
    if (scriptureBooks.length === 0) {
      return;
    }
    setForm((prev) => {
      const fallback = scriptureBooks[0]?.slug ?? "";
      const base = prev.scriptures.length > 0 ? prev.scriptures : [createEmptyScriptureSelection()];
      let changed = base.length !== prev.scriptures.length;
      const updated = base.map((selection) => {
        if (selection.book) {
          return selection;
        }
        if (!fallback) {
          return selection;
        }
        changed = true;
        return { ...selection, book: fallback };
      });
      return changed ? { ...prev, scriptures: updated } : prev;
    });
  }, [scriptureBooks]);

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

  const currentService = useMemo(() => {
    if (!editingDate) {
      return null;
    }
    return services.find((entry) => entry.date === editingDate) ?? null;
  }, [services, editingDate]);

  const upcomingServiceDate = useMemo(() => {
    if (services.length === 0) {
      return null;
    }
    const now = new Date();
    const todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    let candidate: { date: string; timestamp: number } | null = null;
    services.forEach((entry) => {
      const normalizedDate = entry.date?.trim() ?? "";
      const timestamp = parseServiceDateValue(normalizedDate);
      if (timestamp === null) {
        return;
      }
      if (timestamp >= todayUtc) {
        if (!candidate || timestamp < candidate.timestamp) {
          candidate = { date: normalizedDate, timestamp };
        }
      }
    });
    return candidate?.date ?? null;
  }, [services]);

  const workersByName = useMemo(() => {
    const map = new Map<string, SundayWorker>();
    resources.workers.forEach((worker) => {
      map.set(worker.name, worker);
    });
    return map;
  }, [resources.workers]);

  const unavailableWorkerNames = useMemo(() => {
    const date = form.date?.trim();
    if (!date) {
      return new Set<string>();
    }
    const set = new Set<string>();
    resources.workers.forEach((worker) => {
      if (isWorkerUnavailableOnDate(worker, date)) {
        set.add(worker.name);
      }
    });
    return set;
  }, [resources.workers, form.date]);

  const availableWorkers = useMemo(() => {
    if (unavailableWorkerNames.size === 0) {
      return resources.workers;
    }
    return resources.workers.filter((worker) => !unavailableWorkerNames.has(worker.name));
  }, [resources.workers, unavailableWorkerNames]);

  const workerNames = useMemo(
    () =>
      availableWorkers.map((worker) => worker.name).filter((name) => name.trim().length > 0),
    [availableWorkers],
  );

  const workerAssignmentFields = [
    "presider",
    "worshipLeader",
    "pianist",
    "sermonSpeaker",
    "scriptureReader1",
    "scriptureReader2",
    "scriptureReader3",
  ] as const;

  useEffect(() => {
    if (unavailableWorkerNames.size === 0) {
      return;
    }
    setForm((prev) => {
      let changed = false;
      const next: FormState = { ...prev };
      workerAssignmentFields.forEach((field) => {
        const current = prev[field];
        if (current && unavailableWorkerNames.has(current)) {
          next[field] = "";
          changed = true;
        }
      });
      return changed ? next : prev;
    });
  }, [unavailableWorkerNames]);

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
      setForm((prev) => {
        if (field === "date") {
          const shouldHoldHolyCommunion = isFirstSundayOfMonth(value);
          return { ...prev, date: value, holdHolyCommunion: shouldHoldHolyCommunion };
        }
        return { ...prev, [field]: value };
      });
    };

  const handleCheckboxChange = (field: keyof FormState) =>
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const checked = event.target.checked;
      setForm((prev) => ({ ...prev, [field]: checked }));
    };

  const handleScriptureFieldChange =
    (index: number, field: keyof ScriptureSelection) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const value = event.target.value;
      setForm((prev) => {
        const updated = prev.scriptures.map((selection, idx) =>
          idx === index ? { ...selection, [field]: value } : selection,
        );
        return { ...prev, scriptures: updated };
      });
    };

  const addScriptureSelection = () => {
    setForm((prev) => {
      const fallback = scriptureBooks[0]?.slug ?? "";
      const next = createEmptyScriptureSelection();
      if (fallback) {
        next.book = fallback;
      }
      return { ...prev, scriptures: [...prev.scriptures, next] };
    });
  };

  const removeScriptureSelection = (index: number) => {
    setForm((prev) => {
      if (prev.scriptures.length <= 1) {
        return prev;
      }
      const updated = prev.scriptures.filter((_, idx) => idx !== index);
      return { ...prev, scriptures: updated };
    });
  };

  const resetForm = () => {
    const base = createEmptyForm();
    const fallbackBook = scriptureBooks[0]?.slug ?? "";
    if (fallbackBook) {
      base.scriptures[0] = { ...base.scriptures[0], book: fallbackBook };
    }
    setForm(base);
    setEditingDate(null);
  };

  const handleEdit = (entry: SundayServiceEntry) => {
    setEditingDate(entry.date);
    const next = toForm(entry);
    if (scriptureBooks.length > 0) {
      const fallback = scriptureBooks[0].slug;
      let changed = false;
      const normalized = next.scriptures.map((selection) => {
        if (selection.book) {
          return selection;
        }
        if (!fallback) {
          return selection;
        }
        changed = true;
        return { ...selection, book: fallback };
      });
      if (changed) {
        next.scriptures = normalized;
      }
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
    const serviceDate = form.date.trim();
    const conflicts: string[] = [];
    const checkAvailability = (value: string | null | undefined, label: string) => {
      if (!value) {
        return;
      }
      const worker = workersByName.get(value);
      if (worker && isWorkerUnavailableOnDate(worker, serviceDate)) {
        conflicts.push(`${label}：${value}`);
      }
    };
    checkAvailability(form.presider, "司會");
    checkAvailability(form.worshipLeader, "領詩");
    checkAvailability(form.pianist, "司琴");
    checkAvailability(form.sermonSpeaker, "證道");
    [form.scriptureReader1, form.scriptureReader2, form.scriptureReader3].forEach(
      (reader, index) => {
        checkAvailability(reader, `讀經同工${index + 1}`);
      },
    );
    if (conflicts.length > 0) {
      setError(`以下同工於 ${serviceDate} 無法服事：${conflicts.join("、")}`);
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

  const handleFinalPptUpload = async (file: File) => {
    if (!editingDate) {
      setError("請先選擇主日日期並儲存資料");
      return;
    }
    if (!currentService) {
      setError("請先儲存主日服事資料後再上傳 PPT");
      return;
    }
    setFinalPptUploading(true);
    setFeedback(null);
    setError(null);
    try {
      await uploadFinalSundayServicePpt(editingDate, file);
      setFeedback("已上傳最終 PPT");
      await loadServices({ preserveForm: true });
    } catch (err) {
      const message = err instanceof Error ? err.message : "上傳最終 PPT 失敗";
      setError(message);
    } finally {
      setFinalPptUploading(false);
      if (finalPptInputRef.current) {
        finalPptInputRef.current.value = "";
      }
    }
  };

  const handleFinalPptFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    void handleFinalPptUpload(file);
  };

  const handleDownloadFinalPpt = async (entry: SundayServiceEntry) => {
    if (!entry.finalPptFilename) {
      setError("尚未上傳最終 PPT");
      return;
    }
    setFinalPptDownloading(entry.date);
    setFeedback(null);
    setError(null);
    try {
      const blob = await downloadFinalSundayServicePpt(entry.date);
      if (blob.size === 0) {
        throw new Error("下載的檔案內容為空");
      }
      const fileName =
        entry.finalPptFilename && entry.finalPptFilename.trim().length > 0
          ? entry.finalPptFilename
          : `聖道教會${formatDisplayDate(entry.date)}主日崇拜.pptx`;
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      window.URL.revokeObjectURL(url);
      setFeedback(`已下載 ${fileName}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "下載最終 PPT 失敗";
      setError(message);
    } finally {
      setFinalPptDownloading(null);
    }
  };

  const handleSendEmail = async (entry: SundayServiceEntry) => {
    if (!entry.finalPptFilename) {
      setError("請先上傳最終 PPT");
      return;
    }
    setEmailSending(entry.date);
    setFeedback(null);
    setError(null);
    try {
      const result = await sendSundayServiceEmail(entry.date);
      const count = result.recipients.length;
      setFeedback(`已發送主日服事 email 給 ${count} 位同工`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "發送 email 失敗";
      setError(message);
    } finally {
      setEmailSending(null);
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
    const trimmed = value.trim();
    const isoMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (isoMatch) {
      return trimmed;
    }
    const ymdSlashMatch = trimmed.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
    if (ymdSlashMatch) {
      const [, year, month, day] = ymdSlashMatch;
      return `${year}-${month}-${day}`;
    }
    const mdySlashMatch = trimmed.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (mdySlashMatch) {
      const [, month, day, year] = mdySlashMatch;
      return `${year}-${month}-${day}`;
    }
    const timestamp = Date.parse(trimmed);
    if (Number.isNaN(timestamp)) {
      return trimmed;
    }
    const formatter = new Intl.DateTimeFormat("zh-TW", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const formattedParts = formatter.formatToParts(new Date(timestamp));
    let year: string | undefined;
    let month: string | undefined;
    let day: string | undefined;
    for (const part of formattedParts) {
      if (part.type === "year") {
        year = part.value;
      } else if (part.type === "month") {
        month = part.value;
      } else if (part.type === "day") {
        day = part.value;
      }
    }
    if (year && month && day) {
      return `${year}-${month}-${day}`;
    }
    return trimmed;
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
            <div className="mb-4 space-y-3 rounded border border-blue-100 bg-blue-50 p-4">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className="rounded border border-blue-300 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                  onClick={() => handleGeneratePpt(editingDate)}
                  disabled={pptGenerating === editingDate || saving}
                >
                  {pptGenerating === editingDate ? "生成中…" : "生成 PPT"}
                </button>
                <button
                  type="button"
                  className="rounded border border-green-300 px-4 py-2 text-sm font-semibold text-green-700 hover:bg-green-100 disabled:cursor-not-allowed disabled:border-green-200 disabled:text-green-300"
                  onClick={() => currentService && handleDownloadFinalPpt(currentService)}
                  disabled={
                    !currentService?.finalPptFilename ||
                    finalPptDownloading === editingDate ||
                    saving
                  }
                >
                  {finalPptDownloading === editingDate
                    ? "下載中…"
                    : currentService?.finalPptFilename
                      ? "下載最終 PPT"
                      : "尚未上傳"}
                </button>
                <button
                  type="button"
                  className="rounded border border-purple-300 px-4 py-2 text-sm font-semibold text-purple-700 hover:bg-purple-100 disabled:cursor-not-allowed disabled:border-purple-200 disabled:text-purple-300"
                  onClick={() => currentService && handleSendEmail(currentService)}
                  disabled={
                    !currentService?.finalPptFilename ||
                    emailSending === editingDate ||
                    saving
                  }
                >
                  {emailSending === editingDate ? "發送中…" : "發送 email"}
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-semibold text-gray-700">上傳最終 PPT</span>
                <input
                  ref={finalPptInputRef}
                  type="file"
                  accept=".pptx"
                  className="text-sm"
                  onChange={handleFinalPptFileChange}
                  disabled={finalPptUploading || saving || !currentService}
                />
                <span className="text-xs text-gray-500">僅接受 .pptx 檔案</span>
              </div>
              <p className="text-xs text-gray-600">
                {finalPptUploading
                  ? "最終 PPT 上傳中…"
                  : currentService?.finalPptFilename
                    ? `目前檔案：${currentService.finalPptFilename}`
                    : "尚未上傳最終 PPT"}
              </p>
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
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  id="holdHolyCommunion"
                  type="checkbox"
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  checked={form.holdHolyCommunion}
                  onChange={handleCheckboxChange("holdHolyCommunion")}
                  disabled={saving}
                />
                <label
                  htmlFor="holdHolyCommunion"
                  className="text-sm font-semibold text-gray-700"
                >
                  守聖餐
                </label>
                <span className="text-xs text-gray-500">每月第一個主日會自動勾選</span>
              </div>
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
                  {availableWorkers.map((worker) => (
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
                  {availableWorkers.map((worker) => (
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
                  {availableWorkers.map((worker) => (
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
              <div className="space-y-3">
                {form.scriptures.map((selection, index) => {
                  const options = resolveBookOptions(selection.book);
                  return (
                    <div
                      key={`scripture-${index}`}
                      className="grid items-end gap-3 md:grid-cols-[minmax(0,2fr)_repeat(3,minmax(0,1fr))_auto]"
                    >
                      <select
                        className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                        value={selection.book}
                        onChange={handleScriptureFieldChange(index, "book")}
                        disabled={saving || options.length === 0}
                      >
                        {options.map((book) => (
                          <option key={`${index}-${book.slug}`} value={book.slug}>
                            {book.name}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min={1}
                        className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                        placeholder="章"
                        value={selection.chapter}
                        onChange={handleScriptureFieldChange(index, "chapter")}
                        disabled={saving}
                      />
                      <input
                        type="number"
                        min={1}
                        className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                        placeholder="起始經文"
                        value={selection.start}
                        onChange={handleScriptureFieldChange(index, "start")}
                        disabled={saving}
                      />
                      <input
                        type="number"
                        min={1}
                        className="rounded border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                        placeholder="結束經文 (可留空)"
                        value={selection.end}
                        onChange={handleScriptureFieldChange(index, "end")}
                        disabled={saving}
                      />
                      {form.scriptures.length > 1 && (
                        <button
                          type="button"
                          className="rounded border border-red-200 px-3 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                          onClick={() => removeScriptureSelection(index)}
                          disabled={saving}
                        >
                          移除
                        </button>
                      )}
                    </div>
                  );
                })}
                <button
                  type="button"
                  className="rounded border border-blue-300 px-3 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:border-blue-200 disabled:text-blue-300"
                  onClick={addScriptureSelection}
                  disabled={saving}
                >
                  新增經文段落
                </button>
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
                <th className="w-20 px-3 py-2 text-center">聖餐</th>
                <th className="w-44 px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {services.map((entry) => {
                const scriptureLines = formatScriptureDisplay(entry.scripture, bookNameMap);
                const holdsCommunion =
                  entry.holdHolyCommunion ??
                  (entry.date ? isFirstSundayOfMonth(entry.date) : false);
                const entryDateValue = entry.date?.trim() ?? "";
                const isUpcoming = upcomingServiceDate ? entryDateValue === upcomingServiceDate : false;
                const rowClasses = `border-t border-gray-100 ${isUpcoming ? "bg-amber-50" : "bg-white"}`;
                return (
                  <tr key={entry.date} className={rowClasses}>
                    <td className="px-3 py-2 font-medium text-gray-900">
                      <div className="flex items-center gap-2">
                        {!isUpcoming && (
                        <span>{formatDisplayDate(entry.date)}</span>
                        )}                        
                        {isUpcoming && (
                          <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-semibold text-amber-900">
                            {formatDisplayDate(entry.date)}
                          </span>
                        )}
                      </div>
                    </td>
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
                      {scriptureLines.length > 0 ? (
                        <div className="flex flex-col">
                          {scriptureLines.map((label, idx) => (
                            <span key={`${entry.date}-scripture-${idx}`}>{label}</span>
                          ))}
                        </div>
                      ) : (
                        <span>-</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-700">
                      {(entry.scriptureReaders && entry.scriptureReaders.length > 0
                        ? entry.scriptureReaders.join("、")
                        : "-")}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {holdsCommunion ? "是" : "否"}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-2">
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
                          className="rounded border border-red-200 px-3 py-1 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-red-100 disabled:text-red-300"
                          onClick={() => handleDelete(entry.date)}
                          disabled={saving}
                        >
                          刪除
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {services.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-3 py-6 text-center text-sm text-gray-500">
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
