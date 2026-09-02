import { useMemo, useRef, useState, type ReactNode } from "react";
import { fmt } from "../lib/format";

/**
 * Charts are hand-built SVG on purpose: four shapes, one visual language, no
 * runtime dependency to keep an air-gapped bundle small.
 *
 * Colours come from the validated dark palette in styles.css - the ordinal blue
 * ramp for p50/p95/p99 (they are ordered magnitudes of the same thing) and the
 * categorical slots for pipeline stages (they are different things). Status red
 * is reserved for errors and always carries a label.
 */

export const STAGE_SERIES = [
  { key: "queue_ms", label: "Queue", color: "var(--color-series-1)" },
  { key: "preprocess_ms", label: "Preprocess", color: "var(--color-series-2)" },
  { key: "inference_ms", label: "Inference", color: "var(--color-series-3)" },
  { key: "postprocess_ms", label: "Postprocess", color: "var(--color-series-4)" },
] as const;

export function Legend({ items }: { items: { label: string; color: string; note?: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 text-[11.5px] text-ink-2">
          <span className="inline-block h-[3px] w-[12px] rounded-full" style={{ background: item.color }} aria-hidden />
          {item.label}
          {item.note ? <span className="text-ink-3">{item.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * The latency waterfall. This is the one chart that shows up everywhere -
 * in the explorer table, in the playground, in a trace detail - because
 * "which stage owns the time?" is the first question worth asking about a slow
 * prediction. Segments carry a 2px gap so adjacent fills never touch.
 */
export function LatencyBar({
  latency,
  height = 6,
  showLabels = false,
  width,
}: {
  latency: Record<string, unknown>;
  height?: number;
  showLabels?: boolean;
  width?: number;
}) {
  const value = (key: string) => {
    const raw = latency[key];
    return typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, raw) : 0;
  };
  const parts = STAGE_SERIES.map((series) => ({ ...series, value: value(series.key) }));
  const measured = parts.reduce((sum, part) => sum + part.value, 0);
  const total = value("total_ms") || measured;
  const overhead = Math.max(0, total - measured);
  const scale = total > 0 ? total : 1;
  const title = parts.map((part) => `${part.label} ${fmt.ms(part.value)}`).join(" · ") + (overhead > 0.5 ? ` · Gateway ${fmt.ms(overhead)}` : "");

  return (
    <div style={width ? { width } : undefined} className="min-w-[80px]">
      <div className="flex items-center gap-[2px]" style={{ height }} title={title} aria-label={`Latency breakdown: ${title}`}>
        {parts.map((part) =>
          part.value <= 0 ? null : (
            <span
              key={part.key}
              style={{ width: `${(part.value / scale) * 100}%`, background: part.color, height }}
              className="rounded-[2px]"
            />
          )
        )}
        {overhead > 0.5 ? (
          <span style={{ width: `${(overhead / scale) * 100}%`, height }} className="rounded-[2px] bg-[#3a3a40]" />
        ) : null}
      </div>
      {showLabels ? (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {parts.map((part) => (
            <div key={part.key} className="flex items-center justify-between gap-2 text-[11.5px]">
              <span className="flex items-center gap-1.5 text-ink-2">
                <span className="inline-block h-[7px] w-[7px] rounded-[2px]" style={{ background: part.color }} aria-hidden />
                {part.label}
              </span>
              <span className="num text-ink">{fmt.ms(part.value)}</span>
            </div>
          ))}
          {overhead > 0.5 ? (
            <div className="flex items-center justify-between gap-2 text-[11.5px]">
              <span className="flex items-center gap-1.5 text-ink-2">
                <span className="inline-block h-[7px] w-[7px] rounded-[2px] bg-[#3a3a40]" aria-hidden />
                Gateway
              </span>
              <span className="num text-ink">{fmt.ms(overhead)}</span>
            </div>
          ) : null}
          <div className="flex items-center justify-between gap-2 border-t border-line pt-1 text-[11.5px] sm:col-span-3">
            <span className="text-ink-2">Total</span>
            <span className="num text-ink">{fmt.ms(total)}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type Point = { ts: string; [key: string]: number | string | null };

/** Line chart with a crosshair and tooltip. One y-axis, always. */
export function TimeSeries({
  points,
  series,
  height = 168,
  unit = "ms",
  emptyMessage = "No traffic in this window.",
}: {
  points: Point[];
  series: { key: string; label: string; color: string }[];
  height?: number;
  unit?: string;
  emptyMessage?: string;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const padding = { top: 10, right: 8, bottom: 20, left: 42 };
  const width = 720;

  const values = points.flatMap((point) => series.map((s) => Number(point[s.key] ?? NaN))).filter((v) => Number.isFinite(v));
  const max = values.length ? Math.max(...values) : 1;
  const ceiling = niceCeiling(max);

  if (!points.length) {
    return <div className="flex h-[168px] items-center justify-center text-[12.5px] text-ink-3">{emptyMessage}</div>;
  }

  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const x = (index: number) => padding.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value: number) => padding.top + plotHeight - (value / ceiling) * plotHeight;

  const ticks = [0, ceiling / 2, ceiling];
  const hovered = hover !== null ? points[hover] : null;

  return (
    <div className="relative">
      <svg
        ref={ref}
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${series.map((s) => s.label).join(", ")} over time`}
        onMouseMove={(event) => {
          const rect = ref.current?.getBoundingClientRect();
          if (!rect) return;
          const ratio = (event.clientX - rect.left) / rect.width;
          const svgX = ratio * width;
          const index = Math.round(((svgX - padding.left) / plotWidth) * (points.length - 1));
          setHover(Math.min(points.length - 1, Math.max(0, index)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)} stroke="#1c1c20" strokeWidth="1" />
            <text x={padding.left - 8} y={y(tick) + 3.5} textAnchor="end" fontSize="10" fill="#6e6e73" className="mono">
              {tick === 0 ? "0" : tick >= 1000 ? `${(tick / 1000).toFixed(1)}k` : tick.toFixed(tick < 10 ? 1 : 0)}
            </text>
          </g>
        ))}

        {series.map((s) => {
          const path = points
            .map((point, index) => {
              const value = Number(point[s.key] ?? 0);
              return `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(Number.isFinite(value) ? value : 0).toFixed(1)}`;
            })
            .join(" ");
          if (points.length === 1) {
            // a one-point line draws nothing, so mark the reading instead
            const value = Number(points[0][s.key] ?? 0);
            return <circle key={s.key} cx={x(0)} cy={y(value)} r="4" fill={s.color} />;
          }
          return <path key={s.key} d={path} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />;
        })}

        {hover !== null ? (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={padding.top} y2={height - padding.bottom} stroke="#3a3a40" strokeWidth="1" />
            {series.map((s) => {
              const value = Number(points[hover][s.key] ?? NaN);
              if (!Number.isFinite(value)) return null;
              return <circle key={s.key} cx={x(hover)} cy={y(value)} r="3.5" fill={s.color} stroke="var(--color-panel)" strokeWidth="2" />;
            })}
          </>
        ) : null}

        <text x={padding.left} y={height - 6} fontSize="10" fill="#6e6e73" className="mono">
          {shortTime(points[0].ts)}
        </text>
        <text x={width - padding.right} y={height - 6} textAnchor="end" fontSize="10" fill="#6e6e73" className="mono">
          {shortTime(points[points.length - 1].ts)}
        </text>
      </svg>

      {hovered ? (
        <div
          className="panel pointer-events-none absolute top-1 z-10 bg-raised px-2.5 py-2 text-[11.5px] shadow-xl"
          style={{ left: `calc(${((x(hover!) - padding.left) / plotWidth) * 100}% - 20px)`, maxWidth: 190 }}
        >
          <p className="mono mb-1 text-[10.5px] text-ink-3">{fmt.when(hovered.ts as string)}</p>
          {series.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5 text-ink-2">
                <span className="inline-block h-[3px] w-[10px] rounded-full" style={{ background: s.color }} aria-hidden />
                {s.label}
              </span>
              <span className="num text-ink">
                {hovered[s.key] === null || hovered[s.key] === undefined ? "—" : unit === "ms" ? fmt.ms(Number(hovered[s.key])) : fmt.int(Number(hovered[s.key]))}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Requests and errors per bucket. Errors wear the reserved status red. */
export function VolumeChart({ points, height = 120 }: { points: Point[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) {
    return <div className="flex items-center justify-center text-[12.5px] text-ink-3" style={{ height }}>No traffic in this window.</div>;
  }
  const max = Math.max(1, ...points.map((point) => Number(point.requests ?? 0)));
  const slot = 100 / points.length;
  // With few buckets a percentage-width bar becomes a filled panel, which reads
  // as "everything" rather than as one measurement. Cap the width instead.
  const barWidth = points.length > 24 ? `calc(${slot}% - 2px)` : `min(calc(${slot}% - 2px), 34px)`;

  return (
    <div className="relative" style={{ height }}>
      <div className="flex h-full items-end gap-[2px]">
        {points.map((point, index) => {
          const requests = Number(point.requests ?? 0);
          const errors = Number(point.errors ?? 0);
          const okHeight = ((requests - errors) / max) * 100;
          const errHeight = (errors / max) * 100;
          return (
            <div
              key={index}
              className="relative flex h-full flex-col justify-end"
              style={{ width: barWidth }}
              onMouseEnter={() => setHover(index)}
              onMouseLeave={() => setHover(null)}
              title={`${fmt.when(point.ts as string)} · ${fmt.int(requests)} requests, ${fmt.int(errors)} errors`}
            >
              {errHeight > 0 ? <div style={{ height: `${errHeight}%`, background: "var(--color-bad)" }} className="rounded-t-[2px]" /> : null}
              <div
                style={{ height: `${Math.max(okHeight, requests > 0 ? 1.5 : 0)}%`, background: "var(--color-series-1)", marginTop: errHeight > 0 ? 2 : 0 }}
                className={errHeight > 0 ? "" : "rounded-t-[2px]"}
              />
            </div>
          );
        })}
      </div>
      {hover !== null ? (
        <div className="panel pointer-events-none absolute -top-1 left-0 z-10 bg-raised px-2.5 py-1.5 text-[11.5px] shadow-xl">
          <span className="num text-ink">{fmt.int(Number(points[hover].requests ?? 0))}</span> <span className="text-ink-3">requests</span>
          {Number(points[hover].errors ?? 0) > 0 ? (
            <>
              {" · "}
              <span className="num text-bad">{fmt.int(Number(points[hover].errors))}</span> <span className="text-ink-3">errors</span>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Average stage cost across a window, as one stacked row per stage. */
export function StageBreakdown({ stages }: { stages: Record<string, number> }) {
  const total = STAGE_SERIES.reduce((sum, series) => sum + (stages[series.key] ?? 0), 0) + (stages.gateway_ms ?? 0);
  const rows = [
    ...STAGE_SERIES.map((series) => ({ label: series.label, value: stages[series.key] ?? 0, color: series.color })),
    { label: "Gateway", value: stages.gateway_ms ?? 0, color: "#3a3a40" },
  ];
  const max = Math.max(...rows.map((row) => row.value), 0.001);

  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[86px_1fr_58px] items-center gap-3">
          <span className="text-[11.5px] text-ink-2">{row.label}</span>
          <div className="h-[8px] rounded-[2px] bg-[#141418]">
            <div className="h-[8px] rounded-[2px]" style={{ width: `${(row.value / max) * 100}%`, background: row.color }} />
          </div>
          <span className="num text-right text-[11.5px] text-ink">{fmt.ms(row.value, 2)}</span>
        </div>
      ))}
      <div className="flex items-center justify-between border-t border-line pt-2 text-[11.5px]">
        <span className="text-ink-2">Mean total</span>
        <span className="num text-ink">{fmt.ms(total, 2)}</span>
      </div>
    </div>
  );
}

export function Sparkline({ values, color = "var(--color-series-1)", height = 26, width = 90 }: { values: number[]; color?: string; height?: number; width?: number }) {
  const path = useMemo(() => {
    if (values.length < 2) return "";
    const max = Math.max(...values, 1);
    return values
      .map((value, index) => `${index === 0 ? "M" : "L"}${((index / (values.length - 1)) * width).toFixed(1)},${(height - (value / max) * (height - 2) - 1).toFixed(1)}`)
      .join(" ");
  }, [values, height, width]);
  if (!path) return <span className="text-[11px] text-ink-3">—</span>;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function MetricTile({ label, value, sub, tone: toneName = "neutral", children }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "neutral" | "good" | "warn" | "bad"; children?: ReactNode }) {
  const colors = { neutral: "text-ink", good: "text-good", warn: "text-warn", bad: "text-bad" };
  return (
    <div className="panel px-4 py-3">
      <p className="eyebrow">{label}</p>
      <p className={`num mt-1.5 text-[22px] leading-none ${colors[toneName]}`}>{value}</p>
      {sub ? <p className="mt-1.5 text-[11.5px] text-ink-3">{sub}</p> : null}
      {children ? <div className="mt-2">{children}</div> : null}
    </div>
  );
}

function niceCeiling(max: number) {
  if (max <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  return Math.ceil(max / magnitude) * magnitude;
}

function shortTime(ts: string) {
  const date = new Date(ts);
  return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
