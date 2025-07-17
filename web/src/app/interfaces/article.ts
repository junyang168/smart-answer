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
export interface BibleVerse {
  book: string;       // 聖經書卷
  chapter_verse: string;   // 章節
  text ?: string;       // 經文內容
}

export interface Sermon {
  id: string;
  title: string;
  speaker: string;
  date: string;       // e.g., "2024年12月25日"
  scripture : string[]; // 經文列表，可能是多個經文
  summary: string;
  videoUrl : string | null;
  audioUrl ?: string;
  book : string;       // 聖經書卷
  topic : string;      // 主題/系列
  status: string;
  source : string;
  assigned_to_name: string;   // 認領人
  markdownContent?: string;   // 可選的 Markdown 內容
  keypoints?: string; // 可選的要點列表
  theme : string; // 主題
  core_bible_verses ?: BibleVerse[]; // 可選的核心經文列表
}