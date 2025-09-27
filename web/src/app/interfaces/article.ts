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
  assigned_to_date?: string;
  completed_date?: string;
  last_updated?: string;
  author_name?: string;
  type?: string | null;
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

export interface FaithQA {
  id: string; // 唯一標識符
  question: string; // 用戶提出的問題
  shortAnswer: string; // 一個簡潔的、可以直接顯示的答案摘要
  fullAnswerMarkdown: string; // 完整的、詳細的答案，使用 Markdown 格式
  category ?: string; // 分類，如 "關於聖經", "關於救恩", "倫理難題"
  relatedScriptures ?: string[]; // 相關經文引用
  createdAt : string; // 創建日期
  isVerified : boolean; // 是否經過教會同工審核和確認
  related_article:string;
  date_asked: string; // 提問日期
}
