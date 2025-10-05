export type FullArticleStatus = "draft" | "generated" | "final";

export interface FullArticleSummary {
  id: string;
  name: string;
  slug: string;
  status: FullArticleStatus;
  created_at: string;
  updated_at: string;
  model?: string | null;
}

export interface FullArticleDetail extends FullArticleSummary {
  scriptMarkdown: string;
  articleMarkdown: string;
  promptMarkdown: string;
}

export interface SaveFullArticlePayload {
  id?: string;
  name: string;
  scriptMarkdown: string;
  articleMarkdown: string;
  status: FullArticleStatus;
  promptMarkdown?: string;
}

export interface GenerateArticleResponse {
  articleMarkdown: string;
  status: FullArticleStatus;
  model?: string | null;
  generatedAt: string;
}
