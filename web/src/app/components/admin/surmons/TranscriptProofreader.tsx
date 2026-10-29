"use client";

import dynamic from "next/dynamic";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Options } from "easymde";
import type { Editor as CodeMirrorEditor } from "codemirror";
import ReactMarkdown from "react-markdown";
import "easymde/dist/easymde.min.css";
import {
  selectReferenceGroup,
  type SlideInspection,
} from "./review-slide-selection";

const MarkdownEditor = dynamic(() => import("react-simplemde-editor"), {
  ssr: false,
});
type Operation = { kind: string; id?: string; [key: string]: unknown };
type Row = { index: string; text: string; type: string };
type Group = {
  id: string;
  member_ids: string[];
  slide_id: string;
  before_paragraph_id: string;
  hidden: boolean;
  reason: string;
  needs_review: boolean;
};
type Asset = {
  visible_text: string;
  image_sha256: string;
  content: { text_regions: { id: string; text: string }[] };
  svg_review_status: string;
};
type Decision = { decision: string; sha256: string; actor: string };
type Verse = { book: string; chapter_verse: string; text: string };
type Metadata = {
  title: string;
  summary: string;
  keypoints: string;
  core_bible_verse?: Verse[];
};
type Context = {
  revision: number;
  paragraph_bindings: Record<
    string,
    { start_ms: number; end_ms: number; source_paragraph_id: string }
  >;
  groups: Group[];
  occurrences: Record<
    string,
    {
      asset_id: string;
      start_ms?: number;
      first_seen_ms?: number;
      time_ms?: number;
      capture_ms?: number;
    }
  >;
  visual_assets: Record<string, Asset>;
  editorial_metadata: Metadata;
  bookmarks?: Record<string, string>;
  reviews: Record<string, Decision>;
};
type Document = {
  script: Row[];
  context: Context;
  version: string;
  actor: string;
  epoch: string;
  header: { title?: string; assigned_to_name?: string; assigned_to?: string };
  can_edit: boolean;
  can_claim: boolean;
  can_takeover: boolean;
  can_publish: boolean;
  published: boolean;
  paragraph_shas: Record<string, string>;
  group_shas: Record<string, string>;
};
type Receipt = {
  version: string;
  revision: number;
  paragraph_shas: Record<string, string>;
  group_shas: Record<string, string>;
  reviews: Record<string, Decision>;
};
type Packet = {
  operation_id: string;
  version: string;
  epoch: string;
  operations?: Operation[];
  action?: string;
};
type Pending = {
  baseVersion: string;
  operations: Operation[];
  packet?: Packet;
};
type Revision = { revision: number; actor: string; time: string; kind: string };
class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new RequestError(
      body?.detail || `請求失敗 (${response.status})`,
      response.status,
    );
  }
  return response.json();
}
async function loadDocument(url: string): Promise<Document> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new RequestError(body?.detail || "載入失敗", response.status);
  }
  return {
    ...(await response.json()),
    ...JSON.parse(response.headers.get("x-review-access") || "{}"),
    version: response.headers.get("etag")?.replaceAll('"', ""),
  };
}
async function draftStorage(
  key: string,
  value?: Pending | null,
): Promise<Pending | undefined> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const r = indexedDB.open("transcript-review-drafts", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("drafts");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(
        "drafts",
        value === undefined ? "readonly" : "readwrite",
      );
      const store = tx.objectStore("drafts");
      const r =
        value === undefined
          ? store.get(key)
          : value === null
            ? store.delete(key)
            : store.put(value, key);
      tx.oncomplete = () => resolve(value === undefined ? r.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}
const stamp = (ms: number) => {
  const sec = Math.floor(ms / 1000);
  return `${Math.floor(sec / 3600)
    .toString()
    .padStart(2, "0")}:${Math.floor((sec / 60) % 60)
    .toString()
    .padStart(2, "0")}:${(sec % 60).toString().padStart(2, "0")}`;
};
const button =
  "rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed";

export function TranscriptReviewEntry({
  item,
  legacy,
}: {
  item: string;
  legacy: React.ReactNode;
}) {
  const [state, setState] = useState<{
    doc?: Document;
    error?: string;
    legacy?: boolean;
    candidate?: Candidate;
  }>({});
  useEffect(() => {
    let alive = true;
    request<{ mode: string }>(
      `/api/sc_api/review_mode/${encodeURIComponent(item)}`,
    )
      .then(async ({ mode }) => {
        if (mode === "legacy") {
          if (alive) setState({ legacy: true });
          return;
        }
        if (mode === "candidate") {
          const candidate = await request<Candidate>(
            `/api/sc_api/review_candidate/${encodeURIComponent(item)}`,
          );
          if (alive) setState({ candidate });
          return;
        }
        const doc = await loadDocument(
          `/api/sc_api/review_document/${encodeURIComponent(item)}`,
        );
        if (alive) setState({ doc });
      })
      .catch((error) => {
        if (alive) setState({ error: error.message });
      });
    return () => {
      alive = false;
    };
  }, [item]);

  if (state.candidate)
    return (
      <CandidateAdoption
        item={item}
        initial={state.candidate}
        onAdopt={(doc) => setState({ doc })}
      />
    );
  if (state.legacy) return <>{legacy}</>;
  if (!state.doc)
    return (
      <p className="p-8 text-slate-600">{state.error || "正在載入圖文審核…"}</p>
    );
  return <TranscriptProofreader key={item} item={item} initial={state.doc} />;
}

function TranscriptProofreader({
  item,
  initial,
}: {
  item: string;
  initial: Document;
}) {
  const base = `/api/sc_api/review_document/${encodeURIComponent(item)}`;
  const [doc, setDoc] = useState(initial);
  const current = useRef(initial);
  const [selected, setSelected] = useState(initial.script[0]?.index || "");
  const [activeGroup, setActiveGroup] = useState<SlideInspection>();
  const [peekSlide, setPeekSlide] = useState<string>();
  const [baseline, setBaseline] = useState<Row[] | null>(null);
  const selectParagraph = useCallback((id: string) => {
    setSelected(id);
    setActiveGroup(undefined);
    setPeekSlide(undefined);
  }, []);
  const [status, setStatus] = useState("已自動儲存");
  const [failure, setFailure] = useState("");
  const [ready, setReady] = useState(false);
  const editor = useRef<CodeMirrorEditor>();
  const focusNext = useRef(false);
  const historyPanel = useRef<HTMLElement>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [history, setHistory] = useState<Revision[] | null>(null);
  const [snapshot, setSnapshot] = useState<{
    script: Row[];
    context: Context;
    revision: number;
  }>();
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [recovered, setRecovered] = useState<Pending>();
  const queue = useRef<Operation[]>([]);
  const packet = useRef<Packet>();
  const blocked = useRef(false);
  const initialized = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const running = useRef<Promise<void>>();
  const persistTail = useRef(Promise.resolve());
  const durable = useRef(true);
  const segmentEnd = useRef<number>();
  const video = useRef<HTMLVideoElement>(null);
  const [segmentPause, setSegmentPause] = useState(true);
  const composing = useRef(false);
  const [tabId] = useState(() => {
    if (typeof window === "undefined") return "";
    const key = "transcript-review-tab";
    let id = sessionStorage.getItem(key);
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem(key, id);
    }
    return id;
  });
  const identity = `${initial.actor}:${item}:${tabId}`;
  const options = useMemo<Options>(
    () => ({
      autofocus: false,
      spellChecker: false,
      status: false,
      minHeight: "180px",
      toolbar: [
        "heading",
        "bold",
        "italic",
        "strikethrough",
        "code",
        "quote",
        "unordered-list",
        "ordered-list",
        "link",
        "table",
        "horizontal-rule",
      ],
      shortcuts: {
        togglePreview: null,
        toggleSideBySide: null,
        toggleFullScreen: null,
      },
    }),
    [],
  );
  useEffect(() => {
    if (history === null) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = historyPanel.current;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel
      ?.querySelector<HTMLButtonElement>("button")
      ?.focus({ preventScroll: true });
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setHistory(null);
        setSnapshot(undefined);
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const controls = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],input,textarea,select,[tabindex="0"]',
        ),
      );
      const first = controls[0],
        last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      document.body.style.overflow = oldOverflow;
      previous?.focus({ preventScroll: true });
    };
  }, [history === null]);
  const updateDoc = useCallback((next: Document) => {
    current.current = next;
    setDoc(next);
  }, []);
  const persist = useCallback(() => {
    const value: Pending = structuredClone({
      baseVersion: current.current.version,
      operations: queue.current,
      packet: packet.current,
    });
    const task = persistTail.current
      .catch(() => {})
      .then(() =>
        draftStorage(
          identity,
          value.operations.length || value.packet ? value : null,
        ),
      )
      .then(
        () => {
          durable.current = true;
        },
        (error) => {
          durable.current = false;
          throw error;
        },
      );
    persistTail.current = task;
    return task;
  }, [identity]);
  const acknowledge = useCallback(
    (result: Receipt) => {
      updateDoc({
        ...current.current,
        version: result.version,
        paragraph_shas: result.paragraph_shas,
        group_shas: result.group_shas,
        context: {
          ...current.current.context,
          revision: result.revision,
          reviews: result.reviews,
        },
      });
      packet.current = undefined;
    },
    [updateDoc],
  );
  const flush = useCallback(async () => {
    if (composing.current) throw new Error("請完成目前的中文輸入後再操作");
    if (timer.current) clearTimeout(timer.current);
    if (running.current) {
      await running.current;
    }
    if (blocked.current) throw new Error("同步已暫停；本機修改仍保留");
    const work = async () => {
      while (packet.current || queue.current.length) {
        if (!packet.current) {
          packet.current = {
            operation_id: crypto.randomUUID(),
            version: current.current.version,
            epoch: current.current.epoch,
            operations: queue.current.splice(0),
          };
        }
        await persist(); // Durable operation identity before sending anything.
        setStatus("正在自動儲存…");
        const pending = packet.current;
        let result: Receipt;
        try {
          result = await request<Receipt>(base, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "If-Match": `"${pending.version}"`,
            },
            body: JSON.stringify(pending),
          });
        } catch (error) {
          if (error instanceof RequestError && error.status < 500) throw error;
          const receipt = await request<Receipt | null>(
            `${base}/operations/${pending.operation_id}`,
          );
          if (!receipt) throw error;
          result = receipt;
        }
        acknowledge(result);
        await persist();
      }
      setDirty(false);
      setStatus("已自動儲存");
      setFailure("");
    };
    const task = work();
    running.current = task;
    try {
      await task;
    } catch (error) {
      blocked.current = true;
      setFailure((error as Error).message);
      setStatus(
        durable.current ? "同步暫停，修改保留在本機" : "尚未同步；請下載備份",
      );
      throw error;
    } finally {
      if (running.current === task) running.current = undefined;
    }
  }, [acknowledge, base, persist]);
  const enqueue = useCallback(
    (op: Operation) => {
      if (!current.current.can_edit || blocked.current) return;
      // Coalesce only unsent edits to the same field. In-flight packets are immutable.
      const key = (v: Operation) => `${v.kind}:${v.id}:${v.region_id || ""}`;
      const index = queue.current.findIndex((v) => key(v) === key(op));
      if (index >= 0) queue.current[index] = op;
      else queue.current.push(op);
      setDirty(true);
      setStatus("待自動儲存…");
      persist().catch((error) => {
        blocked.current = true;
        setFailure(`無法保留本機草稿：${error.message}`);
      });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        if (!composing.current) void flush().catch(() => {});
      }, 900);
    },
    [flush, persist],
  );
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    let cached: Pending | undefined;
    draftStorage(identity)
      .then(async (saved) => {
        cached = saved;
        const command = await draftStorage(identity + ":command");
        if (command?.packet) {
          const receipt = await request<Receipt | null>(
            `${base}/operations/${command.packet.operation_id}`,
          );
          if (receipt) updateDoc(await loadDocument(base));
          // A command with no committed receipt remains an explicit user action.
          // CAS prevents a late original request and a repeated action both landing.
          await draftStorage(identity + ":command", null);
          if (!receipt) setStatus("上次操作未確認完成；請檢查目前狀態後再操作");
        }
        if (!saved) return;
        if (saved.packet) {
          const receipt = await request<Receipt | null>(
            `${base}/operations/${saved.packet.operation_id}`,
          );
          if (receipt) {
            saved.packet = undefined;
            saved.baseVersion = receipt.version;
          }
        }
        if (saved.operations.length || saved.packet) {
          blocked.current = true;
          setRecovered(saved);
          setFailure("找到上次尚未同步的修改；請先檢視或恢復。");
        } else await draftStorage(identity, null);
      })
      .catch((error) => {
        blocked.current = true;
        if (cached?.operations.length || cached?.packet) setRecovered(cached);
        setFailure(error.message);
      })
      .finally(() => setReady(true));
  }, [base, identity, updateDoc]);
  useEffect(() => {
    const leave = (event: BeforeUnloadEvent) => {
      if (queue.current.length || packet.current || recovered) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    const navigate = (event: MouseEvent) => {
      const a = (event.target as HTMLElement).closest(
        "a[href]",
      ) as HTMLAnchorElement | null;
      if (
        !a ||
        a.target === "_blank" ||
        a.download ||
        (!queue.current.length && !packet.current)
      )
        return;
      event.preventDefault();
      void flush()
        .then(() => {
          window.location.href = a.href;
        })
        .catch(() => {});
    };
    window.addEventListener("beforeunload", leave);
    document.addEventListener("click", navigate, true);
    return () => {
      window.removeEventListener("beforeunload", leave);
      document.removeEventListener("click", navigate, true);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [flush, recovered]);
  const editText = useCallback(
    (text: string) => {
      if (!current.current.can_edit || blocked.current) return;
      const reviews = { ...current.current.context.reviews };
      delete reviews["p:" + selected];
      updateDoc({
        ...current.current,
        context: { ...current.current.context, reviews },
        script: current.current.script.map((p) =>
          p.index === selected ? { ...p, text } : p,
        ),
      });
      enqueue({ kind: "text", id: selected, text });
    },
    [enqueue, selected, updateDoc],
  );
  const command = async (action: string) => {
    setBusy(true);
    editor.current?.setOption("readOnly", true);
    setFailure("");
    try {
      await flush();
      const value: Packet = {
        version: current.current.version,
        epoch: current.current.epoch,
        operation_id: crypto.randomUUID(),
        action,
      };
      // Commands have their own durable receipt; do not automatically replay a publication.
      const key = identity + ":command";
      await draftStorage(key, {
        baseVersion: value.version,
        operations: [],
        packet: value,
      });
      try {
        await request(`${base}/${action === "publish" ? "publish" : "claim"}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "If-Match": `"${value.version}"`,
          },
          body: JSON.stringify(value),
        });
      } catch (error) {
        if (error instanceof RequestError && error.status < 500) {
          await draftStorage(key, null);
          throw error;
        }
        const receipt = await request(
          `${base}/operations/${value.operation_id}`,
        );
        if (!receipt) throw error;
      }
      await draftStorage(key, null);
      updateDoc(await loadDocument(base));
      setStatus(action === "publish" ? "已發佈目前版本" : "已更新認領狀態");
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (
    kind: "paragraph_review" | "group_review",
    id: string,
    decision: string,
  ) => {
    setBusy(true);
    try {
      await flush();
      enqueue({
        kind,
        id,
        decision,
        content_sha256: (kind === "paragraph_review"
          ? current.current.paragraph_shas
          : current.current.group_shas)[id],
      });
      await flush();
      if (kind === "paragraph_review" && decision === "confirmed") {
        const n = current.current.script.findIndex((p) => p.index === id);
        if (n < current.current.script.length - 1) {
          focusNext.current = true;
          selectParagraph(current.current.script[n + 1].index);
        }
      }
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const modifyGroup = (op: Operation) => {
    const groups = current.current.context.groups.map((g) =>
      g.id === op.id
        ? {
            ...g,
            ...(op.kind === "representative"
              ? { slide_id: op.slide_id as string }
              : op.kind === "placement"
                ? { before_paragraph_id: op.paragraph_id as string }
                : {
                    hidden: op.hidden as boolean,
                    reason: op.reason as string,
                  }),
          }
        : g,
    );
    updateDoc({
      ...current.current,
      context: { ...current.current.context, groups },
    });
    enqueue(op);
  };
  const discardLocal = async () => {
    setBusy(true);
    editor.current?.setOption("readOnly", true);
    try {
      download();
      await persistTail.current.catch(() => {});
      await draftStorage(
        identity + ":archive:" + Date.now(),
        recovered || {
          baseVersion: current.current.version,
          operations: queue.current,
          packet: packet.current,
        },
      );
      queue.current = [];
      packet.current = undefined;
      await draftStorage(identity, null);
      updateDoc(await loadDocument(base));
      blocked.current = false;
      setRecovered(undefined);
      setFailure("");
      setDirty(false);
      setStatus("已載入最新稿件；原本修改已下載備份");
      editor.current?.clearHistory();
    } finally {
      setBusy(false);
    }
  };
  const editOCR = (id: string, region_id: string, text: string) => {
    const c = structuredClone(current.current.context);
    c.visual_assets[id].content.text_regions.find(
      (r) => r.id === region_id,
    )!.text = text;
    updateDoc({ ...current.current, context: c });
    enqueue({ kind: "ocr", id, region_id, text });
  };
  const play = (id: string) => {
    const binding = current.current.context.paragraph_bindings[id];
    selectParagraph(id);
    segmentEnd.current = binding.end_ms / 1000;
    if (video.current) {
      video.current.currentTime = binding.start_ms / 1000;
      void video.current.play();
    }
  };
  const row = doc.script.find((p) => p.index === selected);
  const group = selectReferenceGroup(doc.context.groups, selected, activeGroup);
  const shownSlide =
    group && peekSlide && group.member_ids.includes(peekSlide)
      ? peekSlide
      : group?.slide_id;
  const assetId = shownSlide && doc.context.occurrences[shownSlide]?.asset_id;
  const asset = assetId && doc.context.visual_assets[assetId];
  const confirmed = doc.script.filter(
    (p) =>
      doc.context.reviews["p:" + p.index]?.decision === "confirmed" &&
      doc.context.reviews["p:" + p.index]?.sha256 ===
        doc.paragraph_shas[p.index],
  ).length;
  const groupsConfirmed = doc.context.groups.filter(
    (g) =>
      doc.context.reviews["g:" + g.id]?.decision === "confirmed" &&
      doc.context.reviews["g:" + g.id]?.sha256 === doc.group_shas[g.id],
  ).length;
  const disabled = !doc.can_edit || busy || blocked.current || !ready;
  useEffect(() => {
    editor.current?.setOption("readOnly", disabled);
  }, [disabled]);
  const openHistory = async () => {
    try {
      await flush();
      setHistory(await request<Revision[]>(base + "/history"));
    } catch (error) {
      setFailure((error as Error).message);
    }
  };
  const download = () => {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            item,
            actor: doc.actor,
            script: doc.script,
            pending: recovered || {
              operations: queue.current,
              packet: packet.current,
            },
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${item}-本機修改.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const restore = async () => {
    if (!recovered || recovered.baseVersion !== current.current.version) {
      setFailure(
        "來源版本已變更，請下載本機修改並與目前版本比較；不會自動覆蓋。",
      );
      return;
    }
    setBusy(true);
    editor.current?.setOption("readOnly", true);
    try {
      const pending = [
        ...(recovered.packet?.operations || []),
        ...recovered.operations,
      ];
      // Text and OCR restore visibly; all saved operations remain exact for server validation.
      const d = structuredClone(current.current);
      for (const op of pending) {
        if (op.kind === "text") {
          const p = d.script.find((p) => p.index === op.id);
          if (p) p.text = op.text as string;
        }
        if (op.kind === "ocr") {
          const r = d.context.visual_assets[op.id!]?.content.text_regions.find(
            (r) => r.id === op.region_id,
          );
          if (r) r.text = op.text as string;
        }
      }
      updateDoc(d);
      queue.current = recovered.operations;
      packet.current = recovered.packet;
      blocked.current = false;
      setRecovered(undefined);
      setFailure("");
      setDirty(true);
      await flush();
      updateDoc(await loadDocument(base));
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main
      onCompositionStartCapture={() => {
        composing.current = true;
      }}
      onCompositionEndCapture={() => {
        composing.current = false;
        void flush().catch(() => {});
      }}
      className="mx-auto max-w-[1700px] p-3 md:p-6 text-slate-800"
    >
      <header className="sticky top-0 z-20 rounded-xl border bg-white/95 p-4 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs text-slate-500">講道圖文審核 · {item}</div>
            <h1 className="text-xl font-semibold">
              {doc.context.editorial_metadata.title || doc.header.title || item}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {doc.header.assigned_to_name
                ? `認領人：${doc.header.assigned_to_name}`
                : "尚未認領"}{" "}
              · 第 {doc.context.revision} 版 ·{" "}
              <span role="status">{status}</span>
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className={button} onClick={() => void openHistory()}>
              編輯歷史
            </button>
            <button
              className={button}
              onClick={() => setMetadataOpen((v) => !v)}
            >
              標題與簡介
            </button>
            <button
              className={button}
              onClick={() => {
                if (baseline) setBaseline(null);
                else
                  void request<Row[]>(base + "/baseline")
                    .then(setBaseline)
                    .catch((e) => setFailure(e.message));
              }}
            >
              {baseline ? "關閉原稿差異" : "原稿差異"}
            </button>
            {doc.context.bookmarks?.[doc.actor] && (
              <button
                className={button}
                onClick={() => {
                  selectParagraph(doc.context.bookmarks![doc.actor]);
                  document
                    .getElementById(
                      `paragraph-${doc.context.bookmarks![doc.actor]}`,
                    )
                    ?.scrollIntoView({ block: "center" });
                }}
              >
                前往書籤
              </button>
            )}
            {doc.can_edit && (
              <button
                className={button}
                disabled={disabled}
                onClick={() => {
                  updateDoc({
                    ...current.current,
                    context: {
                      ...current.current.context,
                      bookmarks: {
                        ...current.current.context.bookmarks,
                        [doc.actor]: selected,
                      },
                    },
                  });
                  enqueue({ kind: "bookmark", id: selected });
                }}
              >
                記住目前段落
              </button>
            )}
            {doc.can_claim && (
              <button
                className={button}
                disabled={busy}
                onClick={() => void command("claim")}
              >
                認領並開始校對
              </button>
            )}
            {doc.can_takeover && !doc.can_edit && doc.header.assigned_to && (
              <button
                className={button}
                disabled={busy}
                onClick={() => void command("takeover")}
              >
                接手編輯
              </button>
            )}
            {doc.can_edit && (
              <>
                <button
                  className={button}
                  disabled={busy || blocked.current || !ready}
                  onClick={() => void command("unclaim")}
                >
                  取消認領
                </button>
                {doc.can_publish && (
                  <button
                    className="rounded-md bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-40"
                    disabled={busy || blocked.current || !ready}
                    onClick={() => void command("publish")}
                  >
                    發佈逐字稿
                  </button>
                )}
              </>
            )}
            {doc.published && (
              <a
                className={button}
                href={`/transcripts/${encodeURIComponent(item)}`}
                target="_blank"
                rel="noreferrer"
              >
                查看已發佈版本
              </a>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-4 text-sm">
          <span>
            口述已核對 {confirmed}/{doc.script.length}
          </span>
          <span>
            圖片已處理 {groupsConfirmed}/{doc.context.groups.length}
          </span>
          <button
            className="text-blue-700"
            onClick={() => {
              const p = doc.script.find(
                (p) =>
                  doc.context.reviews["p:" + p.index]?.decision !== "confirmed",
              );
              if (p) selectParagraph(p.index);
            }}
          >
            前往待核對段落
          </button>
        </div>
        {failure && (
          <div
            role="alert"
            className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
          >
            {failure}
            <div className="mt-2 flex gap-2">
              <button className={button} onClick={download}>
                下載本機修改
              </button>
              {recovered && (
                <button className={button} onClick={() => void restore()}>
                  恢復待存修改
                </button>
              )}
              <button
                className={button}
                onClick={() =>
                  void discardLocal().catch((e) => setFailure(e.message))
                }
              >
                下載備份並載入最新稿
              </button>
              {!recovered && blocked.current && (
                <button
                  className={button}
                  onClick={() => {
                    blocked.current = false;
                    setFailure("");
                    void flush().catch(() => {});
                  }}
                >
                  重試同步
                </button>
              )}
            </div>
          </div>
        )}
      </header>
      {metadataOpen && (
        <section className="my-4 rounded-xl border bg-white p-4">
          <p className="mb-3 text-sm text-slate-500">
            講道資料修改後自動儲存；再次發佈才會更新公開版本。
          </p>
          {(["title", "summary", "keypoints"] as const).map((key) => (
            <label key={key} className="mb-3 block text-sm">
              {{ title: "標題", summary: "簡介", keypoints: "重點" }[key]}
              <textarea
                disabled={disabled}
                className="mt-1 block w-full rounded-md border p-2"
                rows={key === "title" ? 1 : 3}
                value={doc.context.editorial_metadata[key] || ""}
                onChange={(e) => {
                  const value = {
                    ...current.current.context.editorial_metadata,
                    [key]: e.target.value,
                  };
                  updateDoc({
                    ...current.current,
                    context: {
                      ...current.current.context,
                      editorial_metadata: value,
                    },
                  });
                  enqueue({ kind: "metadata", value });
                }}
              />
            </label>
          ))}
          <div className="mt-4">
            <h3 className="text-sm font-medium">核心經文</h3>
            {(doc.context.editorial_metadata.core_bible_verse || []).map(
              (verse, i) => (
                <div
                  key={i}
                  className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_3fr]"
                >
                  {(["book", "chapter_verse", "text"] as const).map((key) => (
                    <input
                      key={key}
                      aria-label={`${i + 1} ${{ book: "書卷", chapter_verse: "章節", text: "經文" }[key]}`}
                      disabled={disabled}
                      className="rounded border p-2 text-sm"
                      value={verse[key] || ""}
                      onChange={(e) => {
                        const verses = [
                          ...(current.current.context.editorial_metadata
                            .core_bible_verse || []),
                        ];
                        verses[i] = { ...verses[i], [key]: e.target.value };
                        const value = {
                          ...current.current.context.editorial_metadata,
                          core_bible_verse: verses,
                        };
                        updateDoc({
                          ...current.current,
                          context: {
                            ...current.current.context,
                            editorial_metadata: value,
                          },
                        });
                        enqueue({ kind: "metadata", value });
                      }}
                    />
                  ))}
                </div>
              ),
            )}
            <button
              className={button + " mt-2"}
              disabled={disabled}
              onClick={() => {
                const value = {
                  ...current.current.context.editorial_metadata,
                  core_bible_verse: [
                    ...(current.current.context.editorial_metadata
                      .core_bible_verse || []),
                    { book: "", chapter_verse: "", text: "" },
                  ],
                };
                updateDoc({
                  ...current.current,
                  context: {
                    ...current.current.context,
                    editorial_metadata: value,
                  },
                });
                enqueue({ kind: "metadata", value });
              }}
            >
              增加經文欄位
            </button>
          </div>
        </section>
      )}
      <div className="mt-4 grid items-start gap-5 lg:grid-cols-[minmax(310px,0.85fr)_minmax(0,1.6fr)]">
        <aside className="space-y-4 lg:sticky lg:top-40 max-h-[calc(100vh-11rem)] overflow-y-auto">
          <section className="overflow-hidden rounded-xl border bg-white">
            <video
              ref={video}
              controls
              preload="metadata"
              src={base + "/media"}
              className="w-full bg-black"
              onTimeUpdate={() => {
                if (
                  segmentPause &&
                  segmentEnd.current !== undefined &&
                  video.current &&
                  video.current.currentTime >= segmentEnd.current
                )
                  video.current.pause();
              }}
            />
            <div className="flex flex-wrap items-center gap-2 p-3">
              <button
                className={button}
                onClick={() => {
                  if (video.current) video.current.currentTime -= 5;
                }}
              >
                −5 秒
              </button>
              <button className={button} onClick={() => row && play(row.index)}>
                播放本段
              </button>
              <button
                className={button}
                onClick={() => {
                  if (video.current) video.current.currentTime += 5;
                }}
              >
                ＋5 秒
              </button>
              <label className="text-xs">
                <input
                  type="checkbox"
                  checked={segmentPause}
                  onChange={(e) => setSegmentPause(e.target.checked)}
                />{" "}
                段末暫停
              </label>
            </div>
          </section>
          <section className="rounded-xl border bg-white p-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold">投影片參考</h2>
              <select
                aria-label="選擇圖片組"
                className="max-w-[190px] rounded border p-1 text-sm"
                value={group?.id || ""}
                onChange={(e) => {
                  setActiveGroup(
                    e.target.value
                      ? { paragraphId: selected, groupId: e.target.value }
                      : undefined,
                  );
                  setPeekSlide(undefined);
                }}
              >
                <option value="">本段圖片</option>
                {doc.context.groups.map((g, i) => (
                  <option key={g.id} value={g.id}>
                    圖片 {i + 1}
                    {doc.context.reviews["g:" + g.id]?.decision ===
                      "confirmed" &&
                    doc.context.reviews["g:" + g.id]?.sha256 ===
                      doc.group_shas[g.id]
                      ? " ✓"
                      : ""}
                    {g.hidden ? "（隱藏）" : ""}
                    {g.needs_review ? " · 待釐清" : ""}
                  </option>
                ))}
              </select>
            </div>
            {group && asset && assetId ? (
              <>
                <a
                  href={`${base}/assets/${assetId}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    className="mt-3 w-full rounded"
                    alt="影片中的原始投影片"
                    src={`${base}/assets/${assetId}`}
                  />
                </a>
                <p className="mt-2 text-xs text-slate-500">
                  影片時間{" "}
                  {stamp(doc.context.occurrences[shownSlide!].time_ms || 0)} ·
                  原始畫面 · {group.member_ids.length} 個逐步狀態 · SVG
                  尚未核實，使用原圖核對
                </p>
                {group.needs_review && (
                  <p className="mt-2 rounded bg-amber-50 p-2 text-sm">
                    此組含不確定的中間內容，請逐張查看。{group.reason}
                  </p>
                )}
                <div
                  className="my-3 flex flex-wrap gap-2"
                  aria-label="本組逐步畫面"
                >
                  {group.member_ids.map((sid, i) => (
                    <button
                      key={sid}
                      className={`rounded border p-1 ${shownSlide === sid ? "border-blue-600" : "border-slate-200"}`}
                      title={`查看中間畫面 ${i + 1}`}
                      onClick={() => setPeekSlide(sid)}
                    >
                      <img
                        className="h-10 w-16 object-contain"
                        alt={`中間畫面 ${i + 1}`}
                        src={`${base}/assets/${doc.context.occurrences[sid].asset_id}`}
                      />
                    </button>
                  ))}
                </div>
                <button
                  className={button}
                  onClick={() => {
                    if (video.current) {
                      segmentEnd.current = undefined;
                      video.current.currentTime =
                        (doc.context.occurrences[shownSlide!].time_ms || 0) /
                        1000;
                      void video.current.play();
                    }
                  }}
                >
                  觀看此畫面在原片的上下文
                </button>
                <label className="mt-3 block text-sm">
                  本組代表畫面
                  <select
                    disabled={disabled}
                    className="mt-1 w-full rounded border p-2"
                    value={group.slide_id}
                    onChange={(e) => {
                      setPeekSlide(undefined);
                      modifyGroup({
                        kind: "representative",
                        id: group.id,
                        slide_id: e.target.value,
                      });
                    }}
                  >
                    {group.member_ids.map((sid, i) => (
                      <option key={sid} value={sid}>
                        畫面 {i + 1} · {sid}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="mt-3 block text-sm">
                  放在此段之前
                  <select
                    disabled={disabled}
                    className="mt-1 w-full rounded border p-2"
                    value={group.before_paragraph_id || ""}
                    onChange={(e) =>
                      modifyGroup({
                        kind: "placement",
                        id: group.id,
                        paragraph_id: e.target.value,
                      })
                    }
                  >
                    <option value="" disabled>
                      尚未指定
                    </option>
                    {doc.script.map((p) => (
                      <option key={p.index} value={p.index}>
                        {stamp(
                          doc.context.paragraph_bindings[p.index].start_ms,
                        )}{" "}
                        · {p.text.slice(0, 30)}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="mb-2 mt-4 text-sm font-medium">
                  投影片文字（非口述；修改自動儲存）
                </p>
                {asset.content.text_regions.map((r) => (
                  <textarea
                    key={r.id}
                    aria-label={`投影片文字 ${r.id}`}
                    disabled={disabled}
                    value={r.text}
                    className="mb-2 w-full rounded border p-2 text-sm"
                    rows={Math.max(1, Math.min(6, r.text.split("\n").length))}
                    onChange={(e) => editOCR(assetId, r.id, e.target.value)}
                  />
                ))}
                <label className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={group.hidden}
                    onChange={(e) =>
                      modifyGroup({
                        kind: "hidden",
                        id: group.id,
                        hidden: e.target.checked,
                        reason:
                          group.reason || "重複或不適合閱讀展示，保留來源",
                      })
                    }
                  />{" "}
                  在閱讀版隱藏（來源仍保留）
                </label>
                <label className="mt-2 block text-xs">
                  本組處置原因
                  <textarea
                    disabled={disabled}
                    className="mt-1 w-full rounded border p-2 text-sm"
                    value={group.reason}
                    onChange={(e) =>
                      modifyGroup({
                        kind: "hidden",
                        id: group.id,
                        hidden: group.hidden,
                        reason: e.target.value,
                      })
                    }
                  />
                </label>
                <p className="my-3 text-xs text-slate-500">
                  確認範圍：代表原圖、文字、閱讀位置及本組中間內容；不等於批准
                  SVG 或文庫來源准入。
                </p>
                <div className="flex gap-2">
                  <button
                    disabled={disabled || !group.reason.trim()}
                    className={button}
                    onClick={() =>
                      void confirm("group_review", group.id, "confirmed")
                    }
                  >
                    確認本組處置
                  </button>
                  <button
                    disabled={disabled}
                    className={button}
                    onClick={() =>
                      void confirm("group_review", group.id, "question")
                    }
                  >
                    有疑問
                  </button>
                </div>
              </>
            ) : (
              <p className="py-6 text-sm text-slate-500">本段沒有投影片。</p>
            )}
          </section>
        </aside>
        <section className="min-w-0 space-y-3" aria-label="逐段校對">
          {doc.script.map((p, i) => {
            const active = p.index === selected;
            const binding = doc.context.paragraph_bindings[p.index];
            const review = doc.context.reviews["p:" + p.index];
            const reviewed =
              review?.sha256 === doc.paragraph_shas[p.index] &&
              review.decision === "confirmed" &&
              !queue.current.some(
                (o) => o.id === p.index && o.kind === "text",
              ) &&
              !packet.current?.operations?.some(
                (o) => o.id === p.index && o.kind === "text",
              );
            return (
              <article
                key={p.index}
                id={`paragraph-${p.index}`}
                className={`rounded-xl border bg-white p-4 ${active ? "border-blue-400 ring-1 ring-blue-100" : "border-slate-200"}`}
              >
                {doc.context.groups
                  .filter((g) => !g.hidden && g.before_paragraph_id === p.index)
                  .map((g) => (
                    <button
                      key={g.id}
                      className="mb-3 flex w-full items-center gap-2 rounded-md bg-slate-100 p-2 text-left text-sm"
                      onClick={() =>
                        setActiveGroup({ paragraphId: selected, groupId: g.id })
                      }
                    >
                      <img
                        className="h-9 w-14 rounded object-contain"
                        alt="投影片錨點"
                        src={`${base}/assets/${doc.context.occurrences[g.slide_id].asset_id}`}
                      />{" "}
                      {g.needs_review
                        ? "待釐清畫面 · 檢視原圖與文字"
                        : "投影片 · 檢視原圖與文字"}
                    </button>
                  ))}
                <div className="mb-3 flex items-center justify-between gap-2 text-xs">
                  <button
                    className="text-blue-700"
                    onClick={() => play(p.index)}
                  >
                    ▶ {stamp(binding.start_ms)}–{stamp(binding.end_ms)}
                  </button>
                  <span
                    className={reviewed ? "text-green-700" : "text-slate-500"}
                  >
                    {i + 1}/{doc.script.length} ·{" "}
                    {reviewed
                      ? "已核對"
                      : review?.decision === "question"
                        ? "有疑問"
                        : "待核對"}
                  </span>
                </div>
                {baseline &&
                  baseline.find((v) => v.index === p.index)?.text !==
                    p.text && (
                    <details className="mb-3 rounded bg-amber-50 p-2 text-sm">
                      <summary>與原稿的差異</summary>
                      <del className="block whitespace-pre-wrap text-red-800">
                        {baseline.find((v) => v.index === p.index)?.text}
                      </del>
                      <ins className="block whitespace-pre-wrap text-green-800 no-underline">
                        {p.text}
                      </ins>
                    </details>
                  )}
                {active && doc.can_edit ? (
                  <div
                    onCompositionStart={() => {
                      composing.current = true;
                    }}
                    onCompositionEnd={() => {
                      composing.current = false;
                      void flush().catch(() => {});
                    }}
                    className="[&_.CodeMirror]:text-base [&_.CodeMirror]:leading-8"
                  >
                    <MarkdownEditor
                      id={`review-editor-${p.index}`}
                      value={p.text}
                      onChange={editText}
                      options={options}
                      getCodemirrorInstance={(cm) => {
                        editor.current = cm;
                        if (focusNext.current) {
                          focusNext.current = false;
                          cm.focus();
                        }
                        cm.setOption("readOnly", disabled);
                      }}
                    />
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        className={button}
                        disabled={disabled}
                        onClick={() =>
                          void confirm("paragraph_review", p.index, "confirmed")
                        }
                      >
                        確認並下一段
                      </button>
                      <button
                        className={button}
                        disabled={disabled}
                        onClick={() =>
                          void confirm("paragraph_review", p.index, "question")
                        }
                      >
                        有疑問
                      </button>
                      <button
                        className={button}
                        disabled={i === 0 || busy}
                        onClick={() => selectParagraph(doc.script[i - 1].index)}
                      >
                        上一段
                      </button>
                      <button
                        className={button}
                        disabled={i === doc.script.length - 1 || busy}
                        onClick={() => selectParagraph(doc.script[i + 1].index)}
                      >
                        下一段
                      </button>
                    </div>
                  </div>
                ) : (
                  <div
                    className="prose max-w-none cursor-text leading-8"
                    onClick={() => {
                      selectParagraph(p.index);
                    }}
                  >
                    <TranscriptText text={p.text} />
                  </div>
                )}
              </article>
            );
          })}
        </section>
      </div>
      {history && (
        <div
          className="fixed inset-0 z-40 flex justify-end bg-black/20"
          onClick={() => {
            setHistory(null);
            setSnapshot(undefined);
          }}
        >
          <section
            ref={historyPanel}
            role="dialog"
            aria-modal="true"
            aria-label="編輯歷史"
            className="h-full w-full max-w-4xl overflow-y-auto bg-white p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between">
              <h2 className="text-lg font-semibold">編輯歷史</h2>
              <button
                className={button}
                onClick={() => {
                  setHistory(null);
                  setSnapshot(undefined);
                }}
              >
                關閉
              </button>
            </div>
            <p className="my-3 text-sm text-slate-500">
              選擇版本，檢視該次修改與上一版本的差異。
            </p>
            {history.map((h) => (
              <button
                key={h.revision}
                className={`mb-2 block w-full rounded border p-3 text-left text-sm ${snapshot?.revision === h.revision ? "border-blue-600" : ""}`}
                onClick={() =>
                  void request<{
                    script: Row[];
                    context: Context;
                    revision: number;
                  }>(`${base}/revisions/${h.revision}`)
                    .then(setSnapshot)
                    .catch((e) => setFailure(e.message))
                }
              >
                第 {h.revision} 版 · {new Date(h.time).toLocaleString()} ·{" "}
                {h.actor} ·{" "}
                {(
                  {
                    edit: "自動儲存",
                    adopt: "採用候選",
                    claim: "認領",
                    unclaim: "取消認領",
                    takeover: "接手",
                    publish: "發佈",
                  } as Record<string, string>
                )[h.kind] || h.kind}
              </button>
            ))}
            {history.length > 0 && (
              <button
                className={button}
                onClick={() =>
                  void request<Revision[]>(
                    `${base}/history?before=${history[history.length - 1].revision}`,
                  ).then((more) => setHistory([...history, ...more]))
                }
              >
                載入更早版本
              </button>
            )}
            {snapshot && <HistoryDiff base={base} snapshot={snapshot} />}
          </section>
        </div>
      )}
      {dirty && <span className="sr-only">尚有待同步修改</span>}
    </main>
  );
}

const TranscriptText = memo(function TranscriptText({
  text,
}: {
  text: string;
}) {
  return <ReactMarkdown>{text}</ReactMarkdown>;
});

function HistoryDiff({
  base,
  snapshot,
}: {
  base: string;
  snapshot: { script: Row[]; context: Context; revision: number };
}) {
  const [previous, setPrevious] = useState<{
    script: Row[];
    context: Context;
  }>();
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setPrevious(undefined);
    setError("");
    if (snapshot.revision > 1)
      request<{ script: Row[]; context: Context }>(
        `${base}/revisions/${snapshot.revision - 1}`,
      )
        .then((v) => {
          if (alive) setPrevious(v);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    else setPrevious({ script: [], context: {} as Context });
    return () => {
      alive = false;
    };
  }, [base, snapshot.revision]);
  if (error)
    return (
      <p role="alert" className="mt-4 text-red-700">
        無法比較歷史：{error}
      </p>
    );
  if (!previous) return <p>正在比較版本…</p>;
  const changes = snapshot.script.filter(
    (p) => previous.script.find((v) => v.index === p.index)?.text !== p.text,
  );
  const groups = snapshot.context.groups.filter(
    (g) =>
      JSON.stringify(previous.context.groups?.find((v) => v.id === g.id)) !==
      JSON.stringify(g),
  );
  const regions = Object.entries(snapshot.context.visual_assets).flatMap(
    ([aid, a]) =>
      a.content.text_regions
        .filter(
          (r) =>
            previous.context.visual_assets?.[aid]?.content.text_regions.find(
              (v) => v.id === r.id,
            )?.text !== r.text,
        )
        .map((r) => ({
          aid,
          region: r,
          before: previous.context.visual_assets?.[
            aid
          ]?.content.text_regions.find((v) => v.id === r.id)?.text,
        })),
  );
  const position = (id: string, c: Context) =>
    id && c.paragraph_bindings?.[id]
      ? `段落 ${id}（${stamp(c.paragraph_bindings[id].start_ms)}）`
      : "尚未指定";
  const reviews = Object.entries(snapshot.context.reviews).filter(
    ([key, v]) =>
      JSON.stringify(previous.context.reviews?.[key]) !== JSON.stringify(v),
  );
  return (
    <div className="mt-5 space-y-3">
      <h3 className="font-semibold">第 {snapshot.revision} 版的差異</h3>
      {changes.map((p) => (
        <div key={p.index} className="rounded border p-3">
          <p className="text-sm text-slate-500">段落 {p.index}</p>
          <del className="block whitespace-pre-wrap bg-red-50 p-2">
            {previous.script.find((v) => v.index === p.index)?.text ||
              "（新增）"}
          </del>
          <ins className="block whitespace-pre-wrap bg-green-50 p-2 no-underline">
            {p.text}
          </ins>
        </div>
      ))}
      {groups.map((g) => {
        const before = previous.context.groups?.find((v) => v.id === g.id);
        return (
          <div key={g.id} className="rounded border p-3 text-sm">
            <p className="font-medium">
              圖片 {snapshot.context.groups.indexOf(g) + 1}
            </p>
            <p>
              代表畫面：{before?.slide_id || "無"} → {g.slide_id}
            </p>
            <p>
              閱讀位置：
              {position(
                before?.before_paragraph_id || "",
                previous.context,
              )} → {position(g.before_paragraph_id, snapshot.context)}
            </p>
            <p>
              展示：{before?.hidden ? "隱藏" : "顯示"} →{" "}
              {g.hidden ? "隱藏" : "顯示"}
            </p>
            <p className="whitespace-pre-wrap">處置原因：{g.reason}</p>
          </div>
        );
      })}
      {regions.length > 0 && (
        <details>
          <summary>投影片文字變更（{regions.length} 處）</summary>
          {regions.map(({ aid, region, before }) => (
            <div
              key={aid + region.id}
              className="my-2 rounded border p-2 text-sm"
            >
              <del className="block whitespace-pre-wrap bg-red-50 p-2">
                {before || "（新增）"}
              </del>
              <ins className="block whitespace-pre-wrap bg-green-50 p-2 no-underline">
                {region.text || "（清空）"}
              </ins>
            </div>
          ))}
        </details>
      )}
      {(["title", "summary", "keypoints"] as const)
        .filter(
          (k) =>
            previous.context.editorial_metadata?.[k] !==
            snapshot.context.editorial_metadata[k],
        )
        .map((k) => (
          <div key={k} className="rounded border p-3 text-sm">
            <p>{{ title: "標題", summary: "簡介", keypoints: "重點" }[k]}</p>
            <del className="block whitespace-pre-wrap bg-red-50 p-2">
              {previous.context.editorial_metadata?.[k] || "（空白）"}
            </del>
            <ins className="block whitespace-pre-wrap bg-green-50 p-2 no-underline">
              {snapshot.context.editorial_metadata[k] || "（空白）"}
            </ins>
          </div>
        ))}
      {reviews.map(([key, r]) => (
        <p key={key} className="text-sm text-slate-600">
          {key.startsWith("p:")
            ? "段落 " + key.slice(2)
            : "圖片組 " + key.slice(2)}
          ：
          {{ confirmed: "已核對", question: "有疑問", pending: "待核對" }[
            r.decision
          ] || r.decision}{" "}
          · {r.actor}
        </p>
      ))}
      {!changes.length &&
        !groups.length &&
        !regions.length &&
        !reviews.length && (
          <p className="text-sm text-slate-500">
            口述正文未變更；本次為認領、書籤、講道資料或發佈操作。
          </p>
        )}
    </div>
  );
}

type Candidate = {
  actor: string;
  version: string;
  epoch: string;
  header: { title?: string; assigned_to?: string; assigned_to_name?: string };
  can_claim: boolean;
  can_adopt: boolean;
  can_takeover: boolean;
  source_video_sha256: string;
};
function CandidateAdoption({
  item,
  initial,
  onAdopt,
}: {
  item: string;
  initial: Candidate;
  onAdopt: (doc: Document) => void;
}) {
  const [candidate, setCandidate] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = `/api/sc_api/review_candidate/${encodeURIComponent(item)}`;
  const act = async (action: string) => {
    setBusy(true);
    setError("");
    try {
      const packet = {
        version: candidate.version,
        epoch: candidate.epoch,
        operation_id: crypto.randomUUID(),
        action,
      };
      await request(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "If-Match": `"${candidate.version}"`,
        },
        body: JSON.stringify(packet),
      });
      if (action === "adopt")
        onAdopt(
          await loadDocument(
            `/api/sc_api/review_document/${encodeURIComponent(item)}`,
          ),
        );
      else setCandidate(await request(url));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">
        {candidate.header.title || item}
      </h1>
      <p className="my-4">
        已有自動產生的圖文候選，包含分段口述、原片時間與投影片。請先認領，再明確採用候選開始核對。
      </p>
      <p className="my-4 text-sm text-slate-500">
        採用前會保留原稿基線；已有人工修改或已發佈時拒絕整份取代。採用不會自動發佈。
      </p>
      <p className="mb-4">
        認領人：
        {candidate.header.assigned_to_name ||
          candidate.header.assigned_to ||
          "尚未認領"}
      </p>
      <div className="flex gap-3">
        {candidate.can_claim && (
          <button
            className={button}
            disabled={busy}
            onClick={() => void act("claim")}
          >
            認領
          </button>
        )}
        {candidate.can_adopt && (
          <>
            <button
              className="rounded bg-blue-700 px-4 py-2 text-white disabled:opacity-40"
              disabled={busy}
              onClick={() => void act("adopt")}
            >
              採用圖文候選並開始審核
            </button>
            <button
              className={button}
              disabled={busy}
              onClick={() => void act("unclaim")}
            >
              取消認領
            </button>
          </>
        )}
        {candidate.can_takeover &&
          !candidate.can_adopt &&
          candidate.header.assigned_to && (
            <button
              className={button}
              disabled={busy}
              onClick={() => void act("takeover")}
            >
              接手編輯
            </button>
          )}
      </div>
      {error && (
        <p className="mt-4 text-red-700" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}
