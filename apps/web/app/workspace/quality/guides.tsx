"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchEvaluatorCatalog,
  type EvaluatorCatalogEntry,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

/** Golden layer blurbs for tooltips and legends — follows UI locale. */
export function useGoldenLayerHelp() {
  const { t } = useI18n();
  return useMemo(
    () =>
      ({
        L1: { title: t("guide.golden.L1.title"), body: t("guide.golden.L1.body") },
        L2: { title: t("guide.golden.L2.title"), body: t("guide.golden.L2.body") },
        L3: { title: t("guide.golden.L3.title"), body: t("guide.golden.L3.body") },
      }) as const,
    [t],
  );
}

const tabBtn = "eo-guide-tab";
const tabRow = "eo-guide-tabs";
const panel = "eo-guide-panel";

type MetricRow = {
  code: string;
  name: string;
  mode: string;
  gt: string;
  cost: string;
  cause: string;
  notes: string;
};

function _metricCost(mode: string): string {
  if (mode.includes("H")) return "$$$";
  if (mode.includes("J") && mode.includes("R")) return "$ / $$";
  if (mode.includes("J")) return "$$";
  return "$";
}

function _metricGroupsFromCatalog(catalog: EvaluatorCatalogEntry[]) {
  const metricRows = catalog
    .filter((c) => c.id.startsWith("metric.") && c.metricCode)
    .map((c) => ({
      code: String(c.metricCode),
      name: c.name,
      mode: c.evaluationMode ?? "R",
      gt: c.gt ?? "—",
      cost: _metricCost(c.evaluationMode ?? "R"),
      cause: c.causeCode ?? "",
      notes: c.description,
    }));
  const grouped = new Map<string, MetricRow[]>();
  for (const r of metricRows) {
    const g = r.code[0] ?? "?";
    const cur = grouped.get(g) ?? [];
    cur.push(r);
    grouped.set(g, cur);
  }
  const titles: Record<string, string> = {
    A: "A. Query — input understanding",
    B: "B. Retrieval",
    C: "C. Context / chunk quality",
    D: "D. Generation — response",
    E: "E. Tool / agent",
    F: "F. Operational",
    G: "G. Human feedback",
  };
  return Array.from(grouped.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, rows]) => ({
      key,
      title: `${titles[key] ?? key} (${rows.length})`,
      rows: rows.sort((a, b) => {
        const ai = Number.parseInt(a.code.slice(1), 10) || 0;
        const bi = Number.parseInt(b.code.slice(1), 10) || 0;
        return ai - bi;
      }),
    }));
}

