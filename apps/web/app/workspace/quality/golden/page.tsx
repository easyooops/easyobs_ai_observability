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
import { useBilingual } from "@/lib/i18n/bilingual";
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
  const b = useBilingual();
  return [
    {
      value: "regression" as GoldenSetMode,
      label: b(
        "regression — ground-truth regression suite",
        "regression — GT 기반 회귀 테스트",
      ),
    },
    {
      value: "cohort" as GoldenSetMode,
      label: b(
        "cohort — trace bundle (no GT)",
        "cohort — GT 없는 trace 묶음",
      ),
    },
    {
      value: "synthesized" as GoldenSetMode,
      label: b(
        "synthesized — LLM-generated",
        "synthesized — LLM 자동 생성",
      ),
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
  const { t } = useI18n();
  const b = useBilingual();
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
          <h3 className="eo-card-title">{b("Golden Sets", "골든 세트")}</h3>
          <span className="eo-card-sub">
            {b(
              `${sets.data?.length ?? 0} total · click a row to inspect`,
              `${sets.data?.length ?? 0} 개 · 행 클릭 시 상세 열림`,
            )}
          </span>
        </div>
        <div className="eo-table-wrap">
          <table className="eo-table">
            <thead>
              <tr>
                <th>{b("Name", "이름")}</th>
                <th>{b("Mode", "Mode")}</th>
                <th>{b("Layer", "Layer")}</th>
                <th>{b("Agent", "Agent")}</th>
                <th>{b("Service", "Service")}</th>
                <th>{b("Items", "항목")}</th>
                <th>{b("Created", "생성")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sets.data?.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div className="eo-empty">
                      {b("No golden sets yet.", "아직 골든 세트가 없습니다.")}
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
                          ? b("Agent ✓", "Agent ✓")
                          : b("Agent —", "Agent —")}
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
                                b(
                                  `Delete golden set "${g.name}"?`,
                                  `골든 세트 "${g.name}" 을 삭제할까요?`,
                                ),
                              )
                            ) {
                              remove.mutate(g.id);
                            }
                          }}
                          disabled={remove.isPending}
                        >
                          {b("Delete", "삭제")}
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
            <h3 className="eo-card-title">{b("New golden set", "새 골든 세트")}</h3>
            <span className="eo-card-sub">
              {b(
                "Layer is auto-recommended from the name + description; you can override anytime.",
                "Layer 는 이름·설명으로 자동 추천됩니다. 언제든 변경 가능.",
              )}
            </span>
          </div>
          <div className="eo-grid-2" style={{ gap: 12 }}>
            <label className="eo-field">
              <span>{b("Name", "이름")}</span>
              <input
                value={newSet.name}
                onChange={(e) => setNewSet({ ...newSet, name: e.target.value })}
                placeholder={b(
                  "e.g. customer-faq-regression",
                  "예: customer-faq-regression",
                )}
              />
            </label>
            <label className="eo-field">
              <span>{b("Mode", "모드")}</span>
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
            <span>{b("Description", "설명 (optional)")}</span>
            <input
              value={newSet.description}
              onChange={(e) =>
                setNewSet({ ...newSet, description: e.target.value })
              }
              placeholder={b(
                "What this set evaluates (optional, used for layer auto-pick)",
                "이 Set 이 평가하는 항목 (선택, layer 자동 추천에 사용)",
              )}
            />
          </label>
          <div className="eo-grid-2" style={{ gap: 12 }}>
            <label className="eo-field">
              <span>{b("Layer", "Layer")}</span>
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
                  {b(
                    `Auto-recommend (→ ${resolvedLayer})`,
                    `자동 추천 (→ ${resolvedLayer})`,
                  )}
                </option>
                {LAYER_OPTIONS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="eo-field">
              <span>{b("Service", "서비스")}</span>
              <select
                value={newSet.projectId ?? ""}
                onChange={(e) =>
                  setNewSet({ ...newSet, projectId: e.target.value || null })
                }
              >
                <option value="">{b("All services", "전체 서비스")}</option>
                {visibleServices.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="eo-mute" style={{ fontSize: 12, marginTop: 4 }}>
            {b(
              `Layer guide: L1 = query/intent, L2 = retrieval, L3 = response. (Auto pick → ${resolvedLayer})`,
              `Layer 가이드: L1 = query/intent, L2 = retrieval, L3 = response. (자동 → ${resolvedLayer})`,
            )}
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
              {b("Cancel", "취소")}
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-primary"
              onClick={() => create.mutate()}
              disabled={create.isPending || !writable}
            >
              {create.isPending
                ? b("Creating…", "생성 중…")
                : b("Create", "만들기")}
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
  const b = useBilingual();
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
            {b("Golden Set detail", "골든 세트 상세")}
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
              {b("Loading set…", "세트 불러오는 중…")}
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
  const b = useBilingual();
  const TABS: { id: DetailTab; label: string }[] = [
    { id: "items", label: b("Items", "항목") },
    { id: "regression", label: b("Regression Run ▶", "Regression Run ▶") },
    { id: "synth", label: b("+ Auto-generate", "+ 자동 생성") },
    { id: "upload", label: b("+ Upload", "+ 업로드") },
    { id: "agent", label: b("Agent connection", "Agent 연결") },
    { id: "revisions", label: b("Revisions / Trust", "Revisions / Trust") },
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
  const b = useBilingual();
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
        throw new Error(b("Payload must be valid JSON", "Payload 는 JSON 이어야 합니다"));
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
      if (!traceId) throw new Error(b("Trace id required", "Trace id 가 필요합니다"));
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
          b(
            "Auto-discover requires a project-scoped set",
            "Auto-discover 는 service 가 지정된 Set 에서만 가능합니다",
          ),
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
              {b("Add items", "항목 추가")}
            </strong>
            <div className="eo-seg">
              {(
                [
                  ["manual", b("Manual", "수동")],
                  ["trace", b("From trace", "Trace 기반")],
                  ["auto", b("Auto-discover", "자동 추출")],
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
                {b(
                  'JSON payload — e.g. {"query":"...","expected":"..."}',
                  'JSON payload — 예: {"query":"...","expected":"..."}',
                )}
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
                  ? b("Adding…", "추가 중…")
                  : b("Add item", "추가")}
              </button>
            </div>
          )}

          {addMode === "trace" && (
            <div>
              <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 6px" }}>
                {b(
                  "Paste a trace id and we'll snapshot its query / response into a candidate item.",
                  "Trace id 를 붙여넣으면 해당 trace 의 query · response 를 candidate 항목으로 만들어줍니다.",
                )}
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
                  ? b("Importing…", "가져오는 중…")
                  : b("Import trace", "Trace 가져오기")}
              </button>
            </div>
          )}

          {addMode === "auto" && (
            <div>
              <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 6px" }}>
                {g.projectId
                  ? b(
                      "Sample N recent traces from this service into candidate items (you'll review before promoting).",
                      "이 서비스의 최근 trace N개를 candidate 항목으로 샘플링합니다. 검토 후 승급하세요.",
                    )
                  : b(
                      "Auto-discover requires a service-scoped Set. Pick a service when creating the Set.",
                      "Auto-discover 는 service 가 지정된 Set 에서만 가능합니다.",
                    )}
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
                    ? b("Sampling…", "샘플링 중…")
                    : b(`Sample ${autoSize}`, `${autoSize}개 샘플링`)}
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
              <th>{b("Layer", "Layer")}</th>
              <th>{b("Source", "Source")}</th>
              <th>{b("Status", "Status")}</th>
              <th>{b("Review", "Review")}</th>
              <th>{b("Trace", "Trace")}</th>
              <th>{b("Created", "Created")}</th>
              <th>{b("Payload", "Payload")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.data?.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <div className="eo-empty">
                    {b("No items yet.", "아직 항목이 없습니다.")}
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
                    confirm(b("Remove this item?", "이 항목을 삭제할까요?"))
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
  const b = useBilingual();
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
              title={b("mark as reviewed", "reviewed 표시")}
            >
              ✓
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              style={{ padding: "0 4px", fontSize: 10 }}
              onClick={() => review.mutate("disputed")}
              disabled={review.isPending}
              title={b("mark as disputed", "disputed 표시")}
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
