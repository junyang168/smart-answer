// Shapes returned by backend/api/reference_commentary.py.

export const API = "/api/admin/reference-commentary";

export type PageSummary = {
  printed_page: string;
  chapters: number[];
  passages: string[];
  status: "ocr" | "proofread";
  gaps: number;
  pending_ocr: boolean;
};

export type Volume = {
  volume_id: string;
  title: string;
  chapters: [number, number];
  isbn: string | null;
  confirmed: boolean;
  pages: PageSummary[];
  proofread: number;
  missing_pages: number[];
  unassigned: { id: string }[];
};

export type ChapterPage = PageSummary & { volume_id: string; text: string };

export type Chapter = { chapter: number; pages: ChapterPage[]; missing_pages: number[] };

export type Revision = { at: string; from: string | null; to: string; source: "ocr" | "proofread" };

export type PageDetail = PageSummary & {
  volume_id: string;
  title: string;
  text: string;
  text_sha256: string;
  gaps: { line: number; column: number }[];
  revisions: Revision[];
  image_history: string[];
  pending_ocr_text: string | null;
  previous_page: string | null;
  next_page: string | null;
};

export type SearchHit = { volume_id: string; printed_page: string; chapters: number[]; snippet: string };

export const pageHref = (volumeId: string, page: string) =>
  `/admin/reference-commentary/pages/${encodeURIComponent(volumeId)}/${encodeURIComponent(page)}`;

export const chapterHref = (chapter: number) => `/admin/reference-commentary/chapters/${chapter}`;

export async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `服務回傳 ${response.status}`);
  }
  return (await response.json()) as T;
}

// Reading size is a per-reader convenience, so it lives in this browser only.
export const SIZES = ["prose-sm", "prose-base", "prose-lg", "prose-xl"] as const;

export function loadSize(): number {
  try {
    const value = Number(window.localStorage.getItem("reference-commentary-size"));
    return Number.isInteger(value) && value >= 0 && value < SIZES.length ? value : 1;
  } catch {
    return 1;
  }
}

export function saveSize(value: number) {
  try {
    window.localStorage.setItem("reference-commentary-size", String(value));
  } catch {
    // Private windows can refuse storage; the size just is not remembered.
  }
}
