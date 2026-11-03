"use client";
import { useEffect, useState } from "react";

interface Release { version: string; artifacts: { id: string; url: string; bytes: number }[] }
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
  return <div className="mt-4 border-t border-teal-200 pt-4">
    <h4 className="font-semibold text-gray-900">下載主日播放 App {release && <span className="text-sm font-normal">v{release.version} · 測試版</span>}</h4>
    <p className="mt-1 text-sm text-gray-600">主電腦安裝 Sunday Presenter；講員電腦安裝 Sunday Remote。每主日 Dallas 時間 10:00 自動開始，請提前開啟並完成準備。</p>
    <div className="mt-3 flex flex-wrap gap-3">{release?.artifacts.map(asset => <a key={asset.id} href={asset.url}
      className="rounded border border-teal-400 bg-white px-3 py-2 text-sm font-medium text-teal-900 hover:bg-teal-100">
      {asset.id === "presenter-mac-arm64" ? "Sunday Presenter · M 系列 Mac" : "Sunday Remote · Intel／M 系列 Mac"}
      <span className="ml-2 font-normal">{(asset.bytes / 1024 / 1024).toFixed(1)} MB</span>
    </a>)}</div>
    {release && <p className="mt-2 text-sm text-gray-600">Presenter 需要 macOS 13 或更新版本。此測試版尚未取得 Apple 開發者簽章／公證；首次權限請在家完成，兩台電腦的收音與自動開場仍需現場驗收。</p>}
    {error && <p role="status" className="mt-2 text-sm text-amber-800">{error}</p>}
  </div>;
}
