export interface FellowshipSourceLink {
  label: string;
  url: string;
}

export interface FellowshipEntry {
  date: string;
  host?: string | null;
  title?: string | null;
  series?: string | null;
  sequence?: number | null;
  sourceLinks?: FellowshipSourceLink[];
  summary?: string | null;
  keyLearnings?: string[];
  audienceQuestions?: string[];
  audienceSharings?: string[];
  leaderResponses?: string[];
  keyLearningsGeneratedAt?: string | null;
  emailSubject?: string | null;
  emailBodyHtml?: string | null;
}

export interface FellowshipLearningContent {
  summary: string;
  keyLearnings: string[];
  audienceQuestions: string[];
  audienceSharings: string[];
  leaderResponses: string[];
  generatedAt?: string | null;
}

export interface FellowshipEmailContent {
  subject: string;
  html: string;
}

export interface FellowshipEmailResult {
  date: string;
  recipients: string[];
  subject: string;
  dryRun?: boolean;
}

export interface FellowshipDocument {
  name: string;
  url: string;
  size: number;
  modifiedAt: string;
}

export interface FellowshipAnalysisAsset {
  name: string;
  source: string;
  kind: string;
  url?: string | null;
  size?: number | null;
  modifiedAt?: string | null;
  driveFileId?: string | null;
  mimeType?: string | null;
  usable: boolean;
  reason?: string | null;
}

export interface FellowshipAnalysisAssets {
  date: string;
  pptx?: FellowshipAnalysisAsset | null;
  transcript?: FellowshipAnalysisAsset | null;
  recording?: FellowshipAnalysisAsset | null;
  emptyChat?: FellowshipAnalysisAsset | null;
  candidates: FellowshipAnalysisAsset[];
  messages: string[];
}

export interface FellowshipInteraction {
  kind: string;
  speaker: string;
  timestampStart?: string | null;
  timestampEnd?: string | null;
  text: string;
  summary: string;
}

export interface FellowshipAnalysisContent {
  theme: string;
  centralMessage: string;
  biblePassage: string;
  outline: string[];
  keyPoints: string[];
  interactions: FellowshipInteraction[];
  applications: string[];
  discussionQuestions: string[];
  markdown: string;
  generatedAt?: string | null;
}

export interface FellowshipAnalysisJob {
  jobId: string;
  date: string;
  status: string;
  message: string;
  resultDocumentName?: string | null;
  error?: string | null;
  content?: FellowshipAnalysisContent | null;
}
