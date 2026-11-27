import { getServerSession } from "next-auth";
import { notFound } from "next/navigation";
import { hasTranscriptBetaAccess } from "@/app/utils/roles";
import { authConfig } from "@/app/utils/auth";

export default async function BetaLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  if (process.env.TRANSCRIPT_EDITOR_CUTOVER === "1") return <>{children}</>;
  const session = await getServerSession(authConfig);
  if (!hasTranscriptBetaAccess(session?.user?.email, session?.user?.role))
    notFound();
  return <>{children}</>;
}
