// types.ts

// ... 您已有的其他類型定義，如 Sermon, Article, FaithQA 等 ...

/**
 * 代表 AI 對話中的單條消息。
 */
export interface Message {
  /**
   * 消息的角色，用於區分是誰發送的。
   * 'user': 代表由真實用戶發送的消息。
   * 'assistant': 代表由 AI 助教發送的回應。
   * 'system': (可選) 代表給 AI 的系統級指令，通常不在前端顯示。
   */
  role: 'user' | 'assistant' | 'system';

  /**
   * 消息的文本內容。
   * 這部分內容可以是純文本，也可以是 Markdown 格式的字符串。
   */
  content: string;

  /**
   * (可選) 消息的唯一標識符。
   * 在需要對特定消息進行更新或刪除操作時非常有用。
   * 可以是一個 UUID 字符串。
   */
  id?: string;

  /**
   * (可選) 消息的創建時間戳。
   * 用於顯示消息時間或按時間排序。
   * 可以是一個 ISO 格式的日期字符串 (e.g., "2023-10-27T10:00:00Z")。
   */
  createdAt?: string;
}