const METRIC_GROUPS: { key: string; title: string; rows: MetricRow[] }[] = [
  {
    key: "A",
    title: "A. Query — input understanding (6)",
    rows: [
      { code: "A1", name: "Intent classification accuracy", mode: "R / J", gt: "L1", cost: "$ / $$", cause: "query.intent_mismatch", notes: "Compare L1.intent vs model classification." },
      { code: "A2", name: "Query rewrite semantic preservation", mode: "J", gt: "—", cost: "$$", cause: "query.rewrite_drift", notes: "Original vs rewritten query meaning." },
      { code: "A3", name: "Ambiguity detection rate", mode: "J", gt: "—", cost: "$$", cause: "query.ambiguous", notes: "Share of ambiguous user prompts." },
      { code: "A4", name: "Missing slot detection", mode: "R", gt: "—", cost: "$", cause: "query.slot_missing", notes: "Required parameters vs schema." },
      { code: "A5", name: "Language detection accuracy", mode: "R", gt: "—", cost: "$", cause: "query.lang_mismatch", notes: "Detector vs response language." },
      { code: "A6", name: "Query complexity score", mode: "R", gt: "—", cost: "$", cause: "query.complexity_high", notes: "Simple vs multi-hop heuristic." },
    ],
  },
  {
    key: "B",
    title: "B. Retrieval (12)",
    rows: [
      { code: "B1", name: "Recall@K", mode: "R", gt: "L2", cost: "$", cause: "retrieval.recall_low", notes: "Relevant doc IDs in top-K." },
      { code: "B2", name: "Precision@K", mode: "R", gt: "L2", cost: "$", cause: "retrieval.noise_high", notes: "Relevant share of top-K." },
      { code: "B3", name: "MRR", mode: "R", gt: "L2", cost: "$", cause: "retrieval.first_hit_late", notes: "Reciprocal rank of first hit." },
      { code: "B4", name: "HitRate@K", mode: "R", gt: "L2", cost: "$", cause: "retrieval.miss", notes: "Any relevant doc in top-K (0/1)." },
      { code: "B5", name: "nDCG@K", mode: "R", gt: "L2", cost: "$", cause: "retrieval.rank_quality", notes: "Graded relevance ranking." },
      { code: "B6", name: "MAP", mode: "R", gt: "L2", cost: "$", cause: "retrieval.precision_low", notes: "Mean average precision." },
      { code: "B7", name: "Chunk relevance score", mode: "J", gt: "—", cost: "$$", cause: "retrieval.chunk_irrelevant", notes: "Per-chunk relevance to query." },
      { code: "B8", name: "Context coverage", mode: "J", gt: "L3", cost: "$$", cause: "retrieval.coverage_low", notes: "Enough evidence retrieved for answer." },
      { code: "B9", name: "Metadata filter accuracy", mode: "R", gt: "L2", cost: "$", cause: "retrieval.filter_wrong", notes: "Filters applied as specified." },
      { code: "B10", name: "Reranker gain", mode: "R", gt: "L2", cost: "$", cause: "retrieval.rerank_no_gain", notes: "nDCG delta pre/post rerank." },
      { code: "B11", name: "Retrieval diversity", mode: "R", gt: "—", cost: "$", cause: "retrieval.dup_high", notes: "Near-duplicate density in top-K." },
      { code: "B12", name: "Retrieval latency budget", mode: "R", gt: "—", cost: "$", cause: "retrieval.latency_over", notes: "SLA for search step." },
    ],
  },
  {
    key: "C",
    title: "C. Context / chunk quality (7)",
    rows: [
      { code: "C1", name: "Chunk noise ratio", mode: "R / J", gt: "—", cost: "$ / $$", cause: "context.noise", notes: "Irrelevant chunks in context window." },
      { code: "C2", name: "Duplicate chunk ratio", mode: "R", gt: "—", cost: "$", cause: "context.dup", notes: "Repeated chunks inflate tokens." },
      { code: "C3", name: "Context token waste", mode: "R", gt: "—", cost: "$", cause: "context.token_waste", notes: "Tokens vs information used." },
      { code: "C4", name: "Evidence sufficiency", mode: "J", gt: "L3", cost: "$$", cause: "context.insufficient", notes: "Can the answer be supported?" },
      { code: "C5", name: "Context ordering quality", mode: "J", gt: "—", cost: "$$", cause: "context.order_bad", notes: "Important evidence placement." },
      { code: "C6", name: "Source attribution completeness", mode: "R", gt: "—", cost: "$", cause: "context.attribution_missing", notes: "Citations present where required." },
      { code: "C7", name: "Chunk semantic similarity", mode: "R", gt: "—", cost: "$", cause: "context.sim_low", notes: "Query–chunk embedding similarity." },
    ],
  },
  {
    key: "D",
    title: "D. Generation — response (12)",
    rows: [
      { code: "D1", name: "Answer relevance", mode: "J", gt: "—", cost: "$$", cause: "gen.relevance_low", notes: "On-topic vs user ask." },
      { code: "D2", name: "Correctness", mode: "J / H", gt: "L3", cost: "$$ / $$$", cause: "gen.incorrect", notes: "must_include / reference answer." },
      { code: "D3", name: "Faithfulness / groundedness", mode: "J", gt: "—", cost: "$$", cause: "gen.unfaithful", notes: "Claims supported by context." },
      { code: "D4", name: "Hallucination rate", mode: "J", gt: "—", cost: "$$", cause: "gen.hallucination", notes: "Facts not in context." },
      { code: "D5", name: "Completeness", mode: "J", gt: "L3", cost: "$$", cause: "gen.incomplete", notes: "Required facets covered." },
      { code: "D6", name: "Conciseness", mode: "R / J", gt: "—", cost: "$ / $$", cause: "gen.verbose", notes: "Length / repetition budget." },
      { code: "D7", name: "Tone consistency", mode: "J", gt: "—", cost: "$$", cause: "gen.tone_off", notes: "Brand / policy voice." },
      { code: "D8", name: "Policy compliance", mode: "R / J", gt: "—", cost: "$ / $$", cause: "gen.policy_violation", notes: "Denylists, must_not_include." },
      { code: "D9", name: "Citation accuracy", mode: "R", gt: "L3", cost: "$", cause: "gen.citation_wrong", notes: "doc_id-level citation check." },
      { code: "D10", name: "Structured format validity", mode: "R", gt: "—", cost: "$", cause: "gen.format_invalid", notes: "JSON / schema conformance." },
      { code: "D11", name: "Language quality", mode: "J", gt: "—", cost: "$$", cause: "gen.grammar", notes: "Grammar and readability." },
      { code: "D12", name: "Refusal appropriateness", mode: "J", gt: "—", cost: "$$", cause: "gen.refusal_bad", notes: "When to answer vs refuse." },
    ],
  },
  {
    key: "E",
    title: "E. Tool / agent (8)",
    rows: [
      { code: "E1", name: "Tool selection accuracy", mode: "R / J", gt: "L1", cost: "$ / $$", cause: "tool.wrong", notes: "Expected vs actual tool." },
      { code: "E2", name: "Tool argument validity", mode: "R", gt: "—", cost: "$", cause: "tool.arg_invalid", notes: "Schema validation." },
      { code: "E3", name: "Tool call success rate", mode: "R", gt: "—", cost: "$", cause: "tool.fail", notes: "HTTP / exception free." },
      { code: "E4", name: "Tool retry count", mode: "R", gt: "—", cost: "$", cause: "tool.retry_high", notes: "Retries over threshold." },
      { code: "E5", name: "Unnecessary tool call ratio", mode: "J", gt: "—", cost: "$$", cause: "tool.over_call", notes: "Redundant calls." },
      { code: "E6", name: "Multi-step plan correctness", mode: "J", gt: "L1", cost: "$$", cause: "agent.plan_wrong", notes: "Plan quality." },
      { code: "E7", name: "Reasoning path length", mode: "R", gt: "—", cost: "$", cause: "agent.path_long", notes: "Step count heuristic." },
      { code: "E8", name: "Final synthesis quality", mode: "J", gt: "—", cost: "$$", cause: "agent.synthesis_low", notes: "Merge multi-tool outputs." },
    ],
  },
  {
    key: "F",
    title: "F. Operational (5)",
    rows: [
      { code: "F1", name: "End-to-end latency", mode: "R", gt: "—", cost: "$", cause: "ops.latency_over", notes: "Trace duration vs budget." },
      { code: "F2", name: "Model inference latency", mode: "R", gt: "—", cost: "$", cause: "ops.gen_slow", notes: "LLM span duration." },
      { code: "F3", name: "Cost per trace", mode: "R", gt: "—", cost: "$", cause: "ops.cost_over", notes: "USD / trace cap." },
      { code: "F4", name: "Token consumption", mode: "R", gt: "—", cost: "$", cause: "ops.token_over", notes: "Input+output token budget." },
      { code: "F5", name: "Failure / timeout rate", mode: "R", gt: "—", cost: "$", cause: "ops.failure", notes: "ERROR status / timeouts." },
    ],
  },
  {
    key: "G",
    title: "G. Human feedback (5)",
    rows: [
      { code: "G1", name: "User thumbs-up ratio", mode: "H", gt: "—", cost: "$$$", cause: "human.dislike", notes: "SDK thumbs signal." },
      { code: "G2", name: "User dissatisfaction rate", mode: "H", gt: "—", cost: "$$$", cause: "human.complaint", notes: "Negative free text." },
      { code: "G3", name: "Reviewer correctness label", mode: "H", gt: "L3", cost: "$$$", cause: "human.incorrect", notes: "Human adjudication." },
      { code: "G4", name: "Reviewer failure taxonomy", mode: "H", gt: "—", cost: "$$$", cause: "human.taxonomy.*", notes: "Structured reviewer codes." },
      { code: "G5", name: "CSAT / NPS-style score", mode: "H", gt: "—", cost: "$$$", cause: "human.csat_low", notes: "External survey hooks." },
    ],
  },
];

type EffortLevel = "low" | "medium" | "high";

/** 46-row playbook mirrored from ``easyobs.eval.services.improvement_catalog``.
 *  Keep in sync — the python module is the source of truth at runtime; this
 *  table only exists so the guide page is fully populated even when the API
 *  catalog endpoint is offline (offline-first docs). */
