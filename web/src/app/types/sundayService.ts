export type SundaySongSource = "custom" | "hymnal";

export interface UnavailableDateRange {
  startDate: string;
  endDate: string;
}

export interface SundayWorker {
  name: string;
  email?: string | null;
  unavailableRanges?: UnavailableDateRange[];
}

export interface SundayServiceEmailResult {
  date: string;
  recipients: string[];
  pptFilename: string;
  subject: string;
  dryRun?: boolean;
}

export interface SundaySong {
  id: string;
  title: string;
  source: SundaySongSource;
  hymnalIndex?: number | null;
  hymnLink?: string | null;
  lyricsMarkdown?: string | null;
}

export interface SundaySongPayload {
  title: string;
  source: SundaySongSource;
  hymnalIndex?: number | null;
  hymnLink?: string | null;
  lyricsMarkdown?: string | null;
}

export interface SundayServiceEntry {
  date: string;
  presider?: string | null;
  worshipLeader?: string | null;
  pianist?: string | null;
  hymn?: string | null;
  responseHymn?: string | null;
  scripture?: string[] | null;
  sermonSpeaker?: string | null;
  sermonTitle?: string | null;
  announcementsMarkdown?: string | null;
  health_prayer_markdown?: string | null;
  donationAmount?: number | null;
  scriptureReaders?: string[] | null;
  holdHolyCommunion?: boolean | null;
  finalPptFilename?: string | null;
  emailBodyHtml?: string | null;
}

export interface SundayServiceResources {
  workers: SundayWorker[];
  songs: SundaySong[];
}

export interface SundayServiceEmailBody {
  html: string;
}

export interface HymnMetadata {
  index: number;
  title: string;
  link?: string | null;
  lyricsUrl?: string | null;
}

export interface GenerateHymnLyricsResponse {
  lyricsMarkdown: string;
}

export interface ScriptureBook {
  slug: string;
  name: string;
}
