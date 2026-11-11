import TranscriptPublication from "@/app/components/admin/surmons/TranscriptPublication";

export default function BetaPublication(props: {
  params: Promise<{ item: string }>;
}) {
  return (
    <TranscriptPublication params={props.params} apiBase="/api/sc_api/beta" />
  );
}
