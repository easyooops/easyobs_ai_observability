"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  createEvalRun,
  deleteHumanLabelAnnotation,
  fetchEvalProfiles,
  fetchHumanLabelAnnotations,
  fetchTraceDetail,
  upsertHumanLabelAnnotation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n/context";
import { canMutateQuality } from "../guard";

/** Human review registry embedded under Golden Sets.
 *
 * The previous version was just three fat free-text fields and an opaque
 * verdict select. Operators told us they had no idea *what* to write or
 * *where* their labels were used, so this revamp:
 *
 *   1. Pre-fills the form from a trace id (via `?traceId=` or pasting one),
 *      so the operator sees the actual question/answer they're judging.
 *   2. Replaces the free-text "verdict" with a structured (verdict +
 *      failure category) pair — the categories mirror the cause-code taxonomy
 *      used by Improvement Packs, which is exactly where these labels feed
 *      back in (we surface that "What is this used for?" right above the
 *      form, with concrete examples).
 *   3. Offers ready-made templates so an operator can write a label in
 *      one click for the most common cases (correct, hallucinated,
 *      missing-citation, …).
 */
export function GoldenHumanLabelsTab() {
  const { t, tsub } = useI18n();
  const auth = useAuth();
  const writable = canMutateQuality(auth);
  const search = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const [traceId, setTraceId] = useState("");
  const [verdict, setVerdict] = useState<"pass" | "warn" | "fail">("pass");
  const [category, setCategory] = useState<string>("");
  const [expected, setExpected] = useState("");
  const [notes, setNotes] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  // Selection state for batch evaluation. Set of trace ids the operator
  // has ticked in the registry list.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filterVerdict, setFilterVerdict] = useState<
    "all" | "pass" | "warn" | "fail"
  >("all");
  const [profileId, setProfileId] = useState("");
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    const tid = search.get("traceId") || "";
    if (tid) setTraceId(tid);
  }, [search]);

  // Pre-fetch trace detail so the operator can see what they're labelling.
  const traceDetail = useQuery({
    queryKey: ["trace", "label-preview", traceId.trim()],
    queryFn: () => fetchTraceDetail(traceId.trim()),
    enabled: traceId.trim().length > 8,
  });

  const list = useQuery({
    queryKey: ["eval", "human-labels"],
    queryFn: () => fetchHumanLabelAnnotations(100),
  });

  const profiles = useQuery({
    queryKey: ["eval", "profiles"],
    queryFn: () => fetchEvalProfiles(true),
  });

  const filteredList = useMemo(() => {
    const rows = list.data ?? [];
    if (filterVerdict === "all") return rows;
    return rows.filter((r) => r.humanVerdict === filterVerdict);
  }, [list.data, filterVerdict]);

  const togglePick = (traceId: string) => {
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(traceId)) n.delete(traceId);
      else n.add(traceId);
      return n;
    });
  };
  const pickAllVisible = () => {
    setPicked((prev) => {
      const n = new Set(prev);
      for (const r of filteredList) n.add(r.traceId);
      return n;
    });
  };
  const clearPicks = () => setPicked(new Set());

  const groupRun = useMutation({
    mutationFn: () => {
      const traceIds = Array.from(picked);
      if (traceIds.length === 0)
        throw new Error(
          t("pages.humanLabels.errPickTrace"),
        );
      if (!profileId)
        throw new Error(
          t("pages.humanLabels.errPickProfile"),
        );
      return createEvalRun({
        profileId,
        projectId: null,
        traceIds,
        runMode: "human_label",
        notes: tsub(
          "pages.humanLabels.groupRunNotes",
          { count: String(traceIds.length) },
        ),
        runContext: { uiSource: "human_label_group" },
      });
    },
    onSuccess: (run) => {
      setRunError(null);
      qc.invalidateQueries({ queryKey: ["eval", "runs"] });
      router.push(`/workspace/quality/runs/?run=${encodeURIComponent(run.id)}`);
    },
    onError: (e: Error) => setRunError(e.message),
  });

  const save = useMutation({
    mutationFn: () =>
      upsertHumanLabelAnnotation({
        traceId: traceId.trim(),
        expectedResponse: expected,
        humanVerdict: verdict,
        notes: category ? `[${category}] ${notes}`.trim() : notes,
      }),
    onSuccess: () => {
      setMsg(t("pages.humanLabels.saved"));
      qc.invalidateQueries({ queryKey: ["eval", "human-labels"] });
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => deleteHumanLabelAnnotation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "human-labels"] }),
  });

  const applyTemplate = (tpl: LabelTemplate) => {
    setVerdict(tpl.verdict);
    setCategory(tpl.category);
    setExpected(tpl.expected);
    setNotes(tpl.notes);
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <UsageBanner />

      <div className="eo-grid-2" style={{ alignItems: "start" }}>
        <section className="eo-card">
          <div className="eo-card-h">
            <h3 className="eo-card-title">
              {t("pages.humanLabels.addLabelTitle")}
            </h3>
            <span className="eo-card-sub">
              {t("pages.humanLabels.addLabelSub")}
            </span>
          </div>

          <label className="eo-field">
            <span>{t("pages.humanLabels.traceIdLabel")}</span>
            <input
              className="eo-input"
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              disabled={!writable}
              placeholder={t("pages.humanLabels.traceIdPlaceholder")}
            />
          </label>

          {traceId.trim().length > 8 && (
            <TracePreview
              status={
                traceDetail.isLoading
                  ? "loading"
                  : traceDetail.isError
                    ? "error"
                    : "ok"
              }
              detail={traceDetail.data}
            />
          )}

          <div
            className="eo-card"
            style={{ background: "var(--eo-bg-2)", padding: 8, marginTop: 8 }}
          >
            <div className="eo-card-sub" style={{ marginBottom: 6 }}>
              {t("pages.humanLabels.quickTemplates")}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {LABEL_TEMPLATES(t).map((tpl) => (
                <button
                  key={tpl.id}
                  type="button"
                  className="eo-btn eo-btn-ghost"
                  style={{ fontSize: 11 }}
                  onClick={() => applyTemplate(tpl)}
                  disabled={!writable}
                  title={tpl.hint}
                >
                  {tpl.label}
                </button>
              ))}
            </div>
          </div>

          <div className="eo-grid-2" style={{ gap: 8 }}>
            <label className="eo-field">
              <span>{t("pages.humanLabels.verdictLabel")}</span>
              <select
                className="eo-input"
                value={verdict}
                onChange={(e) =>
                  setVerdict(e.target.value as "pass" | "warn" | "fail")
                }
                disabled={!writable}
              >
                <option value="pass">
                  {t("pages.humanLabels.verdictPass")}
                </option>
                <option value="warn">
                  {t("pages.humanLabels.verdictWarn")}
                </option>
                <option value="fail">
                  {t("pages.humanLabels.verdictFail")}
                </option>
              </select>
            </label>
            <label className="eo-field">
              <span>{t("pages.humanLabels.failureCategory")}</span>
              <select
                className="eo-input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={!writable}
              >
                <option value="">
                  {t("pages.humanLabels.categoryNone")}
                </option>
                {CATEGORY_GROUPS.map((g) => (
                  <optgroup key={g.title} label={g.title}>
                    {g.items.map((it) => (
                      <option key={it.code} value={it.code}>
                        {it.code} — {t(`pages.humanLabels.category.${it.code}`)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>

          <label className="eo-field">
            <span>
              {t("pages.humanLabels.expectedLabel")}
            </span>
            <textarea
              className="eo-input"
              rows={3}
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
              disabled={!writable}
              placeholder={t("pages.humanLabels.expectedPlaceholder")}
            />
          </label>

          <label className="eo-field">
            <span>{t("pages.humanLabels.notesLabel")}</span>
            <textarea
              className="eo-input"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={!writable}
              placeholder={t("pages.humanLabels.notesPlaceholder")}
            />
          </label>

          {msg && (
            <p className="eo-mute" style={{ fontSize: 12 }}>
              {msg}
            </p>
          )}
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={!writable || !traceId.trim() || save.isPending}
            onClick={() => {
              setMsg(null);
              save.mutate();
            }}
          >
            {save.isPending
              ? t("pages.humanLabels.saving")
              : t("pages.humanLabels.save")}
          </button>
        </section>

        <section className="eo-card">
          <div className="eo-card-h">
            <h3 className="eo-card-title">
              {t("pages.humanLabels.registryTitle")}
            </h3>
            <span className="eo-card-sub">
              {tsub(
                "pages.humanLabels.registrySub",
                { labelled: String((list.data ?? []).length), selected: String(picked.size) },
              )}
            </span>
          </div>

          <div
            className="eo-card"
            style={{
              background: "var(--eo-bg-2)",
              padding: 8,
              marginBottom: 10,
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 6,
                alignItems: "center",
                flexWrap: "wrap",
              }}
            >
              <strong style={{ fontSize: 12 }}>
                {t("pages.humanLabels.runOnSelected")}
              </strong>
              <span
                className="eo-mute"
                style={{ fontSize: 11, marginLeft: "auto" }}
              >
                {t("pages.humanLabels.runCompareHint")}
              </span>
            </div>
            <div
              style={{
                display: "flex",
                gap: 6,
                alignItems: "center",
                marginTop: 6,
                flexWrap: "wrap",
              }}
            >
              <select
                className="eo-input"
                value={profileId}
                onChange={(e) => setProfileId(e.target.value)}
                disabled={!writable || groupRun.isPending}
                style={{ flex: "1 1 220px", minWidth: 180 }}
              >
                <option value="">
                  {t("pages.humanLabels.pickProfile")}
                </option>
                {(profiles.data ?? [])
                  .filter((p) => p.enabled)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.judgeModels.length === 0 ? " (rules only)" : ""}
                    </option>
                  ))}
              </select>
              <select
                className="eo-input"
                value={filterVerdict}
                onChange={(e) =>
                  setFilterVerdict(
                    e.target.value as "all" | "pass" | "warn" | "fail",
                  )
                }
                style={{ minWidth: 120 }}
                title={t("pages.humanLabels.filterByVerdict")}
              >
                <option value="all">{t("pages.humanLabels.allVerdicts")}</option>
                <option value="pass">pass</option>
                <option value="warn">warn</option>
                <option value="fail">fail</option>
              </select>
              <button
                type="button"
                className="eo-btn eo-btn-ghost"
                onClick={pickAllVisible}
                disabled={filteredList.length === 0}
              >
                {tsub(
                  "pages.humanLabels.selectVisible",
                  { count: String(filteredList.length) },
                )}
              </button>
              <button
                type="button"
                className="eo-btn eo-btn-ghost"
                onClick={clearPicks}
                disabled={picked.size === 0}
              >
                {t("pages.humanLabels.clear")}
              </button>
              <button
                type="button"
                className="eo-btn eo-btn-primary"
                disabled={
                  !writable ||
                  picked.size === 0 ||
                  !profileId ||
                  groupRun.isPending
                }
                onClick={() => {
                  setRunError(null);
                  groupRun.mutate();
                }}
              >
                {groupRun.isPending
                  ? t("pages.humanLabels.launching")
                  : tsub(
                      "pages.humanLabels.runCount",
                      { count: String(picked.size) },
                    )}
              </button>
            </div>
            {runError && (
              <div
                className="eo-empty"
                style={{ color: "var(--eo-err)", marginTop: 6 }}
              >
                {runError}
              </div>
            )}
          </div>

          {list.isLoading && <p className="eo-mute">…</p>}
          {!list.isLoading && (list.data ?? []).length === 0 && (
            <p className="eo-empty">{t("pages.humanLabels.empty")}</p>
          )}
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
            {filteredList.map((row) => {
              const isPicked = picked.has(row.traceId);
              return (
                <li
                  key={row.id}
                  style={{
                    padding: "8px 0",
                    borderBottom: "1px solid var(--eo-border)",
                    fontSize: 13,
                    display: "flex",
                    gap: 8,
                    alignItems: "flex-start",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={isPicked}
                    onChange={() => togglePick(row.traceId)}
                    aria-label="select for batch evaluation"
                    style={{ marginTop: 2 }}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 8,
                      }}
                    >
                      <code
                        className="mono"
                        style={{ wordBreak: "break-all" }}
                      >
                        {row.traceId}
                      </code>
                      <span
                        className="eo-status"
                        data-tone={
                          row.humanVerdict === "fail"
                            ? "err"
                            : row.humanVerdict === "warn"
                              ? "warn"
                              : "ok"
                        }
                      >
                        {row.humanVerdict}
                      </span>
                    </div>
                    {row.expectedResponse && (
                      <div
                        className="eo-mute"
                        style={{ marginTop: 4, fontSize: 12 }}
                      >
                        {row.expectedResponse.slice(0, 200)}
                        {row.expectedResponse.length > 200 ? "…" : ""}
                      </div>
                    )}
                    <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
                      <Link
                        href={`/workspace/tracing/detail/?id=${encodeURIComponent(row.traceId)}`}
                        className="eo-link"
                      >
                        {t("pages.humanLabels.openTrace")}
                      </Link>
                      {writable && (
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          style={{ fontSize: 12 }}
                          onClick={() => {
                            if (
                              confirm(
                                t("pages.humanLabels.deleteConfirm"),
                              )
                            )
                              del.mutate(row.id);
                          }}
                        >
                          {t("pages.humanLabels.delete")}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}

function UsageBanner() {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  return (
    <div
      className="eo-card"
      style={{ background: "var(--eo-bg-2)", padding: 10 }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          all: "unset",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 8,
          width: "100%",
          fontSize: 12,
          color: "var(--eo-mute)",
        }}
        aria-expanded={open}
      >
        <span style={{ fontFamily: "var(--eo-mono)" }}>
          {open ? "▾" : "▸"}
        </span>
        <strong style={{ color: "var(--eo-ink)" }}>
          {t("pages.humanLabels.usageTitle")}
        </strong>
      </button>
      {open && (
        <>
          <ul
            style={{
              margin: "8px 0 0",
              paddingLeft: 18,
              fontSize: 12,
              lineHeight: 1.55,
            }}
          >
            <li>
              {t("pages.humanLabels.usageGroupRun")}
            </li>
            <li>
              {t("pages.humanLabels.usageAdjudicate")}
            </li>
            <li>
              {t("pages.humanLabels.usagePromote")}
            </li>
            <li>
              {t("pages.humanLabels.usageImprovement")}
            </li>
            <li>
              {t("pages.humanLabels.usageCalibration")}
            </li>
          </ul>
          <div
            style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}
          >
            <a
              href="/workspace/quality/runs/?src=human_label"
              className="eo-btn eo-btn-ghost"
              style={{ fontSize: 11 }}
            >
              {t("pages.humanLabels.usageOpenRuns")}
            </a>
          </div>
        </>
      )}
    </div>
  );
}

function TracePreview({
  status,
  detail,
}: {
  status: "loading" | "error" | "ok";
  detail?: Awaited<ReturnType<typeof fetchTraceDetail>>;
}) {
  const { t } = useI18n();
  if (status === "loading")
    return (
      <div className="eo-empty" style={{ marginTop: 6 }}>
        {t("pages.humanLabels.previewLoading")}
      </div>
    );
  if (status === "error" || !detail)
    return (
      <div className="eo-empty" style={{ marginTop: 6 }}>
        {t("pages.humanLabels.previewNotFound")}
      </div>
    );
  return (
    <div
      className="eo-card"
      style={{ background: "var(--eo-bg-2)", padding: 8, marginTop: 6 }}
    >
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
          fontSize: 12,
        }}
      >
        <strong style={{ fontSize: 13 }}>
          {detail.rootName || t("pages.humanLabels.previewUnnamed")}
        </strong>
        <span className="eo-tag">{detail.serviceName ?? "—"}</span>
        <span
          className="eo-status"
          data-tone={
            detail.status === "ERROR" ? "err" : detail.status === "OK" ? "ok" : ""
          }
        >
          {detail.status}
        </span>
        <Link
          href={`/workspace/tracing/detail/?id=${encodeURIComponent(detail.traceId)}`}
          className="eo-link"
          style={{ marginLeft: "auto", fontSize: 11 }}
        >
          {t("pages.humanLabels.previewOpenFull")}
        </Link>
      </div>
    </div>
  );
}

type LabelTemplate = {
  id: string;
  label: string;
  hint: string;
  verdict: "pass" | "warn" | "fail";
  category: string;
  expected: string;
  notes: string;
};

function LABEL_TEMPLATES(t: (key: string) => string): LabelTemplate[] {
  return [
    {
      id: "correct",
      label: t("pages.humanLabels.tpl.correct.label"),
      hint: t("pages.humanLabels.tpl.correct.hint"),
      verdict: "pass",
      category: "",
      expected: "",
      notes: t("pages.humanLabels.tpl.correct.notes"),
    },
    {
      id: "hallucination",
      label: t("pages.humanLabels.tpl.hallucination.label"),
      hint: t("pages.humanLabels.tpl.hallucination.hint"),
      verdict: "fail",
      category: "gen.hallucination",
      expected: "",
      notes: t("pages.humanLabels.tpl.hallucination.notes"),
    },
    {
      id: "missing-citation",
      label: t("pages.humanLabels.tpl.missingCitation.label"),
      hint: t("pages.humanLabels.tpl.missingCitation.hint"),
      verdict: "warn",
      category: "gen.citation_wrong",
      expected: "",
      notes: t("pages.humanLabels.tpl.missingCitation.notes"),
    },
    {
      id: "retrieval-miss",
      label: t("pages.humanLabels.tpl.retrievalMiss.label"),
      hint: t("pages.humanLabels.tpl.retrievalMiss.hint"),
      verdict: "fail",
      category: "retrieval.miss",
      expected: "",
      notes: t("pages.humanLabels.tpl.retrievalMiss.notes"),
    },
    {
      id: "wrong-tool",
      label: t("pages.humanLabels.tpl.wrongTool.label"),
      hint: t("pages.humanLabels.tpl.wrongTool.hint"),
      verdict: "fail",
      category: "tool.wrong",
      expected: "",
      notes: t("pages.humanLabels.tpl.wrongTool.notes"),
    },
    {
      id: "policy-violation",
      label: t("pages.humanLabels.tpl.policyViolation.label"),
      hint: t("pages.humanLabels.tpl.policyViolation.hint"),
      verdict: "fail",
      category: "gen.policy_violation",
      expected: "",
      notes: t("pages.humanLabels.tpl.policyViolation.notes"),
    },
  ];
}

const CATEGORY_GROUPS: {
  title: string;
  items: { code: string }[];
}[] = [
  {
    title: "Generation (D)",
    items: [
      { code: "gen.hallucination" },
      { code: "gen.unfaithful" },
      { code: "gen.incorrect" },
      { code: "gen.incomplete" },
      { code: "gen.citation_wrong" },
      { code: "gen.policy_violation" },
      { code: "gen.format_invalid" },
    ],
  },
  {
    title: "Retrieval (B)",
    items: [
      { code: "retrieval.miss" },
      { code: "retrieval.noise_high" },
      { code: "retrieval.first_hit_late" },
    ],
  },
  {
    title: "Tool / agent (E)",
    items: [
      { code: "tool.wrong" },
      { code: "tool.arg_invalid" },
      { code: "agent.plan_wrong" },
    ],
  },
  {
    title: "Query (A)",
    items: [
      { code: "query.intent_mismatch" },
      { code: "query.ambiguous" },
    ],
  },
  {
    title: "Operational (F)",
    items: [
      { code: "ops.latency_over" },
      { code: "ops.cost_over" },
      { code: "ops.failure" },
    ],
  },
];
