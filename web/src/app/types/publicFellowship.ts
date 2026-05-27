import { FellowshipSourceLink } from "@/app/types/fellowship";

export interface PublicFellowshipEntry {
  date: string;
  isoDate: string;
  host?: string | null;
  title?: string | null;
  series?: string | null;
  sequence?: number | null;
  sourceLinks: FellowshipSourceLink[];
  summary?: string | null;
  keyLearnings: string[];
  hasDocuments: boolean;
  documentCount: number;
}
