"use client";

/**
 * Run Detail — sticky left sidebar + 7-tab structure.
 *
 * Hosted under ``/workspace/quality/runs``; tabs are toggled via internal
 * state (route splitting will follow). The sticky sidebar exposes
 * status / cost / trust ★ / source / profile / triggered_by at all times.
 */

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchEvalResults,
  fetchEvalRun,
  type EvalFinding,
  type EvalResult,
  type EvalRun,
} from "@/lib/api";
import { fmtInt, fmtPct, fmtPrice, fmtRel, fmtScore, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";
import { buildCsv, triggerCsvDownload, fetchTraceEnrichments, TRACE_ENRICHMENT_HEADERS, traceEnrichmentToRow } from "@/lib/csv-export";

type TabId =
  | "summary"
  | "results"
  | "trust"
  | "replay"
  | "findings"
  | "improvements"
  | "report";

function useTabs() {
  const { t } = useI18n();
  return [
    { id: "summary" as const, label: t("pages.runs.detail.tabSummary") },
    { id: "results" as const, label: t("pages.runs.detail.tabResults") },
    { id: "trust" as const, label: t("pages.runs.detail.tabTrust") },
    { id: "replay" as const, label: t("pages.runs.detail.tabReplay") },
    { id: "findings" as const, label: t("pages.runs.detail.tabFindings") },
    { id: "improvements" as const, label: t("pages.runs.detail.tabImprovements") },
    { id: "report" as const, label: t("pages.runs.detail.tabReport") },
  ];
}

// ---------------------------------------------------------------------------
// Trust 5종 — 게이지 계산
// ---------------------------------------------------------------------------

function inferRunSource(run: EvalRun): {
  id: string;
  icon: string;
  label: string;
} {
  if (run.runMode === "golden_gt" || run.runMode === "golden_judge") {
    return { id: "golden_set", icon: "★", label: "Golden Set" };
  }
  if (run.runMode === "human_label") {
    const ctx = (run.runContext ?? {}) as { uiSource?: unknown; source?: unknown };
    const ui = String(ctx.uiSource ?? ctx.source ?? "");
    if (ui === "human_label_group" || ui === "human_label") {
      return { id: "human_label", icon: "✎", label: "Human-labeled" };
    }
    return { id: "human_label", icon: "✎", label: "Human-labeled" };
  }
  if (run.subjectCount === 1) return { id: "single_trace", icon: "◇", label: "Single Trace" };
  return { id: "window", icon: "◫", label: "Window" };
}

type TrustValues = {
  coverage: number;
  agreement: number | null;
  ruleVsJudge: number | null;
  humanKappa: number | null;
  drift: number | null;
  meanStars: number;
  sigmas: number[];
  ruleJudgeMatrix: { rrJj: number; rpJp: number; rpJn: number; rnJp: number; rnJn: number };
  errorByType: Map<string, number>;
};

function computeTrust(run: EvalRun, results: EvalResult[]): TrustValues {
  const errors = results.filter((r) => r.verdict === "error");
  const valid = results.filter((r) => r.verdict !== "error");

  const coverage = Math.min(1, run.subjectCount / 50);

  const sigmas = valid
    .map((r) => r.judgeDisagreement)
    .filter((v): v is number => typeof v === "number" && v >= 0);
  const avgSigma =
    sigmas.length === 0 ? null : sigmas.reduce((s, v) => s + v, 0) / sigmas.length;
  const agreement = avgSigma == null ? null : Math.max(0, Math.min(1, 1 - avgSigma));

  let total = 0;
  let matched = 0;
  let rpJp = 0,
    rpJn = 0,
    rnJp = 0,
    rnJn = 0;
  for (const r of valid) {
    if (r.judgeScore == null) continue;
    total += 1;
    const ruleP = r.ruleScore >= 0.5;
    const judgeP = r.judgeScore >= 0.5;
    if (ruleP === judgeP) matched += 1;
    if (ruleP && judgeP) rpJp += 1;
    else if (ruleP && !judgeP) rpJn += 1;
    else if (!ruleP && judgeP) rnJp += 1;
    else rnJn += 1;
  }
  const ruleVsJudge = total === 0 ? null : matched / total;

  const errorByType = new Map<string, number>();
  for (const r of errors) {
    const t = String(
      (r.judgeErrorDetail as { errorType?: unknown } | undefined)?.errorType ??
        "unknown",
    );
    errorByType.set(t, (errorByType.get(t) ?? 0) + 1);
  }

  const known = [coverage, agreement, ruleVsJudge].filter(
    (v): v is number => v != null,
  );
  const meanStars =
    known.length === 0 ? 0 : known.reduce((s, v) => s + v, 0) / known.length;

  return {
    coverage,
    agreement,
    ruleVsJudge,
    humanKappa: null,
    drift: null,
    meanStars,
    sigmas,
    ruleJudgeMatrix: { rrJj: total, rpJp, rpJn, rnJp, rnJn },
    errorByType,
  };
}

// ---------------------------------------------------------------------------
// 좌측 sticky 사이드
// ---------------------------------------------------------------------------

function RunSticky({
  run,
  trust,
  tab,
  onTab,
}: {
  run: EvalRun;
  trust: TrustValues;
  tab: TabId;
  onTab: (t: TabId) => void;
}) {
  const tabs = useTabs();
  const source = inferRunSource(run);
  const stars = Math.round(trust.meanStars * 5);
  const tone =
    run.status === "succeeded" ? "ok" : run.status === "failed" ? "err" : "warn";
  return (
    <aside
      className="eo-card eo-run-sticky"
      style={{
        padding: 10,
        fontSize: 12,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        minWidth: 220,
      }}
    >
      <div className="eo-card-h" style={{ padding: 0 }}>
        <h3 className="eo-card-title" style={{ fontSize: 13 }}>
          Run #{run.id.slice(0, 8)}
        </h3>
        <span className="eo-status" data-tone={tone}>
          {run.status}
        </span>
      </div>
      <Row label="cost" value={`${fmtPrice(run.costActualUsd)} / ${fmtPrice(run.costEstimateUsd)}`} />
      <Row label="trust" value={"★".repeat(stars) + "☆".repeat(5 - stars)} />
      <Row label="source" value={`${source.icon} ${source.label}`} />
      <Row label="profile" value={run.profileId?.slice(0, 8) ?? "—"} />
      <Row label="by" value={run.triggeredBy?.slice(0, 8) ?? "system"} />
      <Row label="started" value={fmtRel(run.startedAt)} />
      <div className="eo-divider" style={{ margin: "4px 0" }} />
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onTab(t.id)}
            data-active={tab === t.id}
            style={{
              textAlign: "left",
              padding: "4px 8px",
              border: "none",
              background:
                tab === t.id ? "var(--eo-bg-3)" : "transparent",
              color: tab === t.id ? "var(--eo-ink)" : "var(--eo-mute)",
              borderLeft:
                tab === t.id
                  ? "2px solid var(--eo-accent, #3b82f6)"
                  : "2px solid transparent",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            ▸ {t.label}
          </button>
        ))}
      </div>
    </aside>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
      <span className="eo-mute" style={{ fontSize: 11 }}>
        {label}
      </span>
      <span className="mono" style={{ fontSize: 11 }}>
        {value}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function SummaryTab({
  run,
  trust,
  results,
}: {
  run: EvalRun;
  trust: TrustValues;
  results: EvalResult[];
}) {
  const { t, tsub } = useI18n();
  const errorCount = results.filter((r) => r.verdict === "error").length;
  const evaluated = results.length - errorCount;
  const lowTrust = trust.meanStars < 0.6;
  return (
    <>
      {lowTrust && (
        <div
          className="eo-empty"
          style={{
            color: "var(--eo-warn, #c89400)",
            border: "1px solid var(--eo-warn, #c89400)",
            marginBottom: 10,
          }}
        >
          {tsub("pages.runs.detail.trustReviewWarning", { pct: String(Math.round(trust.meanStars * 100)) })}
        </div>
      )}
      <div className="eo-kpi-grid">
        <article className="eo-kpi">
          <span className="eo-kpi-label">{t("pages.runs.detail.passRate")}</span>
          <strong className="eo-kpi-value">{fmtPct(run.passRate * 100)}</strong>
          <span className="eo-kpi-meta">
            {run.completedCount} / {run.subjectCount} {t("pages.runs.detail.subjects")}
          </span>
        </article>
        <article className="eo-kpi">
          <span className="eo-kpi-label">{t("pages.runs.detail.avgScore")}</span>
          <strong className="eo-kpi-value">{fmtScore(run.avgScore)}</strong>
          <span className="eo-kpi-meta">{run.failedCount} {t("pages.runs.detail.fail")}</span>
        </article>
        <article className="eo-kpi" data-tone="warn">
          <span className="eo-kpi-label">{t("pages.runs.detail.cost")}</span>
          <strong className="eo-kpi-value">{fmtPrice(run.costActualUsd)}</strong>
          <span className="eo-kpi-meta">{t("pages.runs.detail.est")} {fmtPrice(run.costEstimateUsd)}</span>
        </article>
        <article className="eo-kpi" data-tone={errorCount > 0 ? "err" : "ok"}>
          <span className="eo-kpi-label">{t("pages.runs.detail.judgeError")}</span>
          <strong className="eo-kpi-value">{errorCount}</strong>
          <span className="eo-kpi-meta">
            {evaluated} / {results.length} {t("pages.runs.detail.evaluatedExcluded")}
          </span>
        </article>
      </div>
      <div
        className="eo-card"
        style={{ background: "var(--eo-bg-2)", marginTop: 10 }}
      >
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("pages.runs.detail.fiveTrustGaugesGlance")}
          </h3>
        </div>
        <TrustFiveGrid trust={trust} compact />
      </div>
      {run.notes && (
        <div className="eo-empty" style={{ marginTop: 10 }}>
          {t("pages.runs.detail.notes")}: {run.notes}
        </div>
      )}
    </>
  );
}

