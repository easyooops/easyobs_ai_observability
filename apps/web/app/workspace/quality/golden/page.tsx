"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addGoldenItem,
  addGoldenItemFromTrace,
  autoDiscoverGoldenItems,
  createGoldenSet,
  deleteGoldenItem,
  deleteGoldenSet,
  fetchGoldenItems,
  fetchGoldenSets,
  fetchOrgServices,
  reviewGoldenItem,
  updateGoldenItemStatus,
  type GoldenItem,
  type GoldenLayer,
  type GoldenSet,
  type GoldenSetMode,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";
import { GoldenLayerLegend } from "../guides";
import { canMutateQuality, QualityGuard, ScopeBanner, WriteHint } from "../guard";
import { useI18n } from "@/lib/i18n/context";
import { GoldenHumanLabelsTab } from "./human-labels-tab";
import {
  GoldenAgentSettingsCard,
  GoldenRevisionsPanel,
} from "./detail-panels";
import { GoldenRegressionPanel } from "./regression-panel";
import { GoldenSynthesizerPanel } from "./synthesizer-panel";
import { GoldenUploadPanel } from "./upload-panel";
import { GoldenTriPanel, GoldenWorkbenchKpiStrip } from "./overview-strip";

const LAYER_OPTIONS: GoldenLayer[] = ["L1", "L2", "L3"];
const STATUS_OPTIONS = ["draft", "approved", "deprecated", "candidate", "active"] as const;

/** "auto" is purely a UI affordance — it tells the form to pick a layer for
 * the operator. The DB column is a strict L1/L2/L3 enum, so we resolve it
 * at submit-time. The default mapping favours L3 (the layer that powers
 * answer-correctness checks, which is the most common case) but also
 * recognises hints in the description to pick L1/L2 instead. */
type LayerChoice = GoldenLayer | "auto";

function recommendLayer(name: string, description: string, mode: GoldenSetMode): GoldenLayer {
  const haystack = `${name} ${description}`.toLowerCase();
  if (
    /retriev|search|recall|chunk|rerank|rag|doc|index/i.test(haystack)
  )
    return "L2";
  if (/intent|route|tool|classif|slot|plan|agent/i.test(haystack)) return "L1";
  if (mode === "cohort") return "L1";
  return "L3";
}

function useModeOptions() {
  const { t } = useI18n();
  return [
    {
      value: "regression" as GoldenSetMode,
      label: t("pages.golden.sets.modeRegression"),
    },
    {
      value: "cohort" as GoldenSetMode,
      label: t("pages.golden.sets.modeCohort"),
    },
    {
      value: "synthesized" as GoldenSetMode,
      label: t("pages.golden.sets.modeSynthesized"),
    },
  ];
}

type DetailTab =
  | "items"
  | "agent"
  | "revisions"
  | "synth"
  | "upload"
  | "regression";

export default function GoldenPage() {
  return (
    <QualityGuard>
      <Inner />
    </QualityGuard>
  );
}