const IMPROVEMENT_CATEGORIES: {
  group: string;
  category: string;
  intent: string;
  example: string;
  effort: EffortLevel;
}[] = [
  // prompt (9)
  { group: "prompt", category: "prompt.rewrite", intent: "Revise prompt body", example: "Add 'cite sources' to system prompt", effort: "low" },
  { group: "prompt", category: "prompt.system_split", intent: "Rebalance system vs user", example: "Move tool rules to system", effort: "low" },
  { group: "prompt", category: "prompt.few_shot", intent: "Few-shot examples", example: "Add 1–3 refusal examples", effort: "low" },
  { group: "prompt", category: "prompt.ko_localize", intent: "Korean / honorifics", example: "Enforce honorifics + language", effort: "low" },
  { group: "prompt", category: "prompt.grounding", intent: "Cite-then-answer", example: "Forbid statements without [doc:id]", effort: "low" },
  { group: "prompt", category: "prompt.checklist", intent: "Answer checklist", example: "Enumerate must_include before send", effort: "low" },
  { group: "prompt", category: "prompt.length_constraint", intent: "Length / repetition cap", example: "Cap response at 800 chars", effort: "low" },
  { group: "prompt", category: "prompt.refusal_policy", intent: "Refusal copy", example: "Standard refusal w/ disclaimer", effort: "low" },
  { group: "prompt", category: "prompt.tone_guide", intent: "Brand voice", example: "Codify persona constants", effort: "low" },
  // query (3)
  { group: "query", category: "query.intent_taxonomy", intent: "Intent taxonomy", example: "Split 'support' into 4 intents", effort: "medium" },
  { group: "query", category: "query.clarify_turn", intent: "Clarification turn", example: "Ask for customer_id when missing", effort: "medium" },
  { group: "query", category: "query.router", intent: "Query router", example: "Route by language / complexity", effort: "medium" },
  // retrieval (8)
  { group: "retrieval", category: "retrieval.tune", intent: "top_k, hybrid weights", example: "top_k 5 → 8", effort: "low" },
  { group: "retrieval", category: "retrieval.reranker", intent: "Reranker on / swap", example: "Enable BGE reranker", effort: "medium" },
  { group: "retrieval", category: "retrieval.chunking", intent: "Chunk strategy", example: "1024 → 512 tokens, overlap 64", effort: "high" },
  { group: "retrieval", category: "retrieval.embedding", intent: "Embedding swap", example: "Multilingual embeddings", effort: "high" },
  { group: "retrieval", category: "retrieval.query_expand", intent: "Query expansion", example: "HyDE / multi-query", effort: "medium" },
  { group: "retrieval", category: "retrieval.metadata_filter", intent: "Metadata filter", example: "Tenant / date range", effort: "medium" },
  { group: "retrieval", category: "retrieval.dedup", intent: "Top-K dedup / MMR", example: "Drop near-duplicates", effort: "low" },
  { group: "retrieval", category: "retrieval.compress", intent: "Context compression", example: "Summarize before LLM", effort: "medium" },
  // context (2)
  { group: "context", category: "context.attribution", intent: "Source attribution", example: "Always attach source meta", effort: "low" },
  { group: "context", category: "context.ordering", intent: "Context ordering", example: "Sort by score+recency", effort: "low" },
  // tool (5)
  { group: "tool", category: "tool.spec", intent: "Tighten JSON schema", example: "Require customer_id", effort: "low" },
  { group: "tool", category: "tool.add", intent: "Add missing tool", example: "Refund history lookup", effort: "high" },
  { group: "tool", category: "tool.remove", intent: "Remove dead tools", example: "Zero calls for 30d", effort: "low" },
  { group: "tool", category: "tool.policy", intent: "Call policy", example: "Dedupe identical args", effort: "low" },
  { group: "tool", category: "tool.cache", intent: "Tool result cache", example: "TTL keyed by arg hash", effort: "medium" },
  // agent (3)
  { group: "agent", category: "agent.planner_template", intent: "Planner template", example: "Plan→Act→Reflect", effort: "medium" },
  { group: "agent", category: "agent.path_limit", intent: "Reasoning path cap", example: "Max 5 steps + fallback", effort: "low" },
  { group: "agent", category: "agent.synthesis_template", intent: "Synthesis template", example: "Tabular merge + 1-line conclusion", effort: "medium" },
  // format (3)
  { group: "format", category: "format.guard", intent: "Response shape", example: "response_format=JSON + 1 retry", effort: "low" },
  { group: "format", category: "format.citation", intent: "Citation format", example: "Fix [doc:id] pattern", effort: "low" },
  { group: "format", category: "format.schema", intent: "JSON Schema rev", example: "Schema v2 with deprecation", effort: "medium" },
  // safety (11) — last 8 are AI-security-hardening additions (see design doc 10)
  { group: "safety", category: "safety.policy", intent: "Policy guards", example: "Denylist before send", effort: "low" },
  { group: "safety", category: "safety.refusal", intent: "Refusal copy", example: "Legal disclaimer template", effort: "low" },
  { group: "safety", category: "safety.pii_mask", intent: "PII masking", example: "Mask email/phone in egress", effort: "medium" },
  { group: "safety", category: "safety.injection_guard", intent: "Prompt injection guard", example: "Block 'ignore previous instructions'", effort: "low" },
  { group: "safety", category: "safety.jailcanary", intent: "Jailbreak canary", example: "Hidden token leak alert", effort: "medium" },
  { group: "safety", category: "safety.exfil_filter", intent: "Exfil URL filter", example: "Block suspicious URLs / base64 blobs", effort: "low" },
  { group: "safety", category: "safety.secret_egress", intent: "Tool-arg secret egress", example: "Scan tool args for sk-/AKIA", effort: "medium" },
  { group: "safety", category: "safety.judge_sanitize", intent: "Sanitize before Judge", example: "Strip PII before external Judge call", effort: "medium" },
  { group: "safety", category: "safety.audit_log", intent: "Admin audit log", example: "who/when/what for profile mutations", effort: "medium" },
  { group: "safety", category: "safety.ingest_redact", intent: "Ingest-time redaction", example: "Mask PII at the ingest pipeline", effort: "medium" },
  { group: "safety", category: "safety.secret_rotate", intent: "Secret rotation", example: "90-day ingest token rotation", effort: "low" },
  // model (3)
  { group: "model", category: "model.swap", intent: "Model change", example: "Smaller / cheaper tier", effort: "medium" },
  { group: "model", category: "model.params", intent: "Sampling params", example: "temperature 0.7 → 0.3", effort: "low" },
  { group: "model", category: "model.judge_swap", intent: "Judge swap / consensus", example: "Single → majority of 3", effort: "medium" },
  // dataset (3)
  { group: "dataset", category: "dataset.expand", intent: "Grow golden set", example: "Mine 32 similar failures", effort: "medium" },
  { group: "dataset", category: "dataset.relabel", intent: "Relabel ambiguous GT", example: "L3 answer unclear", effort: "medium" },
  { group: "dataset", category: "dataset.curate", intent: "Curate L1/L2/L3", example: "Fill missing layers", effort: "medium" },
  // infra (4)
  { group: "infra", category: "infra.cache", intent: "Response / embed cache", example: "5m cache per query", effort: "medium" },
  { group: "infra", category: "infra.timeout", intent: "Timeouts", example: "Search 1.2s → 2.0s", effort: "low" },
  { group: "infra", category: "infra.parallel", intent: "Parallelize steps", example: "Run search + tool in parallel", effort: "medium" },
  { group: "infra", category: "infra.retry", intent: "Retry / backoff", example: "Exp backoff on 5xx", effort: "low" },
  // supply (4) — AI security hardening / Mythos lessons (design doc 10)
  { group: "supply", category: "supply.vendor_review", intent: "Third-party vendor review", example: "Quarterly access posture audit", effort: "high" },
  { group: "supply", category: "supply.sbom", intent: "SBOM publishing", example: "Track transitive CVEs at AI speed", effort: "medium" },
  { group: "supply", category: "supply.sourcemap_strip", intent: "Strip source maps", example: "No webpack .map in production", effort: "low" },
  { group: "supply", category: "supply.cache_acl", intent: "Public cache ACL audit", example: "Block world-readable model meta", effort: "low" },
];

/** 52 metric × N detail mapping.
 *  Each row is the operator-facing remediation menu — primary = first try,
 *  secondary = also worth investigating. */
