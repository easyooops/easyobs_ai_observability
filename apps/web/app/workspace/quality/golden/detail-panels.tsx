"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  type AgentInvokeSettings,
  fetchGoldenRevisions,
  fetchRevisionTrust,
  testAgentSettings,
  updateAgentSettings,
  type GoldenSet,
} from "@/lib/api";
import { fmtRel } from "@/lib/format";
import { useBilingual } from "@/lib/i18n/bilingual";

const DEFAULT_REQUEST_TEMPLATE = {
  message: "{{query_text}}",
  metadata: {
    golden_run_id: "{{run_id}}",
    golden_item_id: "{{item_id}}",
  },
};

type Props = {
  set: GoldenSet;
  writable: boolean;
};

/** Agent service connection card for a Golden Set.
 *
 * - A Regression Run can only start once the endpoint URL, request
 *   template, auth ref, timeout, and concurrency are saved.
 * - The "Test connection" button performs a single non-persisting probe
 *   (200 OK / 4xx / timeout — surfaced as a message).
 * - The request template variables ``{{query_text}}``, ``{{run_id}}`` and
 *   ``{{item_id}}`` are auto-substituted by the worker at run time.
 */
export function GoldenAgentSettingsCard({ set, writable }: Props) {
  const b = useBilingual();
  const qc = useQueryClient();
  const initial = set.agentInvoke;
  const [endpointUrl, setEndpointUrl] = useState(initial?.endpointUrl ?? "");
  const [requestTemplate, setRequestTemplate] = useState(
    JSON.stringify(initial?.requestTemplate ?? DEFAULT_REQUEST_TEMPLATE, null, 2),
  );
  const [authRef, setAuthRef] = useState(initial?.authRef ?? "");
  const [timeoutSec, setTimeoutSec] = useState(initial?.timeoutSec ?? 30);
  const [maxConcurrent, setMaxConcurrent] = useState(initial?.maxConcurrent ?? 5);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    setEndpointUrl(set.agentInvoke?.endpointUrl ?? "");
    setRequestTemplate(
      JSON.stringify(
        set.agentInvoke?.requestTemplate ?? DEFAULT_REQUEST_TEMPLATE,
        null,
        2,
      ),
    );
    setAuthRef(set.agentInvoke?.authRef ?? "");
    setTimeoutSec(set.agentInvoke?.timeoutSec ?? 30);
    setMaxConcurrent(set.agentInvoke?.maxConcurrent ?? 5);
    setError(null);
    setTestResult(null);
  }, [set.id, set.agentInvoke]);

  const buildSettings = (): AgentInvokeSettings | null => {
    if (!endpointUrl.trim()) {
      setError("Agent endpoint URL is required");
      return null;
    }
    let template: Record<string, unknown>;
    try {
      const parsed = JSON.parse(requestTemplate || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("must be JSON object");
      }
      template = parsed as Record<string, unknown>;
    } catch (e) {
      setError(e instanceof Error ? `request template: ${e.message}` : "request template: invalid JSON");
      return null;
    }
    setError(null);
    return {
      endpointUrl: endpointUrl.trim(),
      requestTemplate: template,
      authRef: authRef.trim(),
      timeoutSec: Math.max(1, Math.min(900, timeoutSec)),
      maxConcurrent: Math.max(1, Math.min(64, maxConcurrent)),
    };
  };

  const save = useMutation({
    mutationFn: () => {
      const cfg = buildSettings();
      if (!cfg) throw new Error("invalid settings");
      return updateAgentSettings(set.id, cfg);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const probe = useMutation({
    mutationFn: () => {
      const cfg = buildSettings();
      if (!cfg) throw new Error("invalid settings");
      return testAgentSettings(set.id, cfg);
    },
    onSuccess: (r) => {
      if (r.ok) {
        setTestResult(`✅ ${r.statusCode ?? 200} OK · ${r.latencyMs ?? 0}ms`);
      } else {
        setTestResult(`❌ ${r.statusCode ?? "?"} · ${r.error ?? "failed"}`);
      }
    },
    onError: (e: Error) => setTestResult(`❌ ${e.message}`),
  });

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{b("Agent connection", "Agent 연결")}</h3>
        <span className="eo-card-sub">
          {b(
            "Target endpoint for Golden Regression Runs",
            "Golden Regression Run 시 호출 대상",
          )}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12, marginBottom: 8 }}>
        {b(
          "When a Regression Run starts we POST each Golden item's L1 query to this endpoint. The agent emits OTLP traces, which we auto-match (by metadata or trace id) and score against the Set's ground-truth.",
          "Regression Run 시 Golden Item 의 L1 query 를 이 endpoint 에 전송하고, 에이전트가 OTLP 로 trace 를 보내면 자동 매칭하여 평가합니다.",
        )}
      </p>
      <label className="eo-field">
        <span>Endpoint URL</span>
        <input
          value={endpointUrl}
          onChange={(e) => setEndpointUrl(e.target.value)}
          placeholder="https://my-agent.internal/invoke"
          disabled={!writable}
        />
      </label>
      <label className="eo-field">
        <span>{b("Request template (JSON)", "Request 템플릿 (JSON)")}</span>
        <textarea
          rows={6}
          value={requestTemplate}
          onChange={(e) => setRequestTemplate(e.target.value)}
          disabled={!writable}
          style={{ fontFamily: "var(--eo-mono)", fontSize: 11 }}
        />
      </label>
      <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 8px" }}>
        {b("Variables", "변수")}: <code className="mono">{"{{query_text}}"}</code>{" "}
        <code className="mono">{"{{run_id}}"}</code>{" "}
        <code className="mono">{"{{item_id}}"}</code>
      </p>
      <div className="eo-grid-3" style={{ gap: 8 }}>
        <label className="eo-field">
          <span>{b("Auth ref (Vault)", "Auth ref (Vault)")}</span>
          <input
            value={authRef}
            onChange={(e) => setAuthRef(e.target.value)}
            placeholder="vault://kv/my-agent#token"
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>{b("Timeout (sec)", "Timeout (초)")}</span>
          <input
            type="number"
            value={timeoutSec}
            min={1}
            max={900}
            onChange={(e) =>
              setTimeoutSec(Number.parseInt(e.target.value || "30", 10))
            }
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>{b("Max concurrent", "최대 동시 호출")}</span>
          <input
            type="number"
            value={maxConcurrent}
            min={1}
            max={64}
            onChange={(e) =>
              setMaxConcurrent(Number.parseInt(e.target.value || "5", 10))
            }
            disabled={!writable}
          />
        </label>
      </div>
      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      {testResult && (
        <div className="eo-empty" style={{ marginTop: 6 }}>
          {testResult}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          className="eo-btn"
          disabled={!writable || probe.isPending}
          onClick={() => {
            setTestResult(null);
            probe.mutate();
          }}
        >
          {probe.isPending
            ? b("Testing…", "테스트 중…")
            : b("Test connection", "연결 테스트")}
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={!writable || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? b("Saving…", "저장 중…") : b("Save", "저장")}
        </button>
      </div>
    </div>
  );
}

/** Revision list + 4 Trust metrics. */
export function GoldenRevisionsPanel({ set }: { set: GoldenSet }) {
  const b = useBilingual();
  const revisions = useQuery({
    queryKey: ["eval", "golden-revisions", set.id],
    queryFn: () => fetchGoldenRevisions(set.id),
  });
  const list = revisions.data ?? [];
  const [activeRev, setActiveRev] = useState<number | null>(null);
  useEffect(() => {
    if (list.length === 0) {
      setActiveRev(null);
      return;
    }
    setActiveRev((cur) => cur ?? list[0]?.revisionNo ?? null);
  }, [list]);
  const trust = useQuery({
    queryKey: ["eval", "golden-trust", set.id, activeRev ?? -1],
    queryFn: () =>
      activeRev != null ? fetchRevisionTrust(set.id, activeRev) : Promise.resolve(null),
    enabled: activeRev != null,
  });
  return (
    <div className="eo-grid-2" style={{ alignItems: "flex-start" }}>
      <div className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{b("Revisions", "Revisions")}</h3>
          <span className="eo-card-sub">{list.length} total</span>
        </div>
        <div className="eo-table-wrap" style={{ maxHeight: 320, overflow: "auto" }}>
          <table className="eo-table">
            <thead>
              <tr>
                <th>{b("Rev", "Rev")}</th>
                <th>{b("Status", "Status")}</th>
                <th>{b("Items", "항목수")}</th>
                <th>{b("Created", "생성")}</th>
              </tr>
            </thead>
            <tbody>
              {list.length === 0 && !revisions.isLoading && (
                <tr>
                  <td colSpan={4}>
                    <div className="eo-empty">
                      {b("No revisions yet.", "아직 revision 이 없습니다.")}
                    </div>
                  </td>
                </tr>
              )}
              {list.map((r) => (
                <tr
                  key={r.id}
                  data-active={activeRev === r.revisionNo}
                  onClick={() => setActiveRev(r.revisionNo)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="mono">rev {r.revisionNo}</td>
                  <td>
                    <span
                      className="eo-tag"
                      data-tone={r.immutable ? "warn" : "ok"}
                    >
                      {r.immutable ? "immutable" : "mutable"}
                    </span>
                  </td>
                  <td className="mono">{r.itemCount}</td>
                  <td>{fmtRel(r.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {b("Trust (4 metrics)", "Trust (4종 메트릭)")}
          </h3>
          <span className="eo-card-sub">
            {activeRev != null ? `rev ${activeRev}` : "—"}
          </span>
        </div>
        {trust.isLoading && <div className="eo-empty">Loading…</div>}
        {!trust.isLoading && !trust.data && (
          <div className="eo-empty">
            {b(
              "No trust data yet — auto-aggregated after the next evaluation Run.",
              "아직 신뢰도 데이터가 없습니다 — 평가 Run 후 자동 집계됩니다.",
            )}
          </div>
        )}
        {trust.data && (
          <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
            <TrustMetric label="Cohen κ" value={trust.data.cohenKappa} threshold={0.6} />
            <TrustMetric label="Fleiss κ" value={trust.data.fleissKappa} threshold={0.4} />
            <TrustMetric
              label="Krippendorff α (nominal)"
              value={trust.data.krippendorffAlphaNominal}
              threshold={0.667}
            />
            <TrustMetric
              label="Krippendorff α (ordinal)"
              value={trust.data.krippendorffAlphaOrdinal}
              threshold={0.667}
            />
            <TrustMetric
              label="Multi-judge agreement"
              value={trust.data.multiJudgeAvgAgreement}
              threshold={0.7}
            />
            <TrustMetric
              label="Human ↔ Judge κ"
              value={trust.data.humanJudgeKappa}
              threshold={0.6}
            />
            <li style={{ padding: "6px 0", fontSize: 12 }} className="eo-mute">
              Raters: {trust.data.raterCount} · Judges:{" "}
              {trust.data.judgeModelCount} · Disputed: {trust.data.disputedItemCount}
            </li>
          </ul>
        )}
      </div>
    </div>
  );
}

function TrustMetric({
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
    <li
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "6px 0",
        borderBottom: "1px solid var(--eo-border)",
        fontSize: 13,
      }}
    >
      <span>{label}</span>
      <span className="eo-status" data-tone={tone}>
        {value == null ? "n/a" : value.toFixed(3)}
      </span>
    </li>
  );
}

/** Lightweight aggregate widget for Golden Set Detail header. */
export function GoldenSetSummary({ set }: { set: GoldenSet }) {
  const mode = set.mode ?? "regression";
  const tone = mode === "regression" ? "ok" : mode === "cohort" ? "warn" : "ink";
  return useMemo(
    () => (
      <div
        className="eo-card"
        style={{ background: "var(--eo-bg-2)", marginBottom: 8 }}
      >
        <div
          style={{
            display: "flex",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
            fontSize: 13,
          }}
        >
          <strong style={{ fontSize: 15 }}>{set.name}</strong>
          <span className="eo-tag" data-tone={tone}>
            {mode}
          </span>
          <span className="eo-tag eo-tag-accent">{set.layer}</span>
          <span className="eo-mute mono">{set.itemCount} items</span>
          <span className="eo-mute mono">created {fmtRel(set.createdAt)}</span>
          {set.description && (
            <span className="eo-mute" style={{ fontSize: 12 }}>
              {set.description}
            </span>
          )}
        </div>
      </div>
    ),
    [set, mode, tone],
  );
}
