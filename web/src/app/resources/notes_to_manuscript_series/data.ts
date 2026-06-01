const RAW_BACKEND_BASE =
  process.env.FULL_ARTICLE_SERVICE_URL ?? process.env.SC_API_SERVICE_URL;
const BACKEND_BASE = RAW_BACKEND_BASE?.replace(/\/$/, "");

if (!BACKEND_BASE) {
  throw new Error(
    "FULL_ARTICLE_SERVICE_URL (or SC_API_SERVICE_URL) is required for notes-to-manuscript resources",
  );
}

export interface NotesToManuscriptProject {
  id: string;
  title: string;
  google_doc_id?: string | null;
  google_doc_url?: string | null;
  available: boolean;
}

export interface NotesToManuscriptLecture {
  id: string;
  title: string;
  description?: string | null;
  folder?: string | null;
  projects: NotesToManuscriptProject[];
}

export interface NotesToManuscriptSeriesSummary {
  id: string;
  title: string;
  description?: string | null;
  folder?: string | null;
  project_type: string;
  lecture_count: number;
  project_count: number;
  available_project_count: number;
}

export interface NotesToManuscriptSeriesDetail {
  id: string;
  title: string;
  description?: string | null;
  folder?: string | null;
  project_type: string;
  lectures: NotesToManuscriptLecture[];
}

export interface NotesToManuscriptManuscript {
  id: string;
  title: string;
  markdown: string;
}

export interface TopicSourceCard {
  project_id: string;
  project_title: string;
  lecture_title: string;
  source_sections: string[];
  section_anchors: string[];
  lun_dian: string[];
}

export interface TopicCard {
  id: string;
  name: string;
  type: "concept" | "passage";
  size: string;
  canonical_ref?: string | null;
  canonical_ref_raw?: string | null;
  chapter?: number | null;
  notes?: string | null;
  sources: TopicSourceCard[];
  aliases: string[];
}

export interface TopicListResponse {
  available: boolean;
  generated_at?: string | null;
  count: number;
  topics: TopicCard[];
}

export const NOTES_TO_MANUSCRIPT_REVALIDATE = 300;

async function fetchBackendJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BACKEND_BASE}${path}`, {
    next: { revalidate: NOTES_TO_MANUSCRIPT_REVALIDATE },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }

  return response.json();
}

export async function fetchNotesToManuscriptSeries(): Promise<
  NotesToManuscriptSeriesSummary[]
> {
  return fetchBackendJson<NotesToManuscriptSeriesSummary[]>(
    "/notes-to-sermon/public/series",
  );
}

export async function fetchNotesToManuscriptSeriesDetail(
  seriesId: string,
): Promise<NotesToManuscriptSeriesDetail> {
  return fetchBackendJson<NotesToManuscriptSeriesDetail>(
    `/notes-to-sermon/public/series/${encodeURIComponent(seriesId)}`,
  );
}

export async function fetchNotesToManuscriptManuscript(
  projectId: string,
): Promise<NotesToManuscriptManuscript> {
  return fetchBackendJson<NotesToManuscriptManuscript>(
    `/notes-to-sermon/public/projects/${encodeURIComponent(projectId)}/manuscript`,
  );
}

export async function fetchSeriesTopics(
  seriesId: string,
): Promise<TopicListResponse> {
  try {
    // Always fresh: the topic index is rebuilt out-of-band (reindex), so this
    // navigation data must reflect the current index rather than a stale cache.
    const response = await fetch(
      `${BACKEND_BASE}/sermon_search/topics?series_id=${encodeURIComponent(seriesId)}`,
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error(`topics ${response.status}`);
    }
    return (await response.json()) as TopicListResponse;
  } catch {
    // Topic index may not be built yet — degrade gracefully.
    return { available: false, count: 0, topics: [] };
  }
}
