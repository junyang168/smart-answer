"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type UnitStatus = "candidate" | "reviewed" | "published" | "archived";

type RepositoryUnit = {
  unit_id: string;
  title: string;
  unit_type: "passage" | "concept";
  status: UnitStatus;
  primary_bible_refs: Array<{ osis: string; display: string }>;
  topic_assignments: Array<{
    path: string[];
    role: "primary" | "secondary";
  }>;
};

const STATUS_LABELS: Record<UnitStatus, string> = {
  candidate: "候選",
  reviewed: "已審閱",
  published: "已發布",
  archived: "已封存",
};

const STATUS_STYLES: Record<UnitStatus, string> = {
  candidate: "bg-amber-50 text-amber-700",
  reviewed: "bg-blue-50 text-blue-700",
  published: "bg-emerald-50 text-emerald-700",
  archived: "bg-slate-100 text-slate-500",
};

function UnitList({ title, units }: { title: string; units: RepositoryUnit[] }) {
  return (
    <section>
      <div className="flex items-center justify-between">
        <h4 className="font-bold text-slate-800">{title}</h4>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">
          {units.length}
        </span>
      </div>
      {units.length ? (
        <div className="mt-2 space-y-2">
          {units.map((unit) => {
            const primaryTopic = unit.topic_assignments.find((assignment) => assignment.role === "primary");
            const context = unit.unit_type === "passage"
              ? unit.primary_bible_refs.map((reference) => reference.display).join("、")
              : primaryTopic?.path.join(" › ") || "尚未指定主題";
            return (
              <article key={unit.unit_id} className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex items-start justify-between gap-2">
                  <Link
                    href={`/admin/canonical-repository/${encodeURIComponent(unit.unit_id)}`}
                    className="text-sm font-semibold leading-5 text-indigo-700 hover:underline"
                  >
                    {unit.title}
                  </Link>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold ${STATUS_STYLES[unit.status]}`}>
                    {STATUS_LABELS[unit.status]}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{context}</p>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="mt-2 text-sm text-slate-500">本篇講道尚無此類單元。</p>
      )}
    </section>
  );
}

export function SermonRepositoryUnits({ sermonId }: { sermonId: string }) {
  const [units, setUnits] = useState<RepositoryUnit[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetch(`/api/admin/canonical-repository/units?source_origin_id=${encodeURIComponent(sermonId)}`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "無法讀取文庫單元");
        setUnits(payload.units || []);
      })
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason.message || "無法讀取文庫單元");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [sermonId]);

  const passageUnits = useMemo(() => units.filter((unit) => unit.unit_type === "passage"), [units]);
  const conceptUnits = useMemo(() => units.filter((unit) => unit.unit_type === "concept"), [units]);

  return (
    <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-xl font-bold font-display">文庫單元</h3>
        <Link href="/admin/canonical-repository" className="text-xs font-semibold text-indigo-700 hover:underline">
          查看全部
        </Link>
      </div>
      <p className="mt-1 text-xs leading-5 text-slate-500">包含尚未發布的候選與已審閱單元。</p>
      {loading ? (
        <p className="mt-4 text-sm text-slate-500">讀取中…</p>
      ) : error ? (
        <p className="mt-4 text-sm text-red-600">{error}</p>
      ) : (
        <div className="mt-4 max-h-[42rem] space-y-5 overflow-y-auto pr-1">
          <UnitList title="釋經單元" units={passageUnits} />
          <UnitList title="主題單元" units={conceptUnits} />
        </div>
      )}
    </div>
  );
}
