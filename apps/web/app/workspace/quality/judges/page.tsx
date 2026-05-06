"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createJudgeModel,
  deleteJudgeModel,
  fetchJudgeModels,
  patchJudgeModel,
  fetchJudgeDimensionCatalog,
  fetchJudgePrompts,
  createJudgePrompt,
  activateJudgePromptVersion,
  type JudgeModel,
  type JudgeDimensionCatalogEntry,
  type JudgePromptVersion,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";
import { canMutateQuality, QualityGuard, ScopeBanner, WriteHint } from "../guard";
import { useI18n } from "@/lib/i18n/context";
import {
  DEFAULT_JUDGE_SYSTEM_PROMPT,
  DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
} from "@/lib/judgeDefaults";

type ProviderDef = {
  id: string;
  label: string;
  emoji: string;
  blurb: string;
};

const PROVIDERS: ProviderDef[] = [
  {
    id: "openai",
    label: "OpenAI",
    emoji: "⌁",
    blurb: "api.openai.com",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    emoji: "◆",
    blurb: "Claude API",
  },
  {
    id: "google_gemini",
    label: "Gemini",
    emoji: "✦",
    blurb: "Google AI Studio",
  },
  {
    id: "google_vertex",
    label: "Vertex",
    emoji: "▣",
    blurb: "GCP Gemini / Vertex",
  },
  {
    id: "azure_openai",
    label: "Azure",
    emoji: "▲",
    blurb: "Azure OpenAI",
  },
  {
    id: "aws_bedrock",
    label: "Bedrock",
    emoji: "☁",
    blurb: "AWS Bedrock",
  },
  {
    id: "onprem_openai_compatible",
    label: "On-prem",
    emoji: "⎈",
    blurb: "OpenAI-compatible (vLLM, LiteLLM proxy, …)",
  },
];

function defaultConnection(provider: string): Record<string, string> {
  switch (provider) {
    case "openai":
      return { api_key_env: "OPENAI_API_KEY", base_url: "" };
    case "anthropic":
      return { api_key_env: "ANTHROPIC_API_KEY" };
    case "google_gemini":
      return { api_key_env: "GOOGLE_API_KEY" };
    case "google_vertex":
      return {
        api_key_env: "GOOGLE_APPLICATION_CREDENTIALS",
        project_id: "",
        location: "us-central1",
      };
    case "azure_openai":
      return {
        api_key_env: "AZURE_OPENAI_API_KEY",
        endpoint: "",
        deployment: "",
        api_version: "2024-02-15-preview",
      };
    case "aws_bedrock":
      return { aws_region: "us-east-1", credential_env_hint: "AWS_PROFILE" };
    case "onprem_openai_compatible":
      return { base_url: "", api_key_env: "OPENAI_API_KEY" };
    default:
      return {};
  }
}

function connToStrings(cfg: Record<string, unknown> | undefined): Record<string, string> {
  if (!cfg || typeof cfg !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(cfg)) {
    if (v == null) continue;
    out[k] = typeof v === "string" ? v : String(v);
  }
  return out;
}

function pruneConnection(c: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(c)) {
    if (v.trim() !== "") out[k] = v.trim();
  }
  return out;
}

type Draft = {
  id?: string;
  name: string;
  provider: string;
  model: string;
  temperature: number;
  weight: number;
  costPer1kInput: number;
  costPer1kOutput: number;
  enabled: boolean;
  connection: Record<string, string>;
};

function emptyDraft(): Draft {
  return {
    name: "",
    provider: "openai",
    model: "",
    temperature: 0,
    weight: 1,
    costPer1kInput: 0,
    costPer1kOutput: 0,
    enabled: true,
    connection: defaultConnection("openai"),
  };
}

function fromExisting(j: JudgeModel): Draft {
  const base = connToStrings(j.connectionConfig ?? {});
  return {
    id: j.id,
    name: j.name,
    provider: j.provider,
    model: j.model,
    temperature: j.temperature,
    weight: j.weight,
    costPer1kInput: j.costPer1kInput,
    costPer1kOutput: j.costPer1kOutput,
    enabled: j.enabled,
    connection: { ...defaultConnection(j.provider), ...base },
  };
}