function Inner() {
  const { t, tsub } = useI18n();
  const MODE_OPTIONS = useModeOptions();
  const router = useRouter();
  const search = useSearchParams();
  const labelsTab = search.get("tab") === "labels";
  const auth = useAuth();
  const orgId = auth.currentOrg?.id ?? "";
  const writable = canMutateQuality(auth);
  const qc = useQueryClient();

  const sets = useQuery({
    queryKey: ["eval", "golden-sets"],
    queryFn: fetchGoldenSets,
  });
  const services = useQuery({
    queryKey: ["org", "services", orgId],
    queryFn: () => fetchOrgServices(orgId),
    enabled: !!orgId,
  });

  const accessibleServiceIds = auth.accessibleServiceIds;
  const visibleServices = useMemo(() => {
    const all = services.data ?? [];
    if (accessibleServiceIds == null) return all;
    const allowed = new Set(accessibleServiceIds);
    return all.filter((s) => allowed.has(s.id));
  }, [services.data, accessibleServiceIds]);

  const [activeSetId, setActiveSetId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("items");
  const [creating, setCreating] = useState(false);
  const [newSet, setNewSet] = useState<{
    projectId: string | null;
    name: string;
    layer: LayerChoice;
    mode: GoldenSetMode;
    description: string;
  }>({ projectId: null, name: "", layer: "auto", mode: "regression", description: "" });
  const [error, setError] = useState<string | null>(null);

  const activeSet = useMemo(
    () => sets.data?.find((g) => g.id === activeSetId) ?? null,
    [sets.data, activeSetId],
  );

  const resolvedLayer: GoldenLayer =
    newSet.layer === "auto"
      ? recommendLayer(newSet.name, newSet.description, newSet.mode)
      : newSet.layer;

  const create = useMutation({
    mutationFn: () => {
      if (!newSet.name.trim()) throw new Error("Name is required");
      return createGoldenSet({
        projectId: newSet.projectId,
        name: newSet.name.trim(),
        layer: resolvedLayer,
        mode: newSet.mode,
        description: newSet.description,
      });
    },
    onSuccess: (g) => {
      setError(null);
      setCreating(false);
      setActiveSetId(g.id);
      qc.setQueryData<GoldenSet[]>(["eval", "golden-sets"], (old) => {
        const prev = old ?? [];
        return [g, ...prev.filter((x) => x.id !== g.id)];
      });
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: deleteGoldenSet,
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
      if (id === activeSetId) setActiveSetId(null);
    },
  });

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.golden.title")}</h1>
          <p className="eo-page-lede">{t("pages.golden.ledeDetail")}</p>
        </div>
        <div className="eo-page-meta">
          {!labelsTab && (
            <button
              type="button"
              className="eo-btn eo-btn-primary"
              onClick={() => setCreating(true)}
              disabled={!writable}
            >
              New golden set
            </button>
          )}
        </div>
      </div>
      <div className="eo-seg" style={{ marginBottom: 12, maxWidth: 480 }}>
        <button
          type="button"
          data-active={!labelsTab}
          onClick={() => router.push("/workspace/quality/golden/")}
        >
          {t("pages.golden.tabSets")}
        </button>
        <button
          type="button"
          data-active={labelsTab}
          onClick={() => router.push("/workspace/quality/golden/?tab=labels")}
        >
          {t("pages.golden.tabHumanLabels")}
        </button>
      </div>
      <ScopeBanner />
      <WriteHint />

      {labelsTab ? (
        <>
          <p className="eo-mute" style={{ fontSize: 12, marginBottom: 12, lineHeight: 1.5 }}>
            {t("pages.golden.humanLabelsBlurb")}{" "}
            <Link href="/workspace/quality/runs/" className="eo-link">
              {t("pages.golden.humanLabelsRunLink")}
            </Link>
          </p>
          <GoldenHumanLabelsTab />
        </>
      ) : (
        <>
      <GoldenWorkbenchKpiStrip sets={sets.data ?? []} />
      <GoldenLayerLegend />

      <div className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("pages.golden.sets.tableTitle")}</h3>
          <span className="eo-card-sub">
            {tsub("pages.golden.sets.tableSub", { count: String(sets.data?.length ?? 0) })}
          </span>
        </div>
        <div className="eo-table-wrap">
          <table className="eo-table">
            <thead>
              <tr>
                <th>{t("pages.golden.sets.colName")}</th>
                <th>{t("pages.golden.sets.colMode")}</th>
                <th>{t("pages.golden.sets.colLayer")}</th>
                <th>{t("pages.golden.sets.colAgent")}</th>
                <th>{t("pages.golden.sets.colService")}</th>
                <th>{t("pages.golden.sets.colItems")}</th>
                <th>{t("pages.golden.sets.colCreated")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sets.data?.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div className="eo-empty">
                      {t("pages.golden.sets.empty")}
                    </div>
                  </td>
                </tr>
              )}
              {sets.data?.map((g) => {
                const mode = g.mode ?? "regression";
                const modeTone =
                  mode === "regression"
                    ? "ok"
                    : mode === "cohort"
                      ? "warn"
                      : "ink";
                const hasAgent = !!g.agentInvoke?.endpointUrl?.trim();
                return (
                  <tr
                    key={g.id}
                    onClick={() => {
                      setActiveSetId(g.id);
                      setDetailTab("items");
                    }}
                    data-active={g.id === activeSetId}
                    style={{ cursor: "pointer" }}
                  >
                    <td className="eo-td-name">
                      <div>{g.name}</div>
                      {g.description && (
                        <div className="eo-mute" style={{ fontSize: 11 }}>
                          {g.description}
                        </div>
                      )}
                    </td>
                    <td>
                      <span className="eo-tag" data-tone={modeTone}>
                        {mode}
                      </span>
                    </td>
                    <td>
                      <span className="eo-tag eo-tag-accent">{g.layer}</span>
                    </td>
                    <td>
                      <span
                        className="eo-status"
                        data-tone={hasAgent ? "ok" : "warn"}
                        style={{ fontSize: 11 }}
                      >
                        {hasAgent
                          ? t("pages.golden.sets.agentYes")
                          : t("pages.golden.sets.agentNo")}
                      </span>
                    </td>
                    <td className="mono">
                      {g.projectId
                        ? services.data?.find((s) => s.id === g.projectId)?.name ??
                          g.projectId.slice(0, 8)
                        : "—"}
                    </td>
                    <td className="mono">{g.itemCount}</td>
                    <td>{fmtRel(g.createdAt)}</td>
                    <td>
                      {writable && (
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (
                              confirm(
                                tsub("pages.golden.sets.deleteConfirm", { name: g.name }),
                              )
                            ) {
                              remove.mutate(g.id);
                            }
                          }}
                          disabled={remove.isPending}
                        >
                          {t("pages.golden.sets.delete")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {activeSetId && (
        <GoldenSetDetailDrawer
          set={activeSet}
          writable={writable}
          tab={detailTab}
          onTab={setDetailTab}
          onClose={() => setActiveSetId(null)}
        />
      )}

      {creating && (
        <div className="eo-card" style={{ marginTop: 12 }}>
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("pages.golden.sets.newTitle")}</h3>
            <span className="eo-card-sub">
              {t("pages.golden.sets.newSub")}
            </span>
          </div>
          <div className="eo-grid-2" style={{ gap: 12 }}>
            <label className="eo-field">
              <span>{t("pages.golden.sets.colName")}</span>
              <input
                value={newSet.name}
                onChange={(e) => setNewSet({ ...newSet, name: e.target.value })}
                placeholder={t("pages.golden.sets.namePlaceholder")}
              />
            </label>
            <label className="eo-field">
              <span>{t("pages.golden.sets.colMode")}</span>
              <select
                value={newSet.mode}
                onChange={(e) =>
                  setNewSet({ ...newSet, mode: e.target.value as GoldenSetMode })
                }
              >
                {MODE_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="eo-field">
            <span>{t("pages.golden.sets.description")}</span>
            <input
              value={newSet.description}
              onChange={(e) =>
                setNewSet({ ...newSet, description: e.target.value })
              }
              placeholder={t("pages.golden.sets.descriptionPlaceholder")}
            />
          </label>
          <div className="eo-grid-2" style={{ gap: 12 }}>
            <label className="eo-field">
              <span>{t("pages.golden.sets.colLayer")}</span>
              <select
                value={newSet.layer}
                onChange={(e) =>
                  setNewSet({
                    ...newSet,
                    layer: e.target.value as LayerChoice,
                  })
                }
              >
                <option value="auto">
                  {tsub("pages.golden.sets.layerAuto", { layer: resolvedLayer })}
                </option>
                {LAYER_OPTIONS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="eo-field">
              <span>{t("pages.golden.sets.colService")}</span>
              <select
                value={newSet.projectId ?? ""}
                onChange={(e) =>
                  setNewSet({ ...newSet, projectId: e.target.value || null })
                }
              >
                <option value="">{t("pages.golden.sets.allServices")}</option>
                {visibleServices.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="eo-mute" style={{ fontSize: 12, marginTop: 4 }}>
            {tsub("pages.golden.sets.layerGuide", { layer: resolvedLayer })}
          </p>
          {error && (
            <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
              {error}
            </div>
          )}
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => setCreating(false)}
            >
              {t("pages.golden.sets.cancel")}
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-primary"
              onClick={() => create.mutate()}
              disabled={create.isPending || !writable}
            >
              {create.isPending
                ? t("pages.golden.sets.creating")
                : t("pages.golden.sets.create")}
            </button>
          </div>
        </div>
      )}
        </>
      )}
    </>
  );
}

function GoldenSetDetailDrawer({
  set,
  writable,
  tab,
  onTab,
  onClose,
}: {
  set: GoldenSet | null;
  writable: boolean;
  tab: DetailTab;
  onTab: (t: DetailTab) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
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
      aria-label="Golden Set detail"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="eo-side-drawer">
        <div className="eo-side-drawer-h">
          <span className="eo-side-drawer-title">
            {t("pages.golden.sets.drawerTitle")}
            {set && (
              <span
                className="eo-mute"
                style={{ marginLeft: 8, fontSize: 12, fontWeight: 500 }}
              >
                · {set.name}
              </span>
            )}
          </span>
          <button
            type="button"
            className="eo-side-drawer-close"
            onClick={onClose}
            aria-label="Close golden set detail"
          >
            ×
          </button>
        </div>
        <div className="eo-side-drawer-body">
          {!set ? (
            <div className="eo-empty">
              {t("pages.golden.sets.loadingSet")}
            </div>
          ) : (
            <SetDetail
              set={set}
              writable={writable}
              tab={tab}
              onTab={onTab}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function SetDetail({
  set: g,
  writable,
  tab,
  onTab,
}: {
  set: GoldenSet;
  writable: boolean;
  tab: DetailTab;
  onTab: (t: DetailTab) => void;
}) {
  const { t } = useI18n();
  const TABS: { id: DetailTab; label: string }[] = [
    { id: "items", label: t("pages.golden.sets.tabItems") },
    { id: "regression", label: t("pages.golden.sets.tabRegression") },
    { id: "synth", label: t("pages.golden.sets.tabSynth") },
    { id: "upload", label: t("pages.golden.sets.tabUpload") },
    { id: "agent", label: t("pages.golden.sets.tabAgent") },
    { id: "revisions", label: t("pages.golden.sets.tabRevisions") },
  ];
  return (
    <>
      <GoldenTriPanel set={g} />
      <div
        className="eo-seg"
        style={{ marginBottom: 12, flexWrap: "wrap", gap: 4 }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-active={tab === t.id}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "items" && <SetItems set={g} writable={writable} />}
      {tab === "agent" && (
        <GoldenAgentSettingsCard set={g} writable={writable} />
      )}
      {tab === "regression" && (
        <GoldenRegressionPanel set={g} writable={writable} />
      )}
      {tab === "synth" && (
        <GoldenSynthesizerPanel set={g} writable={writable} />
      )}
      {tab === "upload" && <GoldenUploadPanel set={g} writable={writable} />}
      {tab === "revisions" && <GoldenRevisionsPanel set={g} />}
    </>
  );
}

type AddMode = "manual" | "trace" | "auto";

function SetItems({ set: g, writable }: { set: GoldenSet; writable: boolean }) {
  const { t, tsub } = useI18n();
  const qc = useQueryClient();
  const items = useQuery({
    queryKey: ["eval", "golden-items", g.id],
    queryFn: () => fetchGoldenItems(g.id),
  });

  const [addMode, setAddMode] = useState<AddMode>("manual");
  const [manualJson, setManualJson] = useState("");
  const [traceId, setTraceId] = useState("");
  const [autoSize, setAutoSize] = useState(20);
  const [error, setError] = useState<string | null>(null);

  const addManual = useMutation({
    mutationFn: () => {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(manualJson);
      } catch {
        throw new Error(t("pages.golden.sets.invalidJson"));
      }
      return addGoldenItem(g.id, payload);
    },
    onSuccess: () => {
      setManualJson("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", g.id] });
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const addFromTrace = useMutation({
    mutationFn: () => {
      if (!traceId) throw new Error(t("pages.golden.sets.traceIdRequired"));
      return addGoldenItemFromTrace(g.id, { traceId });
    },
    onSuccess: () => {
      setTraceId("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", g.id] });
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const auto = useMutation({
    mutationFn: () => {
      if (!g.projectId)
        throw new Error(
          t("pages.golden.sets.autoRequiresProject"),
        );
      return autoDiscoverGoldenItems(g.id, {
        projectId: g.projectId,
        sampleSize: autoSize,
      });
    },
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", g.id] });
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      updateGoldenItemStatus(id, status),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", g.id] }),
  });
  const removeItem = useMutation({
    mutationFn: deleteGoldenItem,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", g.id] }),
  });

  return (
    <>
      {writable && (
        <div
          className="eo-card"
          style={{ background: "var(--eo-bg-2)", padding: 10 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <strong style={{ fontSize: 13 }}>
              {t("pages.golden.sets.addItems")}
            </strong>
            <div className="eo-seg">
              {(
                [
                  ["manual", t("pages.golden.sets.addManual")],
                  ["trace", t("pages.golden.sets.addFromTrace")],
                  ["auto", t("pages.golden.sets.addAutoDiscover")],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  data-active={addMode === id}
                  onClick={() => setAddMode(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {addMode === "manual" && (
            <div>
              <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 6px" }}>
                {t("pages.golden.sets.manualHint")}
              </p>
              <textarea
                rows={4}
                value={manualJson}
                onChange={(e) => setManualJson(e.target.value)}
                placeholder='{"query":"...","expected":"..."}'
                style={{
                  width: "100%",
                  fontFamily: "var(--eo-mono)",
                  fontSize: 12,
                }}
              />
              <button
                type="button"
                className="eo-btn eo-btn-primary"
                onClick={() => addManual.mutate()}
                disabled={addManual.isPending || !manualJson.trim()}
                style={{ marginTop: 6 }}
              >
                {addManual.isPending
                  ? t("pages.golden.sets.adding")
                  : t("pages.golden.sets.addItem")}
              </button>
            </div>
          )}

          {addMode === "trace" && (
            <div>
              <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 6px" }}>
                {t("pages.golden.sets.traceHint")}
              </p>
              <input
                value={traceId}
                onChange={(e) => setTraceId(e.target.value)}
                placeholder="trace_id"
                style={{ width: "100%" }}
              />
              <button
                type="button"
                className="eo-btn eo-btn-primary"
                onClick={() => addFromTrace.mutate()}
                disabled={addFromTrace.isPending || !traceId.trim()}
                style={{ marginTop: 6 }}
              >
                {addFromTrace.isPending
                  ? t("pages.golden.sets.importing")
                  : t("pages.golden.sets.importTrace")}
              </button>
            </div>
          )}

          {addMode === "auto" && (
            <div>
              <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 6px" }}>
                {g.projectId
                  ? t("pages.golden.sets.autoHintReady")
                  : t("pages.golden.sets.autoHintNoService")}
              </p>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                  type="number"
                  value={autoSize}
                  min={1}
                  max={200}
                  onChange={(e) =>
                    setAutoSize(
                      Number.parseInt(e.target.value || "0", 10) || 0,
                    )
                  }
                  style={{ width: 100 }}
                  disabled={!g.projectId}
                />
                <button
                  type="button"
                  className="eo-btn eo-btn-primary"
                  onClick={() => auto.mutate()}
                  disabled={auto.isPending || !g.projectId || autoSize <= 0}
                >
                  {auto.isPending
                    ? t("pages.golden.sets.sampling")
                    : tsub("pages.golden.sets.sampleN", { n: String(autoSize) })}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}

      <div className="eo-divider" style={{ margin: "10px 0" }} />
      <div className="eo-table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
        <table className="eo-table">
          <thead>
            <tr>
              <th>{t("pages.golden.sets.colLayer")}</th>
              <th>{t("pages.golden.sets.colSource")}</th>
              <th>{t("pages.golden.sets.colStatus")}</th>
              <th>{t("pages.golden.sets.colReview")}</th>
              <th>{t("pages.golden.sets.colTrace")}</th>
              <th>{t("pages.golden.sets.colCreated")}</th>
              <th>{t("pages.golden.sets.colPayload")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.data?.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <div className="eo-empty">
                    {t("pages.golden.sets.noItems")}
                  </div>
                </td>
              </tr>
            )}
            {items.data?.map((it) => (
              <ItemRow
                key={it.id}
                item={it}
                writable={writable}
                onStatus={(s) => setStatus.mutate({ id: it.id, status: s })}
                onRemove={() => {
                  if (
                    confirm(t("pages.golden.sets.removeItemConfirm"))
                  )
                    removeItem.mutate(it.id);
                }}
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ItemRow({
  item: it,
  writable,
  onStatus,
  onRemove,
}: {
  item: GoldenItem;
  writable: boolean;
  onStatus: (status: string) => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const review = useMutation({
    mutationFn: (state: "unreviewed" | "reviewed" | "disputed") =>
      reviewGoldenItem(it.id, { reviewState: state }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", it.setId] });
    },
  });
  const reviewState = it.reviewState ?? "unreviewed";
  const tone =
    reviewState === "reviewed"
      ? "ok"
      : reviewState === "disputed"
        ? "err"
        : "warn";
  return (
    <tr>
      <td className="mono">{it.layer}</td>
      <td className="mono">{it.sourceKind}</td>
      <td>
        {writable ? (
          <select value={it.status} onChange={(e) => onStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        ) : (
          <span>{it.status}</span>
        )}
      </td>
      <td>
        <span className="eo-status" data-tone={tone}>
          {reviewState}
        </span>
        {writable && (
          <span style={{ marginLeft: 4, display: "inline-flex", gap: 2 }}>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              style={{ padding: "0 4px", fontSize: 10 }}
              onClick={() => review.mutate("reviewed")}
              disabled={review.isPending}
              title={t("pages.golden.sets.markReviewed")}
            >
              ✓
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              style={{ padding: "0 4px", fontSize: 10 }}
              onClick={() => review.mutate("disputed")}
              disabled={review.isPending}
              title={t("pages.golden.sets.markDisputed")}
            >
              !
            </button>
          </span>
        )}
      </td>
      <td className="mono">{it.sourceTraceId?.slice(0, 12) ?? "—"}</td>
      <td>{fmtRel(it.createdAt)}</td>
      <td>
        <code style={{ fontSize: 10 }}>
          {JSON.stringify(it.payload).slice(0, 80)}
        </code>
      </td>
      <td>
        {writable && (
          <button
            type="button"
            className="eo-btn eo-btn-ghost"
            onClick={onRemove}
          >
            ×
          </button>
        )}
      </td>
    </tr>
  );
}
