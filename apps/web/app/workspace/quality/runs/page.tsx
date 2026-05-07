"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Fragment, Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  rangeKey,
  resolveRange,
  useWorkspace,
  windowLabel,
} from "@/lib/context";
import {
  createEvalRun,
  estimateEvalRun,
  fetchEvalProfiles,
  fetchEvalRuns,
  fetchHumanLabelAnnotations,
  fetchTraces,
  fetchJudgeModels,
  type EvalProfile,
  type EvalRun,
  type EvalRunMode,
  type HumanLabelAnnotation,
  type RunEstimate,
  type TraceListItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtPct, fmtPrice, fmtRel, truncate } from "@/lib/format";
import { canMutateQuality, QualityGuard, ScopeBanner, WriteHint } from "../guard";
import { useI18n } from "@/lib/i18n/context";
import { buildCsv, triggerCsvDownload } from "@/lib/csv-export";
import { RunStatusHub } from "./status-hub";
import { WorkbenchRunDetail } from "./run-detail";
import {
  SourceCards,
  SOURCES,
  useSourceLabel,
  WorkbenchKpiStrip,
  type RunSource,
} from "./workbench-strip";

export default function RunsPage() {
  return (
    <QualityGuard>
      <Suspense fallback={<div className="eo-empty">Loading runs…</div>}>
        <Inner />
      </Suspense>
    </QualityGuard>
  );
}

