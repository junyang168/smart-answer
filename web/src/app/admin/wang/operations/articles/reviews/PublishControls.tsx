"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpenCheck, ExternalLink, Undo2 } from "lucide-react";

type Decision = { decision?: string; public_slug?: string } | null;

export function PublishControls(props: {
  reviewId: string;
  workflowStatus: string;
  integrityStatus: string;
  publicationDecision: Decision;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const published = props.publicationDecision?.decision === "approved";
  const eligible = props.workflowStatus === "review_passed" && props.integrityStatus === "verified";

  async function call(path: "publish" | "unpublish") {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(
        `/admin/wang/operations/articles/reviews/${encodeURIComponent(props.reviewId)}/${path}`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail || `发布服务返回 ${response.status}`);
      setConfirming(false);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  if (published) {
    const slug = props.publicationDecision?.public_slug || "";
    return (
      <div className="flex flex-wrap items-center gap-3">
        <a
          href={`/resources/wang-repository/articles/${slug}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white hover:bg-emerald-800"
        >
          <ExternalLink className="h-4 w-4" />已出版 · 在读者站查看
        </a>
        <button
          type="button"
          disabled={busy}
          onClick={() => call("unpublish")}
          className="inline-flex items-center gap-2 rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm font-bold text-stone-700 hover:bg-stone-50 disabled:opacity-50"
        >
          <Undo2 className="h-4 w-4" />{busy ? "处理中…" : "从读者站撤下"}
        </button>
        {error ? <p className="w-full text-sm font-semibold text-rose-700">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      {confirming ? (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={() => call("publish")}
            className="inline-flex items-center gap-2 rounded-xl bg-rose-700 px-4 py-2.5 text-sm font-bold text-white hover:bg-rose-800 disabled:opacity-50"
          >
            <BookOpenCheck className="h-4 w-4" />{busy ? "发布中…" : "确认发布——读者立即可见"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setConfirming(false)}
            className="rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm font-bold text-stone-700 hover:bg-stone-50"
          >
            再想想
          </button>
        </>
      ) : (
        <button
          type="button"
          disabled={!eligible}
          title={eligible ? "" : "须闸门全部通过且完整性校验一致才能发布"}
          onClick={() => setConfirming(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-stone-900 px-4 py-2.5 text-sm font-bold text-white hover:bg-stone-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <BookOpenCheck className="h-4 w-4" />正式出版
        </button>
      )}
      {error ? <p className="w-full text-sm font-semibold text-rose-700">{error}</p> : null}
    </div>
  );
}
