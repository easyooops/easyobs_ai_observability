"""Seed EasyObs with realistic LLM-agent traces using OTLP/JSON.

Each generated trace mimics a single RAG-style turn: plan → refine →
retrieve → fact-check → generate → reflect, with a couple of traces also
invoking a tool step. Spans carry the EasyObs ``o.*`` attributes that the
backend and UI already know how to render (model, tokens, cost, docs,
query, response, session, etc.).

Usage:
    python scripts/seed-llm-demo.py                      # 20 sessions, 24h window
    python scripts/seed-llm-demo.py --sessions 40 --turns 4 --window-hours 12

No external deps — stdlib only (works without the easyobs_agent SDK).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# demo content pool (Korean + English mixed so the UI shows both)
# ---------------------------------------------------------------------------

SERVICES = (
    "rag-copilot",
    "support-bot",
    "research-agent",
)

MODELS = (
    ("local-llm-7b", "local"),
    ("gpt-4o-mini", "openai"),
    ("claude-3-haiku", "anthropic"),
    ("qwen2.5-14b", "alibaba"),
)

QUERIES = (
    "What is trace-based observability and why does it matter for agents?",
    "RAG 파이프라인에서 reflection 루프가 필요한 이유는?",
    "OTLP/HTTP 로 에이전트 trace 를 보내려면 어떤 엔드포인트를 호출해야 해?",
    "Summarise the latest release notes for our support knowledge base.",
    "벡터 검색 점수가 낮을 때 답변 품질을 높이려면 어떤 전략을 써야 할까?",
    "Compare latency tradeoffs between re-ranking and multi-step reflection.",
    "주문 번호 A-19384 의 배송 현황과 지연 사유를 알려줘.",
    "Draft an onboarding email for a new data analyst covering our dashboards.",
    "로그 파이프라인에서 span attribute 누락을 디버깅하는 체크리스트?",
    "How should I tag retrieval steps so I can filter by corpus later?",
)

DOC_SNIPPETS = (
    "EasyObs groups every LLM turn into a trace composed of plan/retrieve/generate spans.",
    "Reflection loops let the agent self-critique before returning an answer.",
    "o.sess groups multiple turns of the same conversation for session-level analytics.",
    "Token usage is captured via o.tok.in and o.tok.out per span.",
    "Retrieve step stores a compact JSON of top-k documents in o.docs.",
    "Tool spans record tool name and input/output for auditing agent behaviour.",
    "Latency outliers often map to retrieval miss + forced regeneration.",
    "OpenTelemetry resource attributes keep service.name stable across deploys.",
    "OTLP/HTTP is supported out of the box by the standard python SDK.",
    "Costs are aggregated per session using o.price summed over spans.",
)

ROOT_NAMES = (
    "easyobs.agent.turn",
    "rag.pipeline.turn",
    "support.assist.turn",
    "research.copilot.turn",
)

STEPS = ("plan", "refine", "retrieve", "fact_check", "generate", "reflect")

TOOL_NAMES = ("search.kb", "sql.lookup", "calc.eval", "format.markdown")

USERS = ("alice", "brian", "chae.s", "dong-hyun", "eunji", "victor", "sana")


# ---------------------------------------------------------------------------
# OTLP/JSON helpers
# ---------------------------------------------------------------------------


def _hex(n: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": value}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _span(
    *,
    trace_id: str,
    span_id: str,
    parent: str | None,
    name: str,
    start_ns: int,
    end_ns: int,
    status: str,
    attrs: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent or "",
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": "STATUS_CODE_ERROR" if status == "ERROR" else "STATUS_CODE_OK"},
        "attributes": [_attr(k, v) for k, v in attrs.items()],
        "events": events or [],
    }


def _estimate_tokens(text: str) -> int:
    # rough heuristic so cost/token numbers vary with input size
    return max(4, len(text) // 4)


def _price_of(model: str, tokens_in: int, tokens_out: int) -> float:
    table = {
        "local-llm-7b": (0.0, 0.0),
        "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        "claude-3-haiku": (0.25 / 1_000_000, 1.25 / 1_000_000),
        "qwen2.5-14b": (0.05 / 1_000_000, 0.20 / 1_000_000),
    }
    pin, pout = table.get(model, (0.05 / 1_000_000, 0.20 / 1_000_000))
    return round(tokens_in * pin + tokens_out * pout, 6)


def _build_turn(
    *,
    started_ns: int,
    service: str,
    session_id: str,
    user_id: str,
    query: str,
    model: str,
    vendor: str,
    force_error: bool,
    force_tool: bool,
) -> dict[str, Any]:
    trace_id = _hex(32)
    root_id = _hex(16)
    req_id = uuid.uuid4().hex[:12]

    turn_name = random.choice(ROOT_NAMES)
    spans: list[dict[str, Any]] = []

    # ---- plan ------------------------------------------------------------
    plan_id = _hex(16)
    plan_start = started_ns + 2_000_000
    plan_dur_ms = random.randint(40, 180)
    plan_end = plan_start + plan_dur_ms * 1_000_000
    plan_output = random.choice(
        [
            "1) refine query  2) retrieve  3) fact-check  4) generate  5) reflect",
            "plan: search top-k docs, draft answer, then verify before returning",
            "한 번에 retrieve → generate → reflect 순으로 진행",
        ]
    )
    plan_in = _estimate_tokens(query)
    plan_out = _estimate_tokens(plan_output)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=plan_id,
            parent=root_id,
            name=f"{service}.plan",
            start_ns=plan_start,
            end_ns=plan_end,
            status="OK",
            attrs={
                "o.kind": "plan",
                "o.step": "plan",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.model": model,
                "o.vendor": vendor,
                "o.q": query,
                "o.r": plan_output,
                "o.tok.in": plan_in,
                "o.tok.out": plan_out,
                "o.tok.sum": plan_in + plan_out,
                "o.price": _price_of(model, plan_in, plan_out),
            },
        )
    )

    # ---- refine ----------------------------------------------------------
    refine_id = _hex(16)
    refine_start = plan_end + 1_000_000
    refine_dur_ms = random.randint(30, 120)
    refine_end = refine_start + refine_dur_ms * 1_000_000
    refined_query = query.split("?")[0].strip() + " (refined)"
    refine_in = _estimate_tokens(query + plan_output)
    refine_out = _estimate_tokens(refined_query)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=refine_id,
            parent=root_id,
            name=f"{service}.refine",
            start_ns=refine_start,
            end_ns=refine_end,
            status="OK",
            attrs={
                "o.kind": "llm",
                "o.step": "refine",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.model": model,
                "o.vendor": vendor,
                "o.q": query,
                "o.r": refined_query,
                "o.tok.in": refine_in,
                "o.tok.out": refine_out,
                "o.tok.sum": refine_in + refine_out,
                "o.price": _price_of(model, refine_in, refine_out),
            },
        )
    )

    # ---- retrieve --------------------------------------------------------
    retrieve_id = _hex(16)
    retrieve_start = refine_end + 1_000_000
    retrieve_dur_ms = random.randint(60, 260)
    retrieve_end = retrieve_start + retrieve_dur_ms * 1_000_000
    doc_count = random.randint(3, 6)
    doc_snippets = random.sample(DOC_SNIPPETS, k=min(doc_count, len(DOC_SNIPPETS)))
    docs = [
        {
            "id": f"doc-{_hex(4)}",
            "score": round(random.uniform(0.55, 0.97), 3),
            "snippet": snippet[:180],
        }
        for snippet in doc_snippets
    ]
    top_score = max(d["score"] for d in docs)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=retrieve_id,
            parent=root_id,
            name=f"{service}.retrieve",
            start_ns=retrieve_start,
            end_ns=retrieve_end,
            status="OK",
            attrs={
                "o.kind": "retrieve",
                "o.step": "vector.lookup",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.q": refined_query,
                "o.docs": json.dumps(docs, ensure_ascii=False),
                "o.docs.n": len(docs),
                "o.docs.top": top_score,
            },
        )
    )

    # ---- fact-check (LLM judging retrieval) -----------------------------
    fact_id = _hex(16)
    fact_start = retrieve_end + 1_000_000
    fact_dur_ms = random.randint(40, 160)
    fact_end = fact_start + fact_dur_ms * 1_000_000
    score = round(random.uniform(0.3, 0.95), 2)
    fact_out = f"relevance={score} – {'good coverage' if score > 0.6 else 'weak coverage'}"
    fact_in_tok = _estimate_tokens(refined_query + json.dumps(docs, ensure_ascii=False))
    fact_out_tok = _estimate_tokens(fact_out)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=fact_id,
            parent=root_id,
            name=f"{service}.fact_check",
            start_ns=fact_start,
            end_ns=fact_end,
            status="OK",
            attrs={
                "o.kind": "llm",
                "o.step": "fact_check",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.model": model,
                "o.vendor": vendor,
                "o.q": refined_query,
                "o.r": fact_out,
                "o.score": score,
                "o.tok.in": fact_in_tok,
                "o.tok.out": fact_out_tok,
                "o.tok.sum": fact_in_tok + fact_out_tok,
                "o.price": _price_of(model, fact_in_tok, fact_out_tok),
            },
        )
    )

    # ---- optional tool step ---------------------------------------------
    tool_end_ns = fact_end
    if force_tool:
        tool_id = _hex(16)
        tool_start = fact_end + 1_000_000
        tool_dur_ms = random.randint(80, 200)
        tool_end_ns = tool_start + tool_dur_ms * 1_000_000
        tool_name = random.choice(TOOL_NAMES)
        spans.append(
            _span(
                trace_id=trace_id,
                span_id=tool_id,
                parent=root_id,
                name=f"{service}.tool",
                start_ns=tool_start,
                end_ns=tool_end_ns,
                status="OK",
                attrs={
                    "o.kind": "tool",
                    "o.step": "tool.call",
                    "o.sess": session_id,
                    "o.user": user_id,
                    "o.req": req_id,
                    "o.tool": tool_name,
                    "o.tool.in": json.dumps({"query": refined_query})[:180],
                    "o.tool.out": f"(mock result from {tool_name})",
                },
            )
        )

    # ---- generate --------------------------------------------------------
    gen_id = _hex(16)
    gen_start = tool_end_ns + 2_000_000
    gen_dur_ms = random.randint(200, 1200)
    gen_end = gen_start + gen_dur_ms * 1_000_000
    gen_status = "ERROR" if force_error else "OK"
    if force_error:
        answer = "[error] model rate-limited — partial answer."
    else:
        answer_fragments = random.sample(DOC_SNIPPETS, k=3)
        answer = " ".join(answer_fragments) + f"\n\n참고 질의: {query[:160]}"
    gen_in = _estimate_tokens(query + json.dumps(docs, ensure_ascii=False))
    gen_out = _estimate_tokens(answer)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=gen_id,
            parent=root_id,
            name=f"{service}.generate",
            start_ns=gen_start,
            end_ns=gen_end,
            status=gen_status,
            attrs={
                "o.kind": "llm",
                "o.step": "compose",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.model": model,
                "o.vendor": vendor,
                "o.q": query,
                "o.r": answer,
                "o.tok.in": gen_in,
                "o.tok.out": gen_out,
                "o.tok.sum": gen_in + gen_out,
                "o.price": _price_of(model, gen_in, gen_out),
            },
            events=(
                [{"name": "rate_limited", "timeUnixNano": str(gen_end)}]
                if force_error
                else None
            ),
        )
    )

    # ---- reflect ---------------------------------------------------------
    ref_id = _hex(16)
    ref_start = gen_end + 1_000_000
    ref_dur_ms = random.randint(40, 180)
    ref_end = ref_start + ref_dur_ms * 1_000_000
    verdict = "pass" if (not force_error and random.random() > 0.2) else "retry"
    ref_in = _estimate_tokens(answer + query)
    ref_out = _estimate_tokens(verdict)
    spans.append(
        _span(
            trace_id=trace_id,
            span_id=ref_id,
            parent=root_id,
            name=f"{service}.reflect",
            start_ns=ref_start,
            end_ns=ref_end,
            status="OK",
            attrs={
                "o.kind": "reflect",
                "o.step": "self_critique",
                "o.sess": session_id,
                "o.user": user_id,
                "o.req": req_id,
                "o.verdict": verdict,
                "o.model": model,
                "o.vendor": vendor,
                "o.q": query,
                "o.r": f"verdict={verdict}",
                "o.tok.in": ref_in,
                "o.tok.out": ref_out,
                "o.tok.sum": ref_in + ref_out,
                "o.price": _price_of(model, ref_in, ref_out),
            },
        )
    )

    # ---- root span -------------------------------------------------------
    root_end = ref_end + 500_000
    tokens_in_total = sum(
        int(a["value"].get("intValue", 0))
        for sp in spans
        for a in sp["attributes"]
        if a["key"] == "o.tok.in"
    )
    tokens_out_total = sum(
        int(a["value"].get("intValue", 0))
        for sp in spans
        for a in sp["attributes"]
        if a["key"] == "o.tok.out"
    )
    price_total = round(
        sum(
            float(a["value"].get("doubleValue", 0.0))
            for sp in spans
            for a in sp["attributes"]
            if a["key"] == "o.price"
        ),
        6,
    )
    root_attrs = {
        "o.kind": "agent",
        "o.step": "turn",
        "o.sess": session_id,
        "o.user": user_id,
        "o.req": req_id,
        "o.q": query,
        "o.verdict": verdict,
        "o.tok.in": tokens_in_total,
        "o.tok.out": tokens_out_total,
        "o.tok.sum": tokens_in_total + tokens_out_total,
        "o.price": price_total,
        "o.model": model,
        "o.vendor": vendor,
    }
    root_span = _span(
        trace_id=trace_id,
        span_id=root_id,
        parent=None,
        name=turn_name,
        start_ns=started_ns,
        end_ns=root_end,
        status="ERROR" if force_error else "OK",
        attrs=root_attrs,
    )
    spans.insert(0, root_span)

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr("service.name", service)]},
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=20, help="number of distinct sessions")
    ap.add_argument("--turns", type=int, default=3, help="turns per session (avg)")
    ap.add_argument("--window-hours", type=int, default=24)
    ap.add_argument("--error-rate", type=float, default=0.12)
    ap.add_argument("--tool-rate", type=float, default=0.25)
    ap.add_argument(
        "--base-url",
        default=os.environ.get("EASYOBS_BASE_URL", "http://127.0.0.1:8787"),
    )
    ap.add_argument(
        "--token",
        default=os.environ.get("EASYOBS_INGEST_TOKEN", ""),
        help=(
            "Service ingest token (eobs_…). Mint one in the UI under "
            "Setup > Organizations > <org> > Services > <service>."
        ),
    )
    args = ap.parse_args()

    if not args.token:
        print(
            "[easyobs] missing ingest token. Pass --token <eobs_…> or set "
            "EASYOBS_INGEST_TOKEN. Mint one in the UI under "
            "Setup > Organizations > <org> > Services > <service>.",
            file=sys.stderr,
        )
        return 2

    now_ns = int(time.time() * 1e9)
    span_ns = args.window_hours * 3600 * 1_000_000_000

    sent = 0
    for _ in range(args.sessions):
        session_id = f"sess-{_hex(6)}"
        service = random.choice(SERVICES)
        user_id = random.choice(USERS)
        model, vendor = random.choice(MODELS)

        session_start = now_ns - random.randint(0, span_ns)
        turn_count = max(1, int(random.gauss(args.turns, 1)))

        cursor_ns = session_start
        for _t in range(turn_count):
            query = random.choice(QUERIES)
            body = _build_turn(
                started_ns=cursor_ns,
                service=service,
                session_id=session_id,
                user_id=user_id,
                query=query,
                model=model,
                vendor=vendor,
                force_error=random.random() < args.error_rate,
                force_tool=random.random() < args.tool_rate,
            )
            req = urllib.request.Request(
                f"{args.base_url}/otlp/v1/traces",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {args.token}",
                    "Content-Type": "application/json",
                },
            )
            try:
                urllib.request.urlopen(req, timeout=5).read()
                sent += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[easyobs] failed turn: {exc}", file=sys.stderr)
                continue

            # step to the next turn inside the same session
            cursor_ns += random.randint(30, 180) * 1_000_000_000

    print(
        f"[easyobs] seeded {sent} turns across {args.sessions} sessions "
        f"into {args.base_url}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
