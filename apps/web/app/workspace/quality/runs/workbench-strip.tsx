"use client";

/**
 * Evaluation Workbench first-entry visual strip.
 *
 * Two pieces:
 *  1. **Workbench KPI Strip** — four cards with run-level trust signals
 *  2. **Source 5-card row** — Single Trace / Window / Session / Golden / Upload
 *
 * Both components are client-only and shown immediately on entry.
 */

import { fmtPct, fmtPrice } from "@/lib/format";
import type { EvalRun, EvalRunMode } from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

export type RunSource =
  | "single_trace"
  | "window"
  | "session"
  | "human_label";

type SourceDef = {
  id: RunSource;
  icon: string;
  titleEn: string;
  titleKo: string;
  descEn: string;
  descKo: string;
  hintEn: string;
  hintKo: string;
  runMode: EvalRunMode;
  enableGolden?: boolean;
  needsAgent?: boolean;
};

const SOURCE_DEFS: SourceDef[] = [
  {
    id: "single_trace",
    icon: "◇",
    titleEn: "Single Trace",
    titleKo: "Single Trace",
    descEn: "Verify a single suspicious production trace immediately",
    descKo: "운영 중 의심스러운 1건 즉시 검증",
    hintEn: "Evaluate one trace_id on the spot",
    hintKo: "trace_id 1개로 즉시 평가",
    runMode: "trace",
  },
  {
    id: "window",
    icon: "◫",
    titleEn: "Window",
    titleKo: "Window",
    descEn: "Batch regression over a time window or filter",
    descKo: "기간/필터로 묶음 회귀 평가",
    hintEn: "Sweep last-N-day failing traces in one go",
    hintKo: "지난 N일 fail 트레이스 일괄 검증",
    runMode: "trace",
  },
  {
    id: "session",
    icon: "≋",
    titleEn: "Session",
    titleKo: "Session",
    descEn: "Multi-turn chatbot / agent consistency",
    descKo: "다턴 챗봇·에이전트 일관성 평가",
    hintEn: "Group traces by session_id and evaluate together",
    hintKo: "session_id 단위로 묶어 평가",
    runMode: "trace",
  },
  {
    id: "human_label",
    icon: "✎",
    titleEn: "Human-labeled",
    titleKo: "휴먼 라벨",
    descEn: "Run on traces operators have already verdict-labelled",
    descKo: "휴먼 판정이 등록된 trace 들로 평가",
    hintEn: "Compare human verdict ↔ rule + judge agreement",
    hintKo: "휴먼 판정과 rule + judge 비교",
    runMode: "human_label",
  },
];

export const SOURCES = SOURCE_DEFS;

/** Localized label getter for callers that need a single source's strings. */
export function useSourceLabel() {
  const { t } = useI18n();
  return (id: RunSource) => {
    const s = SOURCE_DEFS.find((x) => x.id === id);
    if (!s) return { title: id, desc: "", hint: "", icon: "·" };
    return {
      icon: s.icon,
      title: t(`pages.runs.workbench.source.${s.id}.title` as never),
      desc: t(`pages.runs.workbench.source.${s.id}.desc` as never),
      hint: t(`pages.runs.workbench.source.${s.id}.hint` as never),
    };
  };
}

/**
 * Top-level KPI strip — answers "how are recent Runs going" in four cards.
 */
