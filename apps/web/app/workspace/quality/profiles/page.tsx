"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEvalProfile,
  deleteEvalProfile,
  fetchEvalProfiles,
  fetchEvaluatorCatalog,
  fetchImprovementPacks,
  fetchJudgeModels,
  fetchOrgServices,
  replaceEvalProfile,
  type ConsensusPolicy,
  type CostExceedAction,
  type EvalProfile,
  type EvaluatorCatalogEntry,
  type ImprovementPackMeta,
  type JudgeModel,
  type JudgeRubricMode,
  type ProfileSavePayload,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n/context";
import { fmtRel } from "@/lib/format";
import {
  DEFAULT_JUDGE_SYSTEM_PROMPT,
  DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
} from "@/lib/judgeDefaults";
import {
  easyobsStandardEvaluators,
  FALLBACK_IMPROVEMENT_PACKS,
} from "@/lib/improvementPackFallbacks";
import { ProfilesGuideContent, useGoldenLayerHelp } from "../guides";
import { canMutateQuality, QualityGuard, ScopeBanner, WriteHint } from "../guard";

export default function ProfilesPage() {
  return (
    <QualityGuard>
      <Inner />
    </QualityGuard>
  );
}

const CONSENSUS_OPTIONS: ConsensusPolicy[] = [
  "single",
  "majority",
  "unanimous",
  "weighted",
];
const ON_EXCEED_OPTIONS: CostExceedAction[] = ["block", "downgrade", "notify"];

const DEFAULT_GUARD = {
  maxCostUsdPerRun: 5,
  maxCostUsdPerSubject: 0.05,
  monthlyBudgetUsd: 100,
  onExceed: "block" as CostExceedAction,
};

type DraftEvaluator = {
  evaluatorId: string;
  weight: number;
  threshold: number;
  params: Record<string, unknown>;
};

type DraftJudge = {
  modelId: string;
  weight: number;
};

type Draft = {
  id?: string;
  projectId: string | null;
  name: string;
  description: string;
  consensus: ConsensusPolicy;
  autoRun: boolean;
  enabled: boolean;
  evaluators: DraftEvaluator[];
  judgeModels: DraftJudge[];
  costGuard: typeof DEFAULT_GUARD;
  judgeRubricText: string;
  judgeRubricMode: JudgeRubricMode;
  judgeSystemPrompt: string;
  judgeUserMessageTemplate: string;
  improvementPack: string;
  judgeDimensionPrompts: Record<string, { en: string; ko: string }>;
};

const LEGACY_EVALUATOR_TO_METRIC: Record<string, string> = {
  "rule.response.present": "metric.d6_concise",
  "rule.safety.no_profanity": "metric.d8_policy",
};

function normalizeEvaluatorIds(
  rows: DraftEvaluator[],
  metricByRuleTarget: Map<string, string>,
): DraftEvaluator[] {
  const out: DraftEvaluator[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const rawId = String(row.evaluatorId || "").trim();
    if (!rawId) continue;
    const mapped =
      rawId.startsWith("metric.")
        ? rawId
        : metricByRuleTarget.get(rawId) ?? LEGACY_EVALUATOR_TO_METRIC[rawId] ?? "";
    if (!mapped || seen.has(mapped)) continue;
    seen.add(mapped);
    out.push({ ...row, evaluatorId: mapped });
  }
  return out;
}

function normaliseDimPrompts(
  raw: EvalProfile["judgeDimensionPrompts"],
): Record<string, { en: string; ko: string }> {
  const out: Record<string, { en: string; ko: string }> = {};
  if (!raw) return out;
  for (const [k, v] of Object.entries(raw)) {
    out[k] = { en: v?.en ?? "", ko: v?.ko ?? "" };
  }
  return out;
}

function fromExisting(p: EvalProfile): Draft {
  return {
    id: p.id,
    projectId: p.projectId,
    name: p.name,
    description: p.description,
    consensus: p.consensus,
    autoRun: p.autoRun,
    enabled: p.enabled,
    evaluators: p.evaluators.map((e) => ({
      evaluatorId: e.evaluatorId,
      weight: e.weight,
      threshold: e.threshold,
      params: { ...(e.params || {}) },
    })),
    judgeModels: p.judgeModels.map((j) => ({
      modelId: j.modelId,
      weight: j.weight,
    })),
    costGuard: {
      maxCostUsdPerRun: p.costGuard.maxCostUsdPerRun,
      maxCostUsdPerSubject: p.costGuard.maxCostUsdPerSubject,
      monthlyBudgetUsd: p.costGuard.monthlyBudgetUsd,
      onExceed: p.costGuard.onExceed,
    },
    judgeRubricText: p.judgeRubricText ?? "",
    judgeRubricMode: (p.judgeRubricMode as JudgeRubricMode) ?? "append",
    judgeSystemPrompt:
      p.judgeSystemPrompt?.trim() ? p.judgeSystemPrompt : DEFAULT_JUDGE_SYSTEM_PROMPT,
    judgeUserMessageTemplate:
      p.judgeUserMessageTemplate?.trim()
        ? p.judgeUserMessageTemplate
        : DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
    improvementPack: p.improvementPack ?? "easyobs_standard",
    judgeDimensionPrompts: normaliseDimPrompts(p.judgeDimensionPrompts),
  };
}

