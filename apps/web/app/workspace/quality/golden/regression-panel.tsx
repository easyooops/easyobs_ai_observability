"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  cancelRegressionRun,
  fetchEvalProfiles,
  fetchEvalResults,
  fetchEvalRun,
  fetchRegressionRunInvokes,
  type GoldenRunInvoke,
  type GoldenSet,
  regressionRunStreamUrl,
  startRegressionRun,
} from "@/lib/api";
import { fmtPct, fmtPrice, fmtRel, fmtScore } from "@/lib/format";
import { useBilingual } from "@/lib/i18n/bilingual";
import { useSseEvents } from "@/lib/useSseEvents";

type Props = {
  set: GoldenSet;
  writable: boolean;
};

type RunProgress = {
  phase?: "queued" | "invoking" | "collecting" | "evaluating" | "done" | "failed" | "cancelled" | string;
  status?: string;
  progress?: number;
  invokedCount?: number;
  collectedCount?: number;
  evaluatedCount?: number;
  totalCount?: number;
  message?: string;
  costActualUsd?: number;
};

const STATUS_TONE: Record<string, string> = {
  pending: "ink",
  invoked: "warn",
  collected: "ok",
  timeout: "err",
  error: "err",
};

/** Golden Regression Run launcher + live status hub.
 *
 * - Pick a Profile → press "Regression Run".
 * - Phase / progress / running cost are streamed via SSE.
 * - The active runId is pinned in sessionStorage so reopening the page
 *   auto-reattaches.
 * - While active, "Cancel" sends a stop signal to the worker.
 */
