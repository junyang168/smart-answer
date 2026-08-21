import { ratio, shortDate, type Trend } from "./types";

const W = 900;
const H = 110;
const PAD = 14;

/**
 * The corpus's stranded rate over time, with the prompt changes drawn on it.
 *
 * No single document's score can show a change that moved everything at once;
 * a prompt edit that strands twice as much looks, one document at a time, like
 * that document having a bad day.  The markers are not a hand-kept list of
 * releases either -- each package records the `prompt_sha256` that produced it,
 * so a marker is a prompt change that provably happened.
 */
export function TrendLine({ trend }: { trend: Trend }) {
  const points = trend.points;
  if (points.length < 2) {
    return (
      <p className="text-sm text-slate-500">
        只有 {points.length} 天跑過抽取，還畫不出趨勢。等第二天的執行落地後這裡就會有線。
      </p>
    );
  }

  const top = Math.max(...points.map((point) => point.median), 0.1) * 1.15;
  const px = (index: number) => PAD + (index / (points.length - 1)) * (W - PAD * 2);
  const py = (value: number) => H - PAD - (value / top) * (H - PAD * 2);
  const line = points.map((point, index) => `${index ? "L" : "M"}${px(index).toFixed(1)},${py(point.median).toFixed(1)}`).join("");
  const area = `${line}L${px(points.length - 1).toFixed(1)},${H - PAD}L${px(0).toFixed(1)},${H - PAD}Z`;
  const indexOfDate = (date: string) => points.findIndex((point) => point.date === date);
  const last = points[points.length - 1];

  return (
    <div className="flex flex-col gap-2">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[110px] w-full overflow-visible" role="img"
           aria-label={`每天抽取的 stranded 比例中位數，最新一天是 ${ratio(last.median)}`}>
        <path d={area} fill="#be123c" opacity="0.07" />
        <path d={line} fill="none" stroke="#be123c" strokeWidth="1.75" />
        {trend.events.map((event) => {
          const index = indexOfDate(event.date);
          if (index < 0) return null;
          return (
            <g key={event.prompt_sha256}>
              <line x1={px(index)} x2={px(index)} y1={PAD - 6} y2={H - PAD} stroke="#94a3b8" strokeDasharray="3 3" strokeWidth="1" />
              <text x={px(index) + 5} y={PAD - 1} fill="#64748b" fontSize="10" className="font-mono">
                {event.prompt_sha256}
              </text>
            </g>
          );
        })}
        {points.map((point, index) => (
          <circle key={point.date} cx={px(index)} cy={py(point.median)} r={index === points.length - 1 ? 4 : 2.5}
                  fill="#be123c" opacity={index === points.length - 1 ? 1 : 0.55} />
        ))}
      </svg>
      <div className="flex justify-between font-mono text-[0.7rem] text-slate-400">
        {points.map((point) => (
          <span key={point.date}>{shortDate(point.date)}</span>
        ))}
      </div>
    </div>
  );
}