function emptyDraft(): Draft {
  const preset = easyobsStandardEvaluators();
  return {
    projectId: null,
    name: "",
    description: "",
    consensus: "single",
    autoRun: false,
    enabled: true,
    evaluators: preset.map((s) => ({
      evaluatorId: s.evaluatorId,
      weight: s.weight,
      threshold: s.threshold,
      params: { ...s.params },
    })),
    judgeModels: [],
    costGuard: { ...DEFAULT_GUARD },
    judgeRubricText: "",
    judgeRubricMode: "append",
    judgeSystemPrompt: DEFAULT_JUDGE_SYSTEM_PROMPT,
    judgeUserMessageTemplate: DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
    improvementPack: "easyobs_standard",
    judgeDimensionPrompts: {},
  };
}

function Inner() {
  const { t } = useI18n();
  const auth = useAuth();
  const orgId = auth.currentOrg?.id ?? "";
  const writable = canMutateQuality(auth);
  const qc = useQueryClient();

  const [editing, setEditing] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageTab, setPageTab] = useState<"profiles" | "guide">("profiles");

  const profiles = useQuery({
    queryKey: ["eval", "profiles"],
    queryFn: () => fetchEvalProfiles(true),
  });
  const catalog = useQuery({
    queryKey: ["eval", "evaluators"],
    queryFn: fetchEvaluatorCatalog,
    staleTime: 60 * 60_000,
  });
  const judges = useQuery({
    queryKey: ["eval", "judges", "all"],
    queryFn: () => fetchJudgeModels(true),
  });
  const services = useQuery({
    queryKey: ["org", "services", orgId],
    queryFn: () => fetchOrgServices(orgId),
    enabled: !!orgId,
  });
  const improvementPacks = useQuery({
    queryKey: ["eval", "improvement-packs", "v2-suggested-rules"],
    queryFn: fetchImprovementPacks,
    staleTime: 0,
  });
  const improvementPacksResolved: ImprovementPackMeta[] =
    improvementPacks.data && improvementPacks.data.length > 0
      ? improvementPacks.data
      : (FALLBACK_IMPROVEMENT_PACKS as unknown as ImprovementPackMeta[]);
  const accessibleServiceIds = auth.accessibleServiceIds;
  const visibleServices = useMemo(() => {
    const all = services.data ?? [];
    if (accessibleServiceIds == null) return all;
    const allowed = new Set(accessibleServiceIds);
    return all.filter((s) => allowed.has(s.id));
  }, [services.data, accessibleServiceIds]);

  const save = useMutation({
    mutationFn: async (draft: Draft) => {
      const payload: ProfileSavePayload = {
        projectId: draft.projectId,
        name: draft.name.trim(),
        description: draft.description.trim(),
        evaluators: draft.evaluators
          .filter((e) => String(e.evaluatorId || "").startsWith("metric."))
          .map((e) => ({
          evaluatorId: e.evaluatorId,
          weight: e.weight,
          threshold: e.threshold,
          params: { ...e.params },
        })),
        judgeModels: draft.judgeModels.map((j) => ({
          modelId: j.modelId,
          weight: j.weight,
        })),
        consensus: draft.consensus,
        autoRun: draft.autoRun,
        costGuard: draft.costGuard,
        enabled: draft.enabled,
        judgeRubricText: draft.judgeRubricText,
        judgeRubricMode: draft.judgeRubricMode,
        judgeSystemPrompt: draft.judgeSystemPrompt,
        judgeUserMessageTemplate: draft.judgeUserMessageTemplate,
        improvementPack: draft.improvementPack,
        judgeDimensionPrompts: draft.judgeDimensionPrompts,
      };
      if (!payload.name) throw new Error("Name is required");
      if (payload.evaluators.length === 0 && payload.judgeModels.length === 0) {
        throw new Error("Pick at least one evaluator or judge model");
      }
      if (draft.id) {
        return replaceEvalProfile(draft.id, payload);
      }
      return createEvalProfile(payload);
    },
    onSuccess: () => {
      setEditing(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "profiles"] });
      qc.invalidateQueries({ queryKey: ["quality", "overview"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteEvalProfile(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "profiles"] });
      qc.invalidateQueries({ queryKey: ["quality", "overview"] });
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("quality.profiles.title")}</h1>
          <p className="eo-page-lede">{t("quality.profiles.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            onClick={() => {
              setPageTab("profiles");
              setEditing(emptyDraft());
              setError(null);
            }}
            disabled={!writable || pageTab !== "profiles"}
            title={writable ? "" : t("quality.profiles.readOnlyTitle")}
          >
            {t("quality.profiles.newProfile")}
          </button>
        </div>
      </div>

      <ScopeBanner />
      <WriteHint />

      <div className="eo-seg" aria-label="Profiles view" style={{ marginBottom: 14 }}>
        <button
          type="button"
          data-active={pageTab === "profiles"}
          onClick={() => setPageTab("profiles")}
        >
          {t("quality.profiles.tabProfiles")}
        </button>
        <button
          type="button"
          data-active={pageTab === "guide"}
          onClick={() => setPageTab("guide")}
        >
          {t("quality.profiles.tabGuide")}
        </button>
      </div>

      {pageTab === "guide" && <ProfilesGuideContent />}

      {pageTab === "profiles" && profiles.isLoading && (
        <div className="eo-empty">{t("quality.profiles.loadingProfiles")}</div>
      )}
      {pageTab === "profiles" && profiles.isError && (
        <div className="eo-empty">{t("quality.profiles.loadError")}</div>
      )}

      {pageTab === "profiles" && (
      <div className="eo-card">
        <div className="eo-table-wrap">
          <table className="eo-table">
            <thead>
              <tr>
                <th>{t("quality.profiles.colName")}</th>
                <th>{t("quality.profiles.colService")}</th>
                <th>{t("quality.profiles.colMix")}</th>
                <th>{t("quality.profiles.colConsensus")}</th>
                <th>{t("quality.profiles.colAuto")}</th>
                <th>{t("quality.profiles.colEnabled")}</th>
                <th>{t("quality.profiles.colCreated")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.data?.length === 0 && (
                <tr>
                  <td colSpan={8}>
                    <div className="eo-empty">{t("quality.profiles.noProfiles")}</div>
                  </td>
                </tr>
              )}
              {profiles.data?.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => {
                    setEditing(fromExisting(p));
                    setError(null);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <td className="eo-td-name">
                    <div>{p.name}</div>
                    {p.description && (
                      <div className="eo-mute" style={{ fontSize: 11 }}>
                        {p.description}
                      </div>
                    )}
                  </td>
                  <td className="mono">
                    {serviceLabel(p.projectId, services.data ?? [], t("quality.profiles.allServices"))}
                  </td>
                  <td>
                    <span className="eo-tag">
                      {p.evaluators.length} {t("quality.profiles.rulesCount")}
                    </span>
                    {p.judgeModels.length > 0 && (
                      <span className="eo-tag eo-tag-accent" style={{ marginLeft: 4 }}>
                        {p.judgeModels.length} {t("quality.profiles.judgesCount")}
                      </span>
                    )}
                  </td>
                  <td className="mono">{p.consensus}</td>
                  <td>{p.autoRun ? "✓" : "—"}</td>
                  <td>{p.enabled ? "✓" : "—"}</td>
                  <td>{fmtRel(p.createdAt)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="eo-btn eo-btn-ghost"
                      onClick={() => {
                        setEditing(fromExisting(p));
                        setError(null);
                      }}
                    >
                      {writable ? t("quality.profiles.edit") : t("quality.profiles.view")}
                    </button>
                    {writable && (
                      <button
                        type="button"
                        className="eo-btn eo-btn-ghost"
                        onClick={() => {
                          if (confirm(`Delete profile "${p.name}"?`)) {
                            remove.mutate(p.id);
                          }
                        }}
                        disabled={remove.isPending}
                      >
                        {t("quality.profiles.delete")}
                      </button>
                    )}
                    <Link
                      href={`/workspace/quality/runs/?profile=${encodeURIComponent(p.id)}`}
                      className="eo-btn eo-btn-ghost"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Run →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      )}

      {pageTab === "profiles" && editing && (
        <Editor
          draft={editing}
          onChange={setEditing}
          onSave={() => save.mutate(editing)}
          onCancel={() => {
            setEditing(null);
            setError(null);
          }}
          saving={save.isPending}
          writable={writable}
          catalog={catalog.data ?? []}
          judges={judges.data ?? []}
          services={visibleServices}
          improvementPacks={improvementPacksResolved}
          error={error}
        />
      )}
    </>
  );
}

function serviceLabel(
  projectId: string | null,
  services: { id: string; name: string }[],
  allServicesLabel: string,
): string {
  if (!projectId) return allServicesLabel;
  return services.find((s) => s.id === projectId)?.name ?? projectId.slice(0, 8);
}

function Editor({
  draft,
  onChange,
  onSave,
  onCancel,
  saving,
  writable,
  catalog,
  judges,
  services,
  improvementPacks,
  error,
}: {
  draft: Draft;
  onChange: (d: Draft) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  writable: boolean;
  catalog: EvaluatorCatalogEntry[];
  judges: JudgeModel[];
  services: { id: string; name: string }[];
  improvementPacks: ImprovementPackMeta[];
  error: string | null;
}) {
  const { locale, t } = useI18n();
  const enabledJudges = judges.filter((j) => j.enabled);
  const metricCatalog = useMemo(() => {
    const onlyMetric = catalog.filter((c) => c.id.startsWith("metric."));
    // Fallback for stale backend instances that still return rule.* only.
    return onlyMetric.length > 0 ? onlyMetric : catalog;
  }, [catalog]);
  const hasMetricCatalog = useMemo(
    () => catalog.some((c) => c.id.startsWith("metric.")),
    [catalog],
  );
  const metricByRuleTarget = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of metricCatalog) {
      if (m.ruleTarget) map.set(m.ruleTarget, m.id);
    }
    return map;
  }, [metricCatalog]);
  const judgeMetricIds = useMemo(
    () => new Set(metricCatalog.filter((m) => m.metricKind === "judge").map((m) => m.id)),
    [metricCatalog],
  );
  const judgeCapableMetricIds = useMemo(
    () =>
      new Set(
        metricCatalog
          .filter((m) => String(m.evaluationMode ?? "").includes("J"))
          .map((m) => m.id),
      ),
    [metricCatalog],
  );
  const normalizedEvaluators = useMemo(
    () => normalizeEvaluatorIds(draft.evaluators, metricByRuleTarget),
    [draft.evaluators, metricByRuleTarget],
  );
  const sanitizedEvaluators = useMemo(() => {
    if (draft.judgeModels.length > 0) return normalizedEvaluators;
    return normalizedEvaluators.filter((e) => !judgeCapableMetricIds.has(e.evaluatorId));
  }, [draft.judgeModels.length, normalizedEvaluators, judgeCapableMetricIds]);
  useEffect(() => {
    const cur = JSON.stringify(draft.evaluators);
    const next = JSON.stringify(sanitizedEvaluators);
    if (cur !== next) onChange({ ...draft, evaluators: sanitizedEvaluators });
  }, [draft, sanitizedEvaluators, onChange]);
  const applyPackJudgeMinimum = (nextJudgeModels: DraftJudge[]) => {
    if (nextJudgeModels.length === 0) return sanitizedEvaluators;
    const pack = improvementPacks.find((p) => p.id === draft.improvementPack);
    const fbPack = FALLBACK_IMPROVEMENT_PACKS.find((p) => p.id === draft.improvementPack);
    const judgeSug =
      pack?.suggestedJudgeMetrics?.length
        ? pack.suggestedJudgeMetrics
        : (fbPack?.suggestedJudgeMetrics ?? []);
    const cur = [...sanitizedEvaluators];
    const ids = new Set(cur.map((e) => e.evaluatorId));
    for (const metricId of judgeSug) {
      if (!judgeMetricIds.has(metricId) || ids.has(metricId)) continue;
      cur.push({ evaluatorId: metricId, weight: 1, threshold: 0.6, params: {} });
      ids.add(metricId);
    }
    return cur;
  };
  const packLabel = (pk: (typeof improvementPacks)[number]) =>
    (locale === "ko" ? pk.labelI18n?.ko : pk.labelI18n?.en) ?? pk.label;
  return (
    <div className="eo-card" style={{ marginTop: 12 }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {draft.id ? "Edit profile" : "New profile"}
        </h3>
        <span className="eo-card-sub">
          {sanitizedEvaluators.length} rules · {draft.judgeModels.length} judges
        </span>
      </div>

      <div className="eo-grid-2" style={{ gap: 12 }}>
        <label className="eo-field">
          <span>Name</span>
          <input
            value={draft.name}
            onChange={(e) => onChange({ ...draft, name: e.target.value })}
            placeholder="e.g. payments-default"
            disabled={!writable}
          />
        </label>
        <label className="eo-field">
          <span>Service (project)</span>
          <select
            value={draft.projectId ?? ""}
            onChange={(e) =>
              onChange({ ...draft, projectId: e.target.value || null })
            }
            disabled={!writable}
          >
            <option value="">All services in org</option>
            {services.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="eo-field">
        <span>Description</span>
        <input
          value={draft.description}
          onChange={(e) => onChange({ ...draft, description: e.target.value })}
          placeholder="optional"
          disabled={!writable}
        />
      </label>

      <div className="eo-divider" style={{ margin: "10px 0" }} />
      <div className="eo-card-h">
        <h3 className="eo-card-title">Rule evaluators</h3>
        <span className="eo-card-sub">{metricCatalog.length} metrics</span>
      </div>
      {!hasMetricCatalog && (
        <div className="eo-empty" style={{ color: "var(--eo-err)", marginBottom: 8 }}>
          API returned legacy `rule.*` catalog only. Restart EasyObs API to load `metric.*` (52) catalog.
        </div>
      )}
      <EvaluatorPicker
        selected={sanitizedEvaluators}
        catalog={metricCatalog}
        hasSelectedJudges={draft.judgeModels.length > 0}
        onChange={(evaluators) => onChange({ ...draft, evaluators })}
        disabled={!writable}
      />

      <div className="eo-divider" style={{ margin: "10px 0" }} />
      <div className="eo-card-h">
        <h3 className="eo-card-title">Judge models</h3>
        <span className="eo-card-sub">{enabledJudges.length} available</span>
      </div>
      <JudgePicker
        selected={draft.judgeModels}
        judges={enabledJudges}
        onChange={(jm) =>
          onChange({
            ...draft,
            judgeModels: jm,
            evaluators:
              draft.judgeModels.length === 0 && jm.length > 0
                ? applyPackJudgeMinimum(jm)
                : sanitizedEvaluators,
          })
        }
        disabled={!writable}
      />
      {enabledJudges.length === 0 && (
        <div className="eo-mute" style={{ fontSize: 12, marginTop: 6 }}>
          No judge models registered yet —{" "}
          <Link href="/workspace/quality/judges/" className="eo-link">
            register one
          </Link>{" "}
          to enable LLM-as-a-Judge for this profile.
        </div>
      )}

      <div className="eo-grid-3" style={{ gap: 12, marginTop: 12 }}>
        <label className="eo-field">
          <span>Consensus policy</span>
          <select
            value={draft.consensus}
            onChange={(e) =>
              onChange({
                ...draft,
                consensus: e.target.value as ConsensusPolicy,
              })
            }
            disabled={!writable}
          >
            {CONSENSUS_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <p className="eo-mute" style={{ fontSize: 11, margin: "4px 0 0", lineHeight: 1.4 }}>
            {t("quality.profiles.consensusHelp")}
          </p>
        </label>
        <div className="eo-field" style={{ display: "grid", gap: 2 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={draft.autoRun}
              onChange={(e) => onChange({ ...draft, autoRun: e.target.checked })}
              disabled={!writable}
            />
            <span>Auto-run rules on ingest</span>
          </label>
          <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 0 24px", lineHeight: 1.4 }}>
            {t("quality.profiles.autoRunHelp")}
          </p>
        </div>
        <div className="eo-field" style={{ display: "grid", gap: 2 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => onChange({ ...draft, enabled: e.target.checked })}
              disabled={!writable}
            />
            <span>Profile enabled</span>
          </label>
          <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 0 24px", lineHeight: 1.4 }}>
            {t("quality.profiles.profileEnabledHelp")}
          </p>
        </div>
      </div>

      <label className="eo-field">
        <span>Improvement pack</span>
        <select
          value={draft.improvementPack}
          onChange={(e) => {
            const nextId = e.target.value;
            const fbPack = FALLBACK_IMPROVEMENT_PACKS.find((p) => p.id === nextId);
            const pack = improvementPacks.find((p) => p.id === nextId);
            const sug =
              pack?.suggestedRuleEvaluators?.length
                ? pack.suggestedRuleEvaluators
                : (fbPack?.suggestedRuleEvaluators ?? []);
            const judgeSug =
              pack?.suggestedJudgeMetrics?.length
                ? pack.suggestedJudgeMetrics
                : (fbPack?.suggestedJudgeMetrics ?? []);
            if (sug.length) {
              const nextEvaluators = sug.map((s) => ({
                evaluatorId: metricByRuleTarget.get(s.evaluatorId) ?? s.evaluatorId,
                weight: s.weight,
                threshold: s.threshold,
                params: { ...s.params },
              }));
              const hasJudge = draft.judgeModels.length > 0;
              const baseEvaluators = hasJudge
                ? nextEvaluators
                : nextEvaluators.filter((e) => !judgeCapableMetricIds.has(e.evaluatorId));
              if (hasJudge) {
                const existing = new Set(baseEvaluators.map((x) => x.evaluatorId));
                for (const metricId of judgeSug) {
                  if (existing.has(metricId)) continue;
                  baseEvaluators.push({
                    evaluatorId: metricId,
                    weight: 1,
                    threshold: 0.6,
                    params: {},
                  });
                }
              }
              onChange({
                ...draft,
                improvementPack: nextId,
                evaluators: baseEvaluators,
              });
            } else {
              onChange({ ...draft, improvementPack: nextId });
            }
          }}
          disabled={!writable}
        >
          {improvementPacks.map((pk) => (
            <option key={pk.id} value={pk.id}>
              {packLabel(pk)}
            </option>
          ))}
        </select>
        <p className="eo-mute" style={{ fontSize: 11, margin: "4px 0 0" }}>
          {t("quality.profiles.improvementPackHelp")}
        </p>
      </label>

      <div className="eo-divider" style={{ margin: "10px 0" }} />
      <div className="eo-card-h">
        <h3 className="eo-card-title">Cost guard</h3>
        <span className="eo-card-sub">enforced before any judge call</span>
      </div>
      <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 8px", lineHeight: 1.45 }}>
        {t("quality.profiles.costGuardHelp")}
      </p>
      <div className="eo-grid-3" style={{ gap: 12 }}>
        <NumberField
          label="Max $ per run"
          value={draft.costGuard.maxCostUsdPerRun}
          onChange={(v) =>
            onChange({
              ...draft,
              costGuard: { ...draft.costGuard, maxCostUsdPerRun: v },
            })
          }
          disabled={!writable}
        />
        <NumberField
          label="Max $ per subject"
          value={draft.costGuard.maxCostUsdPerSubject}
          step={0.01}
          onChange={(v) =>
            onChange({
              ...draft,
              costGuard: { ...draft.costGuard, maxCostUsdPerSubject: v },
            })
          }
          disabled={!writable}
        />
        <NumberField
          label="Monthly budget $"
          value={draft.costGuard.monthlyBudgetUsd}
          onChange={(v) =>
            onChange({
              ...draft,
              costGuard: { ...draft.costGuard, monthlyBudgetUsd: v },
            })
          }
          disabled={!writable}
        />
        <label className="eo-field">
          <span>On exceed</span>
          <select
            value={draft.costGuard.onExceed}
            onChange={(e) =>
              onChange({
                ...draft,
                costGuard: {
                  ...draft.costGuard,
                  onExceed: e.target.value as CostExceedAction,
                },
              })
            }
            disabled={!writable}
          >
            {ON_EXCEED_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <div className="eo-empty" style={{ marginTop: 8, color: "var(--eo-err)" }}>
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
          onClick={onSave}
          disabled={!writable || saving}
        >
          {saving ? "Saving…" : draft.id ? "Save changes" : "Create profile"}
        </button>
      </div>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  disabled,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  step?: number;
}) {
  return (
    <label className="eo-field">
      <span>{label}</span>
      <input
        type="number"
        value={value}
        step={step}
        min={0}
        onChange={(e) => {
          const n = Number.parseFloat(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
        disabled={disabled}
      />
    </label>
  );
}

function EvaluatorPicker({
  selected,
  catalog,
  hasSelectedJudges,
  onChange,
  disabled,
}: {
  selected: DraftEvaluator[];
  catalog: EvaluatorCatalogEntry[];
  hasSelectedJudges: boolean;
  onChange: (next: DraftEvaluator[]) => void;
  disabled?: boolean;
}) {
  const { t, tsub } = useI18n();
  const golden = useGoldenLayerHelp();
  const aliasByRuleTarget = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of catalog) {
      if (c.ruleTarget) m.set(c.ruleTarget, c.id);
    }
    return m;
  }, [catalog]);
  const resolveId = (id: string) => aliasByRuleTarget.get(id) ?? id;
  const selectedIds = new Set(selected.map((s) => resolveId(s.evaluatorId)));
  const grouped = useMemo(() => {
    const map: Record<string, EvaluatorCatalogEntry[]> = {};
    for (const e of catalog) {
      (map[e.category] ||= []).push(e);
    }
    return map;
  }, [catalog]);

  const toggle = (id: string) => {
    if (disabled) return;
    const isSel = selected.some((s) => resolveId(s.evaluatorId) === id);
    if (isSel) {
      onChange(selected.filter((s) => resolveId(s.evaluatorId) !== id));
    } else {
      onChange([
        ...selected,
        { evaluatorId: id, weight: 1, threshold: 0.6, params: {} },
      ]);
    }
  };
  const updateRow = (id: string, patch: Partial<DraftEvaluator>) => {
    onChange(
      selected.map((s) => (resolveId(s.evaluatorId) === id ? { ...s, ...patch } : s)),
    );
  };
  const [openCats, setOpenCats] = useState<Record<string, boolean>>({});
  const isCatOpen = (cat: string) => openCats[cat] ?? true;
  const toggleCat = (cat: string) =>
    setOpenCats((prev) => ({ ...prev, [cat]: !(prev[cat] ?? true) }));
  const categoryTitle = (cat: string) => {
    const normalized = String(cat).startsWith("metric_")
      ? String(cat).slice("metric_".length)
      : String(cat);
    const key = `quality.profiles.group${normalized}` as const;
    const txt = t(key);
    return txt === key ? normalized : txt;
  };

  const colStyle: CSSProperties = {
    background: "var(--eo-bg-2)",
    display: "flex",
    flexDirection: "column",
    minHeight: "min(320px, 50vh)",
    maxHeight: "min(70vh, 720px)",
    minWidth: 0,
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))",
        gap: 12,
        width: "100%",
        alignItems: "stretch",
      }}
    >
      <div className="eo-card" style={colStyle}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("quality.profiles.catalogTitle")}</h3>
        </div>
        <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 8px", lineHeight: 1.45 }}>
          {tsub("quality.profiles.catalogPackNote", { count: String(catalog.length) })}
        </p>
        <div style={{ flex: 1, minHeight: 0, overflow: "auto", paddingRight: 4 }}>
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat} style={{ marginBottom: 10 }}>
              <button
                type="button"
                className="eo-btn eo-btn-ghost"
                style={{
                  marginBottom: 6,
                  padding: "4px 10px",
                  fontSize: 14,
                  fontWeight: 700,
                  lineHeight: 1.2,
                }}
                onClick={() => toggleCat(cat)}
              >
                <span style={{ display: "inline-block", width: 14, fontSize: 14 }}>
                  {isCatOpen(cat) ? "▼" : "▶"}
                </span>{" "}
                {categoryTitle(cat)}
              </button>
              {isCatOpen(cat) &&
                items.map((e) => (
                (() => {
                  const mode = String(e.evaluationMode ?? "");
                  const hasJudgeMode = mode.includes("J");
                  const judgeDisabled = hasJudgeMode && !hasSelectedJudges;
                  const needsGt = hasJudgeMode && String((e as { gt?: string }).gt ?? "—") !== "—";
                  return (
                <label
                  key={e.id}
                  style={{
                    display: "flex",
                    gap: 6,
                    alignItems: "flex-start",
                    fontSize: 12,
                    padding: "3px 0",
                    opacity: judgeDisabled ? 0.55 : 1,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(e.id)}
                    onChange={() => toggle(e.id)}
                    disabled={disabled || judgeDisabled}
                  />
                  <span>
                    <strong>{e.name}</strong>{" "}
                    {hasJudgeMode && (
                      <span className="eo-tag eo-tag-accent" style={{ marginRight: 4, fontSize: 10 }}>
                        J
                      </span>
                    )}
                    {needsGt && (
                      <span className="eo-tag" style={{ marginRight: 4, fontSize: 10 }}>
                        GT
                      </span>
                    )}
                    <span
                      className="eo-mute"
                      title={
                        e.layer === "L1" || e.layer === "L2" || e.layer === "L3"
                          ? `${golden[e.layer].title}\n${golden[e.layer].body}`
                          : String(e.layer)
                      }
                    >
                      ({e.layer})
                    </span>
                    <div className="eo-mute" style={{ fontSize: 11 }}>
                      {e.description}
                    </div>
                  </span>
                </label>
                  );
                })()
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="eo-card" style={colStyle}>
        <div className="eo-card-h">
          <h3 className="eo-card-title">
            {t("quality.profiles.selectedTitle").replace("{count}", String(selected.length))}
          </h3>
        </div>
        <p className="eo-mute" style={{ fontSize: 11, margin: "0 0 8px", lineHeight: 1.45 }}>
          {t("quality.profiles.selectedLegend")}
        </p>
        {selected.length === 0 && (
          <div className="eo-empty">{t("quality.profiles.selectedEmpty")}</div>
        )}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto", display: "grid", gap: 6 }}>
          {selected.length > 0 && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 70px 70px 28px",
                gap: 6,
                alignItems: "start",
                fontSize: 10,
                color: "var(--eo-mute)",
                textTransform: "uppercase",
                letterSpacing: "0.02em",
              }}
            >
              <span />
              <span title={t("quality.profiles.weightTitle")}>{t("quality.profiles.weightAbbr")}</span>
              <span title={t("quality.profiles.thresholdTitle")}>{t("quality.profiles.thresholdAbbr")}</span>
              <span />
            </div>
          )}
          {selected.map((s) => {
            const rid = resolveId(s.evaluatorId);
            const meta = catalog.find((c) => c.id === rid);
            return (
              <div key={rid} style={{ display: "grid", gap: 4 }}>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 70px 70px 28px",
                    gap: 6,
                    alignItems: "start",
                  }}
                >
                  <span style={{ fontSize: 12, alignSelf: "start" }} title={meta?.description}>
                    {meta?.name ?? s.evaluatorId}
                  </span>
                  <input
                    type="number"
                    value={s.weight}
                    step={0.1}
                    min={0}
                    style={{ alignSelf: "start" }}
                    onChange={(e) =>
                      updateRow(rid, {
                        weight: Number.parseFloat(e.target.value) || 0,
                      })
                    }
                    disabled={disabled}
                    title={t("quality.profiles.weightTitle")}
                  />
                  <input
                    type="number"
                    value={s.threshold}
                    step={0.05}
                    min={0}
                    max={1}
                    style={{ alignSelf: "start" }}
                    onChange={(e) =>
                      updateRow(rid, {
                        threshold: Number.parseFloat(e.target.value) || 0,
                      })
                    }
                    disabled={disabled}
                    title={t("quality.profiles.thresholdTitle")}
                  />
                  <button
                    type="button"
                    className="eo-btn eo-btn-ghost"
                    style={{ alignSelf: "start" }}
                    onClick={() => toggle(rid)}
                    disabled={disabled}
                    title="Remove"
                  >
                    ×
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function JudgePicker({
  selected,
  judges,
  onChange,
  disabled,
}: {
  selected: DraftJudge[];
  judges: JudgeModel[];
  onChange: (next: DraftJudge[]) => void;
  disabled?: boolean;
}) {
  const selectedIds = new Set(selected.map((s) => s.modelId));
  const toggle = (id: string) => {
    if (disabled) return;
    if (selectedIds.has(id)) {
      onChange(selected.filter((s) => s.modelId !== id));
    } else {
      onChange([...selected, { modelId: id, weight: 1 }]);
    }
  };
  const updateWeight = (id: string, weight: number) => {
    onChange(selected.map((s) => (s.modelId === id ? { ...s, weight } : s)));
  };
  return (
    <div style={{ display: "grid", gap: 6 }}>
      {judges.length === 0 && (
        <div className="eo-empty">No judge models available.</div>
      )}
      {judges.map((j) => {
        const sel = selected.find((s) => s.modelId === j.id);
        return (
          <div
            key={j.id}
            style={{
              display: "grid",
              gridTemplateColumns: "20px 1fr 110px 80px",
              gap: 6,
              alignItems: "center",
              fontSize: 12,
            }}
          >
            <input
              type="checkbox"
              checked={!!sel}
              onChange={() => toggle(j.id)}
              disabled={disabled}
            />
            <span>
              <strong>{j.name}</strong>{" "}
              <span className="eo-mute">
                {j.provider}/{j.model || "—"}
              </span>
            </span>
            <span className="mono eo-mute" style={{ fontSize: 11 }}>
              ${j.costPer1kInput.toFixed(4)}/${j.costPer1kOutput.toFixed(4)} per 1k
            </span>
            {sel && (
              <input
                type="number"
                value={sel.weight}
                step={0.1}
                min={0}
                onChange={(e) =>
                  updateWeight(j.id, Number.parseFloat(e.target.value) || 0)
                }
                disabled={disabled}
                title="weight"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