export function GoldenRegressionPanel({ set, writable }: Props) {
  const b = useBilingual();
  const qc = useQueryClient();
  const sessionKey = `easyobs.regrun.${set.id}`;

  const [profileId, setProfileId] = useState<string>("");
  const [revisionNo, setRevisionNo] = useState<string>("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return window.sessionStorage.getItem(sessionKey);
  });

  const profiles = useQuery({
    queryKey: ["eval", "profiles"],
    queryFn: () => fetchEvalProfiles(true),
  });
  const filteredProfiles = (profiles.data ?? []).filter((p) => {
    if (!set.projectId) return p.enabled;
    return p.enabled && (!p.projectId || p.projectId === set.projectId);
  });

  const start = useMutation({
    mutationFn: () => {
      if (!profileId) {
        throw new Error(b("Pick a Profile first.", "Profile 을 먼저 선택하세요"));
      }
      if (!set.agentInvoke?.endpointUrl) {
        throw new Error(
          b(
            "Agent connection is required for Regression Run.",
            "Agent 연결 설정이 필요합니다",
          ),
        );
      }
      return startRegressionRun(set.id, {
        profileId,
        revisionNo: revisionNo ? Number.parseInt(revisionNo, 10) : undefined,
        notes,
      });
    },
    onSuccess: (run) => {
      setError(null);
      setActiveRunId(run.id);
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem(sessionKey, run.id);
      }
      qc.invalidateQueries({ queryKey: ["eval", "runs"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const cancel = useMutation({
    mutationFn: () => {
      if (!activeRunId) throw new Error("No active run");
      return cancelRegressionRun(set.id, activeRunId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "run", activeRunId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const clearActiveRun = () => {
    setActiveRunId(null);
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(sessionKey);
    }
  };

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">Regression Run</h3>
        <span className="eo-card-sub">
          {b(
            "agent API call → trace collection → evaluation",
            "에이전트 API 호출 → trace 수집 → 평가",
          )}
        </span>
      </div>
      <div className="eo-grid-3" style={{ gap: 8 }}>
        <label className="eo-field">
          <span>{b("Profile", "프로필")}</span>
          <select
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
            disabled={!writable}
          >
            <option value="">{b("— select —", "— 선택 —")}</option>
            {filteredProfiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="eo-field">
          <span>{b("Revision (optional)", "Revision (선택)")}</span>
          <input
            value={revisionNo}
            onChange={(e) => setRevisionNo(e.target.value)}
            placeholder="latest"
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>{b("Notes", "메모")}</span>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={!writable}
          />
        </label>
      </div>
      {!set.agentInvoke?.endpointUrl && (
        <div className="eo-empty" style={{ marginTop: 8 }}>
          {b(
            "⚠ Link an agent first — register an endpoint in the [Agent connection] panel on the left.",
            "⚠ 에이전트 연결을 먼저 설정하세요 — 좌측 [Agent 연결] 패널에서 endpoint 를 등록하면 활성됩니다.",
          )}
        </div>
      )}
      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={
            !writable ||
            !profileId ||
            !set.agentInvoke?.endpointUrl ||
            start.isPending
          }
          onClick={() => start.mutate()}
        >
          {start.isPending
            ? b("Starting…", "시작 중…")
            : b("Start Regression Run", "Regression Run 시작")}
        </button>
        {activeRunId && (
          <button
            type="button"
            className="eo-btn eo-btn-ghost"
            onClick={clearActiveRun}
          >
            {b("Detach", "추적 해제")}
          </button>
        )}
      </div>

      {activeRunId && (
        <RegressionRunStatus
          setId={set.id}
          runId={activeRunId}
          writable={writable}
          onCancel={() => cancel.mutate()}
          cancelling={cancel.isPending}
        />
      )}
    </div>
  );
}

function RegressionRunStatus({
  setId,
  runId,
  writable,
  onCancel,
  cancelling,
}: {
  setId: string;
  runId: string;
  writable: boolean;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const b = useBilingual();
  const url = regressionRunStreamUrl(setId, runId);
  const sse = useSseEvents<RunProgress>({ url });

  // Always also poll `runs/{id}` so the summary KPIs come from the
  // canonical row (SSE may have lossy intermediate states).
  const run = useQuery({
    queryKey: ["eval", "run", runId],
    queryFn: () => fetchEvalRun(runId),
    refetchInterval: (q) =>
      q.state.data && q.state.data.status === "running" ? 4000 : false,
  });
  const results = useQuery({
    queryKey: ["eval", "results", runId],
    queryFn: () => fetchEvalResults(runId, 200),
    enabled: !!run.data && run.data.status !== "running",
  });
  const invokes = useQuery({
    queryKey: ["eval", "regression-invokes", setId, runId],
    queryFn: () => fetchRegressionRunInvokes(setId, runId),
    refetchInterval: (q) => {
      const arr = q.state.data;
      if (!arr) return 5000;
      const stillRunning = arr.some(
        (i) => i.invokeStatus === "pending" || i.invokeStatus === "invoked",
      );
      return stillRunning ? 4000 : false;
    },
  });

  const phase = sse.latest?.phase ?? sse.latest?.status ?? run.data?.status ?? "queued";
  const total = sse.latest?.totalCount ?? invokes.data?.length ?? 0;
  const collected = countByStatus(invokes.data, "collected");
  const invoked = countByStatus(invokes.data, "invoked");
  const errored =
    countByStatus(invokes.data, "error") + countByStatus(invokes.data, "timeout");
  const evaluated = run.data?.completedCount ?? 0;
  const progress = sse.latest?.progress ?? 0;

  const errorResults = (results.data ?? []).filter((r) => r.verdict === "error");

  const isActive =
    phase === "queued" ||
    phase === "invoking" ||
    phase === "collecting" ||
    phase === "evaluating" ||
    phase === "running";

  return (
    <div
      className="eo-card"
      style={{ background: "var(--eo-bg-2)", marginTop: 12 }}
    >
      <div className="eo-card-h">
        <h3 className="eo-card-title">Run {runId.slice(0, 12)}</h3>
        <span className="eo-card-sub">
          phase{" "}
          <span className="eo-status" data-tone={isActive ? "warn" : "ok"}>
            {phase}
          </span>
        </span>
      </div>
      <div
        className="eo-mute"
        style={{ fontSize: 12, marginBottom: 6 }}
      >
        Progress {Math.max(0, Math.min(100, progress))}% · invoked {invoked} ·
        collected {collected} · evaluated {evaluated} · errored {errored} / total{" "}
        {total}
      </div>
      <ProgressBar value={progress} />
      {run.data && (
        <div
          className="eo-kpi-grid"
          style={{ marginTop: 8, gridTemplateColumns: "repeat(4, 1fr)" }}
        >
          <article className="eo-kpi">
            <span className="eo-kpi-label">Pass rate</span>
            <strong className="eo-kpi-value">
              {fmtPct((run.data.passRate ?? 0) * 100)}
            </strong>
            <span className="eo-kpi-meta">
              {run.data.completedCount}/{run.data.subjectCount}
            </span>
          </article>
          <article className="eo-kpi">
            <span className="eo-kpi-label">Avg score</span>
            <strong className="eo-kpi-value">
              {fmtScore(run.data.avgScore ?? 0)}
            </strong>
          </article>
          <article className="eo-kpi" data-tone="warn">
            <span className="eo-kpi-label">Cost</span>
            <strong className="eo-kpi-value">
              {fmtPrice(run.data.costActualUsd ?? 0)}
            </strong>
            <span className="eo-kpi-meta">
              est {fmtPrice(run.data.costEstimateUsd ?? 0)}
            </span>
          </article>
          <article className="eo-kpi" data-tone="err">
            <span className="eo-kpi-label">Judge errors</span>
            <strong className="eo-kpi-value">{errorResults.length}</strong>
            <span className="eo-kpi-meta">
              {b("excluded from stats", "통계 집계에서 제외됨")}
            </span>
          </article>
        </div>
      )}
      {errorResults.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>
            {b("Judge error detail", "Judge error 상세")} ({errorResults.length})
          </summary>
          <ul style={{ marginTop: 6, paddingLeft: 16, fontSize: 12 }}>
            {errorResults.slice(0, 20).map((r) => {
              const errorType =
                (r.judgeErrorDetail as { errorType?: unknown } | undefined)
                  ?.errorType;
              return (
                <li key={r.id}>
                  <code className="mono">{r.traceId.slice(0, 12)}</code> —{" "}
                  {typeof errorType === "string" ? errorType : "unknown"}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      <div style={{ marginTop: 10 }}>
        <strong style={{ fontSize: 12 }}>
          {b("Per-item invocations", "항목별 호출")}
        </strong>
        <div className="eo-table-wrap" style={{ maxHeight: 240, overflow: "auto", marginTop: 4 }}>
          <table className="eo-table">
            <thead>
              <tr>
                <th>{b("Item", "항목")}</th>
                <th>{b("Status", "상태")}</th>
                <th>{b("Trace", "Trace")}</th>
                <th>{b("Started", "시작")}</th>
                <th>{b("Finished", "완료")}</th>
              </tr>
            </thead>
            <tbody>
              {(invokes.data ?? []).slice(0, 100).map((row) => (
                <InvokeRow key={row.id} row={row} />
              ))}
              {(invokes.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={5}>
                    <div className="eo-empty">
                      {b("No invocations yet.", "아직 호출 기록이 없습니다.")}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {writable && isActive && (
        <div style={{ marginTop: 10 }}>
          <button
            type="button"
            className="eo-btn"
            onClick={onCancel}
            disabled={cancelling}
          >
            {cancelling ? b("Cancelling…", "취소 중…") : b("Cancel", "취소")}
          </button>
        </div>
      )}
      {sse.error && (
        <div className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
          ({sse.error}) {b("— polling fallback in use", "— polling fallback 사용 중")}
        </div>
      )}
    </div>
  );
}

function InvokeRow({ row }: { row: GoldenRunInvoke }) {
  const tone = STATUS_TONE[row.invokeStatus] ?? "warn";
  return (
    <tr>
      <td className="mono">{row.goldenItemId.slice(0, 8)}</td>
      <td>
        <span className="eo-status" data-tone={tone}>
          {row.invokeStatus}
        </span>
      </td>
      <td className="mono">{row.traceId ? row.traceId.slice(0, 12) : "—"}</td>
      <td>{row.invokeStarted ? fmtRel(row.invokeStarted) : "—"}</td>
      <td>{row.invokeFinished ? fmtRel(row.invokeFinished) : "—"}</td>
    </tr>
  );
}

function ProgressBar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div
      style={{
        height: 8,
        background: "var(--eo-bg-3)",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${v}%`,
          height: "100%",
          background: "var(--eo-accent, #4cafef)",
          transition: "width 220ms linear",
        }}
      />
    </div>
  );
}

function countByStatus(
  rows: GoldenRunInvoke[] | undefined,
  status: string,
): number {
  return (rows ?? []).filter((r) => r.invokeStatus === status).length;
}
