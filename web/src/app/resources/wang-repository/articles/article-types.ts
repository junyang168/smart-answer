import type { ReviewSourceAnnotation } from "@/app/admin/wang/operations/articles/reviews/types";

export type PublicMedia = {
  kind: "audio" | "video" | "unknown";
  url: string | null;
};

export type PublicArticleClip = {
  title: string;
  sermon_label: string;
  delivered_on: string | null;
  public_url: string;
  media: PublicMedia;
  start_seconds: number | null;
  end_seconds: number | null;
};

export type PublicAudioSection = {
  heading: string;
  title: string;
  passage: string;
  clips: PublicArticleClip[];
};

export type PublicWangArticle = {
  slug: string;
  title: string;
  passage: string;
  markdown: string;
  audio_sections: PublicAudioSection[];
  audio_section_count: number;
  player_count: number;
  source_annotations?: ReviewSourceAnnotation[];
};
