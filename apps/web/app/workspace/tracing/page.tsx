"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { fetchSpans, fetchTraceDetail, fetchTraces, type SpanListRow, type TraceListItem } from "@/lib/api";
import { TraceLlmSummaryCard } from "@/components/llm-view";
import { rangeKey, resolveRange, useWorkspace } from "@/lib/context";
import { fmtInt, fmtMs, fmtPrice, fmtRel, fmtTime, fmtTokens, truncate } from "@/lib/format";
import { buildCsv, triggerCsvDownload, fetchTraceEnrichments, TRACE_ENRICHMENT_HEADERS, traceEnrichmentToRow } from "@/lib/csv-export";
import { useI18n } from "@/lib/i18n/context";
import { buildTree, SpanFlowGraph } from "./detail/inner";

type TabKey = "traces" | "spans";
// Column toggles. The LLM-flavoured ones (tokens / cost / model / session)
// are off by default to keep the table compact for non-LLM workloads, but
// arriving via `?session=…` from the Sessions page flips them on so the
// drill-down lands on a screen that actually answers "what did this turn
// cost / which model handled it".
type Cols = {
  timestamp: boolean;
  name: boolean;
  service: boolean;
  duration: boolean;
  spans: boolean;
  status: boolean;
  tokens: boolean;
  cost: boolean;
  model: boolean;
  session: boolean;
  id: boolean;
};

const DEFAULT_COLS: Cols = {
  timestamp: true,
  name: true,
  service: true,
  duration: true,
  spans: true,
  status: true,
  tokens: false,
  cost: false,
  model: false,
  session: false,
  id: false,
};

const LLM_COLS: Cols = {
  ...DEFAULT_COLS,
  tokens: true,
  cost: true,
  model: true,
  session: true,
};

const STATUSES = ["OK", "ERROR", "UNSET"] as const;

function durationMsFromItem(t: TraceListItem): number {
  if (!t.endedAt) return 0;
  return Math.max(0, new Date(t.endedAt).getTime() - new Date(t.startedAt).getTime());
}

