"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { fetchSessions, fetchTraces, fetchUsers } from "@/lib/api";
import { rangeKey, resolveRange, useWorkspace } from "@/lib/context";
import { fmtInt, fmtMs, fmtPrice, fmtRel, fmtTime, fmtTokens, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";
import { buildCsv, triggerCsvDownload } from "@/lib/csv-export";

type Tab = "sessions" | "users";

export default function InteractionsPage() {
  const { t, tsub } = useI18n();
  const ws = useWorkspace();
  const { search, live } = ws;
  const range = resolveRange(ws);
  const rk = rangeKey(ws);
  const [tab, setTab] = useState<Tab>("sessions");

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.interactions.title")}</h1>
          <p className="eo-page-lede">{t("pages.interactions.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <Link href="/workspace/tracing/" className="eo-btn">
            {t("pages.interactions.backToTracing")}
          </Link>
        </div>
      </div>

      <div className="eo-tab-bar" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "sessions"}
          className="eo-tab"
          data-active={tab === "sessions"}
          onClick={() => setTab("sessions")}
        >
          {t("pages.interactions.tabSessions")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "users"}
          className="eo-tab"
          data-active={tab === "users"}
          onClick={() => setTab("users")}
        >
          {t("pages.interactions.tabUsers")}
        </button>
      </div>

      {tab === "sessions" && (
        <SessionsTab range={range} rk={rk} search={search} live={live} t={t} tsub={tsub} />
      )}
      {tab === "users" && (
        <UsersTab range={range} rk={rk} search={search} live={live} t={t} tsub={tsub} />
      )}
    </>
  );
}

/* ─── Sessions Tab ─────────────────────────────────────────────────────── */

function SessionsTab({
  range,
  rk,
  search,
  live,
  t,
  tsub,
}: {
  range: ReturnType<typeof resolveRange>;
  rk: string;
  search: string;
  live: boolean;
  t: (k: string) => string;
  tsub: (k: string, vars: Record<string, string>) => string;
}) {
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

  const drawerTracesQ = useQuery({
    queryKey: ["session-traces", selected, rk],
    queryFn: () =>
      fetchTraces(range, { sessionId: selected ?? undefined, withLlm: true }),
    enabled: Boolean(selected),
  });

  return (
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
        <div className="eo-page-meta" style={{ marginBottom: 8 }}>
          <span className="eo-tag">
            {tsub("pages.interactions.sessionCount", { n: fmtInt(rows.length) })}
          </span>
          <button
            type="button"
            className="eo-btn"
            disabled={rows.length === 0}
            onClick={() => {
              const headers = ["session_id", "service", "user", "trace_count", "error_count", "tokens_in", "tokens_out", "tokens_total", "cost", "models", "first_seen_at", "last_seen_at"];
              const csvRows = rows.map((s) => [
                s.sessionId,
                s.serviceName,
                s.user ?? "",
                s.traceCount,
                s.errorCount,
                s.tokensIn,
                s.tokensOut,
                s.tokensTotal,
                s.price,
                s.models.join("; "),
                s.firstSeenAt,
                s.lastSeenAt,
              ]);
              triggerCsvDownload("sessions.csv", buildCsv(headers, csvRows));
            }}
          >
            CSV
          </button>
        </div>
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
                .map((tr, idx) => (
                  <li key={tr.traceId}>
                    <Link
                      href={`/workspace/tracing/detail/?id=${encodeURIComponent(tr.traceId)}`}
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
                          tr.status === "ERROR"
                            ? "err"
                            : tr.status === "OK"
                              ? "ok"
                              : ""
                        }
                      >
                        {tr.status}
                      </span>
                      <span className="eo-td-name">{tr.rootName || "(unnamed)"}</span>
                      {tr.tokensTotal != null && tr.tokensTotal > 0 && (
                        <span className="eo-td-sub mono">
                          {fmtTokens(tr.tokensTotal)} tok
                        </span>
                      )}
                      {tr.price != null && tr.price > 0 && (
                        <span className="eo-td-sub mono">{fmtPrice(tr.price)}</span>
                      )}
                      <span className="mono eo-td-sub" style={{ marginLeft: "auto" }}>
                        {fmtMs(
                          tr.endedAt
                            ? Math.max(
                                0,
                                +new Date(tr.endedAt) - +new Date(tr.startedAt),
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
  );
}

/* ─── Users Tab ────────────────────────────────────────────────────────── */

function UsersTab({
  range,
  rk,
  search,
  live,
  t,
  tsub,
}: {
  range: ReturnType<typeof resolveRange>;
  rk: string;
  search: string;
  live: boolean;
  t: (k: string) => string;
  tsub: (k: string, vars: Record<string, string>) => string;
}) {
  const [userFilter, setUserFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const usersQ = useQuery({
    queryKey: ["users", rk],
    queryFn: () => fetchUsers(range),
    refetchInterval: live ? 5_000 : false,
  });

  const rows = useMemo(() => {
    const uf = userFilter.trim().toLowerCase();
    const q = search.trim().toLowerCase();
    return (usersQ.data ?? []).filter((u) => {
      if (uf && !u.userId.toLowerCase().includes(uf)) return false;
      if (q) {
        const hay = `${u.userId} ${u.serviceName}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [usersQ.data, userFilter, search]);

  const selectedUser = rows.find((u) => u.userId === selected) ?? null;

  const drawerTracesQ = useQuery({
    queryKey: ["user-traces", selected, rk],
    queryFn: () =>
      fetchTraces(range, { userId: selected ?? undefined, withLlm: true }),
    enabled: Boolean(selected),
  });

  return (
    <div className="eo-trace-layout" data-drawer={selected ? "true" : "false"}>
      <aside className="eo-filters" aria-label="filters">
        <section className="eo-filter-section">
          <div className="eo-filter-h">
            <span>User ID</span>
            <button
              type="button"
              onClick={() => setUserFilter("")}
              disabled={!userFilter}
            >
              reset
            </button>
          </div>
          <input
            type="search"
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            placeholder="filter by user id…"
            spellCheck={false}
            className="eo-input"
            style={{ margin: 6, width: "calc(100% - 12px)" }}
          />
          <div className="eo-muted" style={{ fontSize: 11.5, padding: 6 }}>
            Users only show up when the agent emits an <code>o.user</code>{" "}
            tag (Python: <code>span_tag(SpanTag.USER, uid)</code>; TS/JS:{" "}
            <code>span.setAttribute(&quot;o.user&quot;, uid)</code>).
          </div>
        </section>
      </aside>

      <div className="eo-card" style={{ padding: 12 }}>
        <div className="eo-page-meta" style={{ marginBottom: 8 }}>
          <span className="eo-tag">
            {tsub("pages.interactions.userCount", { n: fmtInt(rows.length) })}
          </span>
          <button
            type="button"
            className="eo-btn"
            disabled={rows.length === 0}
            onClick={() => {
              const headers = ["user_id", "service", "session_count", "trace_count", "error_count", "tokens_in", "tokens_out", "tokens_total", "cost", "models", "first_seen_at", "last_seen_at"];
              const csvRows = rows.map((u) => [
                u.userId,
                u.serviceName,
                u.sessionCount,
                u.traceCount,
                u.errorCount,
                u.tokensIn,
                u.tokensOut,
                u.tokensTotal,
                u.price,
                u.models.join("; "),
                u.firstSeenAt,
                u.lastSeenAt,
              ]);
              triggerCsvDownload("users.csv", buildCsv(headers, csvRows));
            }}
          >
            CSV
          </button>
        </div>
        <div className="eo-table-wrap">
          <table className="eo-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Service</th>
                <th>Sessions</th>
                <th>Traces</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {usersQ.isLoading && (
                <tr>
                  <td colSpan={7}>
                    <div className="eo-empty">Loading users…</div>
                  </td>
                </tr>
              )}
              {!usersQ.isLoading && rows.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <div className="eo-empty">
                      No users yet. Tag a span with{" "}
                      <code>o.user</code> to start tracking users here.
                    </div>
                  </td>
                </tr>
              )}
              {rows.map((u) => (
                <tr
                  key={u.userId}
                  data-active={selected === u.userId}
                  onClick={() => setSelected(u.userId)}
                >
                  <td>
                    <div className="eo-td-name mono">{truncate(u.userId, 28)}</div>
                    <div className="eo-td-sub">{fmtRel(u.lastSeenAt)}</div>
                  </td>
                  <td>
                    <span className="eo-tag">{u.serviceName}</span>
                  </td>
                  <td className="mono">{fmtInt(u.sessionCount)}</td>
                  <td className="mono">{fmtInt(u.traceCount)}</td>
                  <td className="mono">{fmtTokens(u.tokensTotal)}</td>
                  <td className="mono">{fmtPrice(u.price)}</td>
                  <td className="mono">{fmtTime(u.lastSeenAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selectedUser && (
        <section className="eo-drawer">
          <div className="eo-drawer-h">
            <strong>User inspector</strong>
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
                <dt>User</dt>
                <dd className="mono">{selectedUser.userId}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>Service</dt>
                <dd>{selectedUser.serviceName}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>Sessions</dt>
                <dd>{selectedUser.sessionCount}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>Traces</dt>
                <dd>{selectedUser.traceCount}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>First seen</dt>
                <dd>{fmtTime(selectedUser.firstSeenAt)}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>Last seen</dt>
                <dd>{fmtTime(selectedUser.lastSeenAt)}</dd>
              </div>
              {selectedUser.tokensTotal > 0 && (
                <div className="eo-kv-row">
                  <dt>Tokens (sum)</dt>
                  <dd className="mono">
                    {fmtTokens(selectedUser.tokensIn)} in ·{" "}
                    {fmtTokens(selectedUser.tokensOut)} out
                  </dd>
                </div>
              )}
              {selectedUser.price > 0 && (
                <div className="eo-kv-row">
                  <dt>Cost (sum)</dt>
                  <dd className="mono">{fmtPrice(selectedUser.price)}</dd>
                </div>
              )}
              {selectedUser.errorCount > 0 && (
                <div className="eo-kv-row">
                  <dt>Errors</dt>
                  <dd>
                    <span className="eo-status" data-tone="err">
                      {selectedUser.errorCount}
                    </span>
                  </dd>
                </div>
              )}
              {selectedUser.models.length > 0 && (
                <div className="eo-kv-row">
                  <dt>Models</dt>
                  <dd>
                    {selectedUser.models.map((m) => (
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
              Recent traces
            </div>
            {drawerTracesQ.isLoading && (
              <div className="eo-empty">Loading traces…</div>
            )}
            {drawerTracesQ.isError && (
              <div className="eo-empty">Failed to load traces.</div>
            )}
            {drawerTracesQ.data && drawerTracesQ.data.length === 0 && (
              <div className="eo-empty">No traces found for this user.</div>
            )}
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}>
              {(drawerTracesQ.data ?? [])
                .slice()
                .sort((a, b) => +new Date(b.startedAt) - +new Date(a.startedAt))
                .slice(0, 30)
                .map((tr, idx) => (
                  <li key={tr.traceId}>
                    <Link
                      href={`/workspace/tracing/detail/?id=${encodeURIComponent(tr.traceId)}`}
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
                          tr.status === "ERROR"
                            ? "err"
                            : tr.status === "OK"
                              ? "ok"
                              : ""
                        }
                      >
                        {tr.status}
                      </span>
                      <span className="eo-td-name">{tr.rootName || "(unnamed)"}</span>
                      {tr.tokensTotal != null && tr.tokensTotal > 0 && (
                        <span className="eo-td-sub mono">
                          {fmtTokens(tr.tokensTotal)} tok
                        </span>
                      )}
                      {tr.price != null && tr.price > 0 && (
                        <span className="eo-td-sub mono">{fmtPrice(tr.price)}</span>
                      )}
                      <span className="mono eo-td-sub" style={{ marginLeft: "auto" }}>
                        {fmtMs(
                          tr.endedAt
                            ? Math.max(
                                0,
                                +new Date(tr.endedAt) - +new Date(tr.startedAt),
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
  );
}
