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
  item ?: string; // 文章ID或其他識別符
  title: string;
  speaker: string;
  date: string;       // e.g., "2024年12月25日"
  published_date ?: string; // 可選的發布日期
  scripture : string[]; // 經文列表，可能是多個經文
  summary: string;
  videoUrl : string | null;
  audioUrl ?: string;
  book : string[];       // 聖經書卷
  topic : string[];      // 主題/系列
  status: string;
  source : string;
  assigned_to_name: string;   // 認領人
  markdownContent?: string;   // 可選的 Markdown 內容
  keypoints?: string; // 可選的要點列表
  theme : string; // 主題
  core_bible_verses ?: { [key: string] : string }; // 可選的核心經文列表
}

// ✅ 新增：定義講道系列的類型
export interface SermonSeries {
  id: string; // URL友好的ID，例如 "gospel-basics"
  title: string;
  summary: string;
  topics: string[]; // 主題列表
  keypoints?: string; // 可選的要點列表
  sermons: Sermon[]; // 包含在此系列中的所有講道

}