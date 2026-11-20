import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";
import { authConfig } from "./auth";
import { canUseSermonEditing } from "./roles";

export async function requireSermonEditing() {
  const session = await getServerSession(authConfig);
  if (!canUseSermonEditing(session?.user?.role)) redirect("/resources/wang-repository");
}
