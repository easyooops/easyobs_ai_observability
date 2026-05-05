"""Built-in rule-based evaluator catalog.

Each entry is a *deterministic* check that runs at trace-ingest time and
produces a bounded score plus a short reason string. Categories follow the
metric tree from ``02.design/06.evaluation-metrics-and-pipelines.md``.

We deliberately bake the catalog as plain Python (rather than reading
shipped YAML) so the test suite can pin the canonical IDs and the API can
expose them through ``GET /v1/evaluations/evaluators``. Operators may still
attach a custom ``params`` dict per profile to override thresholds.

None of these heuristics depend on a third-party library.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from easyobs.eval.rules.dsl import (
    DSLError,
    RuleContext,
    coerce_score,
    evaluate_dsl,
)
from easyobs.eval.types import Verdict


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    score: float
    verdict: Verdict
    reason: str
    details: dict[str, Any]


def _verdict(score: float, threshold: float) -> Verdict:
    if score >= threshold:
        return Verdict.PASS
    if score >= max(threshold - 0.2, 0.0):
        return Verdict.WARN
    return Verdict.FAIL


# ---------------------------------------------------------------------------
# Helpers used by multiple evaluators
# ---------------------------------------------------------------------------


def _trace_response(ctx: RuleContext) -> str:
    summary = ctx.summary or {}
    return str(summary.get("response") or summary.get("query") or "")


def _trace_query(ctx: RuleContext) -> str:
    summary = ctx.summary or {}
    return str(summary.get("query") or "")


def _docs_top(ctx: RuleContext) -> list[dict[str, Any]]:
    """Pull retrieval docs out of the trace summary if any.

    The shape comes from `easyobs.services.llm_attrs.summarise_trace` which
    surfaces `docsRaw` (raw JSON string) and `docsCount`. Older payloads
    may stash it under `extra.docs` (golden-set assisted runs)."""

    extra_docs = ctx.extra.get("docs")
    if isinstance(extra_docs, list):
        return extra_docs
    summary = ctx.summary or {}
    raw = summary.get("docsRaw") or summary.get("docs")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _span_attrs(span: dict[str, Any]) -> dict[str, Any]:
    raw = span.get("attributes")
    if isinstance(raw, dict):
        return raw
    out: dict[str, Any] = {}
    if isinstance(raw, list):
        for a in raw:
            if not isinstance(a, dict):
                continue
            k = a.get("key")
            if not isinstance(k, str) or not k:
                continue
            v = a.get("value")
            if isinstance(v, dict) and v:
                out[k] = next(iter(v.values()))
            else:
                out[k] = v
    return out


def _tool_spans(ctx: RuleContext) -> list[dict[str, Any]]:
    spans = ctx.spans or []
    out: list[dict[str, Any]] = []
    for s in spans:
        if not isinstance(s, dict):
            continue
        attrs = _span_attrs(s)
        kind = str(attrs.get("o.kind") or attrs.get("kind") or "").lower()
        if kind == "tool":
            out.append({"span": s, "attrs": attrs})
    return out


def _parse_ms(start: Any, end: Any) -> float | None:
    try:
        from datetime import datetime

        if isinstance(start, str) and isinstance(end, str) and ("T" in start or "T" in end):
            return max(
                0.0,
                (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000.0,
            )
        sn = float(start)
        en = float(end)
        if sn > 1e12 and en > 1e12:
            return max(0.0, (en - sn) / 1e6)  # ns -> ms
        return max(0.0, en - sn)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Evaluator implementations
# ---------------------------------------------------------------------------


def _eval_response_present(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    text = _trace_response(ctx)
    score = 1.0 if text.strip() else 0.0
    return RuleOutcome(
        score=score,
        verdict=Verdict.PASS if score == 1.0 else Verdict.FAIL,
        reason="response empty" if score == 0.0 else "response captured",
        details={"length": len(text)},
    )


def _eval_response_length(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    text = _trace_response(ctx)
    minimum = int(params.get("min_chars", 20))
    maximum = int(params.get("max_chars", 4000))
    n = len(text)
    if n == 0:
        return RuleOutcome(0.0, Verdict.FAIL, "empty response", {"chars": 0})
    if n < minimum:
        return RuleOutcome(0.3, Verdict.WARN, f"too short ({n} < {minimum})", {"chars": n})
    if n > maximum:
        return RuleOutcome(0.5, Verdict.WARN, f"too long ({n} > {maximum})", {"chars": n})
    return RuleOutcome(1.0, Verdict.PASS, f"{n} chars", {"chars": n})


def _eval_response_language(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Detect mixed-language responses with a cheap heuristic.

    We don't try to be cleverer than ``langdetect`` here — the goal is to
    surface the obvious failure mode of an English-only model answering a
    Korean prompt with English. So we just measure the ratio of CJK vs.
    ASCII letters and warn when it's wildly different from the prompt."""

    text = _trace_response(ctx)
    prompt = _trace_query(ctx)
    if not text:
        return RuleOutcome(0.0, Verdict.FAIL, "empty response", {})
    cjk_text = sum(1 for c in text if "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7a3")
    ascii_text = sum(1 for c in text if c.isascii() and c.isalpha())
    cjk_prompt = sum(1 for c in prompt if "\u4e00" <= c <= "\u9fff" or "\uac00" <= c <= "\ud7a3")
    ascii_prompt = sum(1 for c in prompt if c.isascii() and c.isalpha())
    expects_cjk = cjk_prompt > ascii_prompt
    has_cjk = cjk_text > ascii_text
    matched = expects_cjk == has_cjk
    return RuleOutcome(
        1.0 if matched else 0.4,
        Verdict.PASS if matched else Verdict.WARN,
        "language matched" if matched else "language mismatch",
        {
            "expects_cjk": expects_cjk,
            "has_cjk": has_cjk,
            "cjk_chars": cjk_text,
            "ascii_chars": ascii_text,
        },
    )


