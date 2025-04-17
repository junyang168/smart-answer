export interface ArticleDetail {
    id: string;
    theme: string;
    title: string;
    snippet: string;    
    paragraphs: Paragraph[];
    quotes: string[];
  }

export interface Paragraph {
  index: string;
  text : string;
  type ?: string;
  user_id?: string;   
}