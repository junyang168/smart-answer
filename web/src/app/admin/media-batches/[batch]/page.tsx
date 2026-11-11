import { MediaBatchProgress } from "../progress";
export default async function Page({
  params,
}: {
  params: Promise<{ batch: string }>;
}) {
  const { batch } = await params;
  return <MediaBatchProgress batch={batch} />;
}
