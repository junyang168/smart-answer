import { count, shortfall, type Layer } from "./types";

/**
 * One of the audit's four layers, as a number and the question it answers.
 *
 * No colour scale and no pass/fail badge. The ratios are measured, not graded;
 * one run is not enough to know where a line belongs, and a line drawn now
 * would be read as meaningful before anyone had reason to believe it. What the
 * card does instead is put the shortfall in words -- "10 條對不上" rather than
 * "99.9%" -- because the number a person acts on is the one still left.
 *
 * Layers 3 and 4 are samples and never render as a percentage. 3 of 20 is not
 * 15% of the library, and a percent sign is the fastest way to make someone
 * believe it is.
 */
export function LayerCard({ layer }: { layer: Layer }) {
  const left = shortfall(layer);
  const clean = left === 0;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[0.7rem] font-semibold uppercase tracking-[0.09em] text-slate-500">
          第 {layer.layer} 層 · {layer.name}
        </span>
      </div>

      {layer.kind === "ratio" ? (
        <p className="font-mono text-xl font-medium text-slate-900">
          {count(layer.passed ?? 0)}
          <span className="text-slate-400">/{count(layer.total ?? 0)}</span>
          <span className="ml-1 text-xs text-slate-400">{layer.unit}</span>
        </p>
      ) : (
        <p className="font-mono text-xl font-medium text-slate-900">
          抽 {layer.judged ?? 0}
          <span className="text-slate-400"> · 有異議 </span>
          {layer.disputed ?? 0}
          <span className="ml-1 text-xs text-slate-400">
            母體 {count(layer.population ?? 0)} {layer.unit}
          </span>
        </p>
      )}

      <p className="text-[0.8rem] leading-snug text-slate-400">{layer.question}</p>

      <p className={`text-[0.8rem] leading-snug ${clean ? "text-emerald-700" : "text-slate-700"}`}>
        {clean
          ? layer.kind === "ratio"
            ? "全部對得上。"
            : "抽到的都沒有異議。"
          : layer.kind === "ratio"
            ? `${count(left)} ${layer.unit}對不上，在下面。`
            : `${left} 條有異議，在下面。`}
      </p>

      {layer.detail.filter((row) => row.count > 0).length > 0 && (
        <dl className="mt-1 flex flex-col gap-1 border-t border-slate-100 pt-2">
          {layer.detail
            .filter((row) => row.count > 0)
            .map((row) => (
              <div key={row.label} className="flex items-baseline justify-between gap-3">
                <dt className="font-mono text-[0.72rem] text-slate-500">{row.label}</dt>
                <dd className="text-right text-[0.72rem] text-slate-400">
                  <span className="font-mono text-slate-700">{count(row.count)}</span> · {row.text}
                </dd>
              </div>
            ))}
        </dl>
      )}

      {(layer.model_errors ?? 0) > 0 && (
        <p className="text-[0.75rem] text-amber-700">
          {layer.model_errors} 次模型呼叫失敗，那幾條沒判到。
        </p>
      )}
    </div>
  );
}
