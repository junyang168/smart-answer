export interface DepthOfFaithEpisode {
  id: string;
  title: string;
  description: string;
  audioFilename?: string | null;
  scripture?: string | null;
  duration?: string | null;
  publishedAt?: string | null;
}

export interface DepthOfFaithEpisodePayload {
  id?: string;
  title: string;
  description: string;
  audioFilename?: string;
  scripture?: string;
  duration?: string;
  publishedAt?: string;
}

export interface DepthOfFaithEpisodeUpdatePayload {
  title?: string;
  description?: string;
  audioFilename?: string;
  scripture?: string;
  duration?: string;
  publishedAt?: string;
}

export interface DepthOfFaithAudioUploadResponse {
  filename: string;
}
