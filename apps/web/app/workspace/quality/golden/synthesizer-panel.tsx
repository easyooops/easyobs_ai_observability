"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  cancelSynthJob,
  fetchJudgeModels,
  fetchSynthJobs,
  type GoldenSet,
  type SynthJob,
  type SynthJobMode,
  type SynthJobSourcePolicy,
  startSynthJob,
  synthJobStreamUrl,
} from "@/lib/api";
import { fmtPrice, fmtRel } from "@/lib/format";
import { useBilingual } from "@/lib/i18n/bilingual";
import { useSseEvents } from "@/lib/useSseEvents";

type Props = { set: GoldenSet; writable: boolean };

function policyOptions(b: (en: string, ko: string) => string) {
  const opts: { value: SynthJobSourcePolicy; label: string }[] = [
    {
      value: "random",
      label: b("random — uniform sample", "random — 단순 무작위 샘플"),
    },
    {
      value: "trace_freq",
      label: b(
        "trace_freq — frequently cited docs first",
        "trace_freq — 자주 인용된 doc 우선",
      ),
    },
    {
      value: "collection",
      label: b("collection — entire collection", "collection — 컬렉션 전체"),
    },
    {
      value: "tag",
      label: b("tag — within a specific tag", "tag — 특정 태그 안에서"),
    },
    {
      value: "explicit",
      label: b("explicit — explicit doc ids", "explicit — doc id 직접 지정"),
    },
  ];
  return opts;
}

/** Synthesizer Hub.
 *
 * - Pick rag_aware / trace_driven mode + source policy + target count.
 * - Progress (generated N / target M, running cost) is streamed via SSE.
 * - [Cancel] terminates the worker.
 * - Completed jobs land in the history below (last 7 days).
 */
