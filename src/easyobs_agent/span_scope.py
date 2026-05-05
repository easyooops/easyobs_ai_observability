from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace

from easyobs_agent.boot import is_configured
from easyobs_agent.tags import SpanTag


def _tracer():
    return trace.get_tracer("easyobs_agent", "0.1.0")


@contextmanager
def span_block(
    name: str,
    *,
    kind: str | None = None,
    **attrs: Any,
) -> Iterator[trace.Span]:
    """Open a child span covering a block of code.

    ``with span_block("retrieve", kind="retrieve", step="vector.lookup"): ...``

    The active span is set while the block executes, so any
    :func:`span_tag`, :func:`record_llm`, :func:`record_retrieval`
    call inside records onto **this** span. Yields the span object for
    advanced callers that want to attach extra attributes by hand.
    """
    if not is_configured():
        raise RuntimeError("Call easyobs_agent.init(...) before span_block(...).")
    with _tracer().start_as_current_span(name) as span:
        if kind is not None and span.is_recording():
            span.set_attribute(SpanTag.KIND, kind)
        for k, v in attrs.items():
            if v is None or not span.is_recording():
                continue
            if isinstance(v, (str, int, float, bool)):
                span.set_attribute(k, v)
            else:
                span.set_attribute(k, str(v))
        yield span
