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

export type SermonSearchStreamEvent =
  | {
      type: "sources";
      sources: SourceCard[];
      search_trace: SearchTrace;
    }
  | {
      type: "answer_delta";
      delta: string;
    }
  | {
      type: "done";
      citations: Citation[];
      related_questions: string[];
    };

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

export async function streamSermonSearch(
  payload: SermonSearchRequest,
  onEvent: (event: SermonSearchStreamEvent) => void,
): Promise<void> {
  if (typeof EventSource !== "undefined") {
    return streamSermonSearchWithEventSource(payload, onEvent);
  }

  const response = await fetch("/api/sermon_search/query_stream", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    const message = await response.text();
    throw new Error(message || `Search failed with ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventText of events) {
      const dataLine = eventText
        .split("\n")
        .find((line) => line.startsWith("data: "));
      if (!dataLine) {
        continue;
      }
      onEvent(JSON.parse(dataLine.slice(6)) as SermonSearchStreamEvent);
    }
  }

  const tail = buffer.trim();
  if (tail) {
    const dataLine = tail.split("\n").find((line) => line.startsWith("data: "));
    if (dataLine) {
      onEvent(JSON.parse(dataLine.slice(6)) as SermonSearchStreamEvent);
    }
  }
}

function streamSermonSearchWithEventSource(
  payload: SermonSearchRequest,
  onEvent: (event: SermonSearchStreamEvent) => void,
): Promise<void> {
  const params = new URLSearchParams({ payload: JSON.stringify(payload) });
  const source = new EventSource(`/api/sermon_search/query_stream?${params.toString()}`);

  return new Promise((resolve, reject) => {
    let completed = false;
    const close = () => {
      completed = true;
      source.close();
    };
    const handleEvent = (event: MessageEvent<string>) => {
      onEvent(JSON.parse(event.data) as SermonSearchStreamEvent);
    };

    source.addEventListener("sources", handleEvent);
    source.addEventListener("answer_delta", handleEvent);
    source.addEventListener("done", (event: MessageEvent<string>) => {
      handleEvent(event);
      close();
      resolve();
    });
    source.onerror = () => {
      source.close();
      if (!completed) {
        reject(new Error("搜尋串流中斷"));
      }
    };
  });
}
