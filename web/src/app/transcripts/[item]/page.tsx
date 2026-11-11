import TranscriptPublication from "@/app/components/admin/surmons/TranscriptPublication";
export default function Page(props: { params: Promise<{ item: string }> }) {
  return <TranscriptPublication params={props.params} />;
}