function Inner() {
  const { t, tsub, locale } = useI18n();
  const auth = useAuth();
  const orgId = auth.currentOrg?.id ?? "";
  const writable = canMutateQuality(auth);
  const ws = useWorkspace();
  const search = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const range = resolveRange(ws);
  const rk = rangeKey(ws);

  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    search.get("run"),
  );
  // Three-way segmented control: launch new runs, browse/inspect results,
  // monitor background tasks. We deliberately split the launcher off so the
  // launch flow has full vertical space and the result list isn't competing
  // for attention.
  type WorkbenchTab = "launch" | "results" | "background";
  const tabFromSearch = (raw: string | null): WorkbenchTab => {
    if (raw === "bg" || raw === "background") return "background";
    if (raw === "results") return "results";
    if (raw === "launch") return "launch";
    return search.get("run") ? "results" : "launch";
  };
  const [view, setView] = useState<WorkbenchTab>(tabFromSearch(search.get("tab")));
  // ``?prefill-trace=<id>`` lands on Single Trace mode with the trace id
  // pre-filled — the typical entry point from the Trace Inspector or the
  // Full-view CTA. ``?src=`` overrides only when explicitly provided.
  const initialPrefill = search.get("prefill-trace") ?? "";
  const [source, setSource] = useState<RunSource>(
    (search.get("src") as RunSource) ??
      (initialPrefill ? "single_trace" : "window"),
  );

  useEffect(() => {
    setSelectedRunId(search.get("run"));
    setView(tabFromSearch(search.get("tab")));
    const s = search.get("src");
    if (s && SOURCES.some((x) => x.id === s)) setSource(s as RunSource);
    else if (search.get("prefill-trace")) setSource("single_trace");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const switchView = (next: WorkbenchTab) => {
    setView(next);
    const sp = new URLSearchParams(search.toString());
    if (next === "launch") sp.delete("tab");
    else sp.set("tab", next === "background" ? "bg" : next);
    if (selectedRunId && next === "results") sp.set("run", selectedRunId);
    if (next === "launch") sp.delete("run");
    sp.set("src", source);
    const qs = sp.toString();
    router.replace(`/workspace/quality/runs/${qs ? `?${qs}` : ""}`);
  };

  const switchSource = (next: RunSource) => {
    setSource(next);
    const def = SOURCES.find((s) => s.id === next);
    if (def) setRunMode(def.runMode);
    const sp = new URLSearchParams(search.toString());
    sp.set("src", next);
    if (selectedRunId) sp.set("run", selectedRunId);
    const qs = sp.toString();
    router.replace(`/workspace/quality/runs/${qs ? `?${qs}` : ""}`);
  };

  const profiles = useQuery({
    queryKey: ["eval", "profiles"],
    queryFn: () => fetchEvalProfiles(true),
  });
  const runs = useQuery({
    queryKey: ["eval", "runs"],
    queryFn: () => fetchEvalRuns(100),
    refetchInterval: 10_000,
  });
  const traces = useQuery({
    queryKey: ["traces", "for-eval", rk],
    queryFn: () => fetchTraces(range, { limit: 2000, withLlm: true }),
    staleTime: 30_000,
  });
  const humanLabels = useQuery({
    queryKey: ["eval", "human-labels"],
    queryFn: () => fetchHumanLabelAnnotations(200),
    enabled: !!orgId,
  });
  const judgeModelsReg = useQuery({
    queryKey: ["eval", "judges", "all"],
    queryFn: () => fetchJudgeModels(true),
    enabled: !!orgId,
  });
  const hasOrgJudges = useMemo(
    () => (judgeModelsReg.data ?? []).some((j) => j.enabled),
    [judgeModelsReg.data],
  );

  const profileFromQuery = search.get("profile");
  const [profileId, setProfileId] = useState<string>(profileFromQuery ?? "");
  useEffect(() => {
    if (profileFromQuery) setProfileId(profileFromQuery);
  }, [profileFromQuery]);

  const [pickedTraceIds, setPickedTraceIds] = useState<Record<string, boolean>>({});
  const [collapsedSessions, setCollapsedSessions] = useState<Set<string>>(
    () => new Set(),
  );
  const [notes, setNotes] = useState("");
  const [runMode, setRunMode] = useState<EvalRunMode>("trace");
  const [singleTraceId, setSingleTraceId] = useState(initialPrefill);
  const [sessionFilter, setSessionFilter] = useState("");
  // Selection state for the human-labelled cohort source.
  const [pickedHumanLabels, setPickedHumanLabels] = useState<Set<string>>(
    new Set(),
  );
  const [humanVerdictFilter, setHumanVerdictFilter] = useState<
    "all" | "pass" | "warn" | "fail"
  >("all");

  // Apply prefill on subsequent navigation (e.g. user clicks the inspector
  // CTA again with a different trace id while already on this page).
  useEffect(() => {
    const v = search.get("prefill-trace");
    if (v && v !== singleTraceId) setSingleTraceId(v);
  }, [search, singleTraceId]);
  const [estimate, setEstimate] = useState<RunEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProfile = useMemo<EvalProfile | undefined>(
    () => profiles.data?.find((p) => p.id === profileId),
    [profiles.data, profileId],
  );

  const filteredTraces = useMemo<TraceListItem[]>(() => {
    const list = traces.data ?? [];
    const projectId = selectedProfile?.projectId;
    let scoped = projectId ? list.filter((tr) => tr.serviceId === projectId) : list;
    if (source === "session" && sessionFilter.trim()) {
      const k = sessionFilter.trim().toLowerCase();
      scoped = scoped.filter((tr) => (tr.session ?? "").toLowerCase().includes(k));
    }
    return scoped;
  }, [traces.data, selectedProfile, source, sessionFilter]);

  const displayTraces = useMemo<TraceListItem[]>(() => {
    const q = ws.search.trim().toLowerCase();
    if (!q) return filteredTraces;
    return filteredTraces.filter((tr) =>
      `${tr.traceId} ${tr.rootName} ${tr.serviceName} ${tr.session ?? ""} ${tr.model ?? ""}`
        .toLowerCase()
        .includes(q),
    );
  }, [filteredTraces, ws.search]);

  const selectedTraceIds = useMemo(() => {
    if (source === "single_trace") {
      return singleTraceId.trim() ? [singleTraceId.trim()] : [];
    }
    if (source === "human_label") {
      return Array.from(pickedHumanLabels);
    }
    return Object.keys(pickedTraceIds).filter((k) => pickedTraceIds[k]);
  }, [source, singleTraceId, pickedTraceIds, pickedHumanLabels]);

  useEffect(() => {
    if (source === "single_trace" || source === "human_label") return;
    setPickedTraceIds({});
  }, [rk, profileId, source, displayTraces]);

  useEffect(() => {
    if (!hasOrgJudges && runMode === "golden_judge") {
      setRunMode("trace");
    }
  }, [hasOrgJudges, runMode]);

  const tracesBySession = useMemo(() => {
    const m = new Map<string, TraceListItem[]>();
    for (const tr of displayTraces) {
      const key = tr.session?.trim() || "— (no session)";
      const arr = m.get(key) ?? [];
      arr.push(tr);
      m.set(key, arr);
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [displayTraces]);

  const estimateRun = useMutation({
    mutationFn: () =>
      estimateEvalRun({
        profileId,
        subjectCount: Math.max(1, selectedTraceIds.length),
        projectId: selectedProfile?.projectId ?? null,
      }),
    onSuccess: (e) => {
      setEstimate(e);
      setError(null);
    },
    onError: (e: Error) => {
      setEstimate(null);
      setError(e.message);
    },
  });

  const launchRun = useMutation({
    mutationFn: () => {
      // Run mode is derived from the selected source — operators no longer
      // pick a mode by hand, the source card already says what they want.
      const sourceDef = SOURCES.find((s) => s.id === source);
      const inferredMode = sourceDef?.runMode ?? "trace";
      // Golden Set + judge requires the org to have a registered judge
      // model; without judges we fall back to the GT-only flow.
      const finalMode =
        inferredMode === "golden_judge" && !hasOrgJudges
          ? "golden_gt"
          : inferredMode;
      return createEvalRun({
        profileId,
        projectId: selectedProfile?.projectId ?? null,
        traceIds: selectedTraceIds,
        notes,
        runMode: finalMode,
        goldenSetId: null,
        runContext: {
          uiLocale: locale,
          source,
          uiSource: source === "human_label" ? "human_label_group" : source,
        },
      });
    },
    onSuccess: (run) => {
      setError(null);
      setSelectedRunId(run.id);
      router.replace(`/workspace/quality/runs/?run=${encodeURIComponent(run.id)}`);
      qc.invalidateQueries({ queryKey: ["eval", "runs"] });
      qc.invalidateQueries({ queryKey: ["quality", "overview"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const selectAllInRange = () => {
    const next: Record<string, boolean> = {};
    for (const tr of displayTraces) next[tr.traceId] = true;
    setPickedTraceIds(next);
  };
  const toggleSessionExpand = (sessionKey: string) => {
    setCollapsedSessions((prev) => {
      const n = new Set(prev);
      if (n.has(sessionKey)) n.delete(sessionKey);
      else n.add(sessionKey);
      return n;
    });
  };
  const setSessionChecked = (sessionTraces: TraceListItem[], checked: boolean) => {
    setPickedTraceIds((m) => {
      const next = { ...m };
      for (const tr of sessionTraces) next[tr.traceId] = checked;
      return next;
    });
  };
  const clearTraceSelection = () => setPickedTraceIds({});

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.runs.title")}</h1>
          <p className="eo-page-lede">
            {t("pages.runs.lede")}
          </p>
        </div>
      </div>

      <ScopeBanner />
      <WriteHint />

      <WorkbenchKpiStrip runs={runs.data ?? []} />

      <div
        className="eo-tabs"
        role="tablist"
        aria-label="Workbench sections"
      >
        <button
          type="button"
          role="tab"
          className="eo-tab"
          data-active={view === "launch"}
          aria-selected={view === "launch"}
          onClick={() => switchView("launch")}
        >
          {t("pages.runs.tabRuns")}
          <span className="eo-tab-meta">
            {t("pages.runs.tabRunsMeta")}
          </span>
        </button>
        <button
          type="button"
          role="tab"
          className="eo-tab"
          data-active={view === "results"}
          aria-selected={view === "results"}
          onClick={() => switchView("results")}
        >
          {t("pages.runs.tabResults")}
          <span className="eo-tab-meta">
            {tsub("pages.runs.tabResultsMeta", { count: String(runs.data?.length ?? 0) })}
          </span>
        </button>
        <button
          type="button"
          role="tab"
          className="eo-tab"
          data-active={view === "background"}
          aria-selected={view === "background"}
          onClick={() => switchView("background")}
        >
          {t("pages.runs.tabBackground")}
          <span className="eo-tab-meta">
            {t("pages.runs.tabBackgroundMeta")}
          </span>
        </button>
      </div>

      {view === "background" && (
        <RunStatusHub
          selectedRunId={selectedRunId}
          onSelect={(id) => {
            setSelectedRunId(id);
            const sp = new URLSearchParams(search.toString());
            sp.set("run", id);
            sp.set("tab", "results");
            setView("results");
            router.replace(`/workspace/quality/runs/?${sp.toString()}`);
          }}
        />
      )}

      {view === "launch" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <SourceCards
            active={source}
            onPick={switchSource}
          />

          <LaunchCard
            source={source}
            profiles={profiles.data ?? []}
            profileId={profileId}
            setProfileId={setProfileId}
            selectedProfile={selectedProfile}
            displayTraces={displayTraces}
            tracesBySession={tracesBySession}
            collapsedSessions={collapsedSessions}
            pickedTraceIds={pickedTraceIds}
            setPickedTraceIds={setPickedTraceIds}
            toggleSessionExpand={toggleSessionExpand}
            setSessionChecked={setSessionChecked}
            singleTraceId={singleTraceId}
            setSingleTraceId={setSingleTraceId}
            sessionFilter={sessionFilter}
            setSessionFilter={setSessionFilter}
            selectedTraceIds={selectedTraceIds}
            selectAllInRange={selectAllInRange}
            clearTraceSelection={clearTraceSelection}
            notes={notes}
            setNotes={setNotes}
            runMode={runMode}
            humanLabelList={humanLabels.data ?? []}
            pickedHumanLabels={pickedHumanLabels}
            setPickedHumanLabels={setPickedHumanLabels}
            humanVerdictFilter={humanVerdictFilter}
            setHumanVerdictFilter={setHumanVerdictFilter}
            estimate={estimate}
            error={error}
            writable={writable}
            hasOrgJudges={hasOrgJudges}
            ws={ws}
            filteredCount={filteredTraces.length}
            setEstimate={setEstimate}
            estimateRunPending={estimateRun.isPending}
            estimateRunMutate={() => estimateRun.mutate()}
            launchRunPending={launchRun.isPending}
            launchRunMutate={() => launchRun.mutate()}
          />
        </div>
      )}

      {view === "results" && (
        <RunList
          runs={runs.data ?? []}
          selectedRunId={selectedRunId}
          onSelect={(id) => {
            setSelectedRunId(id);
            const sp = new URLSearchParams(search.toString());
            sp.set("run", id);
            sp.set("tab", "results");
            router.replace(`/workspace/quality/runs/?${sp.toString()}`);
          }}
        />
      )}

      {selectedRunId && (view === "results" || view === "background") && (
        <RunDetailDrawer
          runId={selectedRunId}
          onClose={() => {
            setSelectedRunId(null);
            const sp = new URLSearchParams(search.toString());
            sp.delete("run");
            const qs = sp.toString();
            router.replace(
              `/workspace/quality/runs/${qs ? `?${qs}` : ""}`,
            );
          }}
        />
      )}
    </>
  );
}

function RunDetailDrawer({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  // Close on Escape (matches the Tracing inspector behaviour).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="eo-side-drawer-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Run detail"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="eo-side-drawer">
        <div className="eo-side-drawer-h">
          <span className="eo-side-drawer-title">
            {t("pages.runs.drawerTitle")}
            <span
              className="eo-mute mono"
              style={{ marginLeft: 8, fontSize: 11 }}
            >
              {runId.slice(0, 12)}
            </span>
          </span>
          <button
            type="button"
            className="eo-side-drawer-close"
            onClick={onClose}
            aria-label="Close run detail"
          >
            ×
          </button>
        </div>
        <div className="eo-side-drawer-body">
          <WorkbenchRunDetail runId={runId} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Launch card — source-aware
// ---------------------------------------------------------------------------

type LaunchCardProps = {
  source: RunSource;
  profiles: EvalProfile[];
  profileId: string;
  setProfileId: (s: string) => void;
  selectedProfile: EvalProfile | undefined;
  displayTraces: TraceListItem[];
  tracesBySession: [string, TraceListItem[]][];
  collapsedSessions: Set<string>;
  pickedTraceIds: Record<string, boolean>;
  setPickedTraceIds: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  toggleSessionExpand: (k: string) => void;
  setSessionChecked: (g: TraceListItem[], checked: boolean) => void;
  singleTraceId: string;
  setSingleTraceId: (s: string) => void;
  sessionFilter: string;
  setSessionFilter: (s: string) => void;
  selectedTraceIds: string[];
  selectAllInRange: () => void;
  clearTraceSelection: () => void;
  notes: string;
  setNotes: (s: string) => void;
  runMode: EvalRunMode;
  humanLabelList: HumanLabelAnnotation[];
  pickedHumanLabels: Set<string>;
  setPickedHumanLabels: React.Dispatch<React.SetStateAction<Set<string>>>;
  humanVerdictFilter: "all" | "pass" | "warn" | "fail";
  setHumanVerdictFilter: (v: "all" | "pass" | "warn" | "fail") => void;
  estimate: RunEstimate | null;
  setEstimate: (e: RunEstimate | null) => void;
  error: string | null;
  writable: boolean;
  hasOrgJudges: boolean;
  ws: ReturnType<typeof useWorkspace>;
  filteredCount: number;
  estimateRunPending: boolean;
  estimateRunMutate: () => void;
  launchRunPending: boolean;
  launchRunMutate: () => void;
};

function runModeLabel(
  mode: EvalRunMode,
  t: (key: string) => string,
): string {
  switch (mode) {
    case "golden_gt":
      return t("pages.runs.modeGoldenGt");
    case "golden_judge":
      return t("pages.runs.modeGoldenJudge");
    case "human_label":
      return t("pages.runs.modeHumanLabel");
    default:
      return t("pages.runs.modeTrace");
  }
}

function LaunchCard(props: LaunchCardProps) {
  const { t, tsub } = useI18n();
  const labelOf = useSourceLabel();
  const def = labelOf(props.source);
  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {def.icon} {def.title} — {t("pages.runs.launch")}
        </h3>
        <span className="eo-card-sub">{def.hint}</span>
      </div>

      <label className="eo-field">
        <span>{t("pages.runs.profile")}</span>
        <select
          value={props.profileId}
          onChange={(e) => {
            props.setProfileId(e.target.value);
            props.setEstimate(null);
          }}
          disabled={!props.writable}
        >
          <option value="">{t("pages.runs.pickProfile")}</option>
          {props.profiles
            .filter((p) => p.enabled)
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.judgeModels.length === 0 ? " (rules only)" : ""}
              </option>
            ))}
        </select>
      </label>

      {props.source === "single_trace" && (
        <label className="eo-field">
          <span>Trace ID</span>
          <input
            value={props.singleTraceId}
            onChange={(e) => props.setSingleTraceId(e.target.value)}
            placeholder={t("pages.runs.singleTracePlaceholder")}
            disabled={!props.writable}
          />
        </label>
      )}

      {props.source === "session" && (
        <label className="eo-field">
          <span>{t("pages.runs.sessionFilter")}</span>
          <input
            value={props.sessionFilter}
            onChange={(e) => props.setSessionFilter(e.target.value)}
            placeholder={t("pages.runs.sessionFilterPlaceholder")}
            disabled={!props.writable}
          />
        </label>
      )}

      {props.source === "human_label" && (
        <HumanLabelCohortPicker
          rows={props.humanLabelList}
          picked={props.pickedHumanLabels}
          setPicked={props.setPickedHumanLabels}
          filter={props.humanVerdictFilter}
          setFilter={props.setHumanVerdictFilter}
          writable={props.writable}
        />
      )}

      {(props.source === "window" || props.source === "session") && (
        <>
          <div
            className="eo-card-sub"
            style={{
              marginTop: 6,
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              alignItems: "center",
            }}
          >
            <span>
              {t("pages.runs.tracesInWindow")} (
              {windowLabel(props.ws)}
              {props.selectedProfile?.projectId
                ? ` · service ${props.selectedProfile.projectId.slice(0, 8)}`
                : ""}
              ){props.ws.search.trim() ? ` · search "${props.ws.search.trim()}"` : ""} ·{" "}
              {props.displayTraces.length} {t("pages.runs.shownOf")}{" "}
              {props.filteredCount})
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button
                type="button"
                className="eo-btn eo-btn-select-action"
                onClick={props.selectAllInRange}
                disabled={!props.writable || props.displayTraces.length === 0}
              >
                {t("pages.runs.selectAllVisible")}
              </button>
              <button
                type="button"
                className="eo-btn eo-btn-clear-action"
                onClick={props.clearTraceSelection}
                disabled={!props.writable || props.selectedTraceIds.length === 0}
              >
                {t("pages.runs.clear")}
              </button>
            </span>
          </div>
          <div className="eo-table-wrap" style={{ maxHeight: 320, overflow: "auto" }}>
            <table className="eo-table">
              <thead>
                <tr>
                  <th />
                  <th>Session / trace</th>
                  <th>Service</th>
                  <th>Status</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {props.displayTraces.length === 0 && (
                  <tr>
                    <td colSpan={5}>
                      <div className="eo-empty">
                        No traces in this window — widen the range or clear search.
                      </div>
                    </td>
                  </tr>
                )}
                {props.tracesBySession.map(([sessionKey, group]) => {
                  const expanded = !props.collapsedSessions.has(sessionKey);
                  const allOn = group.every((tr) => props.pickedTraceIds[tr.traceId]);
                  const someOn = group.some((tr) => props.pickedTraceIds[tr.traceId]);
                  return (
                    <Fragment key={sessionKey}>
                      <tr style={{ background: "var(--eo-bg-2)" }}>
                        <td>
                          <input
                            type="checkbox"
                            checked={allOn}
                            ref={(el) => {
                              if (el) el.indeterminate = !allOn && someOn;
                            }}
                            disabled={!props.writable}
                            onChange={(e) =>
                              props.setSessionChecked(group, e.target.checked)
                            }
                          />
                        </td>
                        <td colSpan={3}>
                          <button
                            type="button"
                            className="eo-btn eo-btn-ghost"
                            style={{ padding: "2px 8px", fontWeight: 600 }}
                            onClick={() => props.toggleSessionExpand(sessionKey)}
                          >
                            {expanded ? "▼" : "▶"} Session{" "}
                            <span className="mono">{truncate(sessionKey, 36)}</span>{" "}
                            <span className="eo-mute">({group.length})</span>
                          </button>
                        </td>
                        <td className="eo-mute" style={{ fontSize: 11 }}>
                          {expanded ? "" : "collapsed"}
                        </td>
                      </tr>
                      {expanded &&
                        group.map((tr) => (
                          <tr key={tr.traceId}>
                            <td>
                              <input
                                type="checkbox"
                                checked={!!props.pickedTraceIds[tr.traceId]}
                                disabled={!props.writable}
                                onChange={(e) =>
                                  props.setPickedTraceIds((m) => ({
                                    ...m,
                                    [tr.traceId]: e.target.checked,
                                  }))
                                }
                              />
                            </td>
                            <td className="mono" style={{ paddingLeft: 20 }}>
                              {tr.traceId.slice(0, 12)}
                            </td>
                            <td>{tr.serviceName}</td>
                            <td>
                              <span
                                className="eo-status"
                                data-tone={tr.status === "ERROR" ? "err" : "ok"}
                              >
                                {tr.status}
                              </span>
                            </td>
                            <td>{fmtRel(tr.startedAt)}</td>
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <label className="eo-field">
        <span>{t("pages.runs.notes")}</span>
        <input
          value={props.notes}
          onChange={(e) => props.setNotes(e.target.value)}
          placeholder={t("pages.runs.notesPlaceholder")}
          disabled={!props.writable}
        />
      </label>

      <div
        className="eo-mute"
        style={{ fontSize: 11, marginTop: 4, marginBottom: 8 }}
      >
        {tsub("pages.runs.runModeAutoSelected", { mode: runModeLabel(props.runMode, t) })}
      </div>

      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="eo-btn"
          onClick={props.estimateRunMutate}
          disabled={
            !props.writable ||
            !props.profileId ||
            props.selectedTraceIds.length === 0 ||
            props.estimateRunPending
          }
        >
          {t("pages.runs.estimateCost")}
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          onClick={props.launchRunMutate}
          disabled={
            !props.writable ||
            !props.profileId ||
            props.selectedTraceIds.length === 0 ||
            props.launchRunPending
          }
        >
          {props.launchRunPending
            ? t("pages.runs.launching")
            : tsub("pages.runs.runOnSubjects", { count: String(props.selectedTraceIds.length) })}
        </button>
      </div>

      {props.estimate && (
        <div
          className="eo-card"
          style={{ background: "var(--eo-bg-2)", marginTop: 8 }}
        >
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("pages.runs.estimate")}</h3>
          </div>
          <div className="eo-mute" style={{ fontSize: 12 }}>
            {t("pages.runs.subjects")} {props.estimate.subjectCount} · {t("pages.runs.judgeCalls")}{" "}
            {props.estimate.judgeCalls} · {t("pages.runs.projected")}{" "}
            {fmtPrice(props.estimate.costEstimateUsd)}
            {props.estimate.ruleOnly ? t("pages.runs.rulesOnly") : ""}
          </div>
          <div className="eo-mute" style={{ fontSize: 12 }}>
            {tsub("pages.runs.monthlySpent", { amount: fmtPrice(props.estimate.monthlySpentUsd) })}
          </div>
          {!props.estimate.costGuard.allowed && (
            <div
              className="eo-empty"
              style={{ color: "var(--eo-err)", marginTop: 6 }}
            >
              {t("pages.runs.costGuardBlock")}: {props.estimate.costGuard.note}
            </div>
          )}
          {props.estimate.costGuard.allowed && props.estimate.costGuard.downgrade && (
            <div
              className="eo-empty"
              style={{ color: "var(--eo-warn, #c89400)", marginTop: 6 }}
            >
              {t("pages.runs.costGuardDowngrade")}: {props.estimate.costGuard.note}
            </div>
          )}
        </div>
      )}

      {props.error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {props.error}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Human-labelled cohort picker (source = human_label)
// ---------------------------------------------------------------------------

function HumanLabelCohortPicker({
  rows,
  picked,
  setPicked,
  filter,
  setFilter,
  writable,
}: {
  rows: HumanLabelAnnotation[];
  picked: Set<string>;
  setPicked: React.Dispatch<React.SetStateAction<Set<string>>>;
  filter: "all" | "pass" | "warn" | "fail";
  setFilter: (v: "all" | "pass" | "warn" | "fail") => void;
  writable: boolean;
}) {
  const { t, tsub } = useI18n();
  const visible = useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => r.humanVerdict === filter);
  }, [rows, filter]);

  const toggle = (traceId: string) =>
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(traceId)) n.delete(traceId);
      else n.add(traceId);
      return n;
    });
  const pickAll = () =>
    setPicked((prev) => {
      const n = new Set(prev);
      for (const r of visible) n.add(r.traceId);
      return n;
    });
  const clear = () => setPicked(new Set());

  return (
    <div style={{ marginBottom: 8 }}>
      <div
        className="eo-card-sub"
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 6,
        }}
      >
        <span>
          {tsub("pages.runs.humanLabelSummary", { registered: String(rows.length), selected: String(picked.size) })}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          <select
            className="eo-input"
            value={filter}
            onChange={(e) =>
              setFilter(e.target.value as "all" | "pass" | "warn" | "fail")
            }
            style={{ minWidth: 110 }}
          >
            <option value="all">{t("pages.runs.allVerdicts")}</option>
            <option value="pass">pass</option>
            <option value="warn">warn</option>
            <option value="fail">fail</option>
          </select>
          <button
            type="button"
            className="eo-btn eo-btn-ghost"
            onClick={pickAll}
            disabled={!writable || visible.length === 0}
          >
            {tsub("pages.runs.selectVisible", { count: String(visible.length) })}
          </button>
          <button
            type="button"
            className="eo-btn eo-btn-ghost"
            onClick={clear}
            disabled={!writable || picked.size === 0}
          >
            {t("pages.runs.clear")}
          </button>
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="eo-empty">
          {t("pages.runs.noHumanLabels")}
        </div>
      ) : (
        <div className="eo-table-wrap" style={{ maxHeight: 280, overflow: "auto" }}>
          <table className="eo-table">
            <thead>
              <tr>
                <th />
                <th>{t("pages.runs.colTrace")}</th>
                <th>{t("pages.runs.colVerdict")}</th>
                <th>{t("pages.runs.colExpected")}</th>
                <th>{t("pages.runs.colUpdated")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => {
                const tone =
                  r.humanVerdict === "fail"
                    ? "err"
                    : r.humanVerdict === "warn"
                      ? "warn"
                      : "ok";
                return (
                  <tr
                    key={r.id}
                    onClick={() => writable && toggle(r.traceId)}
                    style={{ cursor: writable ? "pointer" : "default" }}
                  >
                    <td>
                      <input
                        type="checkbox"
                        checked={picked.has(r.traceId)}
                        disabled={!writable}
                        onChange={() => toggle(r.traceId)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                    <td className="mono">{r.traceId.slice(0, 16)}</td>
                    <td>
                      <span className="eo-status" data-tone={tone}>
                        {r.humanVerdict ?? "—"}
                      </span>
                    </td>
                    <td className="eo-mute" style={{ fontSize: 12 }}>
                      {r.expectedResponse
                        ? r.expectedResponse.slice(0, 80) +
                          (r.expectedResponse.length > 80 ? "…" : "")
                        : "—"}
                    </td>
                    <td>{fmtRel(r.updatedAt)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run List — columns: source / trust / pass-fail bar / trigger
// ---------------------------------------------------------------------------

function RunList({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: EvalRun[];
  selectedRunId: string | null;
  onSelect: (id: string) => void;
}) {
  const { t, tsub } = useI18n();
  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.runs.runListTitle")}</h3>
        <span className="eo-card-sub">
          {tsub("pages.runs.runListSub", { count: String(runs.length) })}
        </span>
        <button
          type="button"
          className="eo-btn"
          style={{ marginLeft: "auto" }}
          disabled={runs.length === 0}
          onClick={() => {
            const headers = [
              "run_id", "status", "run_mode", "trigger_lane", "subject_count",
              "completed_count", "failed_count", "pass_rate", "avg_score",
              "cost_estimate_usd", "cost_actual_usd", "profile_id", "project_id",
              "golden_set_id", "notes", "started_at", "finished_at",
            ];
            const rows = runs.map((r) => [
              r.id, r.status, r.runMode ?? "", r.triggerLane, r.subjectCount,
              r.completedCount, r.failedCount, r.passRate, r.avgScore,
              r.costEstimateUsd, r.costActualUsd, r.profileId ?? "", r.projectId ?? "",
              r.goldenSetId ?? "", r.notes, r.startedAt, r.finishedAt ?? "",
            ]);
            triggerCsvDownload("eval-runs.csv", buildCsv(headers, rows));
          }}
        >
          CSV
        </button>
      </div>
      <div className="eo-table-wrap" style={{ maxHeight: 480, overflow: "auto" }}>
        <table className="eo-table">
          <thead>
            <tr>
              <th>{t("pages.runs.colRun")}</th>
              <th>{t("pages.runs.colSource")}</th>
              <th>{t("pages.runs.colStatus")}</th>
              <th>{t("pages.runs.colTrust")}</th>
              <th>{t("pages.runs.colPassFail")}</th>
              <th>{t("pages.runs.colSubjects")}</th>
              <th>{t("pages.runs.colTrigger")}</th>
              <th>{t("pages.runs.colCost")}</th>
              <th>{t("pages.runs.colStarted")}</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={9}>
                  <div className="eo-empty">{t("pages.runs.noRunsYet")}</div>
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <RunListRow
                key={r.id}
                run={r}
                active={r.id === selectedRunId}
                onClick={() => onSelect(r.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunListRow({
  run,
  active,
  onClick,
}: {
  run: EvalRun;
  active: boolean;
  onClick: () => void;
}) {
  const tone =
    run.status === "succeeded"
      ? "ok"
      : run.status === "failed"
        ? "err"
        : "warn";
  const sourceMeta = inferSource(run);
  const passPct = Math.max(0, Math.min(100, run.passRate * 100));
  const failPct =
    run.subjectCount === 0 ? 0 : (run.failedCount / run.subjectCount) * 100;
  const trustStars = Math.round(
    Math.min(1, run.subjectCount / 50) * 5,
  );
  return (
    <tr
      data-active={active}
      onClick={onClick}
      style={{ cursor: "pointer" }}
    >
      <td className="mono">{run.id.slice(0, 8)}</td>
      <td>
        <span className="eo-tag" data-tone="ink">
          {sourceMeta.icon} {sourceMeta.label}
        </span>
      </td>
      <td>
        <span className="eo-status" data-tone={tone}>
          {run.status}
        </span>
      </td>
      <td>
        <span
          className="eo-status"
          data-tone={trustStars >= 4 ? "ok" : trustStars >= 2 ? "warn" : "err"}
          style={{ fontSize: 12 }}
        >
          {"★".repeat(trustStars)}
          {"☆".repeat(5 - trustStars)}
        </span>
      </td>
      <td>
        <PassFailBar passPct={passPct} failPct={failPct} />
      </td>
      <td className="mono">{run.subjectCount}</td>
      <td>
        <span className="eo-tag" data-tone={run.triggerLane === "rule_auto" ? "ok" : "ink"}>
          {run.triggerLane}
        </span>
      </td>
      <td className="mono">{fmtPrice(run.costActualUsd)}</td>
      <td>{fmtRel(run.startedAt)}</td>
    </tr>
  );
}

function inferSource(run: EvalRun): { icon: string; label: string } {
  if (run.runMode === "golden_gt" || run.runMode === "golden_judge") {
    return { icon: "★", label: "Golden" };
  }
  if (run.runMode === "human_label") {
    const ctx = (run.runContext ?? {}) as { uiSource?: unknown; source?: unknown };
    const ui = String(ctx.uiSource ?? ctx.source ?? "");
    if (ui === "human_label_group" || ui === "human_label") {
      return { icon: "✎", label: "Human" };
    }
    return { icon: "⇪", label: "Upload" };
  }
  if (run.subjectCount === 1) return { icon: "◇", label: "Trace" };
  return { icon: "◫", label: "Window" };
}

function PassFailBar({ passPct, failPct }: { passPct: number; failPct: number }) {
  const otherPct = Math.max(0, 100 - passPct - failPct);
  return (
    <div
      style={{
        display: "flex",
        height: 10,
        width: 120,
        background: "var(--eo-bg-3)",
        borderRadius: 5,
        overflow: "hidden",
      }}
      title={`pass ${passPct.toFixed(1)}% · fail ${failPct.toFixed(1)}%`}
    >
      <div
        style={{
          width: `${passPct}%`,
          background: "var(--eo-ok, #4ade80)",
        }}
      />
      <div
        style={{
          width: `${otherPct}%`,
          background: "var(--eo-warn, #c89400)",
          opacity: 0.6,
        }}
      />
      <div
        style={{
          width: `${failPct}%`,
          background: "var(--eo-err, #ef4444)",
        }}
      />
      <span
        className="eo-mute"
        style={{
          position: "relative",
          left: -118,
          top: -2,
          fontSize: 10,
          mixBlendMode: "difference",
        }}
      >
        {fmtPct(passPct)}
      </span>
    </div>
  );
}
