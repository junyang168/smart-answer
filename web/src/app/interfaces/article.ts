export interface Article {
    id: string;
    publishedUrl: string;
    theme: string;
    title: string;
    snippet: string;
    deliver_date: string;
    status: string;
    assigned_to_name: string;
//    duration: string;    
    thumbnail: {
      url: string;
      width: number;
      height: number;
      imageId: string;
    };

  }

export interface Sermon {
  id: string;
  title: string;
  speaker: string;
  date: string;       // e.g., "2024年12月25日"
  scripture : string;
  summary: string;
  videoUrl ?: string;
  audioUrl ?: string;
  book : string;       // 聖經書卷
  topic : string;      // 主題/系列
  status: string;
  source : string;
  assigned_to_name: string;   // 認領人
  markdownContent?: string;   // 可選的 Markdown 內容
}