import type { ReactElement, ReactNode } from "react";

import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { authConfig } from "@/app/utils/auth";

export default async function NotesToManuscriptLayout({
  children,
}: {
  children: ReactNode;
}): Promise<ReactElement> {
  const session = await getServerSession(authConfig);

  if (!session) {
    redirect(
      "/api/auth/signin?callbackUrl=%2Fresources%2Fnotes_to_manuscript_series",
    );
  }

  return <>{children}</>;
}
