export interface MicroSermon {
  id: string;
  title: string;
  series?: string | null;
  seriesNumber?: number | null;
  youtubeUrl: string;
  intro?: string | null;
  description: string;
  tag?: string | null;
  isFeatured: boolean;
  publishedAt?: string | null;
}

export interface MicroSermonPayload {
  id?: string;
  title: string;
  series?: string;
  seriesNumber?: number;
  youtubeUrl?: string;
  intro?: string;
  description?: string;
  tag?: string;
  isFeatured?: boolean;
  publishedAt?: string;
}

export interface MicroSermonUpdatePayload {
  title?: string;
  series?: string;
  seriesNumber?: number;
  youtubeUrl?: string;
  intro?: string;
  description?: string;
  tag?: string;
  isFeatured?: boolean;
  publishedAt?: string;
}
