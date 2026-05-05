from __future__ import annotations

from typing import Any

from opentelemetry import trace


class SpanTag:
    """EasyObs-native span attribute keys (``o.*`` namespace).

    The namespace is intentionally distinct from existing industry
    conventions (e.g. ``llm.*``, ``gen_ai.*``, ``openinference.*``,
    ``langfuse.*``) to avoid semantic collisions on the ingest side —
    the backend/UI treats ``o.*`` as the canonical EasyObs schema.
    """

    # -- generic ---------------------------------------------------------
    MESSAGE = "o.m"
    CORRELATION = "o.c"
    TARGET = "o.t"

    # -- identity / workflow --------------------------------------------
    KIND = "o.kind"          # llm | retrieve | tool | plan | reflect | router | agent
    STEP = "o.step"          # short label of a pipeline step
    SESSION = "o.sess"       # session id
    USER = "o.user"          # end-user id
    REQUEST = "o.req"        # server-side request id
    ATTEMPT = "o.attempt"    # retry index (integer)
    VERDICT = "o.verdict"    # pass | retry | fail
    SCORE = "o.score"        # quality score (0..1 or 0..100)

    # -- LLM I/O ---------------------------------------------------------
    QUERY = "o.q"            # user query / prompt-visible input
    RESPONSE = "o.r"         # model response / visible output
    MODEL = "o.model"        # model identifier, e.g. "gpt-4o-mini"
    VENDOR = "o.vendor"      # vendor/provider label, e.g. "openai", "local"
    TOKENS_IN = "o.tok.in"
    TOKENS_OUT = "o.tok.out"
    TOKENS_TOTAL = "o.tok.sum"
    PRICE = "o.price"        # total cost (USD, float)

    # -- retrieval -------------------------------------------------------
    DOCS = "o.docs"          # compact JSON array of {id, score, snippet}
    DOCS_COUNT = "o.docs.n"
    DOCS_TOP_SCORE = "o.docs.top"

    # -- tool call -------------------------------------------------------
    TOOL = "o.tool"
    TOOL_INPUT = "o.tool.in"
    TOOL_OUTPUT = "o.tool.out"


def _set(span: "trace.Span", key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, (str, int, float, bool)):
        span.set_attribute(key, value)
    else:
        span.set_attribute(key, str(value))


def span_tag(key: str, value: str | int | float | bool) -> None:
    """Attach a single attribute to the **currently active** span.

    No-op if there is no active span (e.g. before :func:`init`).
    """
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(key, value)


def record_llm(
    *,
    model: str | None = None,
    vendor: str | None = None,
    query: str | None = None,
    response: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    price: float | None = None,
    step: str | None = None,
) -> None:
    """Record one LLM call's observable fields on the **current span**.

    Designed for minimum boilerplate: missing fields are skipped and the
    helper sets ``o.kind = 'llm'`` automatically unless the caller already
    set it via :func:`span_tag`.
    """
    span = trace.get_current_span()
    if not span.is_recording():
        return
    _set(span, SpanTag.KIND, "llm")
    _set(span, SpanTag.STEP, step)
    _set(span, SpanTag.MODEL, model)
    _set(span, SpanTag.VENDOR, vendor)
    _set(span, SpanTag.QUERY, query)
    _set(span, SpanTag.RESPONSE, response)
    if tokens_in is not None:
        _set(span, SpanTag.TOKENS_IN, int(tokens_in))
    if tokens_out is not None:
        _set(span, SpanTag.TOKENS_OUT, int(tokens_out))
    if tokens_in is not None and tokens_out is not None:
        _set(span, SpanTag.TOKENS_TOTAL, int(tokens_in) + int(tokens_out))
    # ``price`` stays optional — if the caller already knows the real
    # invoice amount (e.g. from a provider webhook) they can pass it and
    # it wins over whatever the collector would compute. Otherwise the
    # EasyObs server fills ``o.price`` automatically during ingest.
    if price is not None:
        _set(span, SpanTag.PRICE, float(price))


def record_retrieval(
    *,
    query: str | None = None,
    docs: list[dict[str, Any]] | None = None,
    top_score: float | None = None,
    step: str | None = None,
) -> None:
    """Record a retrieval step on the current span.

    ``docs`` is serialised as a compact JSON string (kept small for span size
    limits). Pass ``[{"id": "...", "score": 0.87, "snippet": "..."}]``.
    """
    import json

    span = trace.get_current_span()
    if not span.is_recording():
        return
    _set(span, SpanTag.KIND, "retrieve")
    _set(span, SpanTag.STEP, step)
    _set(span, SpanTag.QUERY, query)
    if docs is not None:
        compact = [
            {
                "id": d.get("id"),
                "score": d.get("score"),
                "snippet": (d.get("snippet") or d.get("content") or "")[:180],
            }
            for d in docs[:10]
        ]
        _set(span, SpanTag.DOCS, json.dumps(compact, ensure_ascii=False))
        _set(span, SpanTag.DOCS_COUNT, len(docs))
        if top_score is None and docs:
            tops = [d.get("score") for d in docs if isinstance(d.get("score"), (int, float))]
            if tops:
                top_score = max(tops)
    if top_score is not None:
        _set(span, SpanTag.DOCS_TOP_SCORE, float(top_score))


def record_tool(
    *,
    name: str | None = None,
    inp: Any = None,
    out: Any = None,
    step: str | None = None,
) -> None:
    """Record a tool invocation on the current span."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    _set(span, SpanTag.KIND, "tool")
    _set(span, SpanTag.STEP, step)
    _set(span, SpanTag.TOOL, name)
    if inp is not None:
        _set(span, SpanTag.TOOL_INPUT, inp if isinstance(inp, str) else str(inp))
    if out is not None:
        _set(span, SpanTag.TOOL_OUTPUT, out if isinstance(out, str) else str(out))


def record_session(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Attach session/user/request identifiers to the current span."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    _set(span, SpanTag.SESSION, session_id)
    _set(span, SpanTag.USER, user_id)
    _set(span, SpanTag.REQUEST, request_id)
