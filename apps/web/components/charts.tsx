"use client";

/**
 * Lightweight SVG primitives; no external chart library to keep the bundle
 * small and the visual language distinct from Recharts-based OSS dashboards.
 */

import { fmtMs } from "@/lib/format";

export function Sparkline({
  values,
  height = 28,
  tone = "accent",
}: {
  values: number[];
  height?: number;
  tone?: "accent" | "err" | "ink";
}) {
  if (!values.length) return <svg className="eo-sparkline" height={height} />;
  const max = Math.max(...values, 1);
  const step = 100 / Math.max(1, values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(2)},${(100 - (v / max) * 100).toFixed(2)}`)
    .join(" ");
  const color =
    tone === "err" ? "#e3465e" : tone === "ink" ? "#0f1624" : "var(--eo-accent)";
  return (
    <svg
      className="eo-sparkline"
      height={height}
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function StackedBars({
  totals,
  errors,
  labels,
}: {
  totals: number[];
  errors: number[];
  labels?: string[];
}) {
  const max = Math.max(1, ...totals);
  return (
    <>
      <div className="eo-chart-stack" aria-label="trace volume">
        {totals.map((t, i) => {
          const e = errors[i] ?? 0;
          const ok = Math.max(0, t - e);
          return (
            <div className="eo-chart-col" key={i} title={`${t} traces (${e} errors)`}>
              {ok > 0 && (
                <div
                  className="eo-chart-seg-ok"
                  style={{ height: `${(ok / max) * 100}%` }}
                />
              )}
              {e > 0 && (
                <div
                  className="eo-chart-seg-err"
                  style={{ height: `${(e / max) * 100}%` }}
                />
              )}
            </div>
          );
        })}
      </div>
      {labels && (
        <div className="eo-chart-axis">
          <span>{labels[0]}</span>
          <span>{labels[Math.floor(labels.length / 2)]}</span>
          <span>{labels[labels.length - 1]}</span>
        </div>
      )}
      <div className="eo-legend" style={{ marginTop: 6 }}>
        <span>
          <i style={{ background: "#22c07a" }} />
          success
        </span>
        <span>
          <i style={{ background: "#e3465e" }} />
          error
        </span>
      </div>
    </>
  );
}

export function Donut({
  slices,
  size = 120,
}: {
  slices: Array<{ label: string; value: number; color: string }>;
  size?: number;
}) {
  const total = Math.max(
    1,
    slices.reduce((acc, s) => acc + s.value, 0),
  );
  const r = size / 2 - 10;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="eo-donut">
      <svg className="eo-donut-svg" viewBox={`0 0 ${size} ${size}`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#edf1f6"
          strokeWidth={12}
        />
        {slices.map((s) => {
          const frac = s.value / total;
          const dash = frac * c;
          const el = (
            <circle
              key={s.label}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={12}
              strokeDasharray={`${dash} ${c - dash}`}
              strokeDashoffset={-offset}
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
            />
          );
          offset += dash;
          return el;
        })}
        <text
          x="50%"
          y="52%"
          textAnchor="middle"
          fontFamily="var(--eo-mono)"
          fontSize="14"
          fontWeight="700"
          fill="var(--eo-ink)"
        >
          {total}
        </text>
      </svg>
      <div className="eo-donut-legend">
        {slices.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} />
            <strong className="mono">{s.value}</strong>
            <span className="eo-muted" style={{ marginLeft: 4 }}>
              {s.label}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function PercentileBars({
  values,
  cap,
}: {
  values: Array<{ key: string; value: number }>;
  cap?: number;
}) {
  const max = cap ?? Math.max(1, ...values.map((v) => v.value));
  return (
    <div className="eo-percentile">
      {values.map((row) => (
        <div
          key={row.key}
          className="eo-perc-row"
          data-kind={row.key.toLowerCase()}
        >
          <span className="mono">{row.key}</span>
          <div className="eo-perc-bar">
            <i style={{ width: `${Math.min(100, (row.value / max) * 100)}%` }} />
          </div>
          <span className="mono">{fmtMs(row.value)}</span>
        </div>
      ))}
    </div>
  );
}