const METRIC_TO_IMPROVEMENT: {
  code: string;
  name: string;
  cause: string;
  primary: string;
  secondary: string[];
  effort: EffortLevel;
}[] = [
  // A. Query (6)
  { code: "A1", name: "Intent classification accuracy", cause: "query.intent_mismatch", primary: "query.intent_taxonomy", secondary: ["prompt.few_shot", "query.router", "dataset.expand"], effort: "medium" },
  { code: "A2", name: "Query rewrite preservation", cause: "query.rewrite_drift", primary: "prompt.rewrite", secondary: ["prompt.few_shot", "model.params"], effort: "low" },
  { code: "A3", name: "Ambiguity detection rate", cause: "query.ambiguous", primary: "query.clarify_turn", secondary: ["prompt.refusal_policy", "query.intent_taxonomy"], effort: "medium" },
  { code: "A4", name: "Missing slot detection", cause: "query.slot_missing", primary: "tool.spec", secondary: ["query.clarify_turn", "format.schema"], effort: "low" },
  { code: "A5", name: "Language detection accuracy", cause: "query.lang_mismatch", primary: "query.router", secondary: ["prompt.ko_localize", "safety.policy"], effort: "medium" },
  { code: "A6", name: "Query complexity score", cause: "query.complexity_high", primary: "query.router", secondary: ["agent.planner_template", "model.swap"], effort: "medium" },
  // B. Retrieval (12)
  { code: "B1", name: "Recall@K", cause: "retrieval.recall_low", primary: "retrieval.tune", secondary: ["retrieval.query_expand", "retrieval.embedding", "dataset.curate"], effort: "low" },
  { code: "B2", name: "Precision@K", cause: "retrieval.noise_high", primary: "retrieval.reranker", secondary: ["retrieval.metadata_filter", "retrieval.tune"], effort: "medium" },
  { code: "B3", name: "MRR", cause: "retrieval.first_hit_late", primary: "retrieval.reranker", secondary: ["retrieval.tune", "context.ordering"], effort: "medium" },
  { code: "B4", name: "HitRate@K", cause: "retrieval.miss", primary: "retrieval.tune", secondary: ["retrieval.query_expand", "dataset.expand"], effort: "low" },
  { code: "B5", name: "nDCG@K", cause: "retrieval.rank_quality", primary: "retrieval.reranker", secondary: ["retrieval.embedding", "context.ordering"], effort: "medium" },
  { code: "B6", name: "MAP", cause: "retrieval.precision_low", primary: "retrieval.reranker", secondary: ["retrieval.tune", "retrieval.metadata_filter"], effort: "medium" },
  { code: "B7", name: "Chunk relevance score", cause: "retrieval.chunk_irrelevant", primary: "retrieval.embedding", secondary: ["retrieval.chunking", "retrieval.reranker"], effort: "high" },
  { code: "B8", name: "Context coverage", cause: "retrieval.coverage_low", primary: "retrieval.tune", secondary: ["retrieval.query_expand", "dataset.curate"], effort: "low" },
  { code: "B9", name: "Metadata filter accuracy", cause: "retrieval.filter_wrong", primary: "retrieval.metadata_filter", secondary: ["format.schema"], effort: "medium" },
  { code: "B10", name: "Reranker gain", cause: "retrieval.rerank_no_gain", primary: "retrieval.reranker", secondary: ["retrieval.embedding"], effort: "medium" },
  { code: "B11", name: "Retrieval diversity", cause: "retrieval.dup_high", primary: "retrieval.dedup", secondary: ["retrieval.tune"], effort: "low" },
  { code: "B12", name: "Retrieval latency budget", cause: "retrieval.latency_over", primary: "infra.cache", secondary: ["infra.parallel", "retrieval.tune", "infra.timeout"], effort: "medium" },
  // C. Context (7)
  { code: "C1", name: "Chunk noise ratio", cause: "context.noise", primary: "retrieval.metadata_filter", secondary: ["retrieval.reranker"], effort: "medium" },
  { code: "C2", name: "Duplicate chunk ratio", cause: "context.dup", primary: "retrieval.dedup", secondary: ["retrieval.tune"], effort: "low" },
  { code: "C3", name: "Context token waste", cause: "context.token_waste", primary: "retrieval.compress", secondary: ["prompt.length_constraint", "retrieval.tune"], effort: "medium" },
  { code: "C4", name: "Evidence sufficiency", cause: "context.insufficient", primary: "retrieval.tune", secondary: ["retrieval.query_expand", "dataset.curate"], effort: "low" },
  { code: "C5", name: "Context ordering quality", cause: "context.order_bad", primary: "context.ordering", secondary: ["retrieval.reranker"], effort: "low" },
  { code: "C6", name: "Source attribution", cause: "context.attribution_missing", primary: "context.attribution", secondary: ["prompt.grounding"], effort: "low" },
  { code: "C7", name: "Chunk semantic similarity", cause: "context.sim_low", primary: "retrieval.embedding", secondary: ["retrieval.chunking"], effort: "high" },
  // D. Generation (12)
  { code: "D1", name: "Answer relevance", cause: "gen.relevance_low", primary: "prompt.rewrite", secondary: ["retrieval.tune", "model.params"], effort: "low" },
  { code: "D2", name: "Correctness", cause: "gen.incorrect", primary: "dataset.expand", secondary: ["prompt.few_shot", "model.swap", "dataset.relabel"], effort: "medium" },
  { code: "D3", name: "Faithfulness / groundedness", cause: "gen.unfaithful", primary: "prompt.grounding", secondary: ["format.citation", "safety.refusal"], effort: "low" },
  { code: "D4", name: "Hallucination rate", cause: "gen.hallucination", primary: "prompt.grounding", secondary: ["prompt.refusal_policy", "safety.policy"], effort: "low" },
  { code: "D5", name: "Completeness", cause: "gen.incomplete", primary: "prompt.checklist", secondary: ["prompt.few_shot", "dataset.curate"], effort: "low" },
  { code: "D6", name: "Conciseness", cause: "gen.verbose", primary: "prompt.length_constraint", secondary: ["model.params"], effort: "low" },
  { code: "D7", name: "Tone consistency", cause: "gen.tone_off", primary: "prompt.tone_guide", secondary: ["prompt.few_shot"], effort: "low" },
  { code: "D8", name: "Policy compliance", cause: "gen.policy_violation", primary: "safety.policy", secondary: ["prompt.refusal_policy", "prompt.rewrite"], effort: "low" },
  { code: "D9", name: "Citation accuracy", cause: "gen.citation_wrong", primary: "format.citation", secondary: ["prompt.grounding"], effort: "low" },
  { code: "D10", name: "Structured format validity", cause: "gen.format_invalid", primary: "format.guard", secondary: ["format.schema", "prompt.rewrite"], effort: "low" },
  { code: "D11", name: "Language quality", cause: "gen.grammar", primary: "prompt.ko_localize", secondary: ["model.swap"], effort: "low" },
  { code: "D12", name: "Refusal appropriateness", cause: "gen.refusal_bad", primary: "prompt.refusal_policy", secondary: ["safety.refusal", "dataset.curate"], effort: "low" },
  // E. Tool / agent (8)
  { code: "E1", name: "Tool selection accuracy", cause: "tool.wrong", primary: "tool.spec", secondary: ["prompt.few_shot", "agent.planner_template", "tool.remove"], effort: "low" },
  { code: "E2", name: "Tool argument validity", cause: "tool.arg_invalid", primary: "tool.spec", secondary: ["format.schema", "prompt.checklist"], effort: "low" },
  { code: "E3", name: "Tool call success rate", cause: "tool.fail", primary: "tool.policy", secondary: ["infra.retry", "infra.timeout"], effort: "low" },
  { code: "E4", name: "Tool retry count", cause: "tool.retry_high", primary: "tool.policy", secondary: ["infra.retry"], effort: "low" },
  { code: "E5", name: "Unnecessary tool call ratio", cause: "tool.over_call", primary: "tool.policy", secondary: ["tool.cache"], effort: "low" },
  { code: "E6", name: "Multi-step plan correctness", cause: "agent.plan_wrong", primary: "agent.planner_template", secondary: ["prompt.few_shot", "tool.spec"], effort: "medium" },
  { code: "E7", name: "Reasoning path length", cause: "agent.path_long", primary: "agent.path_limit", secondary: ["agent.planner_template"], effort: "low" },
  { code: "E8", name: "Final synthesis quality", cause: "agent.synthesis_low", primary: "agent.synthesis_template", secondary: ["prompt.checklist"], effort: "medium" },
  // F. Operational (5)
  { code: "F1", name: "End-to-end latency", cause: "ops.latency_over", primary: "infra.cache", secondary: ["infra.parallel", "model.swap"], effort: "medium" },
  { code: "F2", name: "Model inference latency", cause: "ops.gen_slow", primary: "model.swap", secondary: ["model.params", "prompt.length_constraint"], effort: "medium" },
  { code: "F3", name: "Cost per trace", cause: "ops.cost_over", primary: "model.swap", secondary: ["infra.cache", "retrieval.compress", "prompt.length_constraint"], effort: "medium" },
  { code: "F4", name: "Token consumption", cause: "ops.token_over", primary: "prompt.length_constraint", secondary: ["retrieval.compress", "retrieval.tune"], effort: "low" },
  { code: "F5", name: "Failure / timeout rate", cause: "ops.failure", primary: "infra.retry", secondary: ["infra.timeout", "tool.policy"], effort: "low" },
  // G. Human feedback (5)
  { code: "G1", name: "User thumbs-up ratio", cause: "human.dislike", primary: "dataset.expand", secondary: ["prompt.rewrite", "query.intent_taxonomy"], effort: "medium" },
  { code: "G2", name: "User dissatisfaction rate", cause: "human.complaint", primary: "dataset.expand", secondary: ["prompt.tone_guide", "prompt.rewrite"], effort: "medium" },
  { code: "G3", name: "Reviewer correctness label", cause: "human.incorrect", primary: "dataset.relabel", secondary: ["dataset.curate", "prompt.few_shot"], effort: "medium" },
  { code: "G4", name: "Reviewer failure taxonomy", cause: "human.taxonomy", primary: "dataset.curate", secondary: [], effort: "medium" },
  { code: "G5", name: "CSAT / NPS-style score", cause: "human.csat_low", primary: "prompt.rewrite", secondary: ["prompt.tone_guide", "dataset.expand"], effort: "low" },
];

