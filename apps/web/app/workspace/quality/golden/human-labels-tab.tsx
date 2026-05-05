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
import { useBilingual } from "@/lib/i18n/bilingual";
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
  const { t } = useI18n();
  const b = useBilingual();
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
          b(
            "Pick at least one labelled trace.",
            "최소 1건 이상 라벨된 trace 를 선택하세요.",
          ),
        );
      if (!profileId)
        throw new Error(
          b("Pick an evaluation profile.", "평가 프로필을 선택하세요."),
        );
      return createEvalRun({
        profileId,
        projectId: null,
        traceIds,
        runMode: "human_label",
        notes: b(
          `Group evaluation of ${traceIds.length} human-labelled traces`,
          `휴먼 라벨 ${traceIds.length}건 그룹 평가`,
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
              {b("Add a human label", "휴먼 라벨 등록")}
            </h3>
            <span className="eo-card-sub">
              {b(
                "Trace id → verdict → optional ground truth",
                "Trace id → 판정 → (선택) 정답",
              )}
            </span>
          </div>

          <label className="eo-field">
            <span>{b("Trace id", "Trace id")}</span>
            <input
              className="eo-input"
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              disabled={!writable}
              placeholder={b(
                "Paste trace id, or click + Label from a trace",
                "trace id 를 붙여넣거나 trace 화면의 + Label 버튼 사용",
              )}
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
              {b("Quick templates", "빠른 템플릿")}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {LABEL_TEMPLATES(b).map((tpl) => (
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
              <span>{b("Verdict", "판정")}</span>
              <select
                className="eo-input"
                value={verdict}
                onChange={(e) =>
                  setVerdict(e.target.value as "pass" | "warn" | "fail")
                }
                disabled={!writable}
              >
                <option value="pass">
                  {b("pass — correct", "pass — 정상")}
                </option>
                <option value="warn">
                  {b("warn — partial / minor issue", "warn — 부분 정답")}
                </option>
                <option value="fail">
                  {b("fail — incorrect / blocked", "fail — 오답 / 차단")}
                </option>
              </select>
            </label>
            <label className="eo-field">
              <span>{b("Failure category", "오류 카테고리")}</span>
              <select
                className="eo-input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={!writable}
              >
                <option value="">
                  {b("— none / pass —", "— 해당 없음 —")}
                </option>
                {CATEGORY_GROUPS.map((g) => (
                  <optgroup key={g.title} label={g.title}>
                    {g.items.map((it) => (
                      <option key={it.code} value={it.code}>
                        {it.code} — {b(it.titleEn, it.titleKo)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          </div>

          <label className="eo-field">
            <span>
              {b(
                "Expected response (ground truth, optional)",
                "정답 응답 (선택, ground truth)",
              )}
            </span>
            <textarea
              className="eo-input"
              rows={3}
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
              disabled={!writable}
              placeholder={b(
                "What should the agent have said? Used as L3 ground-truth when promoted.",
                "에이전트가 어떤 답을 했어야 하나? 승급 시 L3 ground-truth 로 사용.",
              )}
            />
          </label>

          <label className="eo-field">
            <span>{b("Reviewer notes", "리뷰어 메모")}</span>
            <textarea
              className="eo-input"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={!writable}
              placeholder={b(
                "Why this verdict? Cite the span / chunk if relevant.",
                "왜 그렇게 판정했는지. 관련 span/chunk 도 적어주세요.",
              )}
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
              {b(
                `${(list.data ?? []).length} labelled · ${picked.size} selected`,
                `${(list.data ?? []).length} 건 · ${picked.size} 선택`,
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
                {b("Run evaluation on selected", "선택한 라벨로 평가 실행")}
              </strong>
              <span
                className="eo-mute"
                style={{ fontSize: 11, marginLeft: "auto" }}
              >
                {b(
                  "Compares human verdict ↔ rule + judge",
                  "휴먼 판정과 rule + judge 비교",
                )}
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
                  {b("— pick profile —", "— 프로필 선택 —")}
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
                title={b("Filter by verdict", "판정으로 필터")}
              >
                <option value="all">{b("All verdicts", "모든 판정")}</option>
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
                {b(
                  `Select visible (${filteredList.length})`,
                  `보이는 ${filteredList.length}건 선택`,
                )}
              </button>
              <button
                type="button"
                className="eo-btn eo-btn-ghost"
                onClick={clearPicks}
                disabled={picked.size === 0}
              >
                {b("Clear", "해제")}
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
                  ? b("Launching…", "실행 중…")
                  : b(
                      `Run ${picked.size} label(s) ▶`,
                      `${picked.size}건 평가 ▶`,
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
                                b(
                                  "Delete this label?",
                                  "이 라벨을 삭제할까요?",
                                ),
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
  const b = useBilingual();
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
          {b(
            "Where do these labels go?",
            "이 라벨은 어디에 쓰이나요?",
          )}
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
              {b(
                "Group + Run: tick rows on the right and click Run ▶ to evaluate that batch — human verdicts become ground-truth, the engine compares Rule + Judge against them.",
                "그룹 평가 — 우측 목록에서 행을 체크하고 [Run ▶] 을 누르면 휴먼 판정을 정답으로 두고 Rule + Judge 를 비교 평가합니다.",
              )}
            </li>
            <li>
              {b(
                "Adjudicate Judge disagreements: when LLM judges split, your label becomes the ground-truth.",
                "Judge 가 의견이 갈릴 때 — 휴먼 라벨이 정답이 됩니다.",
              )}
            </li>
            <li>
              {b(
                "Promote into a Golden Set: pass labels become regression items, fails become L3 ground-truth.",
                "Golden Set 승급 — pass 는 회귀 항목, fail 은 L3 정답으로.",
              )}
            </li>
            <li>
              {b(
                "Drive Improvement Packs: failure category routes to the right remediation (prompt vs retrieval vs tool).",
                "Improvement Pack 라우팅 — 카테고리에 따라 prompt/retrieval/tool 개선안이 추천됩니다.",
              )}
            </li>
            <li>
              {b(
                "Trust calibration: human ↔ judge agreement (Cohen κ) is shown on every Run.",
                "신뢰도 보정 — Run 결과의 Human ↔ Judge κ 메트릭이 자동 계산됩니다.",
              )}
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
              {b(
                "Or open Runs → Human-labeled source →",
                "또는 Runs → 휴먼 라벨 source 로 이동 →",
              )}
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
  const b = useBilingual();
  if (status === "loading")
    return (
      <div className="eo-empty" style={{ marginTop: 6 }}>
        {b("Loading trace…", "trace 불러오는 중…")}
      </div>
    );
  if (status === "error" || !detail)
    return (
      <div className="eo-empty" style={{ marginTop: 6 }}>
        {b("No trace found for this id.", "해당 trace 를 찾을 수 없습니다.")}
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
          {detail.rootName || b("(unnamed)", "(이름 없음)")}
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
          {b("Open full view →", "전체 보기 →")}
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

function LABEL_TEMPLATES(b: (en: string, ko: string) => string): LabelTemplate[] {
  return [
    {
      id: "correct",
      label: b("✓ Correct", "✓ 정상"),
      hint: b(
        "Use when the agent answered correctly.",
        "에이전트가 정확히 답했을 때.",
      ),
      verdict: "pass",
      category: "",
      expected: "",
      notes: b("Looks good.", "정상 응답."),
    },
    {
      id: "hallucination",
      label: b("✗ Hallucination", "✗ 환각"),
      hint: b(
        "Agent invented facts not in context.",
        "컨텍스트에 없는 사실을 만들어냈을 때.",
      ),
      verdict: "fail",
      category: "gen.hallucination",
      expected: "",
      notes: b(
        "Claim not supported by retrieved context.",
        "검색된 컨텍스트에 없는 주장.",
      ),
    },
    {
      id: "missing-citation",
      label: b("✗ Missing citation", "✗ 인용 누락"),
      hint: b(
        "Answer is correct but lacks required citations.",
        "답은 맞지만 인용이 없음.",
      ),
      verdict: "warn",
      category: "gen.citation_wrong",
      expected: "",
      notes: b(
        "Required [doc:id] citations are missing.",
        "필수 인용이 빠져있습니다.",
      ),
    },
    {
      id: "retrieval-miss",
      label: b("✗ Retrieval miss", "✗ 검색 실패"),
      hint: b(
        "The right document wasn't retrieved.",
        "정답 문서가 검색되지 않았을 때.",
      ),
      verdict: "fail",
      category: "retrieval.miss",
      expected: "",
      notes: b(
        "Top-K does not include the relevant doc.",
        "Top-K 에 정답 문서가 없습니다.",
      ),
    },
    {
      id: "wrong-tool",
      label: b("✗ Wrong tool", "✗ 잘못된 도구"),
      hint: b(
        "Agent picked the wrong tool / function call.",
        "에이전트가 잘못된 도구를 선택했을 때.",
      ),
      verdict: "fail",
      category: "tool.wrong",
      expected: "",
      notes: b("Selected tool does not match the user's intent.", "사용자 의도와 다른 도구."),
    },
    {
      id: "policy-violation",
      label: b("✗ Policy violation", "✗ 정책 위반"),
      hint: b(
        "Output contains banned content.",
        "정책 위반 내용 포함.",
      ),
      verdict: "fail",
      category: "gen.policy_violation",
      expected: "",
      notes: b(
        "Response violates content policy.",
        "응답이 정책을 위반했습니다.",
      ),
    },
  ];
}

const CATEGORY_GROUPS: {
  title: string;
  items: { code: string; titleEn: string; titleKo: string }[];
}[] = [
  {
    title: "Generation (D)",
    items: [
      {
        code: "gen.hallucination",
        titleEn: "hallucination",
        titleKo: "환각",
      },
      {
        code: "gen.unfaithful",
        titleEn: "unfaithful / not grounded",
        titleKo: "근거 없음",
      },
      {
        code: "gen.incorrect",
        titleEn: "incorrect answer",
        titleKo: "오답",
      },
      {
        code: "gen.incomplete",
        titleEn: "incomplete answer",
        titleKo: "불완전한 답",
      },
      {
        code: "gen.citation_wrong",
        titleEn: "wrong / missing citation",
        titleKo: "인용 오류",
      },
      {
        code: "gen.policy_violation",
        titleEn: "policy violation",
        titleKo: "정책 위반",
      },
      {
        code: "gen.format_invalid",
        titleEn: "format invalid",
        titleKo: "형식 오류",
      },
    ],
  },
  {
    title: "Retrieval (B)",
    items: [
      {
        code: "retrieval.miss",
        titleEn: "relevant doc not in top-K",
        titleKo: "정답 문서 누락",
      },
      {
        code: "retrieval.noise_high",
        titleEn: "too much irrelevant chunks",
        titleKo: "노이즈 chunk 많음",
      },
      {
        code: "retrieval.first_hit_late",
        titleEn: "first hit too low rank",
        titleKo: "첫 적중 순위 늦음",
      },
    ],
  },
  {
    title: "Tool / agent (E)",
    items: [
      {
        code: "tool.wrong",
        titleEn: "wrong tool selected",
        titleKo: "잘못된 도구 선택",
      },
      {
        code: "tool.arg_invalid",
        titleEn: "invalid tool arguments",
        titleKo: "도구 인자 오류",
      },
      {
        code: "agent.plan_wrong",
        titleEn: "wrong multi-step plan",
        titleKo: "잘못된 추론 계획",
      },
    ],
  },
  {
    title: "Query (A)",
    items: [
      {
        code: "query.intent_mismatch",
        titleEn: "intent classification wrong",
        titleKo: "의도 분류 오류",
      },
      {
        code: "query.ambiguous",
        titleEn: "ambiguous user input",
        titleKo: "모호한 입력",
      },
    ],
  },
  {
    title: "Operational (F)",
    items: [
      {
        code: "ops.latency_over",
        titleEn: "latency over budget",
        titleKo: "지연 초과",
      },
      {
        code: "ops.cost_over",
        titleEn: "cost over budget",
        titleKo: "비용 초과",
      },
      {
        code: "ops.failure",
        titleEn: "error / timeout",
        titleKo: "오류 / 타임아웃",
      },
    ],
  },
];
