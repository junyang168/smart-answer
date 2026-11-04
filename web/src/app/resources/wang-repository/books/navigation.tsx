"use client";

/** 入口、章列表、按章页面共用的来源标签。来源清单来自后端，不写死。 */

export type SourceCount = { name: string; count: number; unit: string };

export const total = (sources: SourceCount[]) => sources.reduce((sum, item) => sum + item.count, 0);

export function SourceChips({
  sources,
  picked,
  onPick,
  withCount = false,
}: {
  sources: SourceCount[];
  picked: string;
  onPick: (name: string) => void;
  withCount?: boolean;
}) {
  const options = [{ name: "", label: "全部", count: total(sources), unit: "篇" }, ...sources.map((s) => ({ ...s, label: s.name }))];
  return (
    <div className="flex flex-wrap gap-2.5" role="group" aria-label="來源">
      {options.map((option) => {
        const on = option.name === picked;
        return (
          <button
            key={option.label}
            type="button"
            aria-pressed={on}
            onClick={() => onPick(option.name)}
            className={`inline-flex min-h-11 items-center gap-2 rounded-full border px-4 text-[15px] transition ${
              on ? "border-stone-900 bg-stone-900 text-white" : "border-stone-300 bg-white text-stone-800 hover:bg-stone-100"
            }`}
          >
            <span>{option.label}</span>
            {withCount && option.name && <span className="font-mono text-[13px] opacity-75">{option.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
