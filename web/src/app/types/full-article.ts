export type FullArticleStatus = "draft" | "generated" | "final";
export type FullArticleType = "釋經" | "神學觀點" | "短文";

export interface FullArticleSummary {
  id: string;
  name: string;
  slug: string;
  subtitle?: string;
  status: FullArticleStatus;
  created_at: string;
  updated_at: string;
  model?: string | null;
  summaryMarkdown?: string;
  articleType?: FullArticleType | null;
  coreBibleVerses?: string[];
}

export interface FullArticleDetail extends FullArticleSummary {
  scriptMarkdown: string;
  articleMarkdown: string;
  promptMarkdown: string;
  summaryMarkdown: string;
  articleType?: FullArticleType | null;
  coreBibleVerses: string[];
}

export interface SaveFullArticlePayload {
  id?: string;
  name: string;
  subtitle?: string;
  scriptMarkdown: string;
  articleMarkdown: string;
  status: FullArticleStatus;
  promptMarkdown?: string;
  summaryMarkdown?: string;
  articleType?: FullArticleType | null;
  coreBibleVerses?: string[];
}

export interface GenerateArticleResponse {
  articleMarkdown: string;
  status: FullArticleStatus;
  model?: string | null;
  generatedAt: string;
}

export interface GenerateSummaryResponse {
  summaryMarkdown: string;
  model?: string | null;
  generatedAt: string;
}