function TrustFiveGrid({
  trust,
  compact = false,
}: {
  trust: TrustValues;
  compact?: boolean;
}) {
  const { t, tsub } = useI18n();
  return (
    <div
      className="eo-grid-3"
      style={{ gap: 8, fontSize: compact ? 12 : 13 }}
    >
      <Gauge
        symbol="◎"
        label={t("pages.runs.detail.coverage")}
        score={trust.coverage}
        subtitle={tsub(
          "pages.runs.detail.coverageSubtitle",
          { count: String(Math.round(trust.coverage * 50)) },
        )}
      />
      <Gauge
        symbol="⊕"
        label={t("pages.runs.detail.multiJudgeAgreement")}
        score={trust.agreement}
        subtitle={
          trust.sigmas.length === 0
            ? "n/a"
            : `σ avg ${(1 - (trust.agreement ?? 0)).toFixed(3)} (${trust.sigmas.length})`
        }
      />
      <Gauge
        symbol="⇆"
        label="Rule ⇆ Judge"
        score={trust.ruleVsJudge}
        subtitle={
          trust.ruleJudgeMatrix.rrJj === 0
            ? "n/a"
            : tsub(
                "pages.runs.detail.ruleJudgeMatch",
                {
                  matched: String(Math.round((trust.ruleVsJudge ?? 0) * trust.ruleJudgeMatrix.rrJj)),
                  total: String(trust.ruleJudgeMatrix.rrJj),
                },
              )
        }
      />
      <Gauge
        symbol="κ"
        label="Human ⇆ Judge"
        score={trust.humanKappa}
        subtitle={t("pages.runs.detail.autoComputedOnLabels")}
      />
      <Gauge
        symbol="δ"
        label={t("pages.runs.detail.verdictDrift")}
        score={trust.drift == null ? null : 1 - trust.drift}
        subtitle={t("pages.runs.detail.shownOnReplayCompare")}
      />
    </div>
  );
}