def _eval_response_json_valid(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    text = _trace_response(ctx).strip()
    if not text:
        return RuleOutcome(0.0, Verdict.FAIL, "empty response", {})
    if not (text.startswith("{") or text.startswith("[")):
        return RuleOutcome(0.0, Verdict.FAIL, "response is not JSON shape", {})
    try:
        parsed = json.loads(text)
    except Exception as exc:
        return RuleOutcome(0.0, Verdict.FAIL, f"invalid JSON: {exc}", {})
    return RuleOutcome(1.0, Verdict.PASS, "valid JSON", {"top_keys": _summarise_keys(parsed)})


def _summarise_keys(parsed: Any) -> list[str]:
    if isinstance(parsed, dict):
        return sorted(parsed.keys())[:8]
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return sorted(parsed[0].keys())[:8]
    return []


def _eval_response_no_pii(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Spot the obvious PII shapes (email / phone / national IDs).

    We only flag what we can prove with a regex; anything fancier belongs
    to the Judge layer so we can argue with it later."""

    text = _trace_response(ctx)
    email = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    phone = re.findall(r"\b(?:\+?\d{1,3}[\s-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s-]?\d{3,4}[\s-]?\d{3,4}\b", text)
    ssn = re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text)
    rrn = re.findall(r"\b\d{6}-\d{7}\b", text)
    hits = len(email) + len(phone) + len(ssn) + len(rrn)
    if hits == 0:
        return RuleOutcome(1.0, Verdict.PASS, "no PII detected", {})
    return RuleOutcome(
        0.0,
        Verdict.FAIL,
        f"{hits} PII candidate(s)",
        {"email": len(email), "phone": len(phone), "us_ssn": len(ssn), "kr_rrn": len(rrn)},
    )


def _eval_response_no_secret(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Guard against the model leaking common bearer/secret formats.

    Patterns intentionally stay narrow — broad regexes over base64-looking
    blobs produce too much noise to action."""

    text = _trace_response(ctx)
    patterns = [
        (r"sk-[A-Za-z0-9]{20,}", "openai"),
        (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
        (r"AIza[0-9A-Za-z_-]{35}", "google_api"),
        (r"ghp_[A-Za-z0-9]{36}", "github_pat"),
        (r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----", "private_key"),
    ]
    hits = []
    for regex, label in patterns:
        if re.search(regex, text):
            hits.append(label)
    if not hits:
        return RuleOutcome(1.0, Verdict.PASS, "no secret pattern", {})
    return RuleOutcome(0.0, Verdict.FAIL, f"secret leak: {','.join(hits)}", {"types": hits})


def _eval_response_no_profanity(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Simple deny-list. Operators can override the list per-profile."""

    text = _trace_response(ctx).lower()
    deny = [w.lower() for w in (params.get("deny", []) or [])]
    default = ["damn", "shit", "fuck", "bastard"]
    deny = deny or default
    hits = [w for w in deny if w and w in text]
    if not hits:
        return RuleOutcome(1.0, Verdict.PASS, "no profanity", {})
    return RuleOutcome(0.0, Verdict.FAIL, f"flagged: {','.join(hits[:3])}", {"hits": hits})


def _eval_retrieval_recall(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Recall@k against the golden set entry attached via ``extra.golden``.

    Without a golden item we *cannot* compute recall — return verdict UNSET
    rather than guessing. This is intentional: the UI shows ``--`` for
    unset and we never want to claim 100% recall on missing ground truth.
    """

    golden = ctx.extra.get("golden") or {}
    expected = golden.get("expected_doc_ids") or []
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top_ids = [str(d.get("id") or d.get("doc_id") or "") for d in docs[:k]]
    hit = sum(1 for e in expected if str(e) in top_ids)
    score = hit / max(len(expected), 1)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.6))),
        f"recall@{k}={score:.2f} ({hit}/{len(expected)})",
        {"hit": hit, "k": k, "expected": len(expected)},
    )


def _eval_retrieval_precision(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    golden = ctx.extra.get("golden") or {}
    expected = set(str(x) for x in (golden.get("expected_doc_ids") or []))
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top = docs[:k]
    if not top:
        return RuleOutcome(0.0, Verdict.FAIL, "no docs returned", {})
    matched = sum(1 for d in top if str(d.get("id") or d.get("doc_id") or "") in expected)
    score = matched / max(len(top), 1)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.6))),
        f"precision@{k}={score:.2f}",
        {"matched": matched, "k": k},
    )


