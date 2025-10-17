export interface SermonSeries {
  id: string;
  title?: string | null;
  summary?: string | null;
  topics?: string | null;
  keypoints?: string | null;
  sermons: string[];
}