/** Mirrors ``easyobs.eval.services.improvements._FINDING_TO_CATEGORY`` (built-in rules → UI category keys). */
const FINDING_TO_CATEGORY_ROWS: { ruleId: string; category: string }[] = [
  { ruleId: "rule.response.length", category: "answer_format" },
  { ruleId: "rule.response.json", category: "answer_format" },
  { ruleId: "rule.response.language", category: "prompt_clarity" },
  { ruleId: "rule.response.present", category: "prompt_clarity" },
  { ruleId: "rule.safety.no_pii", category: "safety_guardrails" },
  { ruleId: "rule.safety.no_secret", category: "safety_guardrails" },
  { ruleId: "rule.safety.no_profanity", category: "safety_guardrails" },
  { ruleId: "rule.retrieval.recall_at_k", category: "retrieval_quality" },
  { ruleId: "rule.retrieval.precision_at_k", category: "retrieval_quality" },
  { ruleId: "rule.retrieval.mrr", category: "retrieval_quality" },
  { ruleId: "rule.perf.latency", category: "performance_budget" },
  { ruleId: "rule.perf.token_budget", category: "performance_budget" },
  { ruleId: "rule.perf.cost_budget", category: "performance_budget" },
  { ruleId: "rule.status.ok", category: "tool_orchestration" },
  { ruleId: "rule.agent.no_tool_loop", category: "tool_orchestration" },
  { ruleId: "rule.custom.dsl", category: "prompt_clarity" },
];

/** Mirrors ``IMPROVEMENT_PACKS`` category filters (null means no filter). */
const BUILTIN_PACK_POLICIES: { packId: string; allowed: string }[] = [
  { packId: "easyobs_standard", allowed: "(all categories — no filter)" },
  {
    packId: "easyobs_security",
    allowed: "safety_guardrails, prompt_clarity, answer_format, context_grounding",
  },
  {
    packId: "easyobs_rag",
    allowed: "retrieval_quality, context_grounding, prompt_clarity",
  },
  {
    packId: "easyobs_efficiency",
    allowed: "performance_budget, model_choice, tool_orchestration",
  },
];

function AccordionSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="eo-guide-acc">
      <button type="button" className="eo-guide-acc-h" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{title}</span>
        <span className="mono" style={{ fontSize: 11, color: "var(--eo-mute)" }}>
          {open ? "−" : "+"}
        </span>
      </button>
      {open && <div className="eo-guide-acc-b">{children}</div>}
    </div>
  );
}

