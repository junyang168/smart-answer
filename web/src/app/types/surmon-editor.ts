export type SurmonMediaType = 'audio' | 'video' | null;

export interface SurmonEditorHeader {
  /** Media type that determines whether to render an audio or video player */
  type: SurmonMediaType;
  /** Surmon title */
  title?: string;
  /** Summary or intro text */
  summary?: string;
  /** Speaker name */
  speaker?: string;
  /** Original delivery date */
  deliver_date?: string;
  /** Primary theme keywords */
  theme?: string;
  /** Content source (internal vs public) */
  source?: string;
}

export type SurmonParagraphType = 'content' | 'comment';

export interface SurmonScriptParagraph {
  /** Unique index that aligns with the media timeline */
  index: string;
  /** Paragraph type; legacy data uses `comment` for reviewer notes */
  type: SurmonParagraphType | string;
  /** Markdown content for the paragraph */
  text: string;
  /** Absolute start time in seconds */
  start_time?: number;
  /** Absolute end time in seconds */
  end_time?: number;
  /** Human readable timeline string (e.g. `00:12:34`) */
  start_timeline?: string;
  /** Internal sequence index used for editing */
  s_index?: number;
  /** Authoring metadata for reviewer comments */
  user_id?: string;
  user_name?: string;
}

export interface SurmonScriptResponse {
  header: SurmonEditorHeader;
  script: SurmonScriptParagraph[];
}

export interface SurmonTimelineEntry {
  index: string;
  timestamp: string;
  next_item?: string | null;
  [key: string]: unknown;
}

export interface SurmonTimelinePayload {
  entries: SurmonTimelineEntry[];
}

export interface SurmonPermissions {
  canRead: boolean;
  canWrite: boolean;
  canAssign: boolean;
  canUnassign: boolean;
  canPublish: boolean;
  canViewPublished: boolean;
}

export interface SurmonBookmark {
  index: string;
}

export interface SurmonChatReference {
  Id: string;
  Title: string;
  Index: string;
}

export interface SurmonChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface SurmonChatResponse {
  answer: string;
  quotes?: SurmonChatReference[];
}

export interface SurmonAssignPayload {
  user_id: string;
  item: string;
  action: 'assign' | 'unassign';
}

export interface SurmonUpdateScriptPayload {
  user_id: string;
  item: string;
  type: 'scripts' | 'slides';
  data: SurmonScriptParagraph[] | unknown;
}

export interface SurmonUpdateHeaderPayload {
  user_id: string;
  item: string;
  title: string;
}

export interface SurmonAdminListItem {
  item: string;
  title: string;
  summary?: string;
  type?: SurmonMediaType;
  status: string;
  deliver_date?: string;
  assigned_to_name?: string;
  assigned_to_date?: string;
  published_date?: string;
  author_name?: string;
  last_updated?: string;
  source?: string | null;
}
