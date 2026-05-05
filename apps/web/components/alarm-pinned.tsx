"use client";

/**
 * Compact alarm strip on the unified workspace Overview.
 *
 * Contract:
 *   - Shows pinned rules' last-known state with a coloured dot, severity
 *     pill, observed value, and threshold.
 *   - Auto-refreshes every 30s; collapses gracefully when the user has no
 *     org context, when the alarms module is disabled (404), or when no
 *     rule is pinned to the surface.
 *   - Read-only by design: management lives in the Org > Alarms tab.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { fetchAlarmOverview, type AlarmSurface } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";

const SEVERITY_TONE: Record<string, string> = {
  info: "#0ea5e9",
  warning: "#f59e0b",
  critical: "#dc2626",
};

const STATE_TONE: Record<string, string> = {
  firing: "#dc2626",
  resolved: "#16a34a",
  ok: "#16a34a",
  insufficient_data: "#94a3b8",
  disabled: "#94a3b8",
  "": "#94a3b8",
};

export function AlarmPinned({
  surface,
  title,
}: {
  surface: AlarmSurface;
  title: string;
}) {
  const auth = useAuth();
  const orgId = auth.currentOrg?.id;
  const q = useQuery({
    queryKey: ["alarm-overview", orgId, surface],
    queryFn: () => fetchAlarmOverview(orgId!, surface),
    enabled: !!orgId,
    refetchInterval: 30_000,
    retry: false,
  });

  if (!orgId) return null;
  if (q.isError) return null;
  const items = q.data?.items ?? [];

  const firing = items.filter((i) => i.rule.lastState === "firing").length;
  const showLink = auth.isPlatformAdmin || auth.role === "PO";

  return (
    <div className="eo-card" style={{ marginBottom: 12 }}>
      <div className="eo-card-h" style={{ alignItems: "center" }}>
        <h3 className="eo-card-title">{title}</h3>
        <span className="eo-card-sub">
          {items.length === 0
            ? "No pinned alarm rules"
            : firing > 0
              ? `${firing} firing of ${items.length} pinned`
              : `${items.length} pinned · all healthy`}
        </span>
        {showLink && (
          <Link
            href={`/workspace/setup/organizations/${orgId}/`}
            className="eo-btn eo-btn-ghost"
            style={{ marginLeft: "auto", fontSize: 11 }}
          >
            Manage alarms →
          </Link>
        )}
      </div>

      {items.length === 0 ? (
        <div className="eo-empty" style={{ fontSize: 12 }}>
          Pin alarm rules from the Org &gt; Alarms tab to surface their
          health here.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 8,
          }}
        >
          {items.map(({ rule }) => {
            const stateColor = STATE_TONE[rule.lastState] ?? "#94a3b8";
            const sevColor = SEVERITY_TONE[rule.severity] ?? "#94a3b8";
            return (
              <div
                key={rule.id}
                className="eo-card"
                style={{ padding: 10, gap: 4 }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: stateColor,
                      boxShadow: `0 0 0 2px ${stateColor}33`,
                    }}
                  />
                  <strong style={{ fontSize: 12 }}>{rule.name}</strong>
                  <span
                    className="eo-pill"
                    style={{
                      marginLeft: "auto",
                      background: sevColor,
                      color: "white",
                      fontSize: 10,
                    }}
                  >
                    {rule.severity}
                  </span>
                </div>
                <div className="eo-mute" style={{ fontSize: 11 }}>
                  {rule.signalKind} · {rule.comparator} {formatNumber(rule.threshold)}
                  {rule.lastObservedValue != null && (
                    <> · obs {formatNumber(rule.lastObservedValue)}</>
                  )}
                </div>
                <div className="eo-mute" style={{ fontSize: 10 }}>
                  {rule.lastEvaluatedAt
                    ? `last eval ${fmtRel(rule.lastEvaluatedAt)}`
                    : "never evaluated"}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatNumber(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Number.isInteger(n)) return String(n);
  if (Math.abs(n) >= 100) return n.toFixed(1);
  return n.toFixed(3);
}
