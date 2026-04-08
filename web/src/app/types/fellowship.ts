export interface FellowshipEntry {
  date: string;
  host?: string | null;
  title?: string | null;
  series?: string | null;
  sequence?: number | null;
  emailSubject?: string | null;
  emailBodyHtml?: string | null;
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
