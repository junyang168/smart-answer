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
  keyLearningsGeneratedAt?: string | null;
  emailSubject?: string | null;
  emailBodyHtml?: string | null;
}

export interface FellowshipLearningContent {
  summary: string;
  keyLearnings: string[];
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
