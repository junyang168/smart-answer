"use client";

import { useParams } from "next/navigation";
import OriginalAudio from "../OriginalAudio";

export default function OriginalAudioPage() {
  return <OriginalAudio passage={String(useParams()?.passage ?? "")} />;
}
