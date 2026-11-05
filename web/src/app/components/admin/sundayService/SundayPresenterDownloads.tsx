"use client";
import { useEffect, useState } from "react";

interface Release { version: string; status: "pilot" | "released"; artifacts: { id: string; url: string; bytes: number }[] }
export function SundayPresenterDownloads() {
  const [release, setRelease] = useState<Release | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    fetch("/api/admin/sunday-presenter", { cache: "no-store" }).then(async response => {
      if (!response.ok) {
        const body = await response.json(); throw Error(body.error || "安裝包暫時無法下載。");
      }
      const data = await response.json(); if (active) setRelease(data);
    }).catch(err => { if (active) setError(err instanceof Error ? err.message : "安裝包暫時無法下載。"); });
    return () => { active = false; };
  }, []);
  const installer = release?.artifacts.find(asset => asset.id === "presenter-mac-arm64");
  return <div className="mt-4 border-t border-teal-200 pt-4">
    <h4 className="font-semibold text-gray-900">下載主日播放 App {release && <span className="text-sm font-normal">v{release.version} · {release.status === "released" ? "Phase 1" : "測試版"}</span>}</h4>
    <p className="mt-1 text-sm text-gray-600">一個 DMG 安裝包包含 Sunday Presenter 和 Sunday Remote。主電腦安裝 Presenter；講員的 Mac 安裝 Remote。Google Meet 的加入、分享、麥克風及錄影由你手動操作。</p>
    <div className="mt-3 flex flex-wrap gap-3">{installer && <a href={installer.url}
      className="rounded border border-teal-400 bg-white px-3 py-2 text-sm font-medium text-teal-900 hover:bg-teal-100">
      下載 Mac 安裝包（DMG）
      <span className="ml-2 font-normal">{(installer.bytes / 1024 / 1024).toFixed(1)} MB</span>
    </a>}</div>
    {release && <p className="mt-2 text-sm text-gray-600">Presenter 需要 M 系列 Mac、macOS 13 或更新版本；Remote 支援 Intel／M 系列 Mac。安裝包尚未取得 Apple 開發者簽章／公證，首次開啟請依 macOS 提示確認。Presenter／Remote 只需本機網路權限，不需輔助使用或麥克風權限。</p>}
    {error && <p role="status" className="mt-2 text-sm text-amber-800">{error}</p>}
  </div>;
}
