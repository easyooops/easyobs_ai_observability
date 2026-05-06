"use client";

/**
 * Golden Anchor Sets — first-entry visual strip.
 *
 * Two pieces:
 *  1. **GoldenWorkbenchKpiStrip** — mode distribution / average items /
 *     agent-wired set count.
 *  2. **GoldenTriPanel**         — Data / Trust / Usage three-panel header
 *     for a selected Set. Always rendered so the user sees ground-truth
 *     distribution, inter-rater reliability, and run history at a glance.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import {
  fetchEvalRuns,
  fetchGoldenItems,
  fetchGoldenRevisions,
  fetchRevisionTrust,
  type EvalRun,
  type GoldenItem,
  type GoldenLayer,
  type GoldenSet,
} from "@/lib/api";
import { fmtPct, fmtRel } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

// ---------------------------------------------------------------------------
// 1) Workbench-level KPI strip (top of Golden list)
// ---------------------------------------------------------------------------

export function GoldenWorkbenchKpiStrip({ sets }: { sets: GoldenSet[] }) {
  const { t, tsub } = useI18n();
  const total = sets.length;
  const byMode = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of sets) {
      const k = s.mode ?? "regression";
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return m;
  }, [sets]);
  const totalItems = sets.reduce((s, g) => s + (g.itemCount || 0), 0);
  const avgItems = total === 0 ? 0 : Math.round(totalItems / total);
  const withAgent = sets.filter(
    (s) => !!s.agentInvoke?.endpointUrl?.trim(),
  ).length;
  const mode = (k: string) => byMode.get(k) ?? 0;
  return (
    <div className="eo-kpi-grid" style={{ marginBottom: 12 }}>
      <article className="eo-kpi" data-tone="ink">
        <span className="eo-kpi-label">{t("pages.golden.overview.goldenSets")}</span>
        <strong className="eo-kpi-value">{total}</strong>
        <span className="eo-kpi-meta">
          regression {mode("regression")} · cohort {mode("cohort")} · synth{" "}
          {mode("synthesized")}
        </span>
      </article>
      <article className="eo-kpi">
        <span className="eo-kpi-label">{t("pages.golden.overview.avgItems")}</span>
        <strong className="eo-kpi-value">{avgItems}</strong>
        <span className="eo-kpi-meta">
          {tsub("pages.golden.overview.totalItems", { count: totalItems.toLocaleString() })}
        </span>
      </article>
      <article
        className="eo-kpi"
        data-tone={withAgent === 0 ? "warn" : "ok"}
      >
        <span className="eo-kpi-label">{t("pages.golden.overview.agentLinked")}</span>
        <strong className="eo-kpi-value">{withAgent}</strong>
        <span className="eo-kpi-meta">
          {t("pages.golden.overview.setsReadyForRegression")}
        </span>
      </article>
      <article className="eo-kpi" data-tone="warn">
        <span className="eo-kpi-label">{t("pages.golden.overview.creationMethods")}</span>
        <strong className="eo-kpi-value" style={{ fontSize: 16 }}>
          A · B · C
        </strong>
        <span className="eo-kpi-meta">
          {t("pages.golden.overview.creationMethodsList")}
        </span>
      </article>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2) Tri-panel Detail Header — Data / Trust / Usage
// ---------------------------------------------------------------------------

const LAYER_ORDER: GoldenLayer[] = ["L1", "L2", "L3"];

function sourceLabel(t: (key: string) => string) {
  return {
    human_manual: t("pages.golden.overview.sourceManual"),
    auto_synth: t("pages.golden.overview.sourceAutoGen"),
    trace_label: t("pages.golden.overview.sourceTraceLabel"),
    import: t("pages.golden.overview.sourceUpload"),
  } as Record<string, string>;
}

export function GoldenTriPanel({ set }: { set: GoldenSet }) {
  const { t, tsub } = useI18n();
  const items = useQuery({
    queryKey: ["eval", "golden-items", set.id],
    queryFn: () => fetchGoldenItems(set.id),
  });
  const revisions = useQuery({
    queryKey: ["eval", "golden-revisions", set.id],
    queryFn: () => fetchGoldenRevisions(set.id),
  });
  const latestRev = revisions.data?.[0]?.revisionNo ?? null;
  const trust = useQuery({
    queryKey: ["eval", "golden-trust", set.id, latestRev ?? -1],
    queryFn: () =>
      latestRev != null
        ? fetchRevisionTrust(set.id, latestRev)
        : Promise.resolve(null),
    enabled: latestRev != null,
  });
  const runs = useQuery({
    queryKey: ["eval", "runs"],
    queryFn: () => fetchEvalRuns(200),
  });
  const matchedRuns = useMemo(
    () => (runs.data ?? []).filter((r: EvalRun) => r.goldenSetId === set.id),
    [runs.data, set.id],
  );
  const lastRun = matchedRuns[0] ?? null;

  const list = items.data ?? [];
  const layerDist = countLayer(list);
  const sourceDist = countSource(list);
  const reviewDist = countReview(list);
  const passSeries = matchedRuns
    .slice()
    .reverse()
    .map((r) => Math.max(0, Math.min(1, r.passRate)));
  const lastPass = lastRun ? lastRun.passRate : null;

  const mode = set.mode ?? "regression";
  const modeTone =
    mode === "regression" ? "ok" : mode === "cohort" ? "warn" : "ink";
  const hasAgent = !!set.agentInvoke?.endpointUrl?.trim();

  return (
    <div
      className="eo-card"
      style={{ background: "var(--eo-bg-2)", marginBottom: 10 }}
    >
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 8,
        }}
      >
        <strong style={{ fontSize: 16 }}>{set.name}</strong>
        <span className="eo-tag" data-tone={modeTone}>
          mode · {mode}
        </span>
        <span className="eo-tag eo-tag-accent">{set.layer}</span>
        <span className="eo-mute mono">{set.itemCount} items</span>
        <span className="eo-mute mono">
          {tsub("pages.golden.overview.revInfo", { count: String(revisions.data?.length ?? 0), latest: String(latestRev ?? "—") })}
        </span>
        <span
          className="eo-status"
          data-tone={hasAgent ? "ok" : "warn"}
          style={{ fontSize: 12 }}
        >
          {hasAgent
            ? t("pages.golden.overview.agentLinkedStatus")
            : t("pages.golden.overview.agentNotLinked")}
        </span>
        <span className="eo-mute mono" style={{ marginLeft: "auto" }}>
          created {fmtRel(set.createdAt)}
        </span>
      </div>

      <div className="eo-grid-3" style={{ gap: 8 }}>
        {/* Data panel */}
        <div
          style={{
            padding: 10,
            background: "var(--eo-paper)",
            borderRadius: 6,
            border: "1px solid var(--eo-line)",
          }}
        >
          <div
            className="eo-card-sub"
            style={{ marginBottom: 6, fontWeight: 600 }}
          >
            {t("pages.golden.overview.dataTitle")}
          </div>
          <Row
            label="L1 / L2 / L3"
            value={`${layerDist.L1} · ${layerDist.L2} · ${layerDist.L3}`}
          />
          <div className="eo-mute" style={{ fontSize: 11, marginTop: 4 }}>
            {t("pages.golden.overview.sourceDistribution")}
          </div>
          <DistList dist={sourceDist} labelMap={sourceLabel(t)} />
          <div className="eo-mute" style={{ fontSize: 11, marginTop: 4 }}>
            {t("pages.golden.overview.reviewState")}
          </div>
          <DistList
            dist={reviewDist}
            labelMap={{
              unreviewed: t("pages.golden.overview.unreviewed"),
              reviewed: t("pages.golden.overview.reviewed"),
              disputed: t("pages.golden.overview.disputed"),
            }}
            tone={(k) =>
              k === "reviewed" ? "ok" : k === "disputed" ? "err" : "warn"
            }
          />
        </div>

        {/* Trust panel */}
        <div
          style={{
            padding: 10,
            background: "var(--eo-paper)",
            borderRadius: 6,
            border: "1px solid var(--eo-line)",
          }}
        >
          <div
            className="eo-card-sub"
            style={{ marginBottom: 6, fontWeight: 600 }}
          >
            {t("pages.golden.overview.trustTitle")}
          </div>
          {!trust.data && (
            <div className="eo-mute" style={{ fontSize: 12 }}>
              {t("pages.golden.overview.trustAutoAggregated")}
            </div>
          )}
          {trust.data && (
            <>
              <TrustLine
                label="Cohen κ"
                value={trust.data.cohenKappa}
                threshold={0.6}
              />
              <TrustLine
                label="Fleiss κ"
                value={trust.data.fleissKappa}
                threshold={0.4}
              />
              <TrustLine
                label="α (nominal)"
                value={trust.data.krippendorffAlphaNominal}
                threshold={0.667}
              />
              <TrustLine
                label="Multi-Judge avg"
                value={trust.data.multiJudgeAvgAgreement}
                threshold={0.7}
              />
              <TrustLine
                label="Human ↔ Judge κ"
                value={trust.data.humanJudgeKappa}
                threshold={0.6}
              />
              <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
                raters {trust.data.raterCount} · judges{" "}
                {trust.data.judgeModelCount} · disputed{" "}
                {trust.data.disputedItemCount}
              </div>
            </>
          )}
        </div>

        {/* Usage panel */}
        <div
          style={{
            padding: 10,
            background: "var(--eo-paper)",
            borderRadius: 6,
            border: "1px solid var(--eo-line)",
          }}
        >
          <div
            className="eo-card-sub"
            style={{ marginBottom: 6, fontWeight: 600 }}
          >
            {t("pages.golden.overview.usageTitle")}
          </div>
          <Row
            label={t("pages.golden.overview.evalRuns")}
            value={
              tsub("pages.golden.overview.runsCount", { count: String(matchedRuns.length) })
            }
          />
          <Row
            label={t("pages.golden.overview.lastPass")}
            value={lastPass == null ? "—" : fmtPct(lastPass * 100)}
          />
          <Row
            label={t("pages.golden.overview.bestPass")}
            value={
              matchedRuns.length === 0
                ? "—"
                : fmtPct(
                    Math.max(...matchedRuns.map((r) => r.passRate)) * 100,
                  )
            }
          />
          {passSeries.length > 0 && (
            <>
              <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
                {t("pages.golden.overview.passRateTrend")}
              </div>
              <Sparkline values={passSeries} />
            </>
          )}
          {matchedRuns.length === 0 && (
            <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
              {t("pages.golden.overview.noRunsYet")}
            </div>
          )}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          marginTop: 8,
          fontSize: 12,
        }}
      >
        <span className="eo-mute">{t("pages.golden.overview.addItems")}</span>
        <span className="eo-tag" data-tone="ok">
          {t("pages.golden.overview.addManual")}
        </span>
        <span className="eo-tag" data-tone="ok">
          {t("pages.golden.overview.addFromTrace")}
        </span>
        <span className="eo-tag" data-tone="ok">
          {t("pages.golden.overview.addLlmAuto")}
        </span>
        <span className="eo-tag" data-tone="ok">
          {t("pages.golden.overview.addUpload")}
        </span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6 }}>
          <span
            className="eo-status"
            data-tone={hasAgent ? "ok" : "warn"}
            style={{ fontSize: 12 }}
          >
            {hasAgent
              ? t("pages.golden.overview.readyForRegression")
              : t("pages.golden.overview.linkAgentFirst")}
          </span>
        </span>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 8,
        fontSize: 12,
        padding: "2px 0",
      }}
    >
      <span className="eo-mute">{label}</span>
      <span className="mono">{value}</span>
    </div>
  );
}

