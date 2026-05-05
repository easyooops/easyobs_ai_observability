"""Minimal agent example.

Run the API first:

    cd docs/comparison/03.develop/easyobs
    .\\scripts\\run-dev.ps1          # (Windows)   or
    ./scripts/run-dev.sh             # (Linux/mac)

Then ``pip install -e ".[agent]"`` and execute this file. A single trace
named ``demo.agent.turn`` will appear in the EasyObs Tracing screen with
LLM / retrieval / tool child spans carrying input/output/tokens/cost.
"""

from __future__ import annotations

import os
import time
import uuid

from opentelemetry import trace

from easyobs_agent import (
    SpanTag,
    init,
    record_llm,
    record_retrieval,
    record_session,
    record_tool,
    span_block,
    span_tag,
    traced,
)


@traced("demo.agent.turn")
def turn(user_query: str, session_id: str) -> str:
    record_session(
        session_id=session_id,
        user_id="demo-user",
        request_id=uuid.uuid4().hex[:12],
    )
    span_tag(SpanTag.KIND, "agent")
    span_tag(SpanTag.QUERY, user_query)

    with span_block("rag.retrieve", kind="retrieve", step="vector.lookup"):
        docs = [
            {"id": "d-1", "score": 0.91, "snippet": "Docs on trace observability."},
            {"id": "d-2", "score": 0.78, "snippet": "Local OTLP ingest sample."},
        ]
        record_retrieval(query=user_query, docs=docs, step="vector.lookup")

    with span_block("rag.generate", kind="llm", step="compose"):
        answer = f"[demo] Answer for: {user_query}"
        record_llm(
            model="local-llm-demo",
            vendor="local",
            query=user_query,
            response=answer,
            tokens_in=42,
            tokens_out=56,
            price=0.0007,
            step="compose",
        )

    with span_block("tool.format", kind="tool", step="format"):
        record_tool(name="format.markdown", inp=answer, out=answer)

    span_tag(SpanTag.RESPONSE, answer)
    span_tag(SpanTag.VERDICT, "pass")
    return answer


if __name__ == "__main__":
    init(
        "http://127.0.0.1:8787",
        token=os.environ.get("EASYOBS_INGEST_TOKEN", "eobs_…"),
        service="demo-agent",
    )
    turn("what is easyobs?", session_id=f"demo-{uuid.uuid4().hex[:6]}")
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    time.sleep(0.3)
