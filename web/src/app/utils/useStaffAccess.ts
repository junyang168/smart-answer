"use client";

import { useSession } from "next-auth/react";
import { hasEditorAccess } from "@/app/utils/roles";

/**
 * 同工（editor 及以上）才看得到講道系列的排列結構與編輯進度。普通信眾從講論文庫
 * 按書卷、章節找講道，不需要知道系列（負責人 2026-11-06）。
 *
 * 開發環境一律當作同工，與講道頁的做法一致。
 */
export function useStaffAccess(): { ready: boolean; isStaff: boolean } {
  const { data: session, status } = useSession();
  if (process.env.NODE_ENV !== "production") {
    return { ready: true, isStaff: true };
  }
  return { ready: status !== "loading", isStaff: hasEditorAccess(session?.user?.role) };
}
