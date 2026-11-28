import { MediaBatchProgress } from "./progress";
import { SermonPipelineMonitor } from "./sermons";
export const metadata = { title: "影音處理進度", robots: { index: false, follow: false } };
export default async function Page({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  return (await searchParams).view === "batches" ? <MediaBatchProgress /> : <SermonPipelineMonitor />;
}
