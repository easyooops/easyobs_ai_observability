"""LangChain + EasyObs zero-boilerplate example.

Run::

    pip install 'easyobs[langchain]' langchain-openai
    export OPENAI_API_KEY=sk-...
    python examples/agent_langchain.py

You should see a new trace on http://localhost:3000/workspace/tracing/
whose LLM span already carries ``o.model``, ``o.tok.in/out``, ``o.price``
and ``o.r`` captured automatically by the callback handler — no manual
``record_llm`` call needed.

The outer ``@traced`` function gives you a parent span so the LangChain
child span and the surrounding Python code belong to a single trace.
"""

from __future__ import annotations

import os
import uuid

from easyobs_agent import init, record_session, span_block, span_tag, SpanTag, traced
from easyobs_agent.callbacks import EasyObsCallbackHandler


def _require(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        raise RuntimeError(f"Please set {var} before running this example.")
    return val


def _make_llm():
    # Kept local to avoid importing langchain when the SDK is just inspected.
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))


@traced("demo.langchain.turn")
def turn(user_query: str, *, handler: EasyObsCallbackHandler) -> str:
    record_session(
        session_id=handler._session or "sess-demo",  # noqa: SLF001 - demo only
        user_id="demo-user",
        request_id=uuid.uuid4().hex[:12],
    )
    span_tag(SpanTag.QUERY, user_query)

    with span_block("demo.pipeline", kind="agent", step="turn"):
        llm = _make_llm()
        # The handler captures model / tokens / cost / response automatically.
        result = llm.invoke(
            [
                {"role": "system", "content": "You are a concise research copilot."},
                {"role": "user", "content": user_query},
            ],
            config={"callbacks": [handler]},
        )

    answer = result.content if hasattr(result, "content") else str(result)
    span_tag(SpanTag.RESPONSE, answer[:2000])
    return answer


def main() -> None:
    base_url = os.environ.get("EASYOBS_BASE_URL", "http://127.0.0.1:8787")
    token = os.environ.get("EASYOBS_INGEST_TOKEN", "")
    if not token:
        raise SystemExit(
            "set EASYOBS_INGEST_TOKEN to a service token (eobs_…) — mint one in the "
            "UI under Setup > Organizations > <org> > Services."
        )
    _require("OPENAI_API_KEY")

    init(base_url, token=token, service="rag-copilot")

    handler = EasyObsCallbackHandler(
        session_id=f"sess-{uuid.uuid4().hex[:6]}",
        user_id="alice",
        extra_tags={SpanTag.VENDOR: "openai"},
    )

    query = "Give me one sentence about trace-based observability."
    answer = turn(query, handler=handler)
    print("user:", query)
    print("assistant:", answer)


if __name__ == "__main__":
    main()
