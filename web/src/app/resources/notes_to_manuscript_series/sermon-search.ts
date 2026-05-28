export type SermonSearchMode = "normal" | "deep";

export interface SermonSearchFilters {
  series_ids?: string[];
  project_types?: string[];
  topics?: string[];
  canonical_refs?: string[];
  content_types?: string[];
}

export interface SermonSearchRequest {
  question: string;
  mode?: SermonSearchMode;
  filters?: SermonSearchFilters;
  top_k?: number;
}

export interface SourceCard {
  source_id: string;
  content_id: string;
  score: number;
  doc_title: string;
  series_title: string;
  lecture_title: string;
  heading_path: string[];
  snippet: string;
  topics: string[];
  canonical_refs: string[];
}

export interface Citation {
  source_id: string;
  doc_title: string;
  heading_path: string[];
  quote: string;
  supports: string;
}

export interface SearchRoundTrace {
  round: number;
  tools_used: string[];
  query: string;
  candidate_count: number;
  selected_count: number;
}

export interface SearchTrace {
  mode: SermonSearchMode;
  rounds: number;
  tools_used: string[];
  notes: string[];
  round_traces: SearchRoundTrace[];
}

export interface SermonSearchResponse {
  answer: string;
  citations: Citation[];
  sources: SourceCard[];
  related_questions: string[];
  search_trace: SearchTrace;
}

export async function querySermonSearch(
  payload: SermonSearchRequest,
): Promise<SermonSearchResponse> {
  const response = await fetch("/api/sermon_search/query", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Search failed with ${response.status}`);
  }

  return response.json();
}
