"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSession, signIn } from "next-auth/react";
import { AlertTriangle, Loader2, Music, PlayCircle } from "lucide-react";
import { SurmonAdminListItem } from "@/app/types/surmon-editor";

interface SurmonAdminListState {
  status: "idle" | "loading" | "ready" | "error";
  data: SurmonAdminListItem[];
  error?: string;
}

const FALLBACK_USER_ID = "junyang168@gmail.com";

export const SurmonAdminList = () => {
  const { data: session, status: authStatus } = useSession();
  const [state, setState] = useState<SurmonAdminListState>({ status: "idle", data: [] });

  const userEmail = useMemo(() => {
    if (session?.user?.email) {
      return session.user.email;
    }
    if (process.env.NODE_ENV !== "production") {
      return FALLBACK_USER_ID;
    }
    return null;
  }, [session?.user?.email]);

  useEffect(() => {
    if (!userEmail || authStatus === "loading") {
      return;
    }

    let ignore = false;

    const loadSurmons = async () => {
      setState({ status: "loading", data: [] });
      try {
        const response = await fetch(`/sc_api/sermons/${encodeURIComponent(userEmail)}`);
        if (!response.ok) {
          throw new Error(`無法載入講道列表：${response.status} ${response.statusText}`);
        }
        const rows: SurmonAdminListItem[] = await response.json();
        if (!ignore) {
          setState({ status: "ready", data: rows });
        }
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "未知錯誤";
        if (!ignore) {
          setState({ status: "error", data: [], error: message });
        }
      }
    };

    loadSurmons();

    return () => {
      ignore = true;
    };
  }, [userEmail, authStatus]);

  if (!userEmail && authStatus !== "loading") {
    return (
      <div className="max-w-2xl mx-auto bg-white border border-amber-200 rounded-lg p-8 text-center shadow-sm">
        <AlertTriangle className="w-8 h-8 text-amber-500 mx-auto mb-4" />
        <h2 className="text-xl font-semibold mb-2">需要登入</h2>
        <p className="text-gray-600 mb-6">
          請使用教會 Google 帳戶登入後，再次造訪講道編輯後台。
        </p>
        <button
          onClick={() => signIn("google")}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white font-medium rounded-md hover:bg-blue-700 transition"
        >
          使用 Google 登入
        </button>
      </div>
    );
  }

  if (state.status === "loading" || authStatus === "loading") {
    return (
      <div className="flex items-center justify-center py-16 text-blue-600">
        <Loader2 className="w-6 h-6 mr-2 animate-spin" />
        正在載入講道列表...
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="max-w-2xl mx-auto bg-white border border-red-200 rounded-lg p-6 text-center text-red-600">
        {state.error ?? "發生未知錯誤"}
      </div>
    );
  }

  const total = state.data.length;

  return (
    <div className="space-y-6">
      <header className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">講道編輯後台</h1>
          <p className="text-gray-600 mt-2">
            管理講道稿件、認領狀態與進度。您目前共有
            <span className="font-semibold text-blue-600"> {total} </span>
            篇講道素材。
          </p>
        </div>
        <div className="bg-blue-50 text-blue-700 px-4 py-2 rounded-md text-sm border border-blue-200">
          登入帳號：{userEmail}
        </div>
      </header>

      <div className="overflow-x-auto bg-white border border-gray-200 rounded-xl shadow-sm">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                類型
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                標題
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                發布日期
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                認領人
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                認領日期
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                完成日期
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                最後更新
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                狀態
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {state.data.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-500">
                  尚未找到任何講道記錄。
                </td>
              </tr>
            )}
            {state.data.map((surmon) => {
              const isDraft = surmon.status?.toLowerCase() === "in development";
              const href = isDraft
                ? undefined
                : `/admin/surmons/${encodeURIComponent(surmon.item)}`;
              const Icon = surmon.type === "audio" ? Music : PlayCircle;

              return (
                <tr key={surmon.item} className="hover:bg-blue-50 transition-colors">
                  <td className="px-4 py-3 whitespace-nowrap">
                    <Icon className="w-5 h-5 text-gray-500" aria-hidden="true" />
                  </td>
                  <td className="px-4 py-3">
                    {href ? (
                      <Link href={href} className="text-blue-600 hover:underline font-medium">
                        {surmon.title}
                      </Link>
                    ) : (
                      <span className="text-gray-700 font-medium">{surmon.title}</span>
                    )}
                    {surmon.summary && (
                      <p className="text-sm text-gray-500 mt-1 overflow-hidden text-ellipsis">
                        {surmon.summary}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600">{surmon.deliver_date ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600">{surmon.assigned_to_name ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600">{surmon.assigned_to_date ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600">{surmon.published_date ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600">{surmon.last_updated ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span className="inline-flex px-2.5 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-700">
                      {surmon.status ?? "未知"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
