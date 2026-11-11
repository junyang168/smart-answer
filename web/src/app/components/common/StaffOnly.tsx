"use client";

import { useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import { useStaffAccess } from "@/app/utils/useStaffAccess";

/** 同工才看得到的頁面；其他人轉到講論文庫，按書卷、章節找講道。 */
export const READER_ENTRY = "/resources/wang-repository";

export function StaffOnly({ children }: { children: ReactNode }) {
  const { ready, isStaff } = useStaffAccess();
  const router = useRouter();
  useEffect(() => {
    if (ready && !isStaff) router.replace(READER_ENTRY);
  }, [ready, isStaff, router]);
  if (!ready || !isStaff) {
    return (
      <p className="py-20 text-center text-stone-600" role="status">
        正在讀取…
      </p>
    );
  }
  return <>{children}</>;
}
