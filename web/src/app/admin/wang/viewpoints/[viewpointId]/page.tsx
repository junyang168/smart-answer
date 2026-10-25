import { redirect } from "next/navigation";

// A viewpoint's own address stays shareable; it opens the graph with that
// viewpoint selected, where its detail now lives (#453).
export default async function ViewpointDetailPage(props: { params: Promise<{ viewpointId: string }> }) {
  const { viewpointId } = await props.params;
  redirect(`/admin/wang/viewpoints?v=${encodeURIComponent(viewpointId)}`);
}