export default function JudgesPage() {
  return (
    <QualityGuard>
      <JudgesShell />
    </QualityGuard>
  );
}

function JudgesShell() {
  const [tab, setTab] = useState<"models" | "prompts">("models");
  const { t } = useI18n();

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.judges.title")}</h1>
          <p className="eo-page-lede">{t("pages.judges.lede")}</p>
        </div>
      </div>
      <ScopeBanner />
      <WriteHint />

      <div className="eo-tabs" style={{ marginBottom: 16 }}>
        <button
          type="button"
          className="eo-tab"
          data-active={tab === "models"}
          onClick={() => setTab("models")}
        >
          {t("pages.judges.tabModels")}
        </button>
        <button
          type="button"
          className="eo-tab"
          data-active={tab === "prompts"}
          onClick={() => setTab("prompts")}
        >
          {t("pages.judges.tabPrompts")}
        </button>
      </div>

      {tab === "models" && <ModelsTab />}
      {tab === "prompts" && <PromptsTab />}
    </>
  );
}

function ModelsTab() {
  const { t } = useI18n();
  const auth = useAuth();
  const writable = canMutateQuality(auth);
  const qc = useQueryClient();

  const judges = useQuery({
    queryKey: ["eval", "judges", "all"],
    queryFn: () => fetchJudgeModels(true),
  });

  const [editing, setEditing] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      if (!d.name.trim()) throw new Error("Name is required");
      const conn = pruneConnection(d.connection);
      if (d.id) {
        return patchJudgeModel(d.id, {
          name: d.name,
          provider: d.provider,
          model: d.model,
          temperature: d.temperature,
          weight: d.weight,
          costPer1kInput: d.costPer1kInput,
          costPer1kOutput: d.costPer1kOutput,
          enabled: d.enabled,
          connectionConfig: conn,
        });
      }
      return createJudgeModel({
        name: d.name,
        provider: d.provider,
        model: d.model,
        temperature: d.temperature,
        weight: d.weight,
        costPer1kInput: d.costPer1kInput,
        costPer1kOutput: d.costPer1kOutput,
        enabled: d.enabled,
        connectionConfig: conn,
      });
    },
    onSuccess: () => {
      setEditing(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "judges", "all"] });
      qc.invalidateQueries({ queryKey: ["quality", "overview"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: deleteJudgeModel,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["eval", "judges", "all"] }),
  });

  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          onClick={() => {
            setEditing(emptyDraft());
            setError(null);
          }}
          disabled={!writable}
        >
          {t("pages.judges.registerJudge")}
        </button>
      </div>

      <div className="eo-card">
        <div className="eo-table-wrap">
          <table className="eo-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Provider / model</th>
                <th>Temp</th>
                <th>Weight</th>
                <th>$/1k in</th>
                <th>$/1k out</th>
                <th>Enabled</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {judges.data?.length === 0 && (
                <tr>
                  <td colSpan={9}>
                    <div className="eo-empty">
                      {t("pages.judges.emptyTable")}
                    </div>
                  </td>
                </tr>
              )}
              {judges.data?.map((j) => (
                <tr
                  key={j.id}
                  onClick={() => {
                    setEditing(fromExisting(j));
                    setError(null);
                  }}
                  style={{ cursor: "pointer" }}
                >
                  <td className="eo-td-name">{j.name}</td>
                  <td className="mono">
                    {j.provider} / {j.model || "—"}
                  </td>
                  <td className="mono">{j.temperature.toFixed(2)}</td>
                  <td className="mono">{j.weight.toFixed(2)}</td>
                  <td className="mono">${j.costPer1kInput.toFixed(4)}</td>
                  <td className="mono">${j.costPer1kOutput.toFixed(4)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <span
                      title={j.enabled ? "Enabled — used when selected on profiles" : "Disabled"}
                      style={{
                        display: "inline-block",
                        width: 10,
                        height: 10,
                        borderRadius: 999,
                        background: j.enabled ? "#22c55e" : "#94a3b8",
                        boxShadow: j.enabled
                          ? "0 0 0 2px rgba(34,197,94,0.35)"
                          : "0 0 0 1px rgba(148,163,184,0.5)",
                      }}
                    />
                    <span className="eo-mute" style={{ marginLeft: 8, fontSize: 11 }}>
                      {j.enabled ? "on" : "off"}
                    </span>
                  </td>
                  <td>{fmtRel(j.createdAt)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="eo-btn eo-btn-ghost"
                      onClick={() => {
                        setEditing(fromExisting(j));
                        setError(null);
                      }}
                    >
                      {writable ? "Edit" : "View"}
                    </button>
                    {writable && (
                      <button
                        type="button"
                        className="eo-btn eo-btn-ghost"
                        onClick={() => {
                          if (confirm(`Delete judge "${j.name}"?`)) {
                            remove.mutate(j.id);
                          }
                        }}
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {editing && (
        <div className="eo-card" style={{ marginTop: 12 }}>
          <div className="eo-card-h">
            <h3 className="eo-card-title">
              {editing.id ? "Edit judge" : "Register judge"}
            </h3>
          </div>

          <div className="eo-field">
            <span>Provider</span>
            <div className="eo-provider-grid" role="listbox" aria-label="Provider">
              {PROVIDERS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className="eo-provider-tile"
                  data-active={editing.provider === p.id}
                  disabled={!writable}
                  title={p.blurb}
                  onClick={() =>
                    setEditing({
                      ...editing,
                      provider: p.id,
                      connection: defaultConnection(p.id),
                    })
                  }
                >
                  <span aria-hidden>{p.emoji}</span>
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
            <p className="eo-mute" style={{ fontSize: 11, marginTop: 6 }}>
              {
                PROVIDERS.find((x) => x.id === editing.provider)?.blurb
              }
            </p>
          </div>

          <ProviderConnectionFields
            provider={editing.provider}
            connection={editing.connection}
            disabled={!writable}
            onChange={(connection) => setEditing({ ...editing, connection })}
          />

          <div className="eo-grid-3" style={{ gap: 12, marginTop: 12 }}>
            <label className="eo-field">
              <span>Name</span>
              <input
                value={editing.name}
                onChange={(e) =>
                  setEditing({ ...editing, name: e.target.value })
                }
                disabled={!writable}
              />
            </label>
            <label className="eo-field">
              <span>Model id</span>
              <input
                value={editing.model}
                onChange={(e) =>
                  setEditing({ ...editing, model: e.target.value })
                }
                placeholder="e.g. gpt-4o-mini, claude-3-5-sonnet, gemini-1.5-pro"
                disabled={!writable}
              />
            </label>
            <label className="eo-field">
              <span>Temperature</span>
              <input
                type="number"
                step={0.05}
                value={editing.temperature}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    temperature: Number.parseFloat(e.target.value) || 0,
                  })
                }
                disabled={!writable}
              />
            </label>
            <label className="eo-field">
              <span>Weight</span>
              <input
                type="number"
                step={0.1}
                value={editing.weight}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    weight: Number.parseFloat(e.target.value) || 0,
                  })
                }
                disabled={!writable}
              />
            </label>
            <label className="eo-field" style={{ flexDirection: "row", gap: 6 }}>
              <input
                type="checkbox"
                checked={editing.enabled}
                onChange={(e) =>
                  setEditing({ ...editing, enabled: e.target.checked })
                }
                disabled={!writable}
              />
              <span>Enabled</span>
            </label>
            <label className="eo-field">
              <span>Cost / 1k input</span>
              <input
                type="number"
                step={0.0001}
                value={editing.costPer1kInput}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    costPer1kInput: Number.parseFloat(e.target.value) || 0,
                  })
                }
                disabled={!writable}
              />
            </label>
            <label className="eo-field">
              <span>Cost / 1k output</span>
              <input
                type="number"
                step={0.0001}
                value={editing.costPer1kOutput}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    costPer1kOutput: Number.parseFloat(e.target.value) || 0,
                  })
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
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => {
                setEditing(null);
                setError(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-primary"
              onClick={() => save.mutate(editing)}
              disabled={!writable || save.isPending}
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}

function textField(
  label: string,
  key: string,
  values: Record<string, string>,
  onChange: (c: Record<string, string>) => void,
  disabled: boolean,
  placeholder?: string,
) {
  return (
    <label className="eo-field" key={key}>
      <span>{label}</span>
      <input
        value={values[key] ?? ""}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) =>
          onChange({ ...values, [key]: e.target.value })
        }
      />
    </label>
  );
}

