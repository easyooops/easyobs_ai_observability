"use client";

/**
 * Alarms tab — channels, rules, and events sections inside the Org detail
 * surface. Mirrors the visual language of the Quality > Judges page so
 * channel selection feels familiar.
 *
 * Data flow:
 *   1. ``/v1/organizations/{org}/alarms/catalog`` populates the channel and
 *      signal tile catalogs.
 *   2. CRUD helpers in ``lib/api`` hit the alarm endpoints; mutations
 *      invalidate the relevant query keys here.
 *
 * Permissions: the parent page already gates the Alarms tab on
 * ``isPlatformAdmin || role === "PO"``. Read-only DV users can still see
 * the events table by selecting a service first; we surface a hint when
 * the caller cannot mutate.
 */

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createAlarmChannel,
  createAlarmRule,
  deleteAlarmChannel,
  deleteAlarmRule,
  evaluateAlarmRule,
  fetchAlarmCatalog,
  fetchAlarmChannels,
  fetchAlarmEvents,
  fetchAlarmPins,
  fetchAlarmRules,
  fetchOrgServices,
  patchAlarmChannel,
  patchAlarmRule,
  replaceAlarmPins,
  testAlarmChannel,
  type AlarmChannel,
  type AlarmChannelKind,
  type AlarmComparator,
  type AlarmRule,
  type AlarmRuleSavePayload,
  type AlarmSeverity,
  type AlarmSignalKind,
  type AlarmSurface,
  type ChannelFieldSpec,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";

const SECTION = ["channels", "rules", "events", "pins"] as const;
type Section = (typeof SECTION)[number];

const SEVERITY_TONES: Record<AlarmSeverity, string> = {
  info: "#0ea5e9",
  warning: "#f59e0b",
  critical: "#dc2626",
};

const STATE_TONES: Record<string, string> = {
  firing: "#dc2626",
  resolved: "#16a34a",
  ok: "#16a34a",
  insufficient_data: "#94a3b8",
  disabled: "#94a3b8",
  "": "#94a3b8",
};

const COMPARATORS: AlarmComparator[] = ["gt", "gte", "lt", "lte", "eq"];
const SEVERITIES: AlarmSeverity[] = ["info", "warning", "critical"];