function MetricTable({ rows }: { rows: MetricRow[] }) {
  return (
    <div className="eo-guide-table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Metric</th>
            <th>Mode</th>
            <th>GT</th>
            <th>Cost</th>
            <th>Cause code</th>
            <th>Rationale / notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code}>
              <td className="mono">{r.code}</td>
              <td>{r.name}</td>
              <td className="mono">{r.mode}</td>
              <td>{r.gt}</td>
              <td>{r.cost}</td>
              <td className="mono">{r.cause}</td>
              <td>{r.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Evaluation Profiles — full rationale. */
export function ProfilesGuideContent() {
  const { t } = useI18n();
  const golden = useGoldenLayerHelp();
  const catalog = useQuery({
    queryKey: ["eval", "evaluators", "guide"],
    queryFn: fetchEvaluatorCatalog,
    staleTime: 60 * 60_000,
  });
  const metricGroups = useMemo(() => {
    const live = _metricGroupsFromCatalog(catalog.data ?? []);
    return live.length > 0 ? live : METRIC_GROUPS;
  }, [catalog.data]);
  const [sec, setSec] = useState<
    "principles" | "metrics" | "parameters" | "pipeline" | "goldens" | "mapping"
  >("principles");

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("guide.profiles.title")}</h3>
        <span className="eo-card-sub">{t("guide.profiles.subtitle")}</span>
      </div>
      <div className={tabRow} role="tablist" aria-label="Guide sections">
        {(
          [
            ["principles", t("guide.profiles.tabPrinciples")],
            ["metrics", t("guide.profiles.tabMetrics")],
            ["parameters", t("guide.profiles.tabParameters")],
            ["pipeline", t("guide.profiles.tabPipeline")],
            ["goldens", t("guide.profiles.tabGoldens")],
            ["mapping", t("guide.profiles.tabMapping")],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={sec === id}
            className={tabBtn}
            data-active={sec === id}
            onClick={() => setSec(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={`${panel} eo-guide-prose`}>
        {sec === "principles" && (
          <>
            <div className="eo-guide-callout" data-tone="legal">
              {t("guide.profiles.principlesCallout")}
            </div>
            <h4>{t("guide.profiles.principlesTreeTitle")}</h4>
            <p style={{ marginTop: 0 }}>{t("guide.profiles.principlesTreeBody")}</p>
            <h4>{t("guide.profiles.fivePrinciplesTitle")}</h4>
            <dl className="eo-guide-kv">
              <dt>{t("guide.profiles.principleComposableDt")}</dt>
              <dd>{t("guide.profiles.principleComposableDd")}</dd>
              <dt>{t("guide.profiles.principleDeterministicDt")}</dt>
              <dd>{t("guide.profiles.principleDeterministicDd")}</dd>
              <dt>{t("guide.profiles.principleCauseDt")}</dt>
              <dd>{t("guide.profiles.principleCauseDd")}</dd>
              <dt>{t("guide.profiles.principleGoldenDt")}</dt>
              <dd>{t("guide.profiles.principleGoldenDd")}</dd>
              <dt>{t("guide.profiles.principleReportsDt")}</dt>
              <dd>{t("guide.profiles.principleReportsDd")}</dd>
            </dl>
            <h4>{t("guide.profiles.modeDistTitle")}</h4>
            <div className="eo-guide-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.profiles.modeDistColMode")}</th>
                    <th>{t("guide.profiles.modeDistColCount")}</th>
                    <th>{t("guide.profiles.modeDistColMeaning")}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="mono">{t("guide.profiles.modeRonly")}</td>
                    <td>{t("guide.profiles.modeRonlyN")}</td>
                    <td>{t("guide.profiles.modeRonlyMean")}</td>
                  </tr>
                  <tr>
                    <td className="mono">{t("guide.profiles.modeJonly")}</td>
                    <td>{t("guide.profiles.modeJonlyN")}</td>
                    <td>{t("guide.profiles.modeJonlyMean")}</td>
                  </tr>
                  <tr>
                    <td className="mono">{t("guide.profiles.modeROrJ")}</td>
                    <td>{t("guide.profiles.modeROrJN")}</td>
                    <td>{t("guide.profiles.modeROrJMean")}</td>
                  </tr>
                  <tr>
                    <td className="mono">{t("guide.profiles.modeH")}</td>
                    <td>{t("guide.profiles.modeHN")}</td>
                    <td>{t("guide.profiles.modeHMean")}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}

        {sec === "metrics" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.profiles.metricsIntro")}</p>
            {metricGroups.map((g, i) => (
              <AccordionSection key={g.key} title={g.title} defaultOpen={i === 0}>
                <MetricTable rows={g.rows} />
              </AccordionSection>
            ))}
          </>
        )}

        {sec === "parameters" && (
          <>
            <h4>{t("guide.profiles.evalFieldsTitle")}</h4>
            <p style={{ marginTop: 0 }}>{t("guide.profiles.evalFieldsIntro")}</p>
            <dl className="eo-guide-kv">
              <dt>{t("guide.profiles.pfServiceScopeDt")}</dt>
              <dd>{t("guide.profiles.pfServiceScopeDd")}</dd>
              <dt>{t("guide.profiles.pfWeightDt")}</dt>
              <dd>{t("guide.profiles.pfWeightDd")}</dd>
              <dt>{t("guide.profiles.pfThresholdDt")}</dt>
              <dd>{t("guide.profiles.pfThresholdDd")}</dd>
              <dt>{t("guide.profiles.pfRuleParamsDt")}</dt>
              <dd>{t("guide.profiles.pfRuleParamsDd")}</dd>
              <dt>{t("guide.profiles.pfConsensusDt")}</dt>
              <dd>{t("guide.profiles.pfConsensusDd")}</dd>
              <dt>{t("guide.profiles.pfMaxRunDt")}</dt>
              <dd>{t("guide.profiles.pfMaxRunDd")}</dd>
              <dt>{t("guide.profiles.pfMaxSubjDt")}</dt>
              <dd>{t("guide.profiles.pfMaxSubjDd")}</dd>
              <dt>{t("guide.profiles.pfMonthlyDt")}</dt>
              <dd>{t("guide.profiles.pfMonthlyDd")}</dd>
              <dt>{t("guide.profiles.pfOnExceedDt")}</dt>
              <dd>{t("guide.profiles.pfOnExceedDd")}</dd>
            </dl>
            <h4>{t("guide.profiles.ruleDslTitle")}</h4>
            <p>{t("guide.profiles.ruleDslIntro")}</p>
            <div className="eo-guide-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.profiles.dslColKeyword")}</th>
                    <th>{t("guide.profiles.dslColPurpose")}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="mono">match</td>
                    <td>{t("guide.profiles.dslMatch")}</td>
                  </tr>
                  <tr>
                    <td className="mono">present</td>
                    <td>{t("guide.profiles.dslPresent")}</td>
                  </tr>
                  <tr>
                    <td className="mono">len</td>
                    <td>{t("guide.profiles.dslLen")}</td>
                  </tr>
                  <tr>
                    <td className="mono">score</td>
                    <td>{t("guide.profiles.dslScore")}</td>
                  </tr>
                  <tr>
                    <td className="mono">requires</td>
                    <td>{t("guide.profiles.dslRequires")}</td>
                  </tr>
                  <tr>
                    <td className="mono">cause</td>
                    <td>{t("guide.profiles.dslCause")}</td>
                  </tr>
                  <tr>
                    <td className="mono">evidence</td>
                    <td>{t("guide.profiles.dslEvidence")}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <h4>{t("guide.profiles.builtinRuleCatalogTitle")}</h4>
            <p className="eo-mute" style={{ fontSize: 12 }}>
              {t("guide.profiles.builtinRuleCatalogP")}
            </p>
          </>
        )}

        {sec === "pipeline" && (
          <>
            <h4>{t("guide.profiles.pipeRuleTitle")}</h4>
            <p style={{ marginTop: 0 }}>{t("guide.profiles.pipeRuleP")}</p>
            <h4>{t("guide.profiles.pipeJudgeTitle")}</h4>
            <p>{t("guide.profiles.pipeJudgeP")}</p>
            <h4>{t("guide.profiles.pipeOutputsTitle")}</h4>
            <p>{t("guide.profiles.pipeOutputsP")}</p>
            <h4>{t("guide.profiles.pipeCostLatencyTitle")}</h4>
            <div className="eo-guide-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.profiles.pipeColLane")}</th>
                    <th>{t("guide.profiles.pipeColCost")}</th>
                    <th>{t("guide.profiles.pipeColLatency")}</th>
                    <th>{t("guide.profiles.pipeColTrigger")}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{t("guide.profiles.pipeRowRuleLane")}</td>
                    <td>{t("guide.profiles.pipeRowRuleCost")}</td>
                    <td>{t("guide.profiles.pipeRowRuleLat")}</td>
                    <td>{t("guide.profiles.pipeRowRuleTrig")}</td>
                  </tr>
                  <tr>
                    <td>{t("guide.profiles.pipeJudge1Label")}</td>
                    <td>{t("guide.profiles.pipeRowJ1Cost")}</td>
                    <td>{t("guide.profiles.pipeRowJ1Lat")}</td>
                    <td>{t("guide.profiles.pipeRowJ1Trig")}</td>
                  </tr>
                  <tr>
                    <td>{t("guide.profiles.pipeJudgeNLabel")}</td>
                    <td>{t("guide.profiles.pipeRowJNCost")}</td>
                    <td>{t("guide.profiles.pipeRowJNLat")}</td>
                    <td>{t("guide.profiles.pipeRowJNTrig")}</td>
                  </tr>
                  <tr>
                    <td>{t("guide.profiles.pipeRowHumanLane")}</td>
                    <td>{t("guide.profiles.pipeRowHumanCost")}</td>
                    <td>{t("guide.profiles.pipeRowHumanLat")}</td>
                    <td>{t("guide.profiles.pipeRowHumanTrig")}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </>
        )}

        {sec === "goldens" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.evaluatorLayerHelp")}</p>
            <dl style={{ margin: "12px 0 0" }}>
              {(["L1", "L2", "L3"] as const).map((k) => (
                <div key={k} style={{ marginBottom: 12 }}>
                  <dt style={{ fontWeight: 700, color: "var(--eo-ink)" }}>{golden[k].title}</dt>
                  <dd style={{ margin: "6px 0 0", color: "var(--eo-ink-soft)", lineHeight: 1.55 }}>
                    {golden[k].body}
                  </dd>
                </div>
              ))}
            </dl>
            <h4>{t("guide.profiles.goldensPartialTitle")}</h4>
            <p>{t("guide.profiles.goldensPartialP")}</p>
          </>
        )}

        {sec === "mapping" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.profiles.mappingIntro")}</p>
            <h4>{t("guide.profiles.packPolicyTitle")}</h4>
            <p className="eo-mute" style={{ fontSize: 12, lineHeight: 1.55 }}>
              {t("guide.profiles.packPolicyIntro")}
            </p>
            <div className="eo-guide-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.profiles.mappingPackIdTh")}</th>
                    <th>{t("guide.profiles.mappingAllowedTh")}</th>
                  </tr>
                </thead>
                <tbody>
                  {BUILTIN_PACK_POLICIES.map((row) => (
                    <tr key={row.packId}>
                      <td className="mono">{row.packId}</td>
                      <td className="mono" style={{ whiteSpace: "pre-wrap" }}>
                        {row.allowed}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h4>{t("guide.profiles.mapping52Title")}</h4>
            <p className="eo-mute" style={{ fontSize: 12, lineHeight: 1.55 }}>
              {t("guide.profiles.mapping52Intro")}
            </p>
            <div className="eo-guide-table-wrap" style={{ maxHeight: 520 }}>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("guide.profiles.mapping52ColMetric")}</th>
                    <th>{t("guide.profiles.mapping52ColCause")}</th>
                    <th>{t("guide.profiles.mapping52ColPrimary")}</th>
                    <th>{t("guide.profiles.mapping52ColSecondary")}</th>
                    <th>{t("guide.profiles.mapping52ColEffort")}</th>
                  </tr>
                </thead>
                <tbody>
                  {METRIC_TO_IMPROVEMENT.map((row) => (
                    <tr key={row.code}>
                      <td className="mono">{row.code}</td>
                      <td>{row.name}</td>
                      <td className="mono">{row.cause}</td>
                      <td className="mono">{row.primary}</td>
                      <td className="mono" style={{ whiteSpace: "pre-wrap" }}>
                        {row.secondary.length === 0 ? "—" : row.secondary.join(", ")}
                      </td>
                      <td>
                        <span className="eo-effort" data-effort={row.effort}>
                          {row.effort}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** Improvement Pack — full category & workflow rationale. */
export function ImprovementPackGuideContent() {
  const { t } = useI18n();
  const [sec, setSec] = useState<
    "definition" | "categories" | "effort" | "trust" | "workflow" | "fields"
  >("definition");

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("guide.improve.title")}</h3>
        <span className="eo-card-sub">{t("guide.improve.subtitle")}</span>
      </div>
      <div className={tabRow} role="tablist" aria-label="Improvement guide sections">
        {(
          [
            ["definition", t("guide.improve.tabDefinition")],
            ["categories", t("guide.improve.tabCategories")],
            ["effort", t("guide.improve.tabEffort")],
            ["trust", t("guide.improve.tabTrust")],
            ["workflow", t("guide.improve.tabWorkflow")],
            ["fields", t("guide.improve.tabFields")],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={sec === id}
            className={tabBtn}
            data-active={sec === id}
            onClick={() => setSec(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={`${panel} eo-guide-prose`}>
        {sec === "definition" && (
          <>
            <div className="eo-guide-callout" data-tone="legal">
              {t("guide.improve.definitionCallout")}
            </div>
            <p style={{ marginTop: 0 }}>{t("guide.improve.definitionP1")}</p>
            <p>{t("guide.improve.definitionP2")}</p>
            <h4>{t("guide.improve.definitionJudgesTitle")}</h4>
            <p>{t("guide.improve.definitionJudgesBody")}</p>
          </>
        )}

        {sec === "categories" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.improve.categoriesIntro")}</p>
            <h4>{t("guide.improve.packBasisTitle")}</h4>
            <p className="eo-mute" style={{ fontSize: 12, lineHeight: 1.55 }}>
              {t("guide.improve.packBasisIntro")}
            </p>
            <div className="eo-guide-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.improve.findingRuleCol")}</th>
                    <th>{t("guide.improve.findingCategoryCol")}</th>
                  </tr>
                </thead>
                <tbody>
                  {FINDING_TO_CATEGORY_ROWS.map((row) => (
                    <tr key={row.ruleId}>
                      <td className="mono">{row.ruleId}</td>
                      <td className="mono">{row.category}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h4>{t("guide.improve.operatorPlaybookTitle")}</h4>
            <p className="eo-mute" style={{ fontSize: 12 }}>
              {t("guide.improve.operatorPlaybookSub")}
            </p>
            <div className="eo-guide-table-wrap" style={{ maxHeight: 520 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("guide.improve.playbookColGroup")}</th>
                    <th>{t("guide.improve.playbookColCategory")}</th>
                    <th>{t("guide.improve.playbookColIntent")}</th>
                    <th>{t("guide.improve.playbookColExample")}</th>
                    <th>{t("guide.improve.playbookColEffort")}</th>
                  </tr>
                </thead>
                <tbody>
                  {IMPROVEMENT_CATEGORIES.map((r) => (
                    <tr key={r.category}>
                      <td className="mono">{r.group}</td>
                      <td className="mono">{r.category}</td>
                      <td>{r.intent}</td>
                      <td>{r.example}</td>
                      <td>
                        <span className="eo-effort" data-effort={r.effort}>
                          {r.effort}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="eo-mute" style={{ fontSize: 12 }}>
              {t("guide.improve.categoriesFootnote")}
            </p>
          </>
        )}

        {sec === "effort" && <EffortGuideSection />}

        {sec === "trust" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.improve.trustIntro")}</p>
          </>
        )}

        {sec === "workflow" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.improve.workflowIntro")}</p>
            <h4>{t("guide.improve.triageTitle")}</h4>
            <dl className="eo-guide-kv">
              <dt>{t("guide.improve.triageOpenDt")}</dt>
              <dd>{t("guide.improve.triageOpenDd")}</dd>
              <dt>{t("guide.improve.triageAcceptedDt")}</dt>
              <dd>{t("guide.improve.triageAcceptedDd")}</dd>
              <dt>{t("guide.improve.triageRejectedDt")}</dt>
              <dd>{t("guide.improve.triageRejectedDd")}</dd>
            </dl>
            <p>{t("guide.improve.triageDeferP")}</p>
            <h4>{t("guide.improve.closedLoopTitle")}</h4>
            <p>{t("guide.improve.closedLoopP")}</p>
          </>
        )}

        {sec === "fields" && (
          <>
            <p style={{ marginTop: 0 }}>{t("guide.improve.fieldsIntro")}</p>
            <dl className="eo-guide-kv">
              <dt>{t("guide.improve.fieldSummaryDt")}</dt>
              <dd>{t("guide.improve.fieldSummaryDd")}</dd>
              <dt>{t("guide.improve.fieldCauseDistDt")}</dt>
              <dd>{t("guide.improve.fieldCauseDistDd")}</dd>
              <dt>{t("guide.improve.fieldTopPatternsDt")}</dt>
              <dd>{t("guide.improve.fieldTopPatternsDd")}</dd>
              <dt>{t("guide.improve.fieldProposalsDt")}</dt>
              <dd>{t("guide.improve.fieldProposalsDd")}</dd>
              <dt>{t("guide.improve.fieldRefsDt")}</dt>
              <dd>{t("guide.improve.fieldRefsDd")}</dd>
              <dt>{t("guide.improve.fieldAgreeDt")}</dt>
              <dd>{t("guide.improve.fieldAgreeDd")}</dd>
            </dl>
            <p className="eo-mute" style={{ fontSize: 12 }}>
              {t("guide.improve.fieldsNumericNote")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

/** Built-in distribution by effort across the 46 catalog rows. Used as the
 *  prose explanation in the new ``Effort`` tab. The numbers are computed at
 *  module load so they always reflect the table above; if you tweak any
 *  ``effort`` value, the distribution updates automatically. */
const EFFORT_DEFAULT_DISTRIBUTION = (() => {
  const counts: Record<EffortLevel, number> = { low: 0, medium: 0, high: 0 };
  for (const row of IMPROVEMENT_CATEGORIES) {
    counts[row.effort] += 1;
  }
  return counts;
})();

function EffortGuideSection() {
  const { t, tsub } = useI18n();
  const total =
    EFFORT_DEFAULT_DISTRIBUTION.low +
    EFFORT_DEFAULT_DISTRIBUTION.medium +
    EFFORT_DEFAULT_DISTRIBUTION.high;
  const pct = (n: number) => (total === 0 ? 0 : Math.round((n / total) * 100));
  return (
    <>
      <p style={{ marginTop: 0 }}>{t("guide.improve.effortIntro")}</p>
      <h4>{t("guide.improve.effortLevelsTitle")}</h4>
      <dl className="eo-guide-kv">
        <dt>
          <span className="eo-effort" data-effort="low">
            {t("quality.improvements.effort.low")}
          </span>
        </dt>
        <dd>{t("guide.improve.effortLowBody")}</dd>
        <dt>
          <span className="eo-effort" data-effort="medium">
            {t("quality.improvements.effort.medium")}
          </span>
        </dt>
        <dd>{t("guide.improve.effortMediumBody")}</dd>
        <dt>
          <span className="eo-effort" data-effort="high">
            {t("quality.improvements.effort.high")}
          </span>
        </dt>
        <dd>{t("guide.improve.effortHighBody")}</dd>
      </dl>
      <h4>{t("guide.improve.effortDistTitle")}</h4>
      <p className="eo-mute" style={{ fontSize: 12 }}>
        {tsub("guide.improve.effortDistSub", { n: String(total) })}
      </p>
      <div className="eo-guide-table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("guide.improve.effortDistColLevel")}</th>
              <th>{t("guide.improve.effortDistColCount")}</th>
              <th>{t("guide.improve.effortDistColShare")}</th>
            </tr>
          </thead>
          <tbody>
            {(["low", "medium", "high"] as const).map((eff) => (
              <tr key={eff}>
                <td>
                  <span className="eo-effort" data-effort={eff}>
                    {t(`quality.improvements.effort.${eff}`)}
                  </span>
                </td>
                <td className="mono">{EFFORT_DEFAULT_DISTRIBUTION[eff]}</td>
                <td className="mono">{pct(EFFORT_DEFAULT_DISTRIBUTION[eff])}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>{t("guide.improve.effortPaletteTitle")}</h4>
      <p>{t("guide.improve.effortPaletteBody")}</p>
      <h4>{t("guide.improve.effortDynamicTitle")}</h4>
      <p>{t("guide.improve.effortDynamicBody")}</p>
      <h4>{t("guide.improve.effortBulkTitle")}</h4>
      <p>{t("guide.improve.effortBulkBody")}</p>
    </>
  );
}

/** Compact, collapsible help banner for L1/L2/L3 layers.
 *
 * It used to occupy ~140px of vertical space at the top of the Golden Sets
 * page even on returning visits. Operators told us the explanation got in
 * the way — we now render it as a single-line caret toggle that defers the
 * detail panel until the user asks for it (closed by default).
 */
export function GoldenLayerLegend() {
  const { t } = useI18n();
  const golden = useGoldenLayerHelp();
  const [open, setOpen] = useState(false);
  return (
    <div
      className="eo-card"
      style={{
        background: "var(--eo-bg-2)",
        padding: open ? 10 : "6px 10px",
        marginBottom: 12,
      }}
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
        <span style={{ fontFamily: "var(--eo-mono)", fontSize: 11 }}>
          {open ? "▾" : "▸"}
        </span>
        <span>
          <strong style={{ color: "var(--eo-ink)" }}>
            {t("guide.golden.legendTitle")}
          </strong>{" "}
          <span style={{ marginLeft: 6 }}>L1 · L2 · L3</span>
        </span>
      </button>
      {open && (
        <dl
          style={{
            fontSize: 12,
            margin: "8px 0 0",
            display: "grid",
            gap: 8,
          }}
        >
          {(["L1", "L2", "L3"] as const).map((k) => (
            <div key={k}>
              <dt style={{ fontWeight: 600 }}>{golden[k].title}</dt>
              <dd style={{ margin: "2px 0 0", color: "var(--eo-mute)" }}>
                {golden[k].body}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