function DistList({
  dist,
  labelMap,
  tone,
}: {
  dist: Map<string, number>;
  labelMap: Record<string, string>;
  tone?: (k: string) => "ok" | "warn" | "err" | "ink";
}) {
  const total = [...dist.values()].reduce((s, v) => s + v, 0);
  if (total === 0) {
    return (
      <div className="eo-mute" style={{ fontSize: 11 }}>
        —
      </div>
    );
  }
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {[...dist.entries()].map(([k, n]) => {
        const t = tone ? tone(k) : "ink";
        const pct = (n / total) * 100;
        return (
          <li
            key={k}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              padding: "2px 0",
            }}
          >
            <span className="eo-status" data-tone={t} style={{ minWidth: 80 }}>
              {labelMap[k] ?? k}
            </span>
            <div
              style={{
                flex: 1,
                height: 6,
                background: "var(--eo-bg-2)",
                borderRadius: 3,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background:
                    t === "ok"
                      ? "var(--eo-ok, #4ade80)"
                      : t === "err"
                        ? "var(--eo-err, #ef4444)"
                        : t === "warn"
                          ? "var(--eo-warn, #c89400)"
                          : "var(--eo-mute)",
                }}
              />
            </div>
            <span className="mono" style={{ width: 40, textAlign: "right" }}>
              {n}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function TrustLine({
  label,
  value,
  threshold,
}: {
  label: string;
  value: number | null;
  threshold: number;
}) {
  const pass = value != null && value >= threshold;
  const tone = value == null ? "warn" : pass ? "ok" : "err";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        fontSize: 12,
        padding: "2px 0",
      }}
    >
      <span className="eo-mute">{label}</span>
      <span className="eo-status" data-tone={tone}>
        {value == null ? "n/a" : value.toFixed(3)}
      </span>
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return (
      <div className="eo-mute" style={{ fontSize: 11 }}>
        —
      </div>
    );
  }
  const max = 1;
  const min = 0;
  const w = 160;
  const h = 30;
  const step = w / (values.length - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const y = h - ((v - min) / (max - min)) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = values[values.length - 1];
  const lastTone =
    last >= 0.7 ? "var(--eo-ok, #4ade80)" : last >= 0.4 ? "var(--eo-warn, #c89400)" : "var(--eo-err, #ef4444)";
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <polyline
        fill="none"
        stroke={lastTone}
        strokeWidth="1.5"
        points={points}
      />
      <circle
        cx={w}
        cy={h - last * h}
        r={2.5}
        fill={lastTone}
      />
    </svg>
  );
}

function countLayer(items: GoldenItem[]): Record<GoldenLayer, number> {
  const out: Record<GoldenLayer, number> = { L1: 0, L2: 0, L3: 0 };
  for (const it of items) {
    if ((LAYER_ORDER as string[]).includes(it.layer)) {
      out[it.layer as GoldenLayer] += 1;
    }
  }
  return out;
}

function countSource(items: GoldenItem[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const it of items) {
    const k = it.sourceKind ?? "unknown";
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  return m;
}

function countReview(items: GoldenItem[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const it of items) {
    const k = it.reviewState ?? "unreviewed";
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  return m;
}