export default function TracingPage() {
  const { t } = useI18n();
  const ws = useWorkspace();
  const { search, live } = ws;
  const range = resolveRange(ws);
  const rk = rangeKey(ws);
  const sp = useSearchParams();
  const initialSession = sp?.get("session") ?? "";
  const [tab, setTab] = useState<TabKey>("traces");
  const [statusFilter, setStatusFilter] = useState<Set<string>>(new Set());
  const [svcFilter, setSvcFilter] = useState<Set<string>>(new Set());
  // Exact-match filter for `o.sess`. Driven by either the left-rail input
  // or the `?session=` query param (used when arriving from the Sessions
  // page) — the latter also pre-flips the LLM column set.
  const [sessionFilter, setSessionFilter] = useState(initialSession);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerTab, setDrawerTab] = useState<"summary" | "llm" | "attributes" | "events">("summary");
  const [showFlowModal, setShowFlowModal] = useState(false);
  const [cols, setCols] = useState<Cols>(initialSession ? LLM_COLS : DEFAULT_COLS);
  const [pageSize, setPageSize] = useState(50);
  const [page, setPage] = useState(1);

  // Keep state in sync if the user navigates within the SPA (e.g. clicks
  // another "Open in Tracing" link from a different session row).
  useEffect(() => {
    const next = sp?.get("session") ?? "";
    if (next !== sessionFilter) {
      setSessionFilter(next);
      if (next) setCols((c) => ({ ...c, ...LLM_COLS }));
    }
    // We intentionally don't depend on `sessionFilter` — typing into the
    // input shouldn't trigger this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);

  // The trace list is cheap on the server even with `with_llm`, but we
  // only ask for enrichment when at least one LLM column is shown. That
  // keeps the small/non-LLM case as fast as before.
  const wantsLlm = cols.tokens || cols.cost || cols.model || cols.session;
  const effectiveSession = sessionFilter.trim() || undefined;

  const tracesQ = useQuery({
    queryKey: ["traces", rk, effectiveSession ?? "", wantsLlm ? "llm" : "lean"],
    queryFn: () =>
      fetchTraces(range, { sessionId: effectiveSession, withLlm: wantsLlm }),
    refetchInterval: live ? 5_000 : false,
  });
  const spansQ = useQuery({
    queryKey: ["spans", rk],
    queryFn: () => fetchSpans(range),
    refetchInterval: live ? 5_000 : false,
    enabled: tab === "spans",
  });

  const services = useMemo(() => {
    const s = new Map<string, number>();
    for (const t of tracesQ.data ?? []) {
      const k = t.serviceName || "unknown";
      s.set(k, (s.get(k) ?? 0) + 1);
    }
    return Array.from(s.entries()).sort((a, b) => b[1] - a[1]);
  }, [tracesQ.data]);

  const statusCounts = useMemo(() => {
    const out: Record<string, number> = { OK: 0, ERROR: 0, UNSET: 0 };
    for (const t of tracesQ.data ?? []) {
      out[t.status] = (out[t.status] ?? 0) + 1;
    }
    return out;
  }, [tracesQ.data]);

  const traceRows = useMemo(() => {
    const src = tracesQ.data ?? [];
    const q = search.trim().toLowerCase();
    return src.filter((t) => {
      if (statusFilter.size > 0 && !statusFilter.has(t.status)) return false;
      if (svcFilter.size > 0 && !svcFilter.has(t.serviceName || "unknown")) return false;
      if (q) {
        const hay = `${t.rootName} ${t.traceId} ${t.serviceName}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [tracesQ.data, statusFilter, svcFilter, search]);

  const spanRows = useMemo(() => {
    const src = spansQ.data ?? [];
    const q = search.trim().toLowerCase();
    return src.filter((s) => {
      if (statusFilter.size > 0 && !statusFilter.has(s.status)) return false;
      if (svcFilter.size > 0 && !svcFilter.has(s.serviceName || "unknown")) return false;
      if (q) {
        const hay = `${s.name} ${s.traceId} ${s.spanId} ${s.serviceName}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [spansQ.data, statusFilter, svcFilter, search]);

  const totalPages = Math.max(
    1,
    Math.ceil((tab === "traces" ? traceRows.length : spanRows.length) / pageSize),
  );
  const safePage = Math.min(page, totalPages);
  const paginated =
    tab === "traces"
      ? traceRows.slice((safePage - 1) * pageSize, safePage * pageSize)
      : spanRows.slice((safePage - 1) * pageSize, safePage * pageSize);

  const toggle = (setter: React.Dispatch<React.SetStateAction<Set<string>>>, v: string) =>
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(v)) next.delete(v);
      else next.add(v);
      return next;
    });

  const detailQ = useQuery({
    queryKey: ["trace-drawer", selected],
    queryFn: () => fetchTraceDetail(selected || ""),
    enabled: Boolean(selected),
  });

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.tracing.title")}</h1>
          <p className="eo-page-lede">{t("pages.tracing.lede")}</p>
        </div>
        <div className="eo-page-meta">
          {effectiveSession && (
            <span
              className="eo-chip eo-chip-accent mono"
              title={effectiveSession}
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              session: {truncate(effectiveSession, 18)}
              <button
                type="button"
                onClick={() => setSessionFilter("")}
                aria-label="clear session filter"
                style={{
                  border: 0,
                  background: "transparent",
                  cursor: "pointer",
                  fontWeight: 700,
                }}
              >
                ×
              </button>
            </span>
          )}
          <span className="eo-tag">{fmtInt(traceRows.length)} matched</span>
          <span className="eo-tag">
            traces: {fmtInt(tracesQ.data?.length ?? 0)}
          </span>
          <Link href="/workspace/interactions/" className="eo-btn">
            Sessions view
          </Link>
          <button
            type="button"
            className="eo-btn"
            disabled={traceRows.length === 0}
            onClick={async () => {
              if (tab === "traces") {
                const traceIds = traceRows.map((t) => t.traceId);
                const enrichments = await fetchTraceEnrichments(traceIds);
                const headers = [
                  "trace_id", "started_at", "ended_at", "operation", "service", "status",
                  "span_count", "duration_ms", "session", "user", "tokens_in", "tokens_out",
                  "tokens_total", "cost", "model", "models",
                  ...TRACE_ENRICHMENT_HEADERS.filter((h) => !["trace_operation","trace_service","trace_status","trace_started_at","trace_ended_at","trace_duration_ms","trace_span_count","trace_session","trace_user","trace_tokens_in","trace_tokens_out","trace_tokens_total","trace_price","trace_models"].includes(h)),
                ];
                const rows = traceRows.map((t) => {
                  const e = enrichments.get(t.traceId);
                  return [
                    t.traceId, t.startedAt, t.endedAt ?? "", t.rootName, t.serviceName, t.status,
                    t.spanCount, durationMsFromItem(t), t.session ?? "", t.user ?? "",
                    t.tokensIn ?? "", t.tokensOut ?? "", t.tokensTotal ?? "", t.price ?? "",
                    t.model ?? "", t.models?.join("; ") ?? "",
                    e?.query ?? "", e?.response ?? "", e?.request ?? "",
                    e?.vendors ?? "", e?.llmCalls ?? "", e?.retrieveCalls ?? "",
                    e?.toolCalls ?? "", e?.docsCount ?? "", e?.verdicts ?? "",
                    e?.spanNames ?? "", e?.errorSpans ?? "", e?.attributes ?? "", e?.events ?? "",
                  ];
                });
                triggerCsvDownload("traces.csv", buildCsv(headers, rows));
              } else {
                const headers = ["trace_id", "span_id", "parent_span_id", "name", "kind", "step", "service", "status", "duration_ms", "start_time", "end_time", "model", "tokens_total", "cost"];
                const rows = spanRows.map((s) => [
                  s.traceId, s.spanId, s.parentSpanId ?? "", s.name, s.kind ?? "", s.step ?? "",
                  s.serviceName, s.status, s.durationMs, s.startTimeUnixNano ?? "", s.endTimeUnixNano ?? "",
                  s.model ?? "", s.tokensTotal ?? "", s.price ?? "",
                ]);
                triggerCsvDownload("spans.csv", buildCsv(headers, rows));
              }
            }}
          >
            CSV
          </button>
        </div>
      </div>

      <div className="eo-trace-layout" data-drawer={selected ? "true" : "false"}>
        <aside className="eo-filters" aria-label="filters">
          <section className="eo-filter-section">
            <div className="eo-filter-h">
              <span>Status</span>
              <button type="button" onClick={() => setStatusFilter(new Set())}>
                reset
              </button>
            </div>
            {STATUSES.map((s) => (
              <label key={s} className="eo-filter-opt">
                <input
                  type="checkbox"
                  checked={statusFilter.has(s)}
                  onChange={() => toggle(setStatusFilter, s)}
                />
                <span>{s}</span>
                <span className="eo-filter-count">{fmtInt(statusCounts[s] ?? 0)}</span>
              </label>
            ))}
          </section>

          <div className="eo-divider" />

          <section className="eo-filter-section">
            <div className="eo-filter-h">
              <span>Session</span>
              <button
                type="button"
                onClick={() => setSessionFilter("")}
                disabled={!sessionFilter}
              >
                reset
              </button>
            </div>
            <input
              type="search"
              value={sessionFilter}
              onChange={(e) => {
                setSessionFilter(e.target.value);
                setPage(1);
              }}
              placeholder="o.sess (exact)…"
              spellCheck={false}
              className="eo-input"
              style={{ margin: 6, width: "calc(100% - 12px)" }}
            />
            {sessionFilter && (
              <div className="eo-muted" style={{ fontSize: 11, padding: "0 6px 6px" }}>
                Showing only traces tagged with this session.
              </div>
            )}
          </section>

          <div className="eo-divider" />

          <section className="eo-filter-section">
            <div className="eo-filter-h">
              <span>Service</span>
              <button type="button" onClick={() => setSvcFilter(new Set())}>
                reset
              </button>
            </div>
            {services.length === 0 && (
              <div className="eo-muted" style={{ fontSize: 11, padding: "4px 6px" }}>
                No services yet
              </div>
            )}
            {services.slice(0, 10).map(([name, count]) => (
              <label key={name} className="eo-filter-opt">
                <input
                  type="checkbox"
                  checked={svcFilter.has(name)}
                  onChange={() => toggle(setSvcFilter, name)}
                />
                <span title={name}>{truncate(name, 16)}</span>
                <span className="eo-filter-count">{fmtInt(count)}</span>
              </label>
            ))}
          </section>

          <div className="eo-divider" />

          <section className="eo-filter-section">
            <div className="eo-filter-h">
              <span>Columns</span>
              <button
                type="button"
                onClick={() => setCols(DEFAULT_COLS)}
                title="Reset columns"
              >
                reset
              </button>
            </div>
            {(Object.keys(cols) as Array<keyof Cols>).map((k) => (
              <label key={k} className="eo-filter-opt">
                <input
                  type="checkbox"
                  checked={cols[k]}
                  onChange={() => setCols((c) => ({ ...c, [k]: !c[k] }))}
                />
                <span style={{ textTransform: "capitalize" }}>{k}</span>
              </label>
            ))}
          </section>
        </aside>

        <div className="eo-card" style={{ padding: 12 }}>
          <div className="eo-list-head">
            <div className="eo-seg" role="tablist" aria-label="tracing tabs">
              <button
                type="button"
                onClick={() => setTab("traces")}
                data-active={tab === "traces"}
              >
                Traces
                <span className="eo-filter-count" style={{ marginLeft: 6 }}>
                  {fmtInt(tracesQ.data?.length ?? 0)}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setTab("spans")}
                data-active={tab === "spans"}
              >
                Spans
                <span className="eo-filter-count" style={{ marginLeft: 6 }}>
                  {fmtInt(spansQ.data?.length ?? 0)}
                </span>
              </button>
            </div>

            <Link href="/workspace/interactions/" className="eo-chip">
              Sessions
            </Link>
            <Link href="/workspace/" className="eo-chip">
              Overview
            </Link>

            <div className="eo-pager">
              <span>
                rows:
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(parseInt(e.target.value, 10));
                    setPage(1);
                  }}
                  style={{
                    border: 0,
                    background: "transparent",
                    fontFamily: "var(--eo-mono)",
                    marginLeft: 4,
                  }}
                >
                  {[25, 50, 100, 200].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </span>
              <button type="button" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={safePage <= 1}>
                ‹
              </button>
              <span className="mono">
                {safePage}/{totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage >= totalPages}
              >
                ›
              </button>
            </div>
          </div>

          <div className="eo-table-wrap">
            {tab === "traces" ? (
              <TracesTable
                rows={paginated as TraceListItem[]}
                cols={cols}
                selected={selected}
                onSelect={(id) => {
                  setSelected(id);
                  setDrawerTab("summary");
                }}
              />
            ) : (
              <SpansTable rows={paginated as SpanListRow[]} />
            )}
          </div>

          {tab === "traces" && tracesQ.isLoading && <div className="eo-empty">Loading traces…</div>}
          {tab === "traces" && !tracesQ.isLoading && paginated.length === 0 && (
            <div className="eo-empty">No traces match the current filters.</div>
          )}
          {tab === "spans" && spansQ.isLoading && <div className="eo-empty">Loading spans…</div>}
          {tab === "spans" && !spansQ.isLoading && paginated.length === 0 && (
            <div className="eo-empty">No spans match the current filters.</div>
          )}
        </div>

        {selected && (
          <section className="eo-drawer" aria-label="trace drawer">
            <div className="eo-drawer-h">
              <strong>Trace inspector</strong>
              <div
                className="eo-inline-actions"
                style={{
                  marginLeft: "auto",
                  display: "flex",
                  gap: 4,
                  flexWrap: "wrap",
                }}
              >
                <Link
                  href={`/workspace/quality/runs/?prefill-trace=${encodeURIComponent(selected)}`}
                  className="eo-btn eo-btn-primary"
                  title="Start an evaluation Run scoped to this single trace"
                >
                  Evaluate ▶
                </Link>
                <Link
                  href={`/workspace/quality/golden/?tab=labels&traceId=${encodeURIComponent(selected)}`}
                  className="eo-btn"
                  title="Open the Human Label form pre-filled with this trace id"
                >
                  + Label
                </Link>
                <Link
                  href={`/workspace/tracing/detail/?id=${encodeURIComponent(selected)}`}
                  className="eo-btn eo-btn-fullview"
                >
                  Full view →
                </Link>
                <button
                  type="button"
                  className="eo-drawer-close"
                  onClick={() => setSelected(null)}
                  aria-label="close"
                >
                  ×
                </button>
              </div>
            </div>
            <div className="eo-drawer-tabs">
              <button
                type="button"
                data-active={drawerTab === "summary"}
                onClick={() => setDrawerTab("summary")}
              >
                Summary
              </button>
              <button
                type="button"
                data-active={drawerTab === "llm"}
                onClick={() => setDrawerTab("llm")}
              >
                LLM
              </button>
              <button
                type="button"
                data-active={drawerTab === "attributes"}
                onClick={() => setDrawerTab("attributes")}
              >
                Attributes
              </button>
              <button
                type="button"
                data-active={drawerTab === "events"}
                onClick={() => setDrawerTab("events")}
              >
                Events
              </button>
            </div>
            <div className="eo-drawer-body">
              {detailQ.isLoading && <div className="eo-empty">Loading…</div>}
              {detailQ.isError && <div className="eo-empty">Failed to load detail.</div>}
              {detailQ.data && drawerTab === "summary" && (
                <DrawerSummary detail={detailQ.data} onFlowClick={() => setShowFlowModal(true)} />
              )}
              {detailQ.data && drawerTab === "llm" && (
                <TraceLlmSummaryCard summary={detailQ.data.llmSummary} />
              )}
              {detailQ.data && drawerTab === "attributes" && (
                <DrawerAttributes detail={detailQ.data} />
              )}
              {detailQ.data && drawerTab === "events" && (
                <DrawerEvents detail={detailQ.data} />
              )}
            </div>
          </section>
        )}

        {showFlowModal && detailQ.data && (
          <div
            className="eo-modal-backdrop"
            onClick={() => setShowFlowModal(false)}
          >
            <div
              className="eo-modal"
              style={{ maxWidth: "92vw", maxHeight: "88vh" }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="eo-modal-h">
                <div>
                  <h2 className="eo-modal-title">Span Flow Graph</h2>
                  <p className="eo-modal-sub">
                    {detailQ.data.rootName || "Trace"} — {detailQ.data.spans?.length ?? 0} spans
                  </p>
                </div>
                <button
                  type="button"
                  className="eo-modal-x"
                  onClick={() => setShowFlowModal(false)}
                >
                  ×
                </button>
              </div>
              <div className="eo-modal-body" style={{ overflow: "auto", padding: 20 }}>
                <SpanFlowGraph
                  roots={buildTree(detailQ.data.spans ?? []).roots}
                  activeId={null}
                  onSelect={() => {}}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function TracesTable({
  rows,
  cols,
  selected,
  onSelect,
}: {
  rows: TraceListItem[];
  cols: Cols;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <table className="eo-table">
      <thead>
        <tr>
          {cols.timestamp && <th>Started</th>}
          {cols.name && <th>Operation</th>}
          {cols.service && <th>Service</th>}
          {cols.duration && <th>Duration</th>}
          {cols.spans && <th>Spans</th>}
          {cols.status && <th>Status</th>}
          {cols.tokens && <th>Tokens</th>}
          {cols.cost && <th>Cost</th>}
          {cols.model && <th>Model</th>}
          {cols.session && <th>Session</th>}
          {cols.id && <th>Trace ID</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => {
          const ms = durationMsFromItem(t);
          return (
            <tr
              key={t.traceId}
              data-active={selected === t.traceId}
              onClick={() => onSelect(t.traceId)}
            >
              {cols.timestamp && (
                <td>
                  <div className="eo-td-name">{fmtTime(t.startedAt)}</div>
                  <div className="eo-td-sub">{fmtRel(t.startedAt)}</div>
                </td>
              )}
              {cols.name && (
                <td>
                  <div className="eo-td-name">{t.rootName || "(unnamed)"}</div>
                </td>
              )}
              {cols.service && (
                <td>
                  <span className="eo-tag">{t.serviceName || "unknown"}</span>
                </td>
              )}
              {cols.duration && (
                <td>
                  <span className="eo-bar-mini">
                    <i style={{ width: `${Math.min(100, (ms / 2000) * 100)}%` }} />
                  </span>
                  <span className="mono">{fmtMs(ms)}</span>
                </td>
              )}
              {cols.spans && <td className="mono">{t.spanCount}</td>}
              {cols.status && (
                <td>
                  <span
                    className="eo-status"
                    data-tone={t.status === "ERROR" ? "err" : t.status === "OK" ? "ok" : ""}
                  >
                    {t.status}
                  </span>
                </td>
              )}
              {cols.tokens && (
                <td className="mono">
                  {t.tokensTotal && t.tokensTotal > 0 ? (
                    fmtTokens(t.tokensTotal)
                  ) : (
                    <span className="eo-td-sub">—</span>
                  )}
                </td>
              )}
              {cols.cost && (
                <td className="mono">
                  {t.price && t.price > 0 ? (
                    fmtPrice(t.price)
                  ) : (
                    <span className="eo-td-sub">—</span>
                  )}
                </td>
              )}
              {cols.model && (
                <td>
                  {t.model ? (
                    <span className="eo-chip eo-chip-accent">{t.model}</span>
                  ) : (
                    <span className="eo-td-sub">—</span>
                  )}
                </td>
              )}
              {cols.session && (
                <td>
                  {t.session ? (
                    <span className="eo-chip mono" title={t.session}>
                      {truncate(t.session, 14)}
                    </span>
                  ) : (
                    <span className="eo-td-sub">—</span>
                  )}
                </td>
              )}
              {cols.id && <td className="mono eo-td-sub">{truncate(t.traceId, 14)}</td>}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function SpansTable({ rows }: { rows: SpanListRow[] }) {
  return (
    <table className="eo-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Kind</th>
          <th>Model</th>
          <th>Service</th>
          <th>Duration</th>
          <th>Tokens</th>
          <th>Cost</th>
          <th>Status</th>
          <th>Trace</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={`${r.traceId}-${r.spanId}`}
            onClick={() => {
              window.location.href = `/workspace/tracing/detail/?id=${encodeURIComponent(r.traceId)}`;
            }}
          >
            <td className="eo-td-name">{r.name}</td>
            <td>
              {r.kind ? (
                <span className="eo-badge" data-kind={r.kind}>
                  {r.kind}
                </span>
              ) : (
                <span className="eo-td-sub">—</span>
              )}
            </td>
            <td>
              {r.model ? (
                <span className="eo-chip eo-chip-accent">{r.model}</span>
              ) : (
                <span className="eo-td-sub">—</span>
              )}
            </td>
            <td>
              <span className="eo-tag">{r.serviceName || "unknown"}</span>
            </td>
            <td>
              <span className="eo-bar-mini">
                <i style={{ width: `${Math.min(100, (r.durationMs / 2000) * 100)}%` }} />
              </span>
              <span className="mono">{fmtMs(r.durationMs)}</span>
            </td>
            <td className="mono">{fmtTokens(r.tokensTotal)}</td>
            <td className="mono">{fmtPrice(r.price)}</td>
            <td>
              <span
                className="eo-status"
                data-tone={r.status === "ERROR" ? "err" : r.status === "OK" ? "ok" : ""}
              >
                {r.status}
              </span>
            </td>
            <td className="mono eo-td-sub">{truncate(r.traceId, 14)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DrawerSummary({
  detail,
  onFlowClick,
}: {
  detail: Awaited<ReturnType<typeof fetchTraceDetail>>;
  onFlowClick?: () => void;
}) {
  const duration =
    detail.endedAt && detail.startedAt
      ? new Date(detail.endedAt).getTime() - new Date(detail.startedAt).getTime()
      : 0;
  const spans = detail.spans ?? [];
  const errCount = spans.filter((s) => s.status === "ERROR").length;
  const llm = detail.llmSummary;
  return (
    <>
      <dl className="eo-kv-list">
        <div className="eo-kv-row">
          <dt>Operation</dt>
          <dd>{detail.rootName || "(unnamed)"}</dd>
        </div>
        <div className="eo-kv-row">
          <dt>Service</dt>
          <dd>{detail.serviceName || "unknown"}</dd>
        </div>
        <div className="eo-kv-row">
          <dt>Status</dt>
          <dd>
            <span
              className="eo-status"
              data-tone={detail.status === "ERROR" ? "err" : detail.status === "OK" ? "ok" : ""}
            >
              {detail.status}
            </span>
          </dd>
        </div>
        <div className="eo-kv-row">
          <dt>Duration</dt>
          <dd>{fmtMs(duration)}</dd>
        </div>
        <div className="eo-kv-row">
          <dt>Spans</dt>
          <dd>
            {spans.length} ({errCount} error)
          </dd>
        </div>
        {llm?.session && (
          <div className="eo-kv-row">
            <dt>Session</dt>
            <dd className="mono">{llm.session}</dd>
          </div>
        )}
        {llm && llm.tokensTotal > 0 && (
          <div className="eo-kv-row">
            <dt>Tokens</dt>
            <dd className="mono">
              {fmtTokens(llm.tokensIn)} in · {fmtTokens(llm.tokensOut)} out
            </dd>
          </div>
        )}
        {llm && llm.price > 0 && (
          <div className="eo-kv-row">
            <dt>Cost</dt>
            <dd className="mono">{fmtPrice(llm.price)}</dd>
          </div>
        )}
        {llm && llm.models.length > 0 && (
          <div className="eo-kv-row">
            <dt>Models</dt>
            <dd>
              {llm.models.map((m) => (
                <span key={m} className="eo-chip eo-chip-accent" style={{ marginRight: 4 }}>
                  {m}
                </span>
              ))}
            </dd>
          </div>
        )}
        <div className="eo-kv-row">
          <dt>Started</dt>
          <dd>{fmtTime(detail.startedAt)}</dd>
        </div>
        <div className="eo-kv-row">
          <dt>Trace ID</dt>
          <dd>{detail.traceId}</dd>
        </div>
      </dl>

      {onFlowClick && (
        <div style={{ margin: "10px 0" }}>
          <button
            type="button"
            className="eo-btn eo-btn-flow"
            style={{ width: "100%" }}
            onClick={onFlowClick}
          >
            ⬡ View Span Flow
          </button>
        </div>
      )}

      <div>
        <div className="eo-card-sub" style={{ marginBottom: 6 }}>First spans</div>
        <pre className="eo-code">
{spans
  .slice(0, 6)
  .map(
    (s) =>
      `${s.status === "ERROR" ? "✗" : "·"} ${s.name}  ${fmtMs(
        s.startTimeUnixNano && s.endTimeUnixNano
          ? (s.endTimeUnixNano - s.startTimeUnixNano) / 1e6
          : 0,
      )}`,
  )
  .join("\n")}
        </pre>
      </div>
    </>
  );
}

function DrawerAttributes({
  detail,
}: {
  detail: Awaited<ReturnType<typeof fetchTraceDetail>>;
}) {
  const first = detail.spans?.[0];
  if (!first) return <div className="eo-empty">No spans in this trace</div>;
  const attrs = (first.attributes ?? []) as Array<{ key: string; value?: Record<string, unknown> }>;
  if (attrs.length === 0) return <div className="eo-empty">No attributes on root span</div>;
  return (
    <dl className="eo-kv-list">
      {attrs.map((a) => {
        const v = a.value ?? {};
        const shown = Object.values(v)[0];
        return (
          <div className="eo-kv-row" key={a.key}>
            <dt>{a.key}</dt>
            <dd>{String(shown ?? "")}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function DrawerEvents({
  detail,
}: {
  detail: Awaited<ReturnType<typeof fetchTraceDetail>>;
}) {
  const events = (detail.spans ?? []).flatMap((s) =>
    (s.events ?? []).map((e) => ({ spanName: s.name, ...e })),
  );
  if (events.length === 0) return <div className="eo-empty">No events recorded</div>;
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
      {events.map((e, i) => (
        <li key={i} className="eo-card" style={{ padding: 10, boxShadow: "none" }}>
          <div style={{ fontWeight: 600 }}>{e.name}</div>
          <div className="eo-td-sub">on {e.spanName}</div>
        </li>
      ))}
    </ul>
  );
}