def _eval_retrieval_mrr(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    golden = ctx.extra.get("golden") or {}
    expected = set(str(x) for x in (golden.get("expected_doc_ids") or []))
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    rr = 0.0
    for idx, d in enumerate(docs, start=1):
        if str(d.get("id") or d.get("doc_id") or "") in expected:
            rr = 1.0 / idx
            break
    return RuleOutcome(
        rr,
        _verdict(rr, float(params.get("threshold", 0.5))),
        f"mrr={rr:.3f}",
        {"docs": len(docs)},
    )


def _eval_hit_rate_at_k(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Binary hit: any golden doc id appears in top-k (0/1), then thresholded."""

    golden = ctx.extra.get("golden") or {}
    expected = golden.get("expected_doc_ids") or []
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top_ids = {str(d.get("id") or d.get("doc_id") or "") for d in docs[:k]}
    hit = any(str(e) in top_ids for e in expected)
    score = 1.0 if hit else 0.0
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.5))),
        "hit@k=1" if hit else "hit@k=0",
        {"k": k},
    )


def _eval_ndcg_at_k_binary(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Binary-relevance nDCG@k (golden ids = relevant) — standard IR formula."""

    golden = ctx.extra.get("golden") or {}
    expected = {str(x) for x in (golden.get("expected_doc_ids") or [])}
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top = docs[:k]
    if not top:
        return RuleOutcome(0.0, Verdict.FAIL, "no docs returned", {})
    rel = [1.0 if str(d.get("id") or d.get("doc_id") or "") in expected else 0.0 for d in top]
    import math

    dcg = sum((2**r - 1) / math.log2(i + 2) for i, r in enumerate(rel))
    ideal = sorted(rel, reverse=True)
    idcg = sum((2**r - 1) / math.log2(i + 2) for i, r in enumerate(ideal))
    score = (dcg / idcg) if idcg > 0 else 0.0
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.5))),
        f"ndcg@{k}={score:.3f}",
        {"k": k, "dcg": round(dcg, 4), "idcg": round(idcg, 4)},
    )


def _eval_map_binary(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Mean Average Precision for a single query with binary relevance."""

    golden = ctx.extra.get("golden") or {}
    expected = {str(x) for x in (golden.get("expected_doc_ids") or [])}
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no golden retrieval", {})
    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top = docs[:k]
    if not top:
        return RuleOutcome(0.0, Verdict.FAIL, "no docs returned", {})
    hits = 0
    precisions: list[float] = []
    for i, d in enumerate(top, start=1):
        if str(d.get("id") or d.get("doc_id") or "") in expected:
            hits += 1
            precisions.append(hits / i)
    score = sum(precisions) / max(len(expected), 1) if expected else 0.0
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.5))),
        f"map@{k}={score:.3f}",
        {"k": k, "relevant_found": hits},
    )


def _eval_retrieval_dup_ratio(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Near-duplicate density in top-k using normalized snippet text equality."""

    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top = docs[:k]
    if len(top) < 2:
        return RuleOutcome(1.0, Verdict.PASS, "insufficient docs for diversity", {"k": k})

    def norm_snippet(d: dict[str, Any]) -> str:
        s = str(d.get("snippet") or d.get("text") or "")[:400].lower()
        return " ".join(s.split())

    norms = [norm_snippet(d) for d in top if norm_snippet(d)]
    if len(norms) < 2:
        return RuleOutcome(1.0, Verdict.PASS, "no snippets to compare", {"k": k})
    dup = 0
    for i in range(len(norms)):
        for j in range(i + 1, len(norms)):
            if norms[i] and norms[i] == norms[j]:
                dup += 1
    pairs = len(norms) * (len(norms) - 1) / 2
    ratio = dup / max(pairs, 1.0)
    thr = float(params.get("dup_threshold", 0.35))
    score = max(0.0, 1.0 - ratio / max(thr, 1e-6))
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.55))),
        f"dup_ratio={ratio:.2f}",
        {"pairs": int(pairs), "dup_pairs": dup},
    )


def _eval_retrieval_step_latency(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Retrieval / search step latency from trace summary when present."""

    summary = ctx.summary or {}
    ms = summary.get("retrieveLatencyMs")
    if ms is None:
        ms = summary.get("retrievalLatencyMs")
    if ms is None:
        return RuleOutcome(0.0, Verdict.UNSET, "no retrieval latency in summary", {})
    try:
        v = float(ms)
    except Exception:
        return RuleOutcome(0.0, Verdict.UNSET, "bad retrieval latency value", {})
    budget = float(params.get("budget_ms", 3000))
    if v <= budget:
        return RuleOutcome(1.0, Verdict.PASS, f"{v:.0f}ms ≤ {budget:.0f}ms", {"ms": v})
    if v <= budget * 1.5:
        return RuleOutcome(0.5, Verdict.WARN, f"{v:.0f}ms", {"ms": v})
    return RuleOutcome(0.0, Verdict.FAIL, f"{v:.0f}ms over budget", {"ms": v})


def _eval_query_complexity(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Lightweight query complexity heuristic (length + multi-hop cues)."""

    q = _trace_query(ctx).strip()
    if not q:
        return RuleOutcome(0.0, Verdict.UNSET, "no query", {})
    score = 1.0
    n = len(q)
    if n > int(params.get("warn_chars", 800)):
        score *= 0.7
    if q.count("?") > 2 or " and " in q.lower() or " vs " in q.lower():
        score *= 0.85
    if n < int(params.get("min_chars", 4)):
        score *= 0.5
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.45))),
        f"complexity score={score:.2f}",
        {"chars": n},
    )