function ProviderConnectionFields({
  provider,
  connection,
  onChange,
  disabled,
}: {
  provider: string;
  connection: Record<string, string>;
  onChange: (c: Record<string, string>) => void;
  disabled: boolean;
}) {
  const merged = useMemo(
    () => ({ ...defaultConnection(provider), ...connection }),
    [provider, connection],
  );

  if (provider === "openai" || provider === "anthropic" || provider === "google_gemini") {
    return (
      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {textField(
          "API key environment variable",
          "api_key_env",
          merged,
          onChange,
          disabled,
          provider === "openai" ? "OPENAI_API_KEY" : undefined,
        )}
        {provider === "openai" &&
          textField(
            "Base URL (optional)",
            "base_url",
            merged,
            onChange,
            disabled,
            "https://api.openai.com/v1",
          )}
      </div>
    );
  }

  if (provider === "onprem_openai_compatible") {
    return (
      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {textField(
          "Base URL (required)",
          "base_url",
          merged,
          onChange,
          disabled,
          "http://llm.internal:8000/v1",
        )}
        {textField(
          "API key environment variable",
          "api_key_env",
          merged,
          onChange,
          disabled,
          "OPENAI_API_KEY",
        )}
      </div>
    );
  }

  if (provider === "azure_openai") {
    return (
      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {textField(
          "Endpoint (resource URL)",
          "endpoint",
          merged,
          onChange,
          disabled,
          "https://YOUR.resource.openai.azure.com/",
        )}
        {textField("Deployment name", "deployment", merged, onChange, disabled)}
        {textField("API version", "api_version", merged, onChange, disabled)}
        {textField(
          "API key environment variable",
          "api_key_env",
          merged,
          onChange,
          disabled,
          "AZURE_OPENAI_API_KEY",
        )}
      </div>
    );
  }

  if (provider === "google_vertex") {
    return (
      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {textField("GCP project id", "project_id", merged, onChange, disabled)}
        {textField("Region (location)", "location", merged, onChange, disabled)}
        {textField(
          "Credentials hint (env var or file path)",
          "api_key_env",
          merged,
          onChange,
          disabled,
          "GOOGLE_APPLICATION_CREDENTIALS",
        )}
      </div>
    );
  }

  if (provider === "aws_bedrock") {
    return (
      <div className="eo-grid-2" style={{ gap: 12, marginTop: 8 }}>
        {textField("AWS region", "aws_region", merged, onChange, disabled)}
        {textField(
          "Credential hint (env name)",
          "credential_env_hint",
          merged,
          onChange,
          disabled,
          "AWS_PROFILE",
        )}
      </div>
    );
  }

  return (
    <p className="eo-mute" style={{ fontSize: 12 }}>
      Connection values are stored for when this provider is fully wired in the
      worker. Until then, pick OpenAI, Anthropic, Gemini, or on-prem
      OpenAI-compatible for live judge calls.
    </p>
  );
}