function Gauge({
  symbol,
  label,
  score,
  subtitle,
}: {
  symbol: string;
  label: string;
  score: number | null;
  subtitle: string;
}) {
  const stars =
    score == null ? 0 : Math.max(0, Math.min(5, Math.round(score * 5)));
  const tone =
    score == null
      ? "warn"
      : score >= 0.7
        ? "ok"
        : score >= 0.4
          ? "warn"
          : "err";
  return (
    <div
      style={{
        padding: 8,
        background: "var(--eo-bg-3)",
        borderRadius: 6,
        border: "1px solid var(--eo-line-soft)",
      }}
    >
      <div style={{ fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: "var(--eo-accent, #3b82f6)", marginRight: 4 }}>
          {symbol}
        </span>
        {label}
      </div>
      <div
        style={{ fontSize: 16, fontWeight: 600 }}
        className="eo-status"
        data-tone={tone}
      >
        {score == null ? "n/a" : "★".repeat(stars) + "☆".repeat(5 - stars)}
      </div>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 2 }}>
        {subtitle}
      </div>
    </div>
  );
}

function ResultsTab({ run, results }: { run: EvalRun; results: EvalResult[] }) {
  const { t } = useI18n();
  const [pickedSession, setPickedSession] = useState<string | null>(null);
  const sessions = useMemo(() => {
    const m = new Map<string, EvalResult[]>();
    for (const r of results) {
      const k = r.sessionId ?? "—";
      const arr = m.get(k) ?? [];
      arr.push(r);
      m.set(k, arr);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [results]);
  const detail = useMemo(() => {
    if (pickedSession == null) return [] as EvalResult[];
    const hit = sessions.find(([k]) => k === pickedSession);
    return hit ? [...hit[1]].sort((a, b) => a.traceId.localeCompare(b.traceId)) : [];
  }, [pickedSession, sessions]);
  return (
    <>
      <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="eo-btn eo-btn-ghost"
          onClick={() => downloadResults(run, results)}
          disabled={!results.length}
        >
          {t("pages.runs.detail.downloadCsv")}
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-ghost"
          onClick={() => downloadResultsJson(run, results)}
          disabled={!results.length}
        >
          {t("pages.runs.detail.downloadJson")}
        </button>
      </div>
      <div className="eo-card-sub">
        {t("pages.runs.detail.sessionGroupsHint")}
      </div>
      <div className="eo-table-wrap" style={{ maxHeight: 220, overflow: "auto" }}>
        <table className="eo-table">
          <thead>
            <tr>
              <th>{t("pages.runs.detail.session")}</th>
              <th>{t("pages.runs.detail.traces")}</th>
              <th>{t("pages.runs.detail.pass")}</th>
              <th>{t("pages.runs.detail.fail")}</th>
              <th>{t("pages.runs.detail.error")}</th>
              <th>{t("pages.runs.detail.avgScore")}</th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 && (
              <tr>
                <td colSpan={6}>
                  <div className="eo-empty">No results recorded.</div>
                </td>
              </tr>
            )}
            {sessions.map(([k, rows]) => {
              const pass = rows.filter((r) => r.verdict === "pass").length;
              const fail = rows.filter((r) => r.verdict === "fail").length;
              const err = rows.filter((r) => r.verdict === "error").length;
              const denom = Math.max(rows.length - err, 1);
              const avg = rows.reduce((s, r) => s + r.score, 0) / denom;
              const active = pickedSession === k;
              return (
                <tr
                  key={k}
                  data-active={active}
                  onClick={() => setPickedSession(k)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="mono">{truncate(k, 42)}</td>
                  <td className="mono">{rows.length}</td>
                  <td className="mono">{pass}</td>
                  <td className="mono">{fail}</td>
                  <td className="mono">{err}</td>
                  <td className="mono">{fmtScore(avg)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pickedSession != null && (
        <>
          <div className="eo-card-sub" style={{ marginTop: 14 }}>
            {t("pages.runs.detail.tracesInSession")}{" "}
            <span className="mono">{truncate(pickedSession, 42)}</span>
          </div>
          <div
            className="eo-table-wrap"
            style={{ maxHeight: 420, overflow: "auto" }}
          >
            <table className="eo-table">
              <thead>
                <tr>
                  <th>{t("pages.runs.detail.trace")}</th>
                  <th>{t("pages.runs.detail.verdict")}</th>
                  <th>{t("pages.runs.detail.score")}</th>
                  <th>{t("pages.runs.detail.rule")}</th>
                  <th>{t("pages.runs.detail.judge")}</th>
                  <th>σ</th>
                  <th>{t("pages.runs.detail.cost")}</th>
                  <th>{t("pages.runs.detail.findings")}</th>
                </tr>
              </thead>
              <tbody>
                {detail.map((r) => (
                  <ResultRow key={r.id} r={r} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function TrustTab({
  run,
  trust,
  results,
}: {
  run: EvalRun;
  trust: TrustValues;
  results: EvalResult[];
}) {
  const { t, tsub } = useI18n();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("pages.runs.detail.fiveTrustGauges")}
          </h3>
        </div>
        <TrustFiveGrid trust={trust} />
      </div>

      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("pages.runs.detail.sigmaDistribution")}
          </h3>
          <span className="eo-card-sub">{trust.sigmas.length} samples</span>
        </div>
        <SigmaHistogram sigmas={trust.sigmas} />
      </div>

      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("pages.runs.detail.confusionMatrix")}
          </h3>
          <span className="eo-card-sub">
            {tsub(
              "pages.runs.detail.comparableResults",
              { count: String(trust.ruleJudgeMatrix.rrJj) },
            )}
          </span>
        </div>
        <RuleJudgeMatrix matrix={trust.ruleJudgeMatrix} />
      </div>

      <div
        className="eo-card"
        style={{
          background: "var(--eo-bg-2)",
          color:
            trust.errorByType.size > 0 ? "var(--eo-warn, #c89400)" : undefined,
        }}
      >
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("pages.runs.detail.judgeErrorBreakdown")}
          </h3>
          <span className="eo-card-sub">
            {trust.errorByType.size === 0
              ? t("pages.runs.detail.ok")
              : tsub(
                  "pages.runs.detail.errorsExcluded",
                  { count: String(Array.from(trust.errorByType.values()).reduce((s, v) => s + v, 0)) },
                )}
          </span>
        </div>
        {trust.errorByType.size === 0 ? (
          <div className="eo-mute" style={{ fontSize: 12 }}>
            {t("pages.runs.detail.allJudgeCallsNormal")}
          </div>
        ) : (
          <ul style={{ marginTop: 4, paddingLeft: 16, fontSize: 12 }}>
            {[...trust.errorByType.entries()].map(([t2, n]) => (
              <li key={t2}>
                <span className="mono">{t2}</span> · {n}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="eo-mute" style={{ fontSize: 12 }}>
        run #{run.id.slice(0, 8)} · {results.length} {t("pages.runs.detail.results")} · {t("pages.runs.detail.avgTrust")} ★{" "}
        {Math.round(trust.meanStars * 100)}%
      </div>
    </div>
  );
}

function SigmaHistogram({ sigmas }: { sigmas: number[] }) {
  if (sigmas.length === 0) {
    return (
      <div className="eo-mute" style={{ fontSize: 12 }}>
        {/* shown when ≥ 2 Judge models compare on the same subject */}
        —
      </div>
    );
  }
  const bins = 10;
  const counts = new Array(bins).fill(0) as number[];
  for (const v of sigmas) {
    const i = Math.min(bins - 1, Math.max(0, Math.floor(v * bins)));
    counts[i] += 1;
  }
  const max = Math.max(...counts);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 80 }}>
      {counts.map((c, i) => {
        const h = max === 0 ? 0 : (c / max) * 70 + 4;
        const tone = i < 3 ? "var(--eo-ok, #4ade80)" : i < 7 ? "var(--eo-warn, #c89400)" : "var(--eo-err, #ef4444)";
        return (
          <div
            key={i}
            style={{
              flex: 1,
              height: h,
              background: tone,
              borderRadius: 2,
              opacity: 0.85,
            }}
            title={`σ ∈ [${(i / bins).toFixed(1)}, ${((i + 1) / bins).toFixed(1)}) — ${c} 건`}
          />
        );
      })}
    </div>
  );
}

function RuleJudgeMatrix({
  matrix,
}: {
  matrix: TrustValues["ruleJudgeMatrix"];
}) {
  const { t } = useI18n();
  const tot = matrix.rrJj;
  if (tot === 0) {
    return (
      <div className="eo-mute" style={{ fontSize: 12 }}>
        {t("pages.runs.detail.noComparableResults")}
      </div>
    );
  }
  const cell = (n: number, tone: "ok" | "warn" | "err" | "ink") => (
    <div
      className="eo-status"
      data-tone={tone}
      style={{
        padding: "8px 10px",
        textAlign: "center",
        minWidth: 80,
        fontSize: 13,
      }}
    >
      {n} <span className="eo-mute">({fmtPct((n / tot) * 100)})</span>
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div style={{ width: 80 }} />
        <div className="eo-mute" style={{ width: 80, textAlign: "center" }}>
          Judge +
        </div>
        <div className="eo-mute" style={{ width: 80, textAlign: "center" }}>
          Judge −
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div className="eo-mute" style={{ width: 80, textAlign: "right" }}>
          Rule +
        </div>
        {cell(matrix.rpJp, "ok")}
        {cell(matrix.rpJn, "warn")}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <div className="eo-mute" style={{ width: 80, textAlign: "right" }}>
          Rule −
        </div>
        {cell(matrix.rnJp, "warn")}
        {cell(matrix.rnJn, "ink")}
      </div>
      <div className="eo-mute" style={{ marginTop: 4 }}>
        {t("pages.runs.detail.matrixExplanation")}
      </div>
    </div>
  );
}

function ReplayTab({ run }: { run: EvalRun }) {
  const { t } = useI18n();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.runs.detail.tabReplay")}</h3>
        <span className="eo-card-sub">
          {t("pages.runs.detail.replaySubtitle")}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12 }}>
        {t("pages.runs.detail.replayDescription")}
      </p>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
        run #{run.id.slice(0, 8)} · status={run.status}
      </div>
    </div>
  );
}

function FindingsTab({ results }: { results: EvalResult[] }) {
  const { t, tsub } = useI18n();
  const flat: { result: EvalResult; finding: EvalFinding }[] = [];
  for (const r of results) {
    for (const f of r.findings) flat.push({ result: r, finding: f });
  }
  const grouped = new Map<string, typeof flat>();
  for (const x of flat) {
    const k = x.finding.evaluatorId;
    const arr = grouped.get(k) ?? [];
    arr.push(x);
    grouped.set(k, arr);
  }
  const sorted = [...grouped.entries()].sort((a, b) => b[1].length - a[1].length);
  if (sorted.length === 0) {
    return (
      <div className="eo-empty">
        {t("pages.runs.detail.noFindings")}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {sorted.map(([evaluatorId, rows]) => {
        const fail = rows.filter((x) => x.finding.verdict !== "pass").length;
        return (
          <div
            key={evaluatorId}
            className="eo-card"
            style={{ background: "var(--eo-bg-2)" }}
          >
            <div className="eo-card-h">
              <h3 className="eo-card-title">{evaluatorId}</h3>
              <span className="eo-card-sub">
                {tsub(
                  "pages.runs.detail.findingSummary",
                  { total: String(rows.length), fail: String(fail) },
                )}
              </span>
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {rows.slice(0, 12).map((x, i) => (
                <li key={i}>
                  <span className="mono">{x.result.traceId.slice(0, 12)}</span>
                  {" · "}
                  {x.finding.verdict} · {fmtScore(x.finding.score)} ·{" "}
                  {x.finding.reason ?? ""}
                </li>
              ))}
              {rows.length > 12 && (
                <li className="eo-mute">
                  {tsub(
                    "pages.runs.detail.andMore",
                    { count: String(rows.length - 12) },
                  )}
                </li>
              )}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function ImprovementsTab({ run }: { run: EvalRun }) {
  const { t } = useI18n();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.runs.detail.improvementPack")}</h3>
        <span className="eo-card-sub">
          {t("pages.runs.detail.trustRecoveryActions")}
        </span>
      </div>
      <p style={{ fontSize: 12, lineHeight: 1.5 }}>
        {t("pages.runs.detail.improvementDescBefore")}
        <Link href={`/workspace/quality/improvements/`} className="eo-link">
          {t("pages.runs.detail.improvements")}
        </Link>
        {t("pages.runs.detail.improvementDescAfter")}
      </p>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
        run #{run.id.slice(0, 8)}
      </div>
    </div>
  );
}

function ReportTab({ run, results }: { run: EvalRun; results: EvalResult[] }) {
  const { t } = useI18n();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.runs.detail.tabReport")}</h3>
        <span className="eo-card-sub">
          {t("pages.runs.detail.csvJsonDownload")}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
        <button
          type="button"
          className="eo-btn"
          onClick={() => downloadResults(run, results)}
          disabled={!results.length}
        >
          {t("pages.runs.detail.downloadCsvFull")}
        </button>
        <button
          type="button"
          className="eo-btn"
          onClick={() => downloadResultsJson(run, results)}
          disabled={!results.length}
        >
          {t("pages.runs.detail.downloadJsonFull")}
        </button>
      </div>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
        run #{run.id.slice(0, 8)} · {fmtInt(results.length)} results
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 결과 행
// ---------------------------------------------------------------------------

function ResultRow({ r }: { r: EvalResult }) {
  const [open, setOpen] = useState(false);
  const tone =
    r.verdict === "pass"
      ? "ok"
      : r.verdict === "fail"
        ? "err"
        : r.verdict === "error"
          ? "err"
          : "warn";
  return (
    <Fragment>
      <tr onClick={() => setOpen((v) => !v)} style={{ cursor: "pointer" }}>
        <td className="mono" onClick={(e) => e.stopPropagation()}>
          <a
            href={`/workspace/tracing/detail/?id=${encodeURIComponent(r.traceId)}`}
            className="eo-link"
          >
            {r.traceId.slice(0, 12)}
          </a>
        </td>
        <td>
          <span className="eo-status" data-tone={tone}>
            {r.verdict}
          </span>
        </td>
        <td className="mono">{fmtScore(r.score)}</td>
        <td className="mono">{fmtScore(r.ruleScore)}</td>
        <td className="mono">{r.judgeScore == null ? "—" : fmtScore(r.judgeScore)}</td>
        <td className="mono">
          {r.judgeDisagreement == null ? "—" : r.judgeDisagreement.toFixed(2)}
        </td>
        <td className="mono">{fmtPrice(r.judgeCostUsd)}</td>
        <td>{r.findings.length}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} style={{ background: "var(--eo-bg-2)" }}>
            <div style={{ padding: "6px 8px" }}>
              <div className="eo-card-sub">Findings</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {r.findings.map((f, i) => (
                  <li key={i}>
                    <strong>{f.evaluatorId}</strong>{" "}
                    <span className="eo-mute">({f.kind})</span> ·{" "}
                    {fmtScore(f.score)} · {f.verdict} · {f.reason ?? ""}
                  </li>
                ))}
              </ul>
              {Array.isArray(r.judgePerModel) && r.judgePerModel.length > 0 && (
                <>
                  <div className="eo-card-sub" style={{ marginTop: 6 }}>
                    Per-judge votes
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                    {(r.judgePerModel as Record<string, unknown>[]).map((v, i) => (
                      <li key={i} className="mono">
                        {JSON.stringify(v)}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

// ---------------------------------------------------------------------------
// CSV/JSON download
// ---------------------------------------------------------------------------

async function downloadResults(run: EvalRun, results: EvalResult[]) {
  const traceIds = results.map((r) => r.traceId);
  const enrichments = await fetchTraceEnrichments(traceIds);
  const header = [
    "trace_id", "session_id", "verdict", "score", "rule_score", "judge_score",
    "judge_disagreement", "judge_cost_usd", "judge_input_tokens", "judge_output_tokens",
    "findings",
    ...TRACE_ENRICHMENT_HEADERS,
  ];
  const rows = results.map((r) => {
    const te = enrichments.get(r.traceId);
    return [
      r.traceId, r.sessionId ?? "", r.verdict, r.score, r.ruleScore,
      r.judgeScore ?? "", r.judgeDisagreement ?? "", r.judgeCostUsd,
      r.judgeInputTokens, r.judgeOutputTokens,
      r.findings.map((f) => `${f.evaluatorId}:${f.verdict}(${f.score})`).join(";"),
      ...traceEnrichmentToRow(te),
    ];
  });
  triggerCsvDownload(`eval-run-${run.id.slice(0, 8)}.csv`, buildCsv(header, rows));
}

function downloadResultsJson(run: EvalRun, results: EvalResult[]) {
  triggerDownload(
    `eval-run-${run.id.slice(0, 8)}.json`,
    JSON.stringify({ run, results }, null, 2),
    "application/json",
  );
}

function triggerDownload(name: string, body: string, mime: string) {
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Public — 7-tab Run Detail
// ---------------------------------------------------------------------------

export function WorkbenchRunDetail({ runId }: { runId: string }) {
  const [tab, setTab] = useState<TabId>("summary");
  const tabs = useTabs();

  useEffect(() => {
    setTab("summary");
  }, [runId]);

  const run = useQuery({
    queryKey: ["eval", "run", runId],
    queryFn: () => fetchEvalRun(runId),
    refetchInterval: (q) =>
      q.state.data && q.state.data.status === "running" ? 4_000 : false,
  });
  const results = useQuery({
    queryKey: ["eval", "results", runId],
    queryFn: () => fetchEvalResults(runId, 500),
    enabled: !!runId,
  });

  const trust = useMemo(() => {
    if (!run.data) return null;
    return computeTrust(run.data, results.data ?? []);
  }, [run.data, results.data]);

  if (!run.data) {
    return (
      <div className="eo-card">
        <div className="eo-empty">Loading run…</div>
      </div>
    );
  }
  const r = run.data;
  const list = results.data ?? [];

  return (
    <div
      className="eo-run-detail-grid"
    >
      <RunSticky
        run={r}
        trust={trust ?? computeTrust(r, [])}
        tab={tab}
        onTab={setTab}
      />
      <div className="eo-card" style={{ minWidth: 0 }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {tabs.find((t) => t.id === tab)?.label} · run {r.id.slice(0, 12)}
          </h3>
          <span className="eo-card-sub">
            {r.runMode ?? "trace"}
            {r.goldenSetId ? ` · golden ${r.goldenSetId.slice(0, 8)}` : ""}
          </span>
        </div>
        {tab === "summary" && (
          <SummaryTab run={r} trust={trust ?? computeTrust(r, [])} results={list} />
        )}
        {tab === "results" && <ResultsTab run={r} results={list} />}
        {tab === "trust" && (
          <TrustTab run={r} trust={trust ?? computeTrust(r, [])} results={list} />
        )}
        {tab === "replay" && <ReplayTab run={r} />}
        {tab === "findings" && <FindingsTab results={list} />}
        {tab === "improvements" && <ImprovementsTab run={r} />}
        {tab === "report" && <ReportTab run={r} results={list} />}
      </div>
    </div>
  );
}
