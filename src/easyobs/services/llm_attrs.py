"""Helpers that read EasyObs ``o.*`` attributes off flattened OTLP spans.

Centralised so that the Trace detail path, the analytics layer, and any
future exporter share the same parsing rules. Only the short EasyObs
namespace (``o.*``) is understood; no other conventions are inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


def _value(raw: dict[str, Any] | None) -> Any:
    if not raw:
        return None
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in raw:
            return raw[k]
    return None


def read_attrs(span: dict[str, Any]) -> dict[str, Any]:
    """Flatten a span's OTLP ``attributes`` list into ``{key: value}``."""
    out: dict[str, Any] = {}
    for attr in span.get("attributes") or []:
        k = attr.get("key")
        if not k:
            continue
        out[k] = _value(attr.get("value"))
    return out


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


@dataclass
class SpanLLM:
    """LLM-relevant fields read off one span (all optional)."""

    kind: str | None = None
    step: str | None = None
    query: str | None = None
    response: str | None = None
    model: str | None = None
    vendor: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    price: float = 0.0
    docs_count: int = 0
    docs_top_score: float | None = None
    docs_raw: str | None = None
    tool: str | None = None
    session: str | None = None
    user: str | None = None
    request: str | None = None
    verdict: str | None = None
    score: float | None = None
    attempt: int | None = None

    @classmethod
    def from_span(cls, span: dict[str, Any]) -> "SpanLLM":
        a = read_attrs(span)
        info = cls(
            kind=a.get("o.kind") if isinstance(a.get("o.kind"), str) else None,
            step=a.get("o.step") if isinstance(a.get("o.step"), str) else None,
            query=a.get("o.q") if isinstance(a.get("o.q"), str) else None,
            response=a.get("o.r") if isinstance(a.get("o.r"), str) else None,
            model=a.get("o.model") if isinstance(a.get("o.model"), str) else None,
            vendor=a.get("o.vendor") if isinstance(a.get("o.vendor"), str) else None,
            tokens_in=_int(a.get("o.tok.in")),
            tokens_out=_int(a.get("o.tok.out")),
            tokens_total=_int(a.get("o.tok.sum")),
            price=_num(a.get("o.price")),
            docs_count=_int(a.get("o.docs.n")),
            docs_top_score=_num(a.get("o.docs.top")) or None,
            docs_raw=a.get("o.docs") if isinstance(a.get("o.docs"), str) else None,
            tool=a.get("o.tool") if isinstance(a.get("o.tool"), str) else None,
            session=a.get("o.sess") if isinstance(a.get("o.sess"), str) else None,
            user=a.get("o.user") if isinstance(a.get("o.user"), str) else None,
            request=a.get("o.req") if isinstance(a.get("o.req"), str) else None,
            verdict=a.get("o.verdict") if isinstance(a.get("o.verdict"), str) else None,
            score=_num(a.get("o.score")) or None,
            attempt=_int(a.get("o.attempt")) or None,
        )
        if info.tokens_total == 0 and (info.tokens_in or info.tokens_out):
            info.tokens_total = info.tokens_in + info.tokens_out
        return info

    def to_public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "step": self.step,
            "query": self.query,
            "response": self.response,
            "model": self.model,
            "vendor": self.vendor,
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "tokensTotal": self.tokens_total,
            "price": self.price,
            "docsCount": self.docs_count,
            "docsTopScore": self.docs_top_score,
            "docsRaw": self.docs_raw,
            "tool": self.tool,
            "session": self.session,
            "user": self.user,
            "request": self.request,
            "verdict": self.verdict,
            "score": self.score,
            "attempt": self.attempt,
        }


def summarise_trace(spans: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate LLM-level numbers across every span of one trace."""
    total_in = total_out = 0
    total_price = 0.0
    models: set[str] = set()
    vendors: set[str] = set()
    session: str | None = None
    user: str | None = None
    request: str | None = None
    top_query: str | None = None
    final_response: str | None = None
    docs_count = 0
    llm_calls = 0
    retrieve_calls = 0
    tool_calls = 0
    verdicts: list[str] = []

    latest_response_ts = 0
    earliest_query_ts = 0

    for sp in spans:
        info = SpanLLM.from_span(sp)
        total_in += info.tokens_in
        total_out += info.tokens_out
        total_price += info.price
        if info.model:
            models.add(info.model)
        if info.vendor:
            vendors.add(info.vendor)
        session = session or info.session
        user = user or info.user
        request = request or info.request
        docs_count = max(docs_count, info.docs_count)
        kind = (info.kind or "").lower()
        if kind == "llm":
            llm_calls += 1
        elif kind == "retrieve":
            retrieve_calls += 1
        elif kind == "tool":
            tool_calls += 1
        if info.verdict:
            verdicts.append(info.verdict)

        start_ns = sp.get("startTimeUnixNano") or 0
        end_ns = sp.get("endTimeUnixNano") or 0
        try:
            s_ns = int(start_ns)
            e_ns = int(end_ns)
        except (TypeError, ValueError):
            s_ns = e_ns = 0

        if info.query and (earliest_query_ts == 0 or s_ns < earliest_query_ts):
            top_query = info.query
            earliest_query_ts = s_ns
        # The final response is taken from LLM spans specifically — root
        # or agent-level placeholders should not overwrite the actual model
        # output that lives on the compose/generate span.
        if info.response and kind == "llm" and e_ns >= latest_response_ts:
            final_response = info.response
            latest_response_ts = e_ns

    return {
        "session": session,
        "user": user,
        "request": request,
        "query": top_query,
        "response": final_response,
        "tokensIn": total_in,
        "tokensOut": total_out,
        "tokensTotal": total_in + total_out,
        "price": round(total_price, 6),
        "models": sorted(models),
        "vendors": sorted(vendors),
        "docsCount": docs_count,
        "llmCalls": llm_calls,
        "retrieveCalls": retrieve_calls,
        "toolCalls": tool_calls,
        "verdicts": verdicts,
    }
