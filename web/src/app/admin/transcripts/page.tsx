import { notFound } from "next/navigation";
import TranscriptList from "./TranscriptList";

export default function TranscriptsPage() {
  if (process.env.TRANSCRIPT_EDITOR_CUTOVER !== "1") notFound();
  return <TranscriptList />;
}
