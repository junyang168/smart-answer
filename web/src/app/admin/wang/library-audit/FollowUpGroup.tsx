"use client";

import { useState } from "react";
import { count, type FollowUpGroup as Group, type FollowUpItem } from "./types";

/**
 * The part of the page that is actually the point.
 *
 * A ratio tells you whether to keep reading; this tells you what to do. Every
 * row carries three things, because without all three the reader has to open
 * the database to act: which record, what is wrong with it, and the evidence
 * for saying so. Ids are shown exactly as the store spells them -- a
 * `viewpoint_claim_link` is not "觀點連結" here, because the thing you paste
 * into a query is `VCL-4d5a6b158418a8d6f6aa`.
 *
 * Dangling references are grouped by what they fail to reach. Ninety-seven
 * routes pointing at the same plan that was never ingested is one problem, and
 * ninety-seven rows would bury the four findings that need a human.
 */
export function FollowUpGroup({ group }: { group: Group }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="flex flex-wrap items-baseline gap-2 text-sm font-semibold tracking-tight text-slate-900">
          <span
            className={`rounded-md px-2 py-0.5 text-[0.68rem] font-semibold ${
              group.needs_human ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-500"
            }`}
          >
            {group.needs_human ? "要人看" : "程序問題"}
          </span>
          {group.title}
          <span className="font-mono text-xs font-normal text-slate-500">{count(group.count)} 條</span>
        </h2>
        <p className="text-[0.8rem] leading-snug text-slate-400">{group.note}</p>
      </div>

      {group.targets ? (
        // A table, not cards. Ninety-seven references resolve to about forty
        // missing targets, and forty cards is a wall you scroll past rather
        // than a list you work through. One row each, widest first, and the
        // referring ids behind a single toggle for the whole group.
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full min-w-[36rem] border-collapse text-left">
            <thead>
              <tr className="border-b border-slate-100 text-[0.68rem] uppercase tracking-[0.06em] text-slate-400">
                <th className="px-4 py-2 font-mono font-normal">指向這個物件，而它不在庫裡</th>
                <th className="px-4 py-2 font-mono font-normal">從哪個 collection</th>
                <th className="px-4 py-2 text-right font-mono font-normal">筆數</th>
              </tr>
            </thead>
            <tbody>
              {group.targets.map((target) => (
                <tr key={target.target} className="border-b border-slate-50 last:border-0 align-top">
                  <td className="break-all px-4 py-2 font-mono text-[0.78rem] text-rose-700">
                    {target.target}
                  </td>
                  <td className="px-4 py-2 font-mono text-[0.72rem] text-slate-500">
                    {target.collections.join("、")}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-[0.78rem] text-slate-900">
                    {count(target.count)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {group.items.map((item) => (
            <FollowUpRow key={`${item.collection}:${item.object_id}`} item={item} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FollowUpRow({ item }: { item: FollowUpItem }) {
  const [copied, setCopied] = useState(false);
  const evidence = item.evidence.filter((row) => row.text);

  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <button
          type="button"
          // The follow-up almost always starts with looking this record up, so
          // the id is one click away from the clipboard rather than a
          // three-line selection out of a monospace block.
          onClick={() => {
            void navigator.clipboard?.writeText(item.object_id).then(
              () => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              },
              () => undefined,
            );
          }}
          className="break-all text-left font-mono text-[0.82rem] font-medium text-slate-900 underline decoration-slate-300 underline-offset-4 hover:decoration-slate-900"
          title="複製 object id"
        >
          {item.object_id}
        </button>
        <span className="inline-flex items-center gap-2">
          {copied && <span className="text-[0.7rem] text-emerald-700">已複製</span>}
          {item.weak && (
            <span
              className="rounded-md bg-amber-50 px-2 py-0.5 text-[0.68rem] text-amber-700"
              title="模型沒拿到逐字稿，只憑證據摘要判"
            >
              沒看到原文
            </span>
          )}
          <span className="rounded-md bg-rose-50 px-2 py-0.5 font-mono text-[0.7rem] text-rose-700">
            {item.verdict.code}
          </span>
        </span>
      </div>

      <p className="mt-1 font-mono text-[0.7rem] text-slate-400">{item.collection}</p>
      <p className="mt-2 text-[0.85rem] leading-relaxed text-slate-800">{item.verdict.text}</p>
      {item.reason && (
        <p className="mt-1 text-[0.82rem] leading-relaxed text-slate-600">{item.reason}</p>
      )}

      {evidence.length > 0 && (
        <dl className="mt-3 flex flex-col gap-2 border-t border-slate-100 pt-3">
          {evidence.map((row) => (
            <div key={row.field} className="flex flex-col gap-0.5">
              <dt className="flex items-baseline gap-2 text-[0.72rem] text-slate-500">
                {row.label}
                <span className="font-mono text-[0.62rem] text-slate-300">{row.field}</span>
              </dt>
              <dd className="break-words text-[0.82rem] leading-relaxed text-slate-700">
                {row.text}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}