export function GoldenSynthesizerPanel({ set, writable }: Props) {
  const b = useBilingual();
  const qc = useQueryClient();
  const [mode, setMode] = useState<SynthJobMode>("rag_aware");
  const [policy, setPolicy] = useState<SynthJobSourcePolicy>("random");
  const [targetCount, setTargetCount] = useState(20);
  const [judgeModelId, setJudgeModelId] = useState("");
  const [customPrompt, setCustomPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const judges = useQuery({
    queryKey: ["eval", "judges", "all"],
    queryFn: () => fetchJudgeModels(true),
  });
  const enabledJudges = (judges.data ?? []).filter((j) => j.enabled);

  const jobs = useQuery({
    queryKey: ["eval", "synth-jobs", set.id],
    queryFn: () => fetchSynthJobs(set.id),
    refetchInterval: (q) => {
      const arr = q.state.data;
      if (!arr) return 5000;
      return arr.some(
        (j) => j.status === "running" || j.status === "queued",
      )
        ? 4000
        : false;
    },
  });

  const start = useMutation({
    mutationFn: () =>
      startSynthJob(set.id, {
        mode,
        sourcePolicy: policy,
        sourceSpec: {},
        judgeModelId: judgeModelId || null,
        targetCount: Math.max(1, Math.min(500, targetCount)),
        customPrompt: customPrompt.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "synth-jobs", set.id] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const cancel = useMutation({
    mutationFn: (jobId: string) => cancelSynthJob(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "synth-jobs", set.id] });
    },
  });

  const activeJob = (jobs.data ?? []).find(
    (j) => j.status === "running" || j.status === "queued",
  );

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {b("LLM Auto-generate (Synthesizer)", "LLM 자동 생성 (Synthesizer)")}
        </h3>
        <span className="eo-card-sub">
          {jobs.data?.length ?? 0} jobs · {b("active", "진행 중")} {activeJob ? 1 : 0}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12, marginBottom: 8 }}>
        {mode === "rag_aware"
          ? b(
              "Samples RAG document sources and lets the LLM author Q+A pairs.",
              "RAG 문서 source 를 샘플링해 LLM 이 Q+A 를 작성합니다.",
            )
          : b(
              "Clusters production traces to extract patterns.",
              "운영 trace 군집을 분석해 패턴을 추출합니다.",
            )}
      </p>
      <div className="eo-grid-3" style={{ gap: 8 }}>
        <label className="eo-field">
          <span>Mode</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SynthJobMode)}
            disabled={!writable}
          >
            <option value="rag_aware">rag_aware</option>
            <option value="trace_driven">trace_driven</option>
          </select>
        </label>
        <label className="eo-field">
          <span>{b("Source policy", "Source 정책")}</span>
          <select
            value={policy}
            onChange={(e) => setPolicy(e.target.value as SynthJobSourcePolicy)}
            disabled={!writable}
          >
            {policyOptions(b).map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label className="eo-field">
          <span>{b("Target count", "목표 수")}</span>
          <input
            type="number"
            min={1}
            max={500}
            value={targetCount}
            onChange={(e) =>
              setTargetCount(Number.parseInt(e.target.value || "20", 10))
            }
            disabled={!writable}
          />
        </label>
      </div>
      <label className="eo-field">
        <span>
          {b(
            "Judge model (auto-pick if blank)",
            "Judge 모델 (선택 안 하면 자동)",
          )}
        </span>
        <select
          value={judgeModelId}
          onChange={(e) => setJudgeModelId(e.target.value)}
          disabled={!writable}
        >
          <option value="">{b("— auto —", "— 자동 —")}</option>
          {enabledJudges.map((j) => (
            <option key={j.id} value={j.id}>
              {j.name} ({j.provider}/{j.model})
            </option>
          ))}
        </select>
      </label>
      <label className="eo-field">
        <span>
          {b(
            "Domain prompt (optional — guides LLM for your domain)",
            "도메인 프롬프트 (선택 — 도메인에 맞게 LLM 을 안내)",
          )}
        </span>
        <textarea
          rows={3}
          value={customPrompt}
          onChange={(e) => setCustomPrompt(e.target.value)}
          disabled={!writable}
          placeholder={b(
            "e.g. Focus on insurance claim scenarios. Generate questions a customer service agent would ask. Use formal Korean.",
            "예: 보험 청구 시나리오에 집중하세요. 고객 상담원이 물어볼 만한 질문을 생성하세요. 존댓말을 사용하세요.",
          )}
          style={{ resize: "vertical", minHeight: 60 }}
        />
      </label>
      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={!writable || !!activeJob || start.isPending}
          onClick={() => start.mutate()}
        >
          {start.isPending
            ? b("Starting…", "시작 중…")
            : b("Start auto-generation", "자동 생성 시작")}
        </button>
      </div>

      {activeJob && (
        <SynthJobStatus
          job={activeJob}
          onCancel={() => cancel.mutate(activeJob.id)}
          cancelling={cancel.isPending}
          writable={writable}
        />
      )}

      <div style={{ marginTop: 10 }}>
        <strong style={{ fontSize: 12 }}>{b("Recent jobs", "최근 잡")}</strong>
        <div className="eo-table-wrap" style={{ maxHeight: 220, overflow: "auto", marginTop: 4 }}>
          <table className="eo-table">
            <thead>
              <tr>
                <th>{b("Job", "Job")}</th>
                <th>{b("Mode", "모드")}</th>
                <th>{b("Status", "상태")}</th>
                <th>{b("Generated", "생성")}</th>
                <th>{b("Cost", "비용")}</th>
                <th>{b("Started", "시작")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(jobs.data ?? []).map((j) => (
                <tr key={j.id}>
                  <td className="mono">{j.id.slice(0, 8)}</td>
                  <td className="mono">{j.mode}</td>
                  <td>
                    <span
                      className="eo-status"
                      data-tone={
                        j.status === "done"
                          ? "ok"
                          : j.status === "failed"
                            ? "err"
                            : "warn"
                      }
                    >
                      {j.status}
                    </span>
                  </td>
                  <td className="mono">
                    {j.generatedCount}/{j.targetCount}
                  </td>
                  <td className="mono">{fmtPrice(j.costActualUsd ?? 0)}</td>
                  <td>{j.startedAt ? fmtRel(j.startedAt) : "—"}</td>
                  <td>
                    {writable &&
                      (j.status === "running" || j.status === "queued") && (
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() => cancel.mutate(j.id)}
                        >
                          ×
                        </button>
                      )}
                  </td>
                </tr>
              ))}
              {(jobs.data ?? []).length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="eo-empty">
                      {b("No auto-generation jobs yet.", "자동 생성 잡이 없습니다.")}
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

type SseSnapshot = {
  jobId?: string;
  status?: string;
  progress?: number;
  generated?: number;
  total?: number;
  costUsd?: number;
};

function SynthJobStatus({
  job,
  onCancel,
  cancelling,
  writable,
}: {
  job: SynthJob;
  onCancel: () => void;
  cancelling: boolean;
  writable: boolean;
}) {
  const b = useBilingual();
  const url = synthJobStreamUrl(job.id);
  const sse = useSseEvents<SseSnapshot>({ url });
  const generated = sse.latest?.generated ?? job.generatedCount;
  const total = sse.latest?.total ?? job.targetCount;
  const progress =
    sse.latest?.progress ?? Math.round((generated / Math.max(1, total)) * 100);
  const cost = sse.latest?.costUsd ?? job.costActualUsd ?? 0;

  return (
    <div
      className="eo-card"
      style={{ background: "var(--eo-bg-2)", marginTop: 12 }}
    >
      <div className="eo-card-h">
        <h3 className="eo-card-title">Job {job.id.slice(0, 12)}</h3>
        <span className="eo-card-sub">
          {sse.latest?.status ?? job.status}
        </span>
      </div>
      <div
        className="eo-mute"
        style={{ fontSize: 12, marginBottom: 6, display: "flex", gap: 12 }}
      >
        <span>
          generated {generated}/{total}
        </span>
        <span>cost {fmtPrice(cost)}</span>
      </div>
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
            width: `${Math.max(0, Math.min(100, progress))}%`,
            height: "100%",
            background: "var(--eo-accent, #4cafef)",
            transition: "width 220ms linear",
          }}
        />
      </div>
      {writable && (
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