def _eval_context_dup_chunks(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Duplicate chunk ratio in retrieved docs list."""

    docs = _docs_top(ctx)
    if len(docs) < 2:
        return RuleOutcome(1.0, Verdict.PASS, "single chunk", {})

    def key(d: dict[str, Any]) -> str:
        return str(d.get("id") or "") or str(d.get("snippet") or "")[:120]

    keys = [key(d) for d in docs if key(d)]
    if not keys:
        return RuleOutcome(1.0, Verdict.PASS, "no chunk keys", {})
    uniq = len(set(keys))
    ratio = 1.0 - (uniq / max(len(keys), 1))
    score = max(0.0, 1.0 - ratio * 2.0)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.5))),
        f"dup_chunks={ratio:.2f}",
        {"chunks": len(keys), "unique": uniq},
    )


def _eval_context_token_waste(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Heuristic: high token count vs short visible answer may indicate waste."""

    summary = ctx.summary or {}
    tok = int(summary.get("tokensTotal") or 0)
    resp = _trace_response(ctx)
    rlen = max(len(resp), 1)
    ratio = tok / max(rlen, 1)
    warn = float(params.get("warn_ratio", 8.0))
    if tok == 0:
        return RuleOutcome(0.0, Verdict.UNSET, "no token usage", {})
    if ratio <= warn:
        return RuleOutcome(1.0, Verdict.PASS, f"tokens/char={ratio:.1f}", {"ratio": ratio})
    if ratio <= warn * 1.5:
        return RuleOutcome(0.5, Verdict.WARN, f"tokens/char={ratio:.1f}", {"ratio": ratio})
    return RuleOutcome(0.0, Verdict.FAIL, f"tokens/char={ratio:.1f}", {"ratio": ratio})


def _eval_intent_match(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Compare expected intent (golden/human/params) with trace intent hints."""

    expected = (
        (ctx.extra.get("golden") or {}).get("intent")
        or (ctx.extra.get("humanLabel") or {}).get("intent")
        or params.get("expected_intent")
        or ""
    )
    if not str(expected).strip():
        return RuleOutcome(0.0, Verdict.UNSET, "no expected intent", {})
    summary = ctx.summary or {}
    got = summary.get("intent") or summary.get("routeIntent") or summary.get("queryIntent") or ""
    if not str(got).strip():
        q = _trace_query(ctx).lower()
        got = "lookup" if any(w in q for w in ("where", "what", "status", "how")) else "chat"
    matched = str(expected).strip().lower() == str(got).strip().lower()
    return RuleOutcome(
        1.0 if matched else 0.0,
        Verdict.PASS if matched else Verdict.FAIL,
        "intent matched" if matched else f"intent mismatch ({got} != {expected})",
        {"expected": expected, "got": got},
    )


def _eval_reranker_gain(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Compare post-rerank nDCG vs pre-rerank nDCG when both are available."""

    summary = ctx.summary or {}
    before = summary.get("rerankNdcgBefore")
    after = summary.get("rerankNdcgAfter")
    if before is None or after is None:
        return RuleOutcome(0.0, Verdict.UNSET, "missing rerank before/after ndcg", {})
    try:
        b = float(before)
        a = float(after)
    except Exception:
        return RuleOutcome(0.0, Verdict.UNSET, "bad rerank ndcg values", {})
    gain = a - b
    threshold = float(params.get("min_gain", 0.02))
    score = 1.0 if gain >= threshold else max(0.0, 1.0 + gain / max(threshold, 1e-6))
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.55))),
        f"rerank gain={gain:.3f}",
        {"before": b, "after": a, "gain": gain},
    )


def _eval_chunk_semantic_similarity(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Average top-k retrieval scores as a similarity proxy."""

    docs = _docs_top(ctx)
    k = int(params.get("k", 5))
    top = docs[:k]
    vals: list[float] = []
    for d in top:
        sc = d.get("score")
        try:
            vals.append(float(sc))
        except Exception:
            continue
    if not vals:
        return RuleOutcome(0.0, Verdict.UNSET, "no retrieval scores", {})
    avg = sum(vals) / len(vals)
    return RuleOutcome(
        avg,
        _verdict(avg, float(params.get("threshold", 0.5))),
        f"avg sim={avg:.3f}",
        {"k": len(vals)},
    )


def _eval_citation_accuracy(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Validate cited [doc_id] tags against retrieved doc ids."""

    import re as _re

    text = _trace_response(ctx)
    cited = set(_re.findall(r"\[([A-Za-z0-9._:-]+)\]", text))
    if not cited:
        return RuleOutcome(0.0, Verdict.UNSET, "no citation tags found", {})
    docs = _docs_top(ctx)
    known = {str(d.get("id") or d.get("doc_id") or "") for d in docs}
    known = {k for k in known if k}
    if not known:
        return RuleOutcome(0.0, Verdict.UNSET, "no retrieved docs to validate", {"cited": len(cited)})
    matched = len([c for c in cited if c in known])
    score = matched / max(len(cited), 1)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.7))),
        f"citations valid={matched}/{len(cited)}",
        {"cited": len(cited), "matched": matched},
    )


