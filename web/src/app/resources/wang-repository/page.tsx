"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type RepositoryView = "bible" | "topic";

type BibleReference = {
  osis: string;
  display: string;
};

type RepositoryUnit = {
  unit_id: string;
  title: string;
  primary_bible_refs?: BibleReference[];
};

type BibleUnitCard = {
  unit: RepositoryUnit;
  indexReferences: BibleReference[];
};

function uniqueBibleUnits(references: Record<string, RepositoryUnit[]>): BibleUnitCard[] {
  const cards = new Map<string, BibleUnitCard>();
  for (const [osis, units] of Object.entries(references)) {
    for (const unit of units) {
      const existing = cards.get(unit.unit_id);
      if (existing) {
        if (!existing.indexReferences.some((reference) => reference.osis === osis)) {
          existing.indexReferences.push({ osis, display: osis });
        }
        continue;
      }
      const primaryReferences = unit.primary_bible_refs?.length
        ? unit.primary_bible_refs
        : [{ osis, display: osis }];
      cards.set(unit.unit_id, { unit, indexReferences: [...primaryReferences] });
    }
  }
  return [...cards.values()];
}

export default function WangRepositoryPage() {
  const [view, setView] = useState<RepositoryView>("bible");
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    setData(null);
    fetch(`/api/canonical-repository/${view === "bible" ? "bible-index" : "topic-index"}`, {
      cache: "no-store",
    })
      .then((response) => response.json())
      .then(setData);
  }, [view]);

  const bibleUnits = useMemo(
    () => uniqueBibleUnits(data?.references ?? {}),
    [data?.references],
  );
  const unavailable = data?.available === false;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="text-sm font-semibold text-sky-700">達拉斯聖道教會文獻整理計畫</p>
      <h1 className="mt-1 text-4xl font-bold">王守仁教授釋經與專題講論文庫</h1>
      <p className="mt-3 max-w-3xl text-slate-600">
        按聖經經卷與講論專題，整理王守仁教授在不同時期、不同場合的釋經內容。
      </p>
      <div className="mt-6 flex gap-3">
        <button
          onClick={() => setView("bible")}
          className={`rounded-lg px-4 py-2 font-semibold ${view === "bible" ? "bg-sky-600 text-white" : "bg-slate-100"}`}
        >
          聖經目錄
        </button>
        <button
          onClick={() => setView("topic")}
          className={`rounded-lg px-4 py-2 font-semibold ${view === "topic" ? "bg-sky-600 text-white" : "bg-slate-100"}`}
        >
          主題目錄
        </button>
      </div>

      {!data ? (
        <p className="py-12">讀取中…</p>
      ) : unavailable ? (
        <div className="mt-8 rounded-xl border border-amber-200 bg-amber-50 p-6">
          <h2 className="font-bold text-amber-900">文庫尚未正式發布</h2>
          <p className="mt-2 text-amber-800">候選單元仍在人工審閱；公開頁不會顯示未批准內容。</p>
        </div>
      ) : view === "bible" ? (
        <div className="mt-8 space-y-5">
          {bibleUnits.map(({ unit, indexReferences }) => (
            <section key={unit.unit_id} className="rounded-xl border bg-white p-5">
              <div className="flex flex-wrap gap-2">
                {indexReferences.map((reference) => (
                  <span
                    key={reference.osis}
                    className="rounded bg-sky-50 px-2 py-1 text-sm font-semibold text-sky-800"
                  >
                    {reference.display}
                  </span>
                ))}
              </div>
              <Link
                href={`/resources/wang-repository/${unit.unit_id}`}
                className="mt-3 block font-semibold hover:underline"
              >
                {unit.title}
              </Link>
            </section>
          ))}
        </div>
      ) : (
        <div className="mt-8 space-y-5">
          {(data.topics ?? []).map((topic: any) => (
            <section key={topic.topic_id} className="rounded-xl border bg-white p-5">
              <h2 className="font-bold">{topic.path.join(" › ")}</h2>
              {topic.units.map((unit: RepositoryUnit) => (
                <Link
                  key={unit.unit_id}
                  href={`/resources/wang-repository/${unit.unit_id}`}
                  className="mt-3 block text-indigo-700 hover:underline"
                >
                  {unit.title}
                </Link>
              ))}
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
