"use client";

import { useCallback, useEffect, useState } from "react";

interface Folder { date: string; folderUrl: string; folderName: string; folderExists: boolean }

export function SundayPresenterFolder() {
  const [folder, setFolder] = useState<Folder | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true); setError(""); setFolder(null);
    try {
      const response = await fetch("/api/admin/sunday-services/presenter/upcoming", { cache: "no-store" });
      if (!response.ok) throw new Error("尚未取得 OneDrive 資料夾，請確認網站設定後重試。");
      setFolder(await response.json());
    } catch (err) { setError(err instanceof Error ? err.message : "無法取得資料夾"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return <section className="rounded-lg border border-teal-200 bg-teal-50 p-5">
    <div className="flex items-center justify-between gap-4">
      <h3 className="font-semibold text-gray-900">本主日 PPT · {folder?.date ?? "OneDrive"}</h3>
      <button type="button" onClick={() => void refresh()} disabled={loading}
        className="rounded border border-teal-300 px-3 py-1 text-sm disabled:opacity-50">{loading ? "讀取中…" : "更新連結"}</button>
    </div>
    {folder && <>
      <a href={folder.folderUrl} target="_blank" rel="noreferrer" className="mt-2 inline-block text-teal-800 underline">開啟 OneDrive · {folder.folderName}</a>
      <p className="mt-2 text-sm text-gray-600">生成的 PPT 與上傳的最終 PPT 會存入各主日的 OneDrive 日期資料夾。Sunday Presenter 啟動時會自動取得本主日連結。</p>
      <p className="mt-1 text-sm text-gray-600">{folder.folderExists ? "資料夾已建立。" : "本週資料夾將於生成或上傳 PPT 時建立。"}雲端可用時間以 OneDrive 同步狀態為準。</p>
    </>}
    {error && <p role="status" className="mt-2 text-sm text-amber-800">{error}</p>}
  </section>;
}