export function AlarmsTab({ orgId }: { orgId: string }) {
  const auth = useAuth();
  const writable =
    !!auth.user?.isSuperAdmin ||
    auth.isPlatformAdmin ||
    auth.role === "PO";
  const [section, setSection] = useState<Section>("channels");

  return (
    <section className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">Alarms</h3>
        <span className="eo-card-sub">
          Threshold alerting for Observe and Quality signals — fan-out to
          Slack, Teams, Discord, PagerDuty, Opsgenie, Webhooks, or Email.
        </span>
      </div>

      <div className="eo-tab-bar" style={{ marginTop: 4, marginBottom: 12 }}>
        {SECTION.map((s) => (
          <button
            key={s}
            type="button"
            className="eo-tab"
            data-active={section === s}
            onClick={() => setSection(s)}
          >
            {s === "channels"
              ? "Channels"
              : s === "rules"
                ? "Rules"
                : s === "events"
                  ? "Events"
                  : "Pins"}
          </button>
        ))}
      </div>

      {!writable && (
        <div className="eo-empty" style={{ marginBottom: 8 }}>
          You can browse alarm state, but only platform admins (SA) and
          organization PO can change channels, rules, or pins.
        </div>
      )}

      {section === "channels" && (
        <ChannelsPanel orgId={orgId} writable={writable} />
      )}
      {section === "rules" && <RulesPanel orgId={orgId} writable={writable} />}
      {section === "events" && <EventsPanel orgId={orgId} />}
      {section === "pins" && <PinsPanel orgId={orgId} writable={writable} />}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Channels
// ---------------------------------------------------------------------------

function ChannelsPanel({
  orgId,
  writable,
}: {
  orgId: string;
  writable: boolean;
}) {
  const qc = useQueryClient();
  const channelsQ = useQuery({
    queryKey: ["alarm-channels", orgId],
    queryFn: () => fetchAlarmChannels(orgId),
  });
  const catalogQ = useQuery({
    queryKey: ["alarm-catalog", orgId],
    queryFn: () => fetchAlarmCatalog(orgId),
    staleTime: 5 * 60 * 1000,
  });

  const [editing, setEditing] = useState<ChannelDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (d: ChannelDraft) => {
      if (!d.name.trim()) throw new Error("Name is required");
      const cleanedConfig = pruneStrings(d.config);
      if (d.id) {
        return patchAlarmChannel(orgId, d.id, {
          name: d.name,
          config: cleanedConfig,
          enabled: d.enabled,
        });
      }
      return createAlarmChannel(orgId, {
        name: d.name,
        channelKind: d.channelKind,
        config: cleanedConfig,
        enabled: d.enabled,
      });
    },
    onSuccess: () => {
      setEditing(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["alarm-channels", orgId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteAlarmChannel(orgId, id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["alarm-channels", orgId] }),
  });

  const test = useMutation({
    mutationFn: (id: string) => testAlarmChannel(orgId, id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["alarm-channels", orgId] }),
  });

  const channels = channelsQ.data ?? [];
  const catalog = catalogQ.data?.channels ?? [];
  const channelByKind = useMemo(
    () => Object.fromEntries(catalog.map((c) => [c.kind, c])),
    [catalog],
  );

  return (
    <div>
      <div className="eo-card-h" style={{ marginBottom: 8 }}>
        <h4 className="eo-card-title" style={{ fontSize: 14 }}>
          Delivery channels
        </h4>
        <span className="eo-card-sub">
          Pick a tile to add a new channel. Channels are reused across rules.
        </span>
      </div>

      {writable && (
        <div className="eo-provider-grid" role="listbox" aria-label="Channel">
          {catalog.map((c) => (
            <button
              key={c.kind}
              type="button"
              className="eo-provider-tile"
              onClick={() => {
                setEditing(emptyChannelDraft(c.kind, c.fields));
                setError(null);
              }}
              title={c.blurb}
            >
              <span aria-hidden style={{ fontSize: 18 }}>
                {c.icon}
              </span>
              <span>{c.label}</span>
            </button>
          ))}
        </div>
      )}

      <div className="eo-table-wrap" style={{ marginTop: 12 }}>
        <table className="eo-table">
          <thead>
            <tr>
              <th>Channel</th>
              <th>Kind</th>
              <th>Enabled</th>
              <th>Last test</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {channelsQ.isLoading && (
              <tr>
                <td colSpan={6} className="eo-empty">
                  Loading…
                </td>
              </tr>
            )}
            {!channelsQ.isLoading && channels.length === 0 && (
              <tr>
                <td colSpan={6} className="eo-empty">
                  No channels yet — add one with the tiles above.
                </td>
              </tr>
            )}
            {channels.map((c) => (
              <tr key={c.id}>
                <td>
                  <strong>{c.name}</strong>
                </td>
                <td>
                  <span style={{ marginRight: 4 }}>
                    {channelByKind[c.channelKind]?.icon ?? "•"}
                  </span>
                  {channelByKind[c.channelKind]?.label ?? c.channelKind}
                </td>
                <td>
                  <Dot ok={c.enabled} />
                  <span className="eo-mute" style={{ marginLeft: 6, fontSize: 11 }}>
                    {c.enabled ? "on" : "off"}
                  </span>
                </td>
                <td>
                  {c.lastTestAt ? (
                    <span title={c.lastTestError}>
                      <Dot ok={c.lastTestStatus === "ok"} />
                      <span style={{ marginLeft: 6 }}>
                        {fmtRel(c.lastTestAt)}
                      </span>
                    </span>
                  ) : (
                    <span className="eo-mute">never</span>
                  )}
                </td>
                <td>{fmtRel(c.createdAt)}</td>
                <td className="eo-col-right">
                  <div className="eo-row-actions">
                    {writable && (
                      <>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          disabled={test.isPending}
                          onClick={() => test.mutate(c.id)}
                        >
                          Test
                        </button>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() => {
                            setEditing(channelToDraft(c, channelByKind[c.channelKind]?.fields ?? []));
                            setError(null);
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() => {
                            if (confirm(`Delete channel "${c.name}"?`)) {
                              remove.mutate(c.id);
                            }
                          }}
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <ChannelEditor
          orgId={orgId}
          draft={editing}
          fields={channelByKind[editing.channelKind]?.fields ?? []}
          writable={writable}
          error={error}
          onChange={setEditing}
          onCancel={() => {
            setEditing(null);
            setError(null);
          }}
          onSave={() => save.mutate(editing)}
          saving={save.isPending}
        />
      )}
    </div>
  );
}

type ChannelDraft = {
  id?: string;
  name: string;
  channelKind: AlarmChannelKind;
  enabled: boolean;
  config: Record<string, string>;
};

function emptyChannelDraft(
  kind: AlarmChannelKind,
  fields: ChannelFieldSpec[],
): ChannelDraft {
  const cfg: Record<string, string> = {};
  for (const f of fields) {
    cfg[f.key] = f.placeholder && f.type === "select" ? "" : "";
  }
  return {
    name: "",
    channelKind: kind,
    enabled: true,
    config: cfg,
  };
}

function channelToDraft(c: AlarmChannel, fields: ChannelFieldSpec[]): ChannelDraft {
  const cfg: Record<string, string> = {};
  for (const f of fields) {
    const v = (c.config ?? {})[f.key];
    cfg[f.key] = v == null ? "" : String(v);
  }
  return {
    id: c.id,
    name: c.name,
    channelKind: c.channelKind,
    enabled: c.enabled,
    config: cfg,
  };
}

function pruneStrings(v: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, val] of Object.entries(v)) {
    const trimmed = val.trim();
    if (trimmed) out[k] = trimmed;
  }
  return out;
}

function ChannelEditor({
  draft,
  fields,
  writable,
  error,
  onChange,
  onCancel,
  onSave,
  saving,
}: {
  orgId: string;
  draft: ChannelDraft;
  fields: ChannelFieldSpec[];
  writable: boolean;
  error: string | null;
  onChange: (d: ChannelDraft) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  return (
    <div className="eo-card" style={{ marginTop: 12 }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {draft.id ? "Edit channel" : "Add channel"}
        </h3>
        <span className="eo-card-sub">
          {draft.channelKind.toUpperCase()} — fields below come from the
          server-side catalog. Secret fields are masked when re-fetched.
        </span>
      </div>

      <div className="eo-grid-2" style={{ gap: 12 }}>
        <label className="eo-field">
          <span>Display name</span>
          <input
            className="eo-input"
            value={draft.name}
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
            placeholder={`${draft.channelKind} channel`}
            disabled={!writable}
          />
        </label>
        <label className="eo-field" style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
            disabled={!writable}
          />
          <span>Enabled</span>
        </label>
      </div>

      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {fields.map((f) => (
          <label key={f.key} className="eo-field">
            <span>
              {f.label}
              {f.required && <span style={{ color: "#dc2626" }}> *</span>}
            </span>
            {f.type === "multiline" ? (
              <textarea
                className="eo-input"
                rows={3}
                value={draft.config[f.key] ?? ""}
                placeholder={f.placeholder}
                disabled={!writable}
                onChange={(e) =>
                  onChange({
                    ...draft,
                    config: { ...draft.config, [f.key]: e.target.value },
                  })
                }
              />
            ) : f.type === "select" ? (
              <select
                className="eo-input"
                value={draft.config[f.key] ?? ""}
                disabled={!writable}
                onChange={(e) =>
                  onChange({
                    ...draft,
                    config: { ...draft.config, [f.key]: e.target.value },
                  })
                }
              >
                <option value="">(default)</option>
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="eo-input"
                type={f.secret ? "password" : f.type === "number" ? "number" : "text"}
                value={draft.config[f.key] ?? ""}
                placeholder={f.placeholder}
                disabled={!writable}
                onChange={(e) =>
                  onChange({
                    ...draft,
                    config: { ...draft.config, [f.key]: e.target.value },
                  })
                }
              />
            )}
            {f.help && <span className="eo-mute" style={{ fontSize: 11 }}>{f.help}</span>}
          </label>
        ))}
      </div>

      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
        <button type="button" className="eo-btn eo-btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={!writable || saving}
          onClick={onSave}
        >
          {saving ? "Saving…" : "Save channel"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

function RulesPanel({
  orgId,
  writable,
}: {
  orgId: string;
  writable: boolean;
}) {
  const qc = useQueryClient();
  const rulesQ = useQuery({
    queryKey: ["alarm-rules", orgId],
    queryFn: () => fetchAlarmRules(orgId),
  });
  const catalogQ = useQuery({
    queryKey: ["alarm-catalog", orgId],
    queryFn: () => fetchAlarmCatalog(orgId),
    staleTime: 5 * 60 * 1000,
  });
  const channelsQ = useQuery({
    queryKey: ["alarm-channels", orgId],
    queryFn: () => fetchAlarmChannels(orgId),
  });
  const servicesQ = useQuery({
    queryKey: ["org-services", orgId],
    queryFn: () => fetchOrgServices(orgId),
  });

  const [editing, setEditing] = useState<RuleDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (d: RuleDraft) => {
      if (!d.name.trim()) throw new Error("Name is required");
      const payload: AlarmRuleSavePayload = {
        name: d.name,
        serviceId: d.serviceId || null,
        description: d.description,
        signalKind: d.signalKind,
        signalParams: d.signalParams ?? {},
        comparator: d.comparator,
        threshold: d.threshold,
        windowMinutes: d.windowMinutes,
        minSamples: d.minSamples,
        dedupMinutes: d.dedupMinutes,
        severity: d.severity,
        enabled: d.enabled,
        channelIds: d.channelIds,
      };
      if (d.id) return patchAlarmRule(orgId, d.id, payload);
      return createAlarmRule(orgId, payload);
    },
    onSuccess: () => {
      setEditing(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["alarm-rules", orgId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteAlarmRule(orgId, id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["alarm-rules", orgId] }),
  });

  const evaluate = useMutation({
    mutationFn: (id: string) => evaluateAlarmRule(orgId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarm-rules", orgId] });
      qc.invalidateQueries({ queryKey: ["alarm-events", orgId] });
    },
  });

  const rules = rulesQ.data ?? [];
  const signals = catalogQ.data?.signals ?? [];
  const signalByKind = useMemo(
    () => Object.fromEntries(signals.map((s) => [s.kind, s])),
    [signals],
  );
  const channelById = useMemo(
    () => Object.fromEntries((channelsQ.data ?? []).map((c) => [c.id, c])),
    [channelsQ.data],
  );

  return (
    <div>
      <div
        className="eo-card-h"
        style={{ marginBottom: 8, alignItems: "center" }}
      >
        <h4 className="eo-card-title" style={{ fontSize: 14 }}>
          Rules
        </h4>
        <span className="eo-card-sub">
          {rules.length} rule{rules.length === 1 ? "" : "s"} configured
        </span>
        {writable && (
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            style={{ marginLeft: "auto" }}
            onClick={() => {
              const fallbackSignal = signals[0];
              if (!fallbackSignal) {
                setError(
                  "Catalog still loading — try again in a moment.",
                );
                return;
              }
              setEditing(emptyRuleDraft(fallbackSignal.kind, fallbackSignal));
              setError(null);
            }}
          >
            New rule
          </button>
        )}
      </div>

      <div className="eo-table-wrap">
        <table className="eo-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Signal</th>
              <th>Threshold</th>
              <th>Severity</th>
              <th>Window</th>
              <th>State</th>
              <th>Channels</th>
              <th>Last eval</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rulesQ.isLoading && (
              <tr>
                <td colSpan={9} className="eo-empty">
                  Loading…
                </td>
              </tr>
            )}
            {!rulesQ.isLoading && rules.length === 0 && (
              <tr>
                <td colSpan={9} className="eo-empty">
                  No rules yet. Click "New rule" to threshold any operational
                  or quality signal.
                </td>
              </tr>
            )}
            {rules.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong>{r.name}</strong>
                  {r.serviceId && (
                    <div className="eo-mute" style={{ fontSize: 11 }}>
                      service: {r.serviceId.slice(0, 8)}
                    </div>
                  )}
                </td>
                <td>{signalByKind[r.signalKind]?.label ?? r.signalKind}</td>
                <td className="mono">
                  {r.comparator} {formatNumber(r.threshold)}
                </td>
                <td>
                  <span
                    className="eo-pill"
                    style={{
                      background: SEVERITY_TONES[r.severity],
                      color: "white",
                    }}
                  >
                    {r.severity}
                  </span>
                </td>
                <td className="mono">{r.windowMinutes}m</td>
                <td>
                  <Dot color={STATE_TONES[r.lastState] ?? "#94a3b8"} />
                  <span style={{ marginLeft: 6, fontSize: 12 }}>
                    {r.lastState || "never"}
                  </span>
                  {r.lastObservedValue != null && (
                    <div className="eo-mute" style={{ fontSize: 11 }}>
                      observed {formatNumber(r.lastObservedValue)}
                    </div>
                  )}
                </td>
                <td>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {r.channelIds.length === 0 && (
                      <span className="eo-mute" style={{ fontSize: 11 }}>
                        none
                      </span>
                    )}
                    {r.channelIds.map((cid) => (
                      <span
                        key={cid}
                        className="eo-chip"
                        title={channelById[cid]?.name}
                      >
                        {channelById[cid]?.name ?? cid.slice(0, 6)}
                      </span>
                    ))}
                  </div>
                </td>
                <td>
                  {r.lastEvaluatedAt ? fmtRel(r.lastEvaluatedAt) : "—"}
                </td>
                <td className="eo-col-right">
                  <div className="eo-row-actions">
                    {writable && (
                      <>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          disabled={evaluate.isPending}
                          onClick={() => evaluate.mutate(r.id)}
                        >
                          Eval now
                        </button>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() => {
                            setEditing(ruleToDraft(r));
                            setError(null);
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() => {
                            if (confirm(`Delete rule "${r.name}"?`)) {
                              remove.mutate(r.id);
                            }
                          }}
                        >
                          Delete
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <RuleEditor
          orgId={orgId}
          draft={editing}
          signals={signals}
          channels={channelsQ.data ?? []}
          services={servicesQ.data ?? []}
          writable={writable}
          error={error}
          onChange={setEditing}
          onCancel={() => {
            setEditing(null);
            setError(null);
          }}
          onSave={() => save.mutate(editing)}
          saving={save.isPending}
        />
      )}
    </div>
  );
}

type RuleDraft = {
  id?: string;
  name: string;
  serviceId: string | null;
  description: string;
  signalKind: AlarmSignalKind;
  signalParams: Record<string, unknown>;
  comparator: AlarmComparator;
  threshold: number;
  windowMinutes: number;
  minSamples: number;
  dedupMinutes: number;
  severity: AlarmSeverity;
  enabled: boolean;
  channelIds: string[];
};

type SignalEntry = {
  kind: AlarmSignalKind;
  label: string;
  blurb: string;
  surface: "observe" | "quality";
  unit: string;
  suggestedWindowMinutes: number;
  suggestedMinSamples: number;
  suggestedSeverity: AlarmSeverity;
  suggestedComparator: AlarmComparator;
  suggestedThreshold: number;
};

function emptyRuleDraft(
  kind: AlarmSignalKind,
  s: SignalEntry,
): RuleDraft {
  return {
    name: "",
    serviceId: null,
    description: "",
    signalKind: kind,
    signalParams: {},
    comparator: s.suggestedComparator,
    threshold: s.suggestedThreshold,
    windowMinutes: s.suggestedWindowMinutes,
    minSamples: s.suggestedMinSamples,
    dedupMinutes: 15,
    severity: s.suggestedSeverity,
    enabled: true,
    channelIds: [],
  };
}

function ruleToDraft(r: AlarmRule): RuleDraft {
  return {
    id: r.id,
    name: r.name,
    serviceId: r.serviceId,
    description: r.description,
    signalKind: r.signalKind,
    signalParams: { ...(r.signalParams ?? {}) },
    comparator: r.comparator,
    threshold: r.threshold,
    windowMinutes: r.windowMinutes,
    minSamples: r.minSamples,
    dedupMinutes: r.dedupMinutes,
    severity: r.severity,
    enabled: r.enabled,
    channelIds: [...r.channelIds],
  };
}

function RuleEditor({
  draft,
  signals,
  channels,
  services,
  writable,
  error,
  onChange,
  onCancel,
  onSave,
  saving,
}: {
  orgId: string;
  draft: RuleDraft;
  signals: SignalEntry[];
  channels: AlarmChannel[];
  services: { id: string; name: string }[];
  writable: boolean;
  error: string | null;
  onChange: (d: RuleDraft) => void;
  onCancel: () => void;
  onSave: () => void;
  saving: boolean;
}) {
  const observeSignals = signals.filter((s) => s.surface === "observe");
  const qualitySignals = signals.filter((s) => s.surface === "quality");

  return (
    <div className="eo-card" style={{ marginTop: 12 }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {draft.id ? "Edit rule" : "New rule"}
        </h3>
        <span className="eo-card-sub">
          Pick the signal first; the form pre-fills sensible thresholds you
          can refine.
        </span>
      </div>

      <div className="eo-field">
        <span>Signal</span>
        <div className="eo-card-sub" style={{ marginTop: 4 }}>Observe</div>
        <div className="eo-provider-grid" role="listbox" aria-label="Signal">
          {observeSignals.map((s) => (
            <SignalTile
              key={s.kind}
              s={s}
              active={draft.signalKind === s.kind}
              disabled={!writable}
              onClick={() =>
                onChange({
                  ...emptyRuleDraft(s.kind, s),
                  id: draft.id,
                  name: draft.name,
                  serviceId: draft.serviceId,
                  description: draft.description,
                  channelIds: draft.channelIds,
                  enabled: draft.enabled,
                })
              }
            />
          ))}
        </div>
        <div className="eo-card-sub" style={{ marginTop: 8 }}>Quality</div>
        <div className="eo-provider-grid" role="listbox" aria-label="Signal">
          {qualitySignals.map((s) => (
            <SignalTile
              key={s.kind}
              s={s}
              active={draft.signalKind === s.kind}
              disabled={!writable}
              onClick={() =>
                onChange({
                  ...emptyRuleDraft(s.kind, s),
                  id: draft.id,
                  name: draft.name,
                  serviceId: draft.serviceId,
                  description: draft.description,
                  channelIds: draft.channelIds,
                  enabled: draft.enabled,
                })
              }
            />
          ))}
        </div>
      </div>

      <div className="eo-grid-3" style={{ gap: 12, marginTop: 12 }}>
        <label className="eo-field">
          <span>Name</span>
          <input
            className="eo-input"
            value={draft.name}
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>Service scope</span>
          <select
            className="eo-input"
            value={draft.serviceId ?? ""}
            onChange={(e) =>
              onChange({
                ...draft,
                serviceId: e.target.value || null,
              })
            }
            disabled={!writable}
          >
            <option value="">All services in this org</option>
            {services.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="eo-field" style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
            disabled={!writable}
          />
          <span>Enabled</span>
        </label>
      </div>

      <div className="eo-grid-3" style={{ gap: 12, marginTop: 8 }}>
        <label className="eo-field">
          <span>Comparator</span>
          <select
            className="eo-input"
            value={draft.comparator}
            onChange={(e) =>
              onChange({ ...draft, comparator: e.target.value as AlarmComparator })
            }
            disabled={!writable}
          >
            {COMPARATORS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="eo-field">
          <span>Threshold</span>
          <input
            className="eo-input"
            type="number"
            step="any"
            value={draft.threshold}
            onChange={(e) =>
              onChange({ ...draft, threshold: Number.parseFloat(e.target.value) || 0 })
            }
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>Severity</span>
          <select
            className="eo-input"
            value={draft.severity}
            onChange={(e) =>
              onChange({ ...draft, severity: e.target.value as AlarmSeverity })
            }
            disabled={!writable}
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="eo-field">
          <span>Window (min)</span>
          <input
            className="eo-input"
            type="number"
            min={1}
            value={draft.windowMinutes}
            onChange={(e) =>
              onChange({ ...draft, windowMinutes: Number.parseInt(e.target.value || "1", 10) || 1 })
            }
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>Min samples</span>
          <input
            className="eo-input"
            type="number"
            min={1}
            value={draft.minSamples}
            onChange={(e) =>
              onChange({ ...draft, minSamples: Number.parseInt(e.target.value || "1", 10) || 1 })
            }
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>Dedup (min)</span>
          <input
            className="eo-input"
            type="number"
            min={0}
            value={draft.dedupMinutes}
            onChange={(e) =>
              onChange({ ...draft, dedupMinutes: Number.parseInt(e.target.value || "0", 10) || 0 })
            }
            disabled={!writable}
          />
        </label>
      </div>

      <div className="eo-field" style={{ marginTop: 8 }}>
        <span>Description (optional)</span>
        <textarea
          className="eo-input"
          rows={2}
          value={draft.description}
          onChange={(e) => onChange({ ...draft, description: e.target.value })}
          placeholder="What does this rule mean? Who should react?"
          disabled={!writable}
        />
      </div>

      <div className="eo-field" style={{ marginTop: 8 }}>
        <span>Channels</span>
        {channels.length === 0 ? (
          <div className="eo-empty">
            No channels yet — add one in the Channels tab first.
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {channels.map((c) => {
              const on = draft.channelIds.includes(c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  className="eo-chip"
                  data-active={on}
                  disabled={!writable}
                  onClick={() => {
                    const next = new Set(draft.channelIds);
                    if (on) next.delete(c.id);
                    else next.add(c.id);
                    onChange({ ...draft, channelIds: Array.from(next) });
                  }}
                >
                  {c.name}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
        <button type="button" className="eo-btn eo-btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={!writable || saving}
          onClick={onSave}
        >
          {saving ? "Saving…" : "Save rule"}
        </button>
      </div>
    </div>
  );
}

function SignalTile({
  s,
  active,
  disabled,
  onClick,
}: {
  s: SignalEntry;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="eo-provider-tile"
      data-active={active}
      disabled={disabled}
      onClick={onClick}
      title={s.blurb}
    >
      <span style={{ fontSize: 11, fontWeight: 600 }}>{s.label}</span>
      <span className="eo-mute" style={{ fontSize: 10 }}>
        {s.suggestedComparator} {s.suggestedThreshold} {s.unit}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

function EventsPanel({ orgId }: { orgId: string }) {
  const [stateFilter, setStateFilter] = useState<"" | "firing" | "resolved">("");
  const eventsQ = useQuery({
    queryKey: ["alarm-events", orgId, stateFilter],
    queryFn: () =>
      fetchAlarmEvents(orgId, {
        state: stateFilter ? stateFilter : undefined,
        limit: 200,
      }),
    refetchInterval: 30_000,
  });

  const events = eventsQ.data ?? [];

  return (
    <div>
      <div className="eo-card-h" style={{ marginBottom: 8 }}>
        <h4 className="eo-card-title" style={{ fontSize: 14 }}>
          Recent events
        </h4>
        <span className="eo-card-sub">
          Auto-refresh every 30s — latest 200 events.
        </span>
        <div style={{ marginLeft: "auto" }}>
          <select
            className="eo-input"
            value={stateFilter}
            onChange={(e) =>
              setStateFilter(e.target.value as "" | "firing" | "resolved")
            }
          >
            <option value="">All states</option>
            <option value="firing">Firing only</option>
            <option value="resolved">Resolved only</option>
          </select>
        </div>
      </div>

      <div className="eo-table-wrap">
        <table className="eo-table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Rule</th>
              <th>State</th>
              <th>Severity</th>
              <th>Observed</th>
              <th>Threshold</th>
              <th>Delivery</th>
            </tr>
          </thead>
          <tbody>
            {eventsQ.isLoading && (
              <tr>
                <td colSpan={7} className="eo-empty">
                  Loading…
                </td>
              </tr>
            )}
            {!eventsQ.isLoading && events.length === 0 && (
              <tr>
                <td colSpan={7} className="eo-empty">
                  No alarm events {stateFilter ? `with state ${stateFilter}` : "yet"}.
                </td>
              </tr>
            )}
            {events.map((e) => (
              <tr key={e.id}>
                <td>{fmtRel(e.startedAt)}</td>
                <td>
                  <strong>{e.ruleName || e.ruleId.slice(0, 8)}</strong>
                  {e.serviceId && (
                    <div className="eo-mute" style={{ fontSize: 11 }}>
                      svc {e.serviceId.slice(0, 8)}
                    </div>
                  )}
                </td>
                <td>
                  <Dot color={STATE_TONES[e.state] ?? "#94a3b8"} />
                  <span style={{ marginLeft: 6 }}>{e.state}</span>
                </td>
                <td>
                  <span
                    className="eo-pill"
                    style={{
                      background: SEVERITY_TONES[e.severity],
                      color: "white",
                    }}
                  >
                    {e.severity}
                  </span>
                </td>
                <td className="mono">{formatNumber(e.observedValue)}</td>
                <td className="mono">{formatNumber(e.threshold)}</td>
                <td>
                  <span
                    className="eo-mute"
                    title={e.lastDeliveryError || ""}
                    style={{ fontSize: 11 }}
                  >
                    {e.deliveryAttempts > 0
                      ? `${e.deliveryAttempts - e.deliveryFailures}/${e.deliveryAttempts} ok`
                      : "—"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pins
// ---------------------------------------------------------------------------

function PinsPanel({
  orgId,
  writable,
}: {
  orgId: string;
  writable: boolean;
}) {
  return (
    <div>
      <div className="eo-card-h" style={{ marginBottom: 8 }}>
        <h4 className="eo-card-title" style={{ fontSize: 14 }}>
          Pin alarms to Overview
        </h4>
        <span className="eo-card-sub">
          Pinned rules show their live state on the unified{" "}
          <Link href="/workspace/" className="eo-link">
            Observe ▸ Overview
          </Link>{" "}
          page so DV users see incidents without leaving the dashboard.
        </span>
      </div>

      <PinSurfaceCard
        orgId={orgId}
        surface="workspace_overview"
        title="Overview pins"
        writable={writable}
      />
    </div>
  );
}

function PinSurfaceCard({
  orgId,
  surface,
  title,
  writable,
}: {
  orgId: string;
  surface: AlarmSurface;
  title: string;
  writable: boolean;
}) {
  const qc = useQueryClient();
  const [pinError, setPinError] = useState<string | null>(null);
  const pinsQ = useQuery({
    queryKey: ["alarm-pins", orgId, surface],
    queryFn: () => fetchAlarmPins(orgId, surface),
  });
  const rulesQ = useQuery({
    queryKey: ["alarm-rules", orgId],
    queryFn: () => fetchAlarmRules(orgId),
  });

  const replace = useMutation({
    mutationFn: (ruleIds: string[]) =>
      replaceAlarmPins(orgId, surface, ruleIds),
    onMutate: () => setPinError(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alarm-pins", orgId] });
      qc.invalidateQueries({ queryKey: ["alarm-overview", orgId] });
    },
    onError: (e: Error) => {
      setPinError(e.message || "Could not update pins");
    },
  });

  const pinnedIds = (pinsQ.data ?? []).map((p) => p.ruleId).filter(Boolean);
  const pinned = new Set(pinnedIds);
  const rules = rulesQ.data ?? [];

  return (
    <div className="eo-card">
      <div className="eo-card-h" style={{ flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <h4 className="eo-card-title" style={{ fontSize: 13 }}>
            {title}
          </h4>
          <p className="eo-card-sub" style={{ marginTop: 4, maxWidth: 520 }}>
            {pinned.size} of {rules.length} rules pinned — click a rule below to pin or unpin.
          </p>
        </div>
        <Link href="/workspace/" className="eo-btn eo-btn-ghost" style={{ flexShrink: 0, fontSize: 11 }}>
          Open Overview →
        </Link>
      </div>
      {pinError && (
        <div className="eo-empty" style={{ marginBottom: 8, color: "var(--eo-err)" }}>
          {pinError}
        </div>
      )}
      {!writable && (
        <div className="eo-mute" style={{ fontSize: 12, marginBottom: 8 }}>
          Read-only — only SA, platform admins, or org PO can change pins.
        </div>
      )}
      {rules.length === 0 ? (
        <div className="eo-empty">
          No rules to pin yet.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {rules.map((r) => {
            const on = pinned.has(r.id);
            return (
              <Fragment key={r.id}>
                <button
                  type="button"
                  className="eo-btn eo-btn-ghost"
                  data-active={on}
                  disabled={!writable || replace.isPending}
                  onClick={() => {
                    const next = new Set(pinned);
                    if (on) next.delete(r.id);
                    else next.add(r.id);
                    replace.mutate(Array.from(next));
                  }}
                  style={{
                    justifyContent: "space-between",
                    width: "100%",
                    textAlign: "left",
                    fontWeight: on ? 600 : 500,
                    borderColor: on ? "var(--eo-accent)" : undefined,
                    background: on ? "var(--eo-accent-soft)" : undefined,
                  }}
                >
                  <span>{r.name}</span>
                  <span className="eo-mute" style={{ fontSize: 11 }}>
                    {r.signalKind}
                  </span>
                </button>
              </Fragment>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Common bits
// ---------------------------------------------------------------------------

function Dot({ ok, color }: { ok?: boolean; color?: string }) {
  const c = color ?? (ok ? "#22c55e" : "#94a3b8");
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: 999,
        background: c,
        boxShadow: `0 0 0 2px ${c}33`,
      }}
    />
  );
}

function formatNumber(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Number.isInteger(n)) return String(n);
  if (Math.abs(n) >= 100) return n.toFixed(1);
  return n.toFixed(3);
}
