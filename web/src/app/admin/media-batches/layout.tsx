import { getServerSession } from "next-auth";
import { notFound } from "next/navigation";
import { getUserByEmail } from "@/app/utils/user-db";
import { hasEditorAccess } from "@/app/utils/roles";
import { authConfig } from "@/app/utils/auth";
export default async function Layout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getServerSession(authConfig);
  const user = await getUserByEmail(session?.user?.email);
  if (!hasEditorAccess(user?.role)) notFound();
  return <>{children}</>;
}
