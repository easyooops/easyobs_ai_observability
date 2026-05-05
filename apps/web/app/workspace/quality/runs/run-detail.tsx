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
import { useBilingual } from "@/lib/i18n/bilingual";

type TabId =
  | "summary"
  | "results"
  | "trust"
  | "replay"
  | "findings"
  | "improvements"
  | "report";

function useTabs() {
  const b = useBilingual();
  return [
    { id: "summary" as const, label: b("Summary", "요약") },
    { id: "results" as const, label: b("Results", "결과") },
    { id: "trust" as const, label: b("Trust", "신뢰도") },
    { id: "replay" as const, label: b("Replay", "리플레이") },
    { id: "findings" as const, label: b("Findings", "발견") },
    { id: "improvements" as const, label: b("Improvements", "개선") },
    { id: "report" as const, label: b("Report", "리포트") },
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
      className="eo-card"
      style={{
        position: "sticky",
        top: 8,
        alignSelf: "flex-start",
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
  const b = useBilingual();
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
          {b(
            `⚠ This Run needs trust review (5-gauge mean ${Math.round(trust.meanStars * 100)}%).`,
            `⚠ 이 Run 은 신뢰도 검토가 필요합니다 (5종 게이지 평균 ${Math.round(trust.meanStars * 100)}%).`,
          )}
        </div>
      )}
      <div className="eo-kpi-grid">
        <article className="eo-kpi">
          <span className="eo-kpi-label">{b("Pass Rate", "Pass Rate")}</span>
          <strong className="eo-kpi-value">{fmtPct(run.passRate * 100)}</strong>
          <span className="eo-kpi-meta">
            {run.completedCount} / {run.subjectCount} {b("subjects", "대상")}
          </span>
        </article>
        <article className="eo-kpi">
          <span className="eo-kpi-label">{b("Avg Score", "평균 점수")}</span>
          <strong className="eo-kpi-value">{fmtScore(run.avgScore)}</strong>
          <span className="eo-kpi-meta">{run.failedCount} {b("fail", "실패")}</span>
        </article>
        <article className="eo-kpi" data-tone="warn">
          <span className="eo-kpi-label">{b("Cost", "비용")}</span>
          <strong className="eo-kpi-value">{fmtPrice(run.costActualUsd)}</strong>
          <span className="eo-kpi-meta">{b("est", "추정")} {fmtPrice(run.costEstimateUsd)}</span>
        </article>
        <article className="eo-kpi" data-tone={errorCount > 0 ? "err" : "ok"}>
          <span className="eo-kpi-label">{b("Judge error", "Judge 오류")}</span>
          <strong className="eo-kpi-value">{errorCount}</strong>
          <span className="eo-kpi-meta">
            {evaluated} / {results.length} {b("evaluated · excluded from stats", "평가됨 · 통계 집계 제외")}
          </span>
        </article>
      </div>
      <div
        className="eo-card"
        style={{ background: "var(--eo-bg-2)", marginTop: 10 }}
      >
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {b("Five trust gauges — at a glance", "5종 신뢰 게이지 — 한눈에")}
          </h3>
        </div>
        <TrustFiveGrid trust={trust} compact />
      </div>
      {run.notes && (
        <div className="eo-empty" style={{ marginTop: 10 }}>
          {b("Notes", "메모")}: {run.notes}
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
  const b = useBilingual();
  return (
    <div
      className="eo-grid-3"
      style={{ gap: 8, fontSize: compact ? 12 : 13 }}
    >
      <Gauge
        symbol="◎"
        label={b("Coverage", "Coverage")}
        score={trust.coverage}
        subtitle={b(
          `${Math.round(trust.coverage * 50)} / 50 recommended`,
          `${Math.round(trust.coverage * 50)} / 50 권장`,
        )}
      />
      <Gauge
        symbol="⊕"
        label={b("Multi-Judge agreement", "Multi-Judge 합의")}
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
            : b(
                `${Math.round((trust.ruleVsJudge ?? 0) * trust.ruleJudgeMatrix.rrJj)} / ${trust.ruleJudgeMatrix.rrJj} match`,
                `${Math.round((trust.ruleVsJudge ?? 0) * trust.ruleJudgeMatrix.rrJj)} / ${trust.ruleJudgeMatrix.rrJj} 일치`,
              )
        }
      />
      <Gauge
        symbol="κ"
        label="Human ⇆ Judge"
        score={trust.humanKappa}
        subtitle={b(
          "auto-computed once labels accrue",
          "라벨 누적 시 자동 계산",
        )}
      />
      <Gauge
        symbol="δ"
        label={b("Verdict drift", "Verdict drift")}
        score={trust.drift == null ? null : 1 - trust.drift}
        subtitle={b("shown on Replay compare", "Replay 비교 시 표시")}
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
  const b = useBilingual();
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
          {b("Download CSV", "CSV 받기")}
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-ghost"
          onClick={() => downloadResultsJson(run, results)}
          disabled={!results.length}
        >
          {b("Download JSON", "JSON 받기")}
        </button>
      </div>
      <div className="eo-card-sub">
        {b(
          "Session groups — click to refresh the trace grid below",
          "세션 묶음 — 클릭하면 아래 trace 그리드가 갱신됩니다",
        )}
      </div>
      <div className="eo-table-wrap" style={{ maxHeight: 220, overflow: "auto" }}>
        <table className="eo-table">
          <thead>
            <tr>
              <th>{b("Session", "세션")}</th>
              <th>{b("Traces", "트레이스")}</th>
              <th>{b("Pass", "통과")}</th>
              <th>{b("Fail", "실패")}</th>
              <th>{b("Error", "오류")}</th>
              <th>{b("Avg score", "평균 점수")}</th>
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
            {b("Traces in session", "세션 내 트레이스")}{" "}
            <span className="mono">{truncate(pickedSession, 42)}</span>
          </div>
          <div
            className="eo-table-wrap"
            style={{ maxHeight: 420, overflow: "auto" }}
          >
            <table className="eo-table">
              <thead>
                <tr>
                  <th>{b("Trace", "Trace")}</th>
                  <th>{b("Verdict", "판정")}</th>
                  <th>{b("Score", "점수")}</th>
                  <th>{b("Rule", "Rule")}</th>
                  <th>{b("Judge", "Judge")}</th>
                  <th>σ</th>
                  <th>{b("Cost", "비용")}</th>
                  <th>{b("Findings", "발견")}</th>
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
  const b = useBilingual();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {b("Five trust gauges", "5종 신뢰 게이지")}
          </h3>
        </div>
        <TrustFiveGrid trust={trust} />
      </div>

      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {b(
              "⊕ Multi-Judge agreement — σ distribution",
              "⊕ Multi-Judge agreement — σ 분포",
            )}
          </h3>
          <span className="eo-card-sub">{trust.sigmas.length} samples</span>
        </div>
        <SigmaHistogram sigmas={trust.sigmas} />
      </div>

      <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {b(
              "⇆ Rule ⇆ Judge confusion matrix",
              "⇆ Rule ⇆ Judge 비교 매트릭스",
            )}
          </h3>
          <span className="eo-card-sub">
            {b(
              `${trust.ruleJudgeMatrix.rrJj} comparable results`,
              `${trust.ruleJudgeMatrix.rrJj} 비교 가능 결과`,
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
            {b("Judge call error breakdown", "Judge 호출 실패 분류")}
          </h3>
          <span className="eo-card-sub">
            {trust.errorByType.size === 0
              ? b("OK", "정상")
              : b(
                  `${Array.from(trust.errorByType.values()).reduce((s, v) => s + v, 0)} errors — excluded from stats`,
                  `${Array.from(trust.errorByType.values()).reduce((s, v) => s + v, 0)} 건 — 통계 집계 제외`,
                )}
          </span>
        </div>
        {trust.errorByType.size === 0 ? (
          <div className="eo-mute" style={{ fontSize: 12 }}>
            {b(
              "All Judge calls completed normally.",
              "모든 Judge 호출이 정상 종료되었습니다.",
            )}
          </div>
        ) : (
          <ul style={{ marginTop: 4, paddingLeft: 16, fontSize: 12 }}>
            {[...trust.errorByType.entries()].map(([t, n]) => (
              <li key={t}>
                <span className="mono">{t}</span> · {n}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="eo-mute" style={{ fontSize: 12 }}>
        run #{run.id.slice(0, 8)} · {results.length} {b("results", "결과")} · {b("avg trust", "평균 신뢰도")} ★{" "}
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
  const b = useBilingual();
  const tot = matrix.rrJj;
  if (tot === 0) {
    return (
      <div className="eo-mute" style={{ fontSize: 12 }}>
        {b(
          "No comparable results — both Rule and Judge scores are required.",
          "Rule + Judge 점수가 모두 있는 결과가 없어 비교할 수 없습니다.",
        )}
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
        {b(
          "Diagonal = agreement. Many Rule⁺Judge⁻ → Rule false-positive; many Rule⁻Judge⁺ → Rule false-negative.",
          "대각선이 일치 — Rule⁺Judge⁻ 가 많으면 Rule false-positive, Rule⁻Judge⁺ 가 많으면 Rule false-negative.",
        )}
      </div>
    </div>
  );
}

function ReplayTab({ run }: { run: EvalRun }) {
  const b = useBilingual();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{b("Replay", "Replay")}</h3>
        <span className="eo-card-sub">
          {b("parent/child slope compare", "parent/child 슬로프 비교")}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12 }}>
        {b(
          "When this Run has a parent_run_id, four metrics (verdict drift δ, pass-rate, avg score, σ) for the same subjects are compared in slope charts. This Run has no recorded parent yet.",
          "이 Run 의 parent_run_id 가 있으면 같은 subject 의 verdict 변동(δ), pass-rate, avg score, σ 4개 metric 을 슬로프 차트로 비교합니다. 현재 Run 은 단독 Run 이거나 parent 가 등록되지 않았습니다.",
        )}
      </p>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
        run #{run.id.slice(0, 8)} · status={run.status}
      </div>
    </div>
  );
}

function FindingsTab({ results }: { results: EvalResult[] }) {
  const b = useBilingual();
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
        {b(
          "No findings recorded for this Run.",
          "이 Run 에 기록된 finding 이 없습니다.",
        )}
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
                {b(
                  `${rows.length} finding · ${fail} non-pass`,
                  `${rows.length} finding · ${fail} non-pass`,
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
                  {b(
                    `… and ${rows.length - 12} more`,
                    `… 외 ${rows.length - 12} 건`,
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
  const b = useBilingual();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{b("Improvement Pack", "개선 팩")}</h3>
        <span className="eo-card-sub">
          {b("trust-recovery actions", "신뢰 게이지 회복 액션")}
        </span>
      </div>
      <p style={{ fontSize: 12, lineHeight: 1.5 }}>
        {b(
          "After a Run, when any of the five trust gauges is below 3 stars, recovery actions are auto-suggested. See the full list on the ",
          "Run 종료 후 5종 신뢰 게이지가 3별 미만이면 자동으로 회복 액션이 제안됩니다 — 자세한 목록은 ",
        )}
        <Link href={`/workspace/quality/improvements/`} className="eo-link">
          {b("Improvements", "개선 팩")}
        </Link>
        {b(
          " screen. Gauge ↔ Improvement category mapping is described in the design doc.",
          " 화면에서 확인하세요.",
        )}
      </p>
      <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
        run #{run.id.slice(0, 8)}
      </div>
    </div>
  );
}

function ReportTab({ run, results }: { run: EvalRun; results: EvalResult[] }) {
  const b = useBilingual();
  return (
    <div className="eo-card" style={{ background: "var(--eo-bg-2)" }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{b("Report", "리포트")}</h3>
        <span className="eo-card-sub">
          {b("CSV / JSON download", "CSV / JSON 다운로드")}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
        <button
          type="button"
          className="eo-btn"
          onClick={() => downloadResults(run, results)}
          disabled={!results.length}
        >
          {b("Download CSV", "CSV 다운로드")}
        </button>
        <button
          type="button"
          className="eo-btn"
          onClick={() => downloadResultsJson(run, results)}
          disabled={!results.length}
        >
          {b("Download JSON", "JSON 다운로드")}
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

function downloadResults(run: EvalRun, results: EvalResult[]) {
  const header = [
    "trace_id",
    "session_id",
    "verdict",
    "score",
    "rule_score",
    "judge_score",
    "judge_disagreement",
    "judge_cost_usd",
    "findings",
  ];
  const csv = [header.join(",")].concat(
    results.map((r) =>
      [
        r.traceId,
        r.sessionId ?? "",
        r.verdict,
        r.score,
        r.ruleScore,
        r.judgeScore ?? "",
        r.judgeDisagreement ?? "",
        r.judgeCostUsd,
        `"${r.findings
          .map((f) => `${f.evaluatorId}:${f.verdict}`)
          .join(";")
          .replace(/"/g, "'")}"`,
      ].join(","),
    ),
  );
  triggerDownload(`eval-run-${run.id.slice(0, 8)}.csv`, csv.join("\n"), "text/csv");
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
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(220px, 240px) 1fr",
        gap: 12,
        alignItems: "flex-start",
      }}
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
