import { getServerSession } from "next-auth";
import { notFound } from "next/navigation";
import { authConfig } from "@/app/utils/auth";

export default async function BetaLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getServerSession(authConfig);
  if (session?.user?.email !== "junyang168@gmail.com") notFound();
  return <>{children}</>;
}
