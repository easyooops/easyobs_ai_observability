"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchEvalRuns,
  type EvalRun,
} from "@/lib/api";
import { fmtPct, fmtPrice, fmtRel } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

type Props = {
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
};

const ACTIVE_STATUSES = new Set([
  "queued",
  "running",
  "invoking",
  "collecting",
  "evaluating",
]);

/** Run Status Hub — embedded as a sub-segment under ``/quality/runs``.
 *
 * - Active Runs: refreshed every 4 s via polling.
 * - Completed Runs: last 30 entries within 24 h.
 * - Failed Runs: red dot + reason inline.
 *
 * SSE is reserved for Golden Regression Run via ``regression-panel.tsx``;
 * this hub aggregates *all* background work and polling is sufficient.
 */
export function RunStatusHub({ selectedRunId, onSelect }: Props) {
  const { t } = useI18n();
  const runs = useQuery({
    queryKey: ["eval", "runs"],
    queryFn: () => fetchEvalRuns(200),
    refetchInterval: 4_000,
  });
  const all = runs.data ?? [];
  const active = all.filter((r) => ACTIVE_STATUSES.has(r.status));
  const completed = all
    .filter((r) => !ACTIVE_STATUSES.has(r.status))
    .slice(0, 30);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("pages.runs.statusHub.active")}</h3>
          <span className="eo-card-sub">{active.length} active</span>
        </div>
        {active.length === 0 ? (
          <div className="eo-empty">
            {t("pages.runs.statusHub.noActiveRuns")}
          </div>
        ) : (
          <ActiveTable
            rows={active}
            onSelect={onSelect}
            selectedRunId={selectedRunId}
          />
        )}
      </div>

      <div className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("pages.runs.statusHub.recentlyCompleted")}</h3>
          <span className="eo-card-sub">{completed.length} runs</span>
        </div>
        {completed.length === 0 ? (
          <div className="eo-empty">
            {t(
              "pages.runs.statusHub.noCompletedRuns",
            )}
          </div>
        ) : (
          <CompletedTable
            rows={completed}
            onSelect={onSelect}
            selectedRunId={selectedRunId}
          />
        )}
      </div>
    </div>
  );
}

function ActiveTable({
  rows,
  onSelect,
  selectedRunId,
}: {
  rows: EvalRun[];
  onSelect: (id: string) => void;
  selectedRunId: string | null;
}) {
  const { t } = useI18n();
  return (
    <div className="eo-table-wrap">
      <table className="eo-table">
        <thead>
          <tr>
            <th>{t("pages.runs.statusHub.colRun")}</th>
            <th>{t("pages.runs.statusHub.colProfile")}</th>
            <th>{t("pages.runs.statusHub.colPhase")}</th>
            <th>{t("pages.runs.statusHub.colSubjects")}</th>
            <th>{t("pages.runs.statusHub.colPass")}</th>
            <th>{t("pages.runs.statusHub.colCost")}</th>
            <th>{t("pages.runs.statusHub.colStarted")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              data-active={selectedRunId === r.id}
              onClick={() => onSelect(r.id)}
              style={{ cursor: "pointer" }}
            >
              <td className="mono">{r.id.slice(0, 8)}</td>
              <td className="mono">{r.profileId?.slice(0, 8) ?? "—"}</td>
              <td>
                <span className="eo-status" data-tone="warn">
                  {r.status}
                </span>
              </td>
              <td className="mono">
                {r.completedCount}/{r.subjectCount}
              </td>
              <td className="mono">
                {r.subjectCount > 0
                  ? fmtPct((r.completedCount / r.subjectCount) * 100)
                  : "0%"}
              </td>
              <td className="mono">
                {fmtPrice(r.costActualUsd)} / {fmtPrice(r.costEstimateUsd)}
              </td>
              <td>{fmtRel(r.startedAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompletedTable({
  rows,
  onSelect,
  selectedRunId,
}: {
  rows: EvalRun[];
  onSelect: (id: string) => void;
  selectedRunId: string | null;
}) {
  const { t } = useI18n();
  return (
    <div className="eo-table-wrap" style={{ maxHeight: 480, overflow: "auto" }}>
      <table className="eo-table">
        <thead>
          <tr>
            <th>{t("pages.runs.statusHub.colRun")}</th>
            <th>{t("pages.runs.statusHub.colProfile")}</th>
            <th>{t("pages.runs.statusHub.colStatus")}</th>
            <th>{t("pages.runs.statusHub.colSubjects")}</th>
            <th>{t("pages.runs.statusHub.colPass")}</th>
            <th>{t("pages.runs.statusHub.colCost")}</th>
            <th>{t("pages.runs.statusHub.colFinished")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const tone =
              r.status === "succeeded" || r.status === "done"
                ? "ok"
                : r.status === "failed"
                  ? "err"
                  : "warn";
            return (
              <tr
                key={r.id}
                data-active={selectedRunId === r.id}
                onClick={() => onSelect(r.id)}
                style={{ cursor: "pointer" }}
              >
                <td className="mono">{r.id.slice(0, 8)}</td>
                <td className="mono">{r.profileId?.slice(0, 8) ?? "—"}</td>
                <td>
                  <span className="eo-status" data-tone={tone}>
                    {r.status}
                  </span>
                </td>
                <td className="mono">{r.subjectCount}</td>
                <td className="mono">{fmtPct(r.passRate * 100)}</td>
                <td className="mono">{fmtPrice(r.costActualUsd)}</td>
                <td>
                  {r.finishedAt ? fmtRel(r.finishedAt) : fmtRel(r.startedAt)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
