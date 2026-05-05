"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { fetchSessions, fetchTraces } from "@/lib/api";
import { rangeKey, resolveRange, useWorkspace } from "@/lib/context";
import { fmtInt, fmtMs, fmtPrice, fmtRel, fmtTime, fmtTokens, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

export default function SessionsPage() {
  const { t, tsub } = useI18n();
  const ws = useWorkspace();
  const { search, live } = ws;
  const range = resolveRange(ws);
  const rk = rangeKey(ws);
  // Sessions live as a separate filter from the global search box. Global
  // search is "free text across the visible grid"; this is "exact match
  // on a single session id" and also seeds the drawer query.
  const [sessionFilter, setSessionFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const sessionsQ = useQuery({
    queryKey: ["sessions", rk],
    queryFn: () => fetchSessions(range),
    refetchInterval: live ? 5_000 : false,
  });

  const rows = useMemo(() => {
    const sf = sessionFilter.trim().toLowerCase();
    const q = search.trim().toLowerCase();
    return (sessionsQ.data ?? []).filter((s) => {
      if (sf && !s.sessionId.toLowerCase().includes(sf)) return false;
      if (q) {
        const hay =
          `${s.sessionId} ${s.serviceName} ${s.user ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [sessionsQ.data, sessionFilter, search]);

  const selectedSession = rows.find((s) => s.sessionId === selected) ?? null;

  // Pull the *exact* set of traces for the selected session straight from
  // the server, using `traceIds` from the session row as a sanity check.
  // This is much more accurate than the previous time-window heuristic,
  // which could pick up unrelated traces that happened to land in the
  // same hour bucket.
  const drawerTracesQ = useQuery({
    queryKey: ["session-traces", selected, rk],
    queryFn: () =>
      fetchTraces(range, { sessionId: selected ?? undefined, withLlm: true }),
    enabled: Boolean(selected),
  });

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.sessions.title")}</h1>
          <p className="eo-page-lede">{t("pages.sessions.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <span className="eo-tag">
            {tsub("pages.sessions.sessionCount", { n: fmtInt(rows.length) })}
          </span>
          <Link href="/workspace/tracing/" className="eo-btn">
            {t("pages.sessions.backToTracing")}
          </Link>
        </div>
      </div>

      <div className="eo-trace-layout" data-drawer={selected ? "true" : "false"}>
        <aside className="eo-filters" aria-label="filters">
          <section className="eo-filter-section">
            <div className="eo-filter-h">
              <span>Session ID</span>
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
              onChange={(e) => setSessionFilter(e.target.value)}
              placeholder="filter by session id…"
              spellCheck={false}
              className="eo-input"
              style={{ margin: 6, width: "calc(100% - 12px)" }}
            />
            <div className="eo-muted" style={{ fontSize: 11.5, padding: 6 }}>
              Sessions only show up when the agent emits an <code>o.sess</code>{" "}
              tag (Python: <code>span_tag(SpanTag.SESSION, sid)</code>; TS/JS:{" "}
              <code>span.setAttribute(&quot;o.sess&quot;, sid)</code>).
            </div>
          </section>
        </aside>

        <div className="eo-card" style={{ padding: 12 }}>
          <div className="eo-table-wrap">
            <table className="eo-table">
              <thead>
                <tr>
                  <th>Session</th>
                  <th>Service</th>
                  <th>User</th>
                  <th>Turns</th>
                  <th>First seen</th>
                  <th>Last seen</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sessionsQ.isLoading && (
                  <tr>
                    <td colSpan={7}>
                      <div className="eo-empty">Loading sessions…</div>
                    </td>
                  </tr>
                )}
                {!sessionsQ.isLoading && rows.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <div className="eo-empty">
                        No sessions yet. Tag a span with{" "}
                        <code>o.sess</code> to start grouping traces here.
                      </div>
                    </td>
                  </tr>
                )}
                {rows.map((s) => (
                  <tr
                    key={s.sessionId}
                    data-active={selected === s.sessionId}
                    onClick={() => setSelected(s.sessionId)}
                  >
                    <td>
                      <div className="eo-td-name mono">{truncate(s.sessionId, 28)}</div>
                      <div className="eo-td-sub">{fmtRel(s.lastSeenAt)}</div>
                    </td>
                    <td>
                      <span className="eo-tag">{s.serviceName}</span>
                    </td>
                    <td>
                      {s.user ? (
                        <span className="eo-chip">{truncate(s.user, 18)}</span>
                      ) : (
                        <span className="eo-td-sub">—</span>
                      )}
                    </td>
                    <td className="mono">{fmtInt(s.traceCount)}</td>
                    <td className="mono">{fmtTime(s.firstSeenAt)}</td>
                    <td className="mono">{fmtTime(s.lastSeenAt)}</td>
                    <td>
                      <Link
                        href={`/workspace/tracing/?session=${encodeURIComponent(s.sessionId)}`}
                        className="eo-btn"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Open in Tracing →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {selectedSession && (
          <section className="eo-drawer">
            <div className="eo-drawer-h">
              <strong>Session inspector</strong>
              <button
                type="button"
                className="eo-drawer-close"
                onClick={() => setSelected(null)}
                style={{ marginLeft: "auto" }}
                aria-label="close"
              >
                ×
              </button>
            </div>
            <div className="eo-drawer-body">
              <dl className="eo-kv-list">
                <div className="eo-kv-row">
                  <dt>Session</dt>
                  <dd className="mono">{selectedSession.sessionId}</dd>
                </div>
                <div className="eo-kv-row">
                  <dt>Service</dt>
                  <dd>{selectedSession.serviceName}</dd>
                </div>
                {selectedSession.user && (
                  <div className="eo-kv-row">
                    <dt>User</dt>
                    <dd>{selectedSession.user}</dd>
                  </div>
                )}
                <div className="eo-kv-row">
                  <dt>Turns (traces)</dt>
                  <dd>{selectedSession.traceCount}</dd>
                </div>
                <div className="eo-kv-row">
                  <dt>First seen</dt>
                  <dd>{fmtTime(selectedSession.firstSeenAt)}</dd>
                </div>
                <div className="eo-kv-row">
                  <dt>Last seen</dt>
                  <dd>{fmtTime(selectedSession.lastSeenAt)}</dd>
                </div>
                {/* Aggregate roll-ups — kept here as a compact summary; the
                    main table intentionally stays minimal because these are
                    fundamentally trace-level metrics. */}
                {selectedSession.tokensTotal > 0 && (
                  <div className="eo-kv-row">
                    <dt>Tokens (sum)</dt>
                    <dd className="mono">
                      {fmtTokens(selectedSession.tokensIn)} in ·{" "}
                      {fmtTokens(selectedSession.tokensOut)} out
                    </dd>
                  </div>
                )}
                {selectedSession.price > 0 && (
                  <div className="eo-kv-row">
                    <dt>Cost (sum)</dt>
                    <dd className="mono">{fmtPrice(selectedSession.price)}</dd>
                  </div>
                )}
                {selectedSession.errorCount > 0 && (
                  <div className="eo-kv-row">
                    <dt>Errors</dt>
                    <dd>
                      <span className="eo-status" data-tone="err">
                        {selectedSession.errorCount}
                      </span>
                    </dd>
                  </div>
                )}
                {selectedSession.models.length > 0 && (
                  <div className="eo-kv-row">
                    <dt>Models</dt>
                    <dd>
                      {selectedSession.models.map((m) => (
                        <span
                          key={m}
                          className="eo-chip eo-chip-accent"
                          style={{ marginRight: 4 }}
                        >
                          {m}
                        </span>
                      ))}
                    </dd>
                  </div>
                )}
              </dl>

              <div className="eo-card-sub" style={{ marginTop: 6 }}>
                Turns in this session
              </div>
              {drawerTracesQ.isLoading && (
                <div className="eo-empty">Loading traces…</div>
              )}
              {drawerTracesQ.isError && (
                <div className="eo-empty">Failed to load traces.</div>
              )}
              {drawerTracesQ.data && drawerTracesQ.data.length === 0 && (
                <div className="eo-empty">No traces match this session.</div>
              )}
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}>
                {(drawerTracesQ.data ?? [])
                  .slice()
                  .sort((a, b) => +new Date(a.startedAt) - +new Date(b.startedAt))
                  .map((t, idx) => (
                    <li key={t.traceId}>
                      <Link
                        href={`/workspace/tracing/detail/?id=${encodeURIComponent(t.traceId)}`}
                        className="eo-card"
                        style={{
                          padding: 8,
                          display: "flex",
                          gap: 8,
                          alignItems: "center",
                          boxShadow: "none",
                        }}
                      >
                        <span className="eo-td-sub mono" style={{ minWidth: 28 }}>
                          #{idx + 1}
                        </span>
                        <span
                          className="eo-status"
                          data-tone={
                            t.status === "ERROR"
                              ? "err"
                              : t.status === "OK"
                                ? "ok"
                                : ""
                          }
                        >
                          {t.status}
                        </span>
                        <span className="eo-td-name">{t.rootName || "(unnamed)"}</span>
                        {t.tokensTotal != null && t.tokensTotal > 0 && (
                          <span className="eo-td-sub mono">
                            {fmtTokens(t.tokensTotal)} tok
                          </span>
                        )}
                        {t.price != null && t.price > 0 && (
                          <span className="eo-td-sub mono">{fmtPrice(t.price)}</span>
                        )}
                        <span className="mono eo-td-sub" style={{ marginLeft: "auto" }}>
                          {fmtMs(
                            t.endedAt
                              ? Math.max(
                                  0,
                                  +new Date(t.endedAt) - +new Date(t.startedAt),
                                )
                              : 0,
                          )}
                        </span>
                      </Link>
                    </li>
                  ))}
              </ul>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
