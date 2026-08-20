export type Stats = {
  segments: number;
  segments_covered: number;
  heading_segments: number;
  sentences: number;
  sentences_covered: number;
  // Coverage is read against the prose. A heading is structure rather than
  // material and is represented 0% of the time by design -- 51 of the 208
  // sentences in the 太16 母本 -- so the total says less than this pair does.
  prose_sentences: number;
  prose_sentences_covered: number;
  chars: number;
  chars_covered: number;
  fragments: number;
  fragments_placed: number;
  steps: number;
  observations: number;
  questions: number;
  positions: number;
  claims: number;
};

export type SourceMeta = {
  source_id: string;
  title: string;
  source_type: string;
  transcript_id: string | null;
  project_id: string | null;
  source_path: string;
  recorded_sha256: string | null;
  file_sha256: string | null;
  file_state: "current" | "drifted" | "missing";
  stats: Stats;
};

export type Run = { start: number; end: number; fragment_ids: string[] };
export type Sentence = { start: number; end: number; covered: boolean };

export type Segment = {
  ordinal: number;
  key: string;
  index: number | string | null;
  text: string;
  is_heading: boolean;
  start_time: number | null;
  end_time: number | null;
  type: string;
  runs: Run[];
  sentences: Sentence[];
  covered_chars: number;
  fragment_ids: string[];
  node_ids: string[];
};

/** How a stored fragment was placed back on the source text. */
export type AnchorMethod =
  | "segment_key"
  | "segment_index"
  | "verbatim_search"
  | "excerpt_moved"
  | "empty_excerpt"
  | "no_excerpt"
  | "ambiguous_excerpt"
  | "not_in_source";

export type Fragment = {
  id: string;
  excerpt: string;
  paragraph_key: string | number | null;
  source_segment_index: number | null;
  media_time: number | null;
  media_end_time: number | null;
  anchor_state: string;
  review_status: string;
  segment_ordinal: number | null;
  anchor_method: AnchorMethod;
  char_start: number | null;
  char_end: number | null;
  found_at_ordinal: number | null;
  node_ids: string[];
};

export type NodeKind = "step" | "observation" | "question" | "position";

export type Node = {
  id: string;
  label: string;
  kind: NodeKind;
  statement: string;
  review_status: string;
  step_type: string;
  discourse_role: string;
  support_eligibility: string;
  observation_type: string;
  argument_role: string;
  answer_state: string;
  attribution: string;
  scripture_refs: string[];
  fragment_ids: string[];
  claim_ids: string[];
};

export type Claim = {
  id: string;
  label: string;
  statement: string;
  claim_type: string;
  maturity: string;
  attribution: string;
  review_status: string;
  scripture_refs: string[];
  evidence_step_ids: string[];
  foreign_evidence_steps: number;
  fragment_ids: string[];
  first_ordinal: number | null;
};

export type SourceDetail = {
  source: SourceMeta;
  segments: Segment[];
  fragments: Record<string, Fragment>;
  nodes: Record<string, Node>;
  claims: Record<string, Claim>;
};

/** One sermon of the catalog, or one manuscript beside it. */
export type CatalogEntry = {
  kind: "sermon" | "notes_manuscript";
  item: string | null;
  /** The file the source is read from: the transcript id, or the project folder. */
  file: string | null;
  title: string;
  book: string | null;
  chapter: number | null;
  verse_start: number | null;
  display: string | null;
  topics: string[];
  organization_mode: string | null;
  organization_mode_label: string | null;
  deliver_date: string | null;
  /** The coverage source, when this one has been extracted at all. */
  source_id: string | null;
};

export type Totals = Stats & {
  sources: number;
  sources_drifted: number;
  sources_unreadable: number;
  catalog_sermons: number;
  catalog_sermons_extracted: number;
  catalog_books: number;
  catalog_books_extracted: number;
  notes_manuscripts: number;
};

export type Overview = { sources: SourceMeta[]; totals: Totals; catalog: CatalogEntry[] };

// Store collections keep their own names; only editorial concepts get Chinese.
export const KIND_NAME: Record<NodeKind, string> = {
  step: "evidence_step",
  observation: "observation",
  question: "question",
  position: "position_node",
};

/** The store's own id field for each collection, so a row can be labelled with it. */
export const KIND_ID_FIELD: Record<NodeKind, string> = {
  step: "evidence_step_id",
  observation: "observation_id",
  question: "question_id",
  position: "position_id",
};

export const KIND_STYLE: Record<NodeKind, string> = {
  step: "bg-indigo-100 text-indigo-800",
  observation: "bg-slate-200 text-slate-700",
  question: "bg-amber-100 text-amber-800",
  position: "bg-violet-100 text-violet-800",
};

/** What each anchor method means for whether the highlight can be trusted. */
export const ANCHOR_NOTE: Record<AnchorMethod, string> = {
  segment_key: "segment key 直接命中",
  segment_index: "以 transcript 自身 index 命中（pilot 記錄的舊式錨點）",
  verbatim_search: "記錄未指定 segment，以逐字內容唯一定位",
  excerpt_moved: "錨點指向的 segment 已不含這段文字——來源被改過",
  empty_excerpt: "記錄沒有 verbatim_excerpt，無法定位",
  no_excerpt: "記錄沒有 verbatim_excerpt，無法定位",
  ambiguous_excerpt: "這段文字在來源出現多次，不猜",
  not_in_source: "這段文字不在來源裡",
};

export const isPlaced = (fragment: Fragment) => fragment.segment_ordinal !== null;

export const percent = (part: number, whole: number) => (whole ? Math.round((100 * part) / whole) : 0);

export const timecode = (seconds: number | null) => {
  if (seconds === null || seconds === undefined) return "";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
};