export function WorkbenchKpiStrip({ runs }: { runs: EvalRun[] }) {
  const { t, tsub } = useI18n();
  const total = runs.length;
  const last24 = runs.filter((r) => {
    const t0 = Date.parse(r.startedAt);
    return Number.isFinite(t0) && Date.now() - t0 < 24 * 3600 * 1000;
  });
  const running = runs.filter(
    (r) => r.status === "running" || r.status === "queued",
  );
  const failed = runs.filter((r) => r.status === "failed");
  const avgPass =
    runs.length === 0
      ? 0
      : runs.reduce(
          (s, r) => s + (Number.isFinite(r.passRate) ? r.passRate : 0),
          0,
        ) / runs.length;
  const totalCost = runs.reduce(
    (s, r) => s + (Number.isFinite(r.costActualUsd) ? r.costActualUsd : 0),
    0,
  );

  return (
    <div className="eo-kpi-grid" style={{ marginBottom: 12 }}>
      <article className="eo-kpi" data-tone="ink">
        <span className="eo-kpi-label">{t("pages.runs.workbench.kpiRunsLabel")}</span>
        <strong className="eo-kpi-value">{total}</strong>
        <span className="eo-kpi-meta">
          {tsub("pages.runs.workbench.kpiRunsMeta", {
            last24: String(last24.length),
            running: String(running.length),
          })}
        </span>
      </article>
      <article className="eo-kpi">
        <span className="eo-kpi-label">{t("pages.runs.workbench.kpiAvgPassLabel")}</span>
        <strong className="eo-kpi-value">{fmtPct(avgPass * 100)}</strong>
        <span className="eo-kpi-meta">
          {t("pages.runs.workbench.kpiOverLast100")}
        </span>
      </article>
      <article className="eo-kpi" data-tone={failed.length > 0 ? "err" : "ok"}>
        <span className="eo-kpi-label">{t("pages.runs.workbench.kpiFailedLabel")}</span>
        <strong className="eo-kpi-value">{failed.length}</strong>
        <span className="eo-kpi-meta">
          {failed.length === 0
            ? t("pages.runs.workbench.kpiStatusOk")
            : t("pages.runs.workbench.kpiStatusFailed")}
        </span>
      </article>
      <article className="eo-kpi" data-tone="warn">
        <span className="eo-kpi-label">{t("pages.runs.workbench.kpiJudgeCostLabel")}</span>
        <strong className="eo-kpi-value">{fmtPrice(totalCost)}</strong>
        <span className="eo-kpi-meta">
          {t("pages.runs.workbench.kpiOverLast100")}
        </span>
      </article>
    </div>
  );
}

/**
 * Five source cards — explicit "what kind of input am I evaluating?" picker.
 */
export function SourceCards({
  active,
  onPick,
}: {
  active: RunSource;
  onPick: (s: RunSource) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="eo-source-cards-grid">
      {SOURCE_DEFS.map((s) => {
        const isActive = s.id === active;
        const title = t(`pages.runs.workbench.source.${s.id}.title` as never);
        const desc = t(`pages.runs.workbench.source.${s.id}.desc` as never);
        const hint = t(`pages.runs.workbench.source.${s.id}.hint` as never);
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => onPick(s.id)}
            data-active={isActive}
            className="eo-source-card"
            style={{
              textAlign: "left",
              padding: 12,
              background: isActive ? "var(--eo-accent-soft)" : "var(--eo-paper)",
              border: `1px solid ${
                isActive ? "var(--eo-accent)" : "var(--eo-line)"
              }`,
              borderRadius: 8,
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              gap: 4,
              minHeight: 100,
              boxShadow: isActive
                ? "0 2px 6px rgba(17, 179, 154, 0.18)"
                : "var(--eo-shadow)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              <span
                style={{
                  fontSize: 18,
                  width: 22,
                  textAlign: "center",
                  color: isActive ? "var(--eo-accent)" : "var(--eo-ink-soft)",
                }}
              >
                {s.icon}
              </span>
              <span style={{ color: "var(--eo-ink)" }}>{title}</span>
              {isActive && (
                <span
                  className="eo-tag eo-tag-accent"
                  style={{ marginLeft: "auto" }}
                >
                  {t("pages.runs.workbench.selected")}
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "var(--eo-ink-soft)" }}>
              {desc}
            </div>
            <div className="eo-mute" style={{ fontSize: 11 }}>
              {hint}
            </div>
          </button>
        );
      })}
    </div>
  );
}