// ---------------------------------------------------------------------------
// Evaluation Prompts Tab
// ---------------------------------------------------------------------------

type PromptDraft = {
  dimensionId: string;
  systemPrompt: string;
  userMessageTemplate: string;
  description: string;
};

function PromptsTab() {
  const { t, locale } = useI18n();
  const auth = useAuth();
  const writable = canMutateQuality(auth);
  const qc = useQueryClient();

  const dimensions = useQuery({
    queryKey: ["eval", "judge-dimensions"],
    queryFn: fetchJudgeDimensionCatalog,
  });

  const prompts = useQuery({
    queryKey: ["eval", "judge-prompts"],
    queryFn: () => fetchJudgePrompts(),
  });

  const [selectedDim, setSelectedDim] = useState<string | null>(null);
  const [editing, setEditing] = useState<PromptDraft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (d: PromptDraft) => {
      if (!d.systemPrompt.trim() && !d.userMessageTemplate.trim()) {
        throw new Error("At least one of system prompt or user message template is required");
      }
      return createJudgePrompt({
        dimensionId: d.dimensionId,
        systemPrompt: d.systemPrompt,
        userMessageTemplate: d.userMessageTemplate,
        description: d.description,
      });
    },
    onSuccess: () => {
      setEditing(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["eval", "judge-prompts"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const activate = useMutation({
    mutationFn: activateJudgePromptVersion,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval", "judge-prompts"] });
    },
  });

  const dimList = dimensions.data ?? [];
  const promptList = prompts.data ?? [];

  const promptsByDim = useMemo(() => {
    const map: Record<string, JudgePromptVersion[]> = {};
    for (const p of promptList) {
      if (!map[p.dimensionId]) map[p.dimensionId] = [];
      map[p.dimensionId].push(p);
    }
    return map;
  }, [promptList]);

  const filteredPrompts = selectedDim ? (promptsByDim[selectedDim] ?? []) : [];
  const selectedDimMeta = dimList.find((d) => d.id === selectedDim);

  return (
    <>
      <p className="eo-mute" style={{ fontSize: 12, marginBottom: 12 }}>
        {t("pages.judges.promptsDesc")}
      </p>

      <div style={{ display: "flex", gap: 16 }}>
        {/* Dimension list */}
        <div className="eo-card" style={{ minWidth: 260, maxWidth: 300 }}>
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("pages.judges.dimensions")}</h3>
          </div>
          <div style={{ maxHeight: 480, overflow: "auto" }}>
            {dimList.map((dim) => {
              const activeVer = (promptsByDim[dim.id] ?? []).find((p) => p.isActive);
              return (
                <button
                  key={dim.id}
                  type="button"
                  className="eo-list-item"
                  data-active={selectedDim === dim.id}
                  onClick={() => {
                    setSelectedDim(dim.id);
                    setEditing(null);
                    setError(null);
                  }}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "8px 12px",
                    border: "none",
                    background: selectedDim === dim.id ? "var(--eo-accent-bg, #e0e7ff)" : "transparent",
                    borderRadius: 6,
                    cursor: "pointer",
                    marginBottom: 2,
                  }}
                >
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{dim.title[locale] || dim.title.en}</div>
                  <div className="eo-mute" style={{ fontSize: 11 }}>
                    <span style={{ display: "inline-block", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", verticalAlign: "bottom" }}>
                      {dim.criterion[locale] || dim.criterion.en}
                    </span>
                    {activeVer && (
                      <span style={{ marginLeft: 6, color: "var(--eo-ok, #22c55e)" }}>
                        v{activeVer.version}
                      </span>
                    )}
                    {!activeVer && (
                      <span style={{ marginLeft: 6, opacity: 0.5 }}>default</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Detail panel */}
        <div style={{ flex: 1 }}>
          {!selectedDim && (
            <div className="eo-empty">{t("pages.judges.selectDimHint")}</div>
          )}

          {selectedDim && selectedDimMeta && (
            <>
              <div className="eo-card" style={{ marginBottom: 12 }}>
                <div className="eo-card-h">
                  <h3 className="eo-card-title">
                    {selectedDimMeta.title[locale] || selectedDimMeta.title.en} — {t("pages.judges.promptHistory")}
                  </h3>
                  {writable && (
                    <button
                      type="button"
                      className="eo-btn eo-btn-primary"
                      style={{ marginLeft: "auto" }}
                      onClick={() => {
                        const active = filteredPrompts.find((p) => p.isActive);
                        setEditing({
                          dimensionId: selectedDim,
                          systemPrompt: active?.systemPrompt ?? DEFAULT_JUDGE_SYSTEM_PROMPT,
                          userMessageTemplate: active?.userMessageTemplate ?? DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
                          description: "",
                        });
                        setError(null);
                      }}
                    >
                      {t("pages.judges.newVersion")}
                    </button>
                  )}
                </div>

                <p className="eo-mute" style={{ fontSize: 12, margin: "4px 0 12px" }}>
                  {t("pages.judges.defaultCriterion")}: {selectedDimMeta.criterion[locale] || selectedDimMeta.criterion.en}
                </p>

                {filteredPrompts.length === 0 && (
                  <div className="eo-empty" style={{ padding: 12 }}>
                    {t("pages.judges.noCustomPrompts")}
                  </div>
                )}

                {filteredPrompts.length > 0 && (
                  <div className="eo-table-wrap">
                    <table className="eo-table">
                      <thead>
                        <tr>
                          <th>{t("pages.judges.version")}</th>
                          <th>{t("pages.judges.description")}</th>
                          <th>{t("pages.judges.active")}</th>
                          <th>{t("pages.judges.created")}</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {filteredPrompts.map((p) => (
                          <tr key={p.id}>
                            <td className="mono">v{p.version}</td>
                            <td>{p.description || "—"}</td>
                            <td>
                              <span
                                style={{
                                  display: "inline-block",
                                  width: 10,
                                  height: 10,
                                  borderRadius: 999,
                                  background: p.isActive ? "#22c55e" : "#94a3b8",
                                }}
                              />
                              <span className="eo-mute" style={{ marginLeft: 6, fontSize: 11 }}>
                                {p.isActive ? "active" : "inactive"}
                              </span>
                            </td>
                            <td>{fmtRel(p.createdAt)}</td>
                            <td>
                              {writable && !p.isActive && (
                                <button
                                  type="button"
                                  className="eo-btn eo-btn-ghost"
                                  onClick={() => activate.mutate(p.id)}
                                >
                                  {t("pages.judges.activate")}
                                </button>
                              )}
                              <button
                                type="button"
                                className="eo-btn eo-btn-ghost"
                                onClick={() => {
                                  setEditing({
                                    dimensionId: selectedDim,
                                    systemPrompt: p.systemPrompt,
                                    userMessageTemplate: p.userMessageTemplate,
                                    description: "",
                                  });
                                  setError(null);
                                }}
                              >
                                {writable ? t("pages.judges.cloneEdit") : t("pages.judges.view")}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Editor */}
              {editing && (
                <div className="eo-card">
                  <div className="eo-card-h">
                    <h3 className="eo-card-title">
                      {t("pages.judges.createVersion").replace("{n}", String((filteredPrompts[0]?.version ?? 0) + 1))}
                      {" — "}{selectedDimMeta.title[locale] || selectedDimMeta.title.en}
                    </h3>
                  </div>

                  <label className="eo-field" style={{ marginTop: 8 }}>
                    <span>{t("pages.judges.descLabel")}</span>
                    <input
                      value={editing.description}
                      onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                      placeholder={t("pages.judges.descPlaceholder")}
                      disabled={!writable}
                    />
                  </label>

                  <label className="eo-field" style={{ marginTop: 12 }}>
                    <span>{t("pages.judges.systemPromptLabel")}</span>
                    <textarea
                      value={editing.systemPrompt}
                      onChange={(e) => setEditing({ ...editing, systemPrompt: e.target.value })}
                      rows={5}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                      disabled={!writable}
                    />
                  </label>

                  <label className="eo-field" style={{ marginTop: 12 }}>
                    <span>
                      {t("pages.judges.userMsgLabel")}{" "}
                      <span className="eo-mute" style={{ fontSize: 11 }}>
                        ({t("pages.judges.userMsgHint")})
                      </span>
                    </span>
                    <textarea
                      value={editing.userMessageTemplate}
                      onChange={(e) => setEditing({ ...editing, userMessageTemplate: e.target.value })}
                      rows={8}
                      style={{ fontFamily: "monospace", fontSize: 12 }}
                      disabled={!writable}
                    />
                  </label>

                  {error && (
                    <div className="eo-empty" style={{ color: "var(--eo-err)", marginTop: 8 }}>
                      {error}
                    </div>
                  )}

                  <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
                    <button
                      type="button"
                      className="eo-btn eo-btn-ghost"
                      onClick={() => { setEditing(null); setError(null); }}
                    >
                      {t("pages.judges.cancelBtn")}
                    </button>
                    {writable && (
                      <button
                        type="button"
                        className="eo-btn eo-btn-primary"
                        onClick={() => save.mutate(editing)}
                        disabled={save.isPending}
                      >
                        {save.isPending ? t("pages.judges.saving") : t("pages.judges.saveBtn")}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
