import { redirect } from "next/navigation";
import BetaLectures from "./BetaLectures";

export default function BetaPage() {
  if (process.env.TRANSCRIPT_EDITOR_CUTOVER === "1") redirect("/admin/transcripts");
  return <BetaLectures />;
}