def _eval_tool_selection_accuracy(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    expected = (
        (ctx.extra.get("golden") or {}).get("expected_tool")
        or (ctx.extra.get("humanLabel") or {}).get("expectedTool")
        or params.get("expected_tool")
        or ""
    )
    tools = [str(x["attrs"].get("o.tool") or "").strip() for x in _tool_spans(ctx)]
    tools = [t for t in tools if t]
    if not expected:
        return RuleOutcome(0.0, Verdict.UNSET, "no expected tool", {"tools": tools[:3]})
    if not tools:
        return RuleOutcome(0.0, Verdict.FAIL, "no tool call", {"expected": expected})
    matched = any(t.lower() == str(expected).lower() for t in tools)
    return RuleOutcome(
        1.0 if matched else 0.0,
        Verdict.PASS if matched else Verdict.FAIL,
        "expected tool used" if matched else f"expected {expected}, got {tools[0]}",
        {"expected": expected, "tools": tools[:5]},
    )


def _eval_tool_argument_validity(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Basic schema-ish check: required arg names must appear in tool input payload."""

    required = params.get("required_args") or []
    if not isinstance(required, list) or not required:
        return RuleOutcome(0.0, Verdict.UNSET, "no required_args configured", {})
    spans = _tool_spans(ctx)
    if not spans:
        return RuleOutcome(0.0, Verdict.UNSET, "no tool span", {})
    payload = str(spans[0]["attrs"].get("o.tool.in") or "")
    hit = sum(1 for r in required if str(r) and str(r) in payload)
    score = hit / max(len(required), 1)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.7))),
        f"tool args matched {hit}/{len(required)}",
        {"required": required, "tool": spans[0]["attrs"].get("o.tool")},
    )


def _eval_tool_call_success_rate(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    spans = _tool_spans(ctx)
    if not spans:
        return RuleOutcome(0.0, Verdict.UNSET, "no tool span", {})
    ok = 0
    for s in spans:
        attrs = s["attrs"]
        status = str(
            attrs.get("o.verdict")
            or attrs.get("tool.status")
            or attrs.get("status")
            or "ok"
        ).lower()
        if status in {"ok", "pass", "success", "succeeded", "200"}:
            ok += 1
    score = ok / max(len(spans), 1)
    return RuleOutcome(
        score,
        _verdict(score, float(params.get("threshold", 0.8))),
        f"tool success {ok}/{len(spans)}",
        {"calls": len(spans)},
    )


def _eval_tool_retry_count(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    spans = _tool_spans(ctx)
    if not spans:
        return RuleOutcome(1.0, Verdict.PASS, "no tool call", {"retries": 0})
    by_tool: dict[str, int] = {}
    for s in spans:
        tool = str(s["attrs"].get("o.tool") or "unknown")
        by_tool[tool] = by_tool.get(tool, 0) + 1
    retries = sum(max(0, n - 1) for n in by_tool.values())
    max_retries = int(params.get("max_retries", 2))
    if retries <= max_retries:
        return RuleOutcome(1.0, Verdict.PASS, f"retries={retries}", {"retries": retries})
    if retries <= max_retries * 2:
        return RuleOutcome(0.5, Verdict.WARN, f"retries={retries}", {"retries": retries})
    return RuleOutcome(0.0, Verdict.FAIL, f"retries={retries}", {"retries": retries})


def _eval_model_inference_latency(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    spans = ctx.spans or []
    llm_ms: list[float] = []
    for s in spans:
        if not isinstance(s, dict):
            continue
        attrs = _span_attrs(s)
        kind = str(attrs.get("o.kind") or attrs.get("kind") or "").lower()
        if kind != "llm":
            continue
        ms = _parse_ms(
            s.get("startTimeUnixNano") or s.get("startedAt"),
            s.get("endTimeUnixNano") or s.get("endedAt"),
        )
        if ms is not None:
            llm_ms.append(ms)
    if not llm_ms:
        return RuleOutcome(0.0, Verdict.UNSET, "no llm spans", {})
    avg = sum(llm_ms) / len(llm_ms)
    budget = float(params.get("budget_ms", 6000))
    if avg <= budget:
        return RuleOutcome(1.0, Verdict.PASS, f"llm avg {avg:.0f}ms", {"avgMs": avg})
    if avg <= budget * 1.5:
        return RuleOutcome(0.5, Verdict.WARN, f"llm avg {avg:.0f}ms", {"avgMs": avg})
    return RuleOutcome(0.0, Verdict.FAIL, f"llm avg {avg:.0f}ms", {"avgMs": avg})


def _eval_latency(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    trace = ctx.trace or {}
    start = trace.get("startedAt")
    end = trace.get("endedAt")
    if not start or not end:
        return RuleOutcome(0.0, Verdict.UNSET, "no end timestamp", {})
    try:
        from datetime import datetime

        ms = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000.0
    except Exception:
        return RuleOutcome(0.0, Verdict.UNSET, "bad timestamps", {})
    budget = float(params.get("budget_ms", 5000))
    if ms <= budget:
        return RuleOutcome(1.0, Verdict.PASS, f"{ms:.0f}ms within {budget:.0f}ms", {"ms": ms})
    if ms <= budget * 1.5:
        return RuleOutcome(0.5, Verdict.WARN, f"{ms:.0f}ms exceeds {budget:.0f}ms", {"ms": ms})
    return RuleOutcome(0.0, Verdict.FAIL, f"{ms:.0f}ms blew through budget", {"ms": ms})


def _eval_token_budget(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    summary = ctx.summary or {}
    used = int(summary.get("tokensTotal") or 0)
    budget = int(params.get("budget_tokens", 8000))
    if used == 0:
        return RuleOutcome(0.0, Verdict.UNSET, "no token usage captured", {})
    if used <= budget:
        return RuleOutcome(1.0, Verdict.PASS, f"{used} ≤ {budget}", {"tokens": used})
    if used <= budget * 1.25:
        return RuleOutcome(0.5, Verdict.WARN, f"{used} > {budget}", {"tokens": used})
    return RuleOutcome(0.0, Verdict.FAIL, f"{used} >> {budget}", {"tokens": used})


def _eval_cost_budget(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    summary = ctx.summary or {}
    cost = float(summary.get("price") or 0.0)
    budget = float(params.get("max_usd", 0.05))
    if cost == 0.0:
        return RuleOutcome(1.0, Verdict.PASS, "no cost", {"usd": cost})
    if cost <= budget:
        return RuleOutcome(1.0, Verdict.PASS, f"${cost:.4f} ≤ ${budget:.4f}", {"usd": cost})
    if cost <= budget * 2:
        return RuleOutcome(0.5, Verdict.WARN, f"${cost:.4f} > ${budget:.4f}", {"usd": cost})
    return RuleOutcome(0.0, Verdict.FAIL, f"${cost:.4f} >> ${budget:.4f}", {"usd": cost})


def _eval_status_ok(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    status = (ctx.trace or {}).get("status") or "UNSET"
    if status == "ERROR":
        return RuleOutcome(0.0, Verdict.FAIL, "trace ended in ERROR", {"status": status})
    if status == "OK":
        return RuleOutcome(1.0, Verdict.PASS, "trace OK", {"status": status})
    return RuleOutcome(0.6, Verdict.WARN, "trace status unset", {"status": status})


def _eval_tool_loop(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    summary = ctx.summary or {}
    tool_calls = int(summary.get("toolCalls") or 0)
    threshold = int(params.get("max_tool_calls", 8))
    if tool_calls == 0:
        return RuleOutcome(1.0, Verdict.PASS, "no tool calls", {"calls": tool_calls})
    if tool_calls <= threshold:
        return RuleOutcome(1.0, Verdict.PASS, f"{tool_calls} tool calls", {"calls": tool_calls})
    if tool_calls <= threshold * 2:
        return RuleOutcome(0.5, Verdict.WARN, f"{tool_calls} tool calls > {threshold}", {"calls": tool_calls})
    return RuleOutcome(0.0, Verdict.FAIL, f"likely tool loop: {tool_calls}", {"calls": tool_calls})


def _eval_dsl(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Generic operator-defined evaluator backed by the safe DSL.

    Profile params:
      - ``expression``: required, returns score (bool/float) or array of bools.
      - ``threshold``: optional, default 0.5
      - ``label``: optional, sets ``reason`` when not failing.
    """

    expression = params.get("expression") or ""
    threshold = float(params.get("threshold", 0.5))
    label = str(params.get("label") or "custom rule")
    try:
        raw = evaluate_dsl(expression, ctx)
    except DSLError as exc:
        return RuleOutcome(0.0, Verdict.FAIL, f"dsl error: {exc}", {"expression": expression})
    score = coerce_score(raw)
    return RuleOutcome(score, _verdict(score, threshold), label, {"raw": _stringify(raw)})


def _stringify(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, default=str)[:240]
    except Exception:
        return str(v)[:240]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuiltinEvaluator:
    """Static metadata for a built-in evaluator. Profiles reference these by
    ``id`` and overlay their own ``params``."""

    id: str
    name: str
    category: str
    description: str
    layer: str
    default_params: dict[str, Any]
    runner: Callable[[RuleContext, dict[str, Any]], RuleOutcome]


CORE_BUILTIN_EVALUATORS: tuple[BuiltinEvaluator, ...] = (
    BuiltinEvaluator(
        "rule.response.present",
        "Response present",
        "response",
        "Trace produced a non-empty assistant response.",
        "L3",
        {},
        _eval_response_present,
    ),
    BuiltinEvaluator(
        "rule.response.length",
        "Response length range",
        "response",
        "Response character count stays inside the configured range.",
        "L3",
        {"min_chars": 20, "max_chars": 4000},
        _eval_response_length,
    ),
    BuiltinEvaluator(
        "rule.response.json",
        "Response is valid JSON",
        "format",
        "Response parses as JSON when the use case requires structured output.",
        "L3",
        {},
        _eval_response_json_valid,
    ),
    BuiltinEvaluator(
        "rule.response.language",
        "Response language matches prompt",
        "i18n",
        "Heuristic CJK/ASCII match between prompt and response.",
        "L3",
        {},
        _eval_response_language,
    ),
    BuiltinEvaluator(
        "rule.safety.no_pii",
        "No PII in response",
        "safety",
        "Block obvious PII (email, phone, SSN, RRN) leaving the agent.",
        "L3",
        {},
        _eval_response_no_pii,
    ),
    BuiltinEvaluator(
        "rule.safety.no_secret",
        "No secret leak",
        "safety",
        "Block bearer/API key shapes from appearing in the response.",
        "L3",
        {},
        _eval_response_no_secret,
    ),
    BuiltinEvaluator(
        "rule.safety.no_profanity",
        "No profanity",
        "safety",
        "Deny-list profanity filter — operators can override the list.",
        "L3",
        {"deny": []},
        _eval_response_no_profanity,
    ),
    BuiltinEvaluator(
        "rule.retrieval.recall_at_k",
        "Retrieval Recall@k",
        "retrieval",
        "Hit rate of expected golden doc IDs in the top-k retrieved set.",
        "L2",
        {"k": 5, "threshold": 0.6},
        _eval_retrieval_recall,
    ),
    BuiltinEvaluator(
        "rule.retrieval.precision_at_k",
        "Retrieval Precision@k",
        "retrieval",
        "Share of the top-k retrieved docs that match the golden set.",
        "L2",
        {"k": 5, "threshold": 0.6},
        _eval_retrieval_precision,
    ),
    BuiltinEvaluator(
        "rule.retrieval.mrr",
        "Retrieval MRR",
        "retrieval",
        "Mean reciprocal rank of the first golden hit in the retrieval list.",
        "L2",
        {"threshold": 0.5},
        _eval_retrieval_mrr,
    ),
    BuiltinEvaluator(
        "rule.retrieval.hit_rate_at_k",
        "Retrieval HitRate@k",
        "retrieval",
        "Binary: any expected golden doc id appears in the top-k retrieved set.",
        "L2",
        {"k": 5, "threshold": 0.5},
        _eval_hit_rate_at_k,
    ),
    BuiltinEvaluator(
        "rule.retrieval.ndcg_at_k_binary",
        "Retrieval nDCG@k (binary relevance)",
        "retrieval",
        "nDCG@k using binary relevance from golden doc ids vs top-k ranking.",
        "L2",
        {"k": 5, "threshold": 0.5},
        _eval_ndcg_at_k_binary,
    ),
    BuiltinEvaluator(
        "rule.retrieval.map_binary",
        "Retrieval MAP (binary relevance)",
        "retrieval",
        "Mean average precision for one query with binary relevance labels.",
        "L2",
        {"k": 5, "threshold": 0.5},
        _eval_map_binary,
    ),
    BuiltinEvaluator(
        "rule.retrieval.dup_ratio",
        "Retrieval duplicate / redundancy",
        "retrieval",
        "Penalises near-duplicate snippets in the top-k retrieved list.",
        "L2",
        {"k": 5, "threshold": 0.55, "dup_threshold": 0.35},
        _eval_retrieval_dup_ratio,
    ),
    BuiltinEvaluator(
        "rule.retrieval.step_latency",
        "Retrieval step latency",
        "retrieval",
        "Compares summary.retrieveLatencyMs (or retrievalLatencyMs) to a budget.",
        "L2",
        {"budget_ms": 3000},
        _eval_retrieval_step_latency,
    ),
    BuiltinEvaluator(
        "rule.query.intent_match",
        "Intent classification accuracy",
        "response",
        "Matches expected intent against trace intent/route hints.",
        "L1",
        {"threshold": 0.6},
        _eval_intent_match,
    ),
    BuiltinEvaluator(
        "rule.query.complexity",
        "Query complexity heuristic",
        "response",
        "Flags very long or multi-hop style queries using lightweight heuristics.",
        "L1",
        {"threshold": 0.45, "warn_chars": 800, "min_chars": 4},
        _eval_query_complexity,
    ),
    BuiltinEvaluator(
        "rule.retrieval.reranker_gain",
        "Reranker gain",
        "retrieval",
        "Checks improvement between rerankNdcgBefore and rerankNdcgAfter.",
        "L2",
        {"min_gain": 0.02, "threshold": 0.55},
        _eval_reranker_gain,
    ),
    BuiltinEvaluator(
        "rule.context.chunk_semantic_similarity",
        "Chunk semantic similarity",
        "retrieval",
        "Averages retrieval score values as a semantic similarity proxy.",
        "L2",
        {"k": 5, "threshold": 0.5},
        _eval_chunk_semantic_similarity,
    ),
    BuiltinEvaluator(
        "rule.response.citation_accuracy",
        "Citation accuracy",
        "response",
        "Validates [doc_id] citations against retrieved document ids.",
        "L3",
        {"threshold": 0.7},
        _eval_citation_accuracy,
    ),
    BuiltinEvaluator(
        "rule.tool.selection_accuracy",
        "Tool selection accuracy",
        "reliability",
        "Compares expected tool label against actual tool spans.",
        "L1",
        {"threshold": 0.6},
        _eval_tool_selection_accuracy,
    ),
    BuiltinEvaluator(
        "rule.tool.argument_validity",
        "Tool argument validity",
        "reliability",
        "Checks required tool argument keys in first tool payload.",
        "L1",
        {"required_args": [], "threshold": 0.7},
        _eval_tool_argument_validity,
    ),
    BuiltinEvaluator(
        "rule.tool.success_rate",
        "Tool call success rate",
        "reliability",
        "Computes pass ratio from tool span status/verdict hints.",
        "L1",
        {"threshold": 0.8},
        _eval_tool_call_success_rate,
    ),
    BuiltinEvaluator(
        "rule.tool.retry_count",
        "Tool retry count",
        "reliability",
        "Counts repeated calls per tool as retries.",
        "L1",
        {"max_retries": 2},
        _eval_tool_retry_count,
    ),
    BuiltinEvaluator(
        "rule.context.dup_chunks",
        "Context duplicate chunks",
        "retrieval",
        "Share of retrieved chunks that are duplicates by id/snippet key.",
        "L2",
        {"threshold": 0.5},
        _eval_context_dup_chunks,
    ),
    BuiltinEvaluator(
        "rule.context.token_waste",
        "Context token efficiency",
        "performance",
        "Heuristic ratio of total tokens to response length to flag token bloat.",
        "L3",
        {"warn_ratio": 8.0, "threshold": 0.55},
        _eval_context_token_waste,
    ),
    BuiltinEvaluator(
        "rule.perf.model_infer_latency",
        "Model inference latency",
        "performance",
        "Average latency of llm-kind spans against a budget.",
        "L3",
        {"budget_ms": 6000},
        _eval_model_inference_latency,
    ),
    BuiltinEvaluator(
        "rule.perf.latency",
        "Latency budget",
        "performance",
        "End-to-end latency stays inside the configured budget.",
        "L3",
        {"budget_ms": 5000},
        _eval_latency,
    ),
    BuiltinEvaluator(
        "rule.perf.token_budget",
        "Token budget",
        "performance",
        "Total LLM token usage stays inside the configured budget.",
        "L3",
        {"budget_tokens": 8000},
        _eval_token_budget,
    ),
    BuiltinEvaluator(
        "rule.perf.cost_budget",
        "Cost budget",
        "performance",
        "Trace cost stays inside the configured USD budget.",
        "L3",
        {"max_usd": 0.05},
        _eval_cost_budget,
    ),
    BuiltinEvaluator(
        "rule.status.ok",
        "Trace status OK",
        "reliability",
        "Trace did not end with an ERROR span status.",
        "L3",
        {},
        _eval_status_ok,
    ),
    BuiltinEvaluator(
        "rule.agent.no_tool_loop",
        "Tool call ceiling",
        "reliability",
        "Tool call count stays below the configured loop ceiling.",
        "L3",
        {"max_tool_calls": 8},
        _eval_tool_loop,
    ),
    BuiltinEvaluator(
        "rule.custom.dsl",
        "Custom DSL rule",
        "custom",
        "Operator-defined evaluator — provide a DSL ``expression`` and threshold.",
        "L3",
        {"expression": "True", "threshold": 0.5, "label": "custom rule"},
        _eval_dsl,
    ),
)

CORE_BY_ID: dict[str, BuiltinEvaluator] = {e.id: e for e in CORE_BUILTIN_EVALUATORS}


def _eval_wired_alias(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Run a core built-in by id (metric catalog ``wired`` rows)."""

    target = str(params.get("_wired_target") or "")
    inner = CORE_BY_ID.get(target)
    if inner is None:
        return RuleOutcome(
            0.0,
            Verdict.UNSET,
            f"missing wired target {target!r}",
            {"catalog_stub": True},
        )
    merged: dict[str, Any] = {**inner.default_params}
    for k, v in params.items():
        if k in ("_wired_target", "_judge_metric") or str(k).startswith("_metric_"):
            continue
        merged[k] = v
    try:
        return inner.runner(ctx, merged)
    except Exception as exc:  # pragma: no cover - defensive
        return RuleOutcome(
            0.0,
            Verdict.UNSET,
            f"wired runner error: {exc}",
            {"catalog_stub": True},
        )


def _eval_metric_stub(ctx: RuleContext, params: dict[str, Any]) -> RuleOutcome:
    """Placeholder for metrics not yet backed by deterministic rules."""

    return RuleOutcome(
        0.0,
        Verdict.UNSET,
        "metric catalog placeholder — not wired to a rule yet",
        {"catalog_stub": True, "judge_metric": bool(params.get("_judge_metric"))},
    )


def _load_metric_catalog_rows() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parent.parent / "catalog" / "eval_metric_catalog_v1.json"
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = root.get("metrics")
    return rows if isinstance(rows, list) else []


def _build_catalog_metric_evaluators() -> tuple[BuiltinEvaluator, ...]:
    out: list[BuiltinEvaluator] = []
    for row in _load_metric_catalog_rows():
        if not isinstance(row, dict):
            continue
        eid = str(row.get("id") or "").strip()
        if not eid or eid in CORE_BY_ID:
            continue
        name = str(row.get("name") or eid)
        group = str(row.get("group") or "?")
        desc = str(row.get("description") or name)
        layer = str(row.get("layer") or "L3")
        kind = str(row.get("kind") or "stub").lower()
        cat = f"metric_{group}"
        defaults: dict[str, Any] = {}
        if isinstance(row.get("defaultParams"), dict):
            defaults.update(row["defaultParams"])  # type: ignore[arg-type]
        if kind == "wired":
            target = str(row.get("ruleTarget") or "").strip()
            if not target:
                continue
            defaults["_wired_target"] = target
            runner: Callable[[RuleContext, dict[str, Any]], RuleOutcome] = _eval_wired_alias
        else:
            if kind == "judge":
                defaults["_judge_metric"] = True
                jd = row.get("judgeDimension")
                if jd:
                    defaults["judgeDimension"] = str(jd)
            runner = _eval_metric_stub
        out.append(
            BuiltinEvaluator(
                eid,
                name,
                cat,
                desc,
                layer,
                defaults,
                runner,
            )
        )
    return tuple(out)


BUILTIN_EVALUATORS: tuple[BuiltinEvaluator, ...] = (
    CORE_BUILTIN_EVALUATORS + _build_catalog_metric_evaluators()
)


_BY_ID: dict[str, BuiltinEvaluator] = {e.id: e for e in BUILTIN_EVALUATORS}


def list_builtins() -> list[BuiltinEvaluator]:
    return list(BUILTIN_EVALUATORS)


def get_builtin(eid: str) -> BuiltinEvaluator | None:
    return _BY_ID.get(eid)


def run_evaluator(
    evaluator_id: str,
    ctx: RuleContext,
    params: dict[str, Any] | None = None,
) -> RuleOutcome:
    spec = get_builtin(evaluator_id)
    if spec is None:
        return RuleOutcome(0.0, Verdict.UNSET, f"unknown evaluator {evaluator_id!r}", {})
    merged: dict[str, Any] = {**spec.default_params, **(params or {})}
    try:
        return spec.runner(ctx, merged)
    except Exception as exc:  # never crash the run because of a bad rule
        return RuleOutcome(0.0, Verdict.UNSET, f"runtime error: {exc}", {})
