export interface ArticleDetail {
    id: string;
    theme: string;
    title: string;
    snippet: string;    
    paragraphs: Paragraph[];
  }

export interface Paragraph {
  index: string;
  text : string;
  type ?: string;
  user_id?: string;   
}