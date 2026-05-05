"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchEvalRuns,
  type EvalRun,
} from "@/lib/api";
import { fmtPct, fmtPrice, fmtRel } from "@/lib/format";
import { useBilingual } from "@/lib/i18n/bilingual";

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
  const b = useBilingual();
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
          <h3 className="eo-card-title">{b("Active", "진행 중")}</h3>
          <span className="eo-card-sub">{active.length} active</span>
        </div>
        {active.length === 0 ? (
          <div className="eo-empty">
            {b("No Runs are currently active.", "현재 진행 중인 Run 이 없습니다.")}
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
          <h3 className="eo-card-title">{b("Recently completed", "최근 완료")}</h3>
          <span className="eo-card-sub">{completed.length} runs</span>
        </div>
        {completed.length === 0 ? (
          <div className="eo-empty">
            {b(
              "No completed Runs in the last 24 hours.",
              "최근 24시간 내 완료된 Run 이 없습니다.",
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
  const b = useBilingual();
  return (
    <div className="eo-table-wrap">
      <table className="eo-table">
        <thead>
          <tr>
            <th>{b("Run", "Run")}</th>
            <th>{b("Profile", "프로필")}</th>
            <th>{b("Phase", "단계")}</th>
            <th>{b("Subjects", "대상")}</th>
            <th>{b("Pass", "통과")}</th>
            <th>{b("Cost", "비용")}</th>
            <th>{b("Started", "시작")}</th>
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
  const b = useBilingual();
  return (
    <div className="eo-table-wrap" style={{ maxHeight: 480, overflow: "auto" }}>
      <table className="eo-table">
        <thead>
          <tr>
            <th>{b("Run", "Run")}</th>
            <th>{b("Profile", "프로필")}</th>
            <th>{b("Status", "상태")}</th>
            <th>{b("Subjects", "대상")}</th>
            <th>{b("Pass", "통과")}</th>
            <th>{b("Cost", "비용")}</th>
            <th>{b("Finished", "완료")}</th>
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
