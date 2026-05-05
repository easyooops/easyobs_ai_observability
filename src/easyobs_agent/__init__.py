"""EasyObs agent/service instrumentation.

Thin wrapper around the stock OpenTelemetry SDK that:

- sends spans to the EasyObs ingest over OTLP/HTTP
- exposes concise helpers for **LLM / retrieval / tool** observability
  using the distinct ``o.*`` attribute namespace (see :class:`SpanTag`)
- ships a LangChain ``BaseCallbackHandler`` for zero-boilerplate capture

Public API (everything you need as an agent developer)::

    from easyobs_agent import init, traced, span_tag, SpanTag, span_block
    from easyobs_agent import record_llm, record_retrieval, record_tool, record_session
    from easyobs_agent.callbacks import EasyObsCallbackHandler  # optional

Derived fields such as USD cost are computed on the **collector** side,
so agents never need to know pricing tables or vendor rates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from easyobs_agent.boot import init
from easyobs_agent.span_scope import span_block
from easyobs_agent.tags import (
    SpanTag,
    record_llm,
    record_retrieval,
    record_session,
    record_tool,
    span_tag,
)
from easyobs_agent.traced import traced

__all__ = [
    "init",
    "traced",
    "span_tag",
    "SpanTag",
    "span_block",
    "record_llm",
    "record_retrieval",
    "record_tool",
    "record_session",
    "EasyObsCallbackHandler",
]


def __getattr__(name: str) -> Any:
    # Import the LangChain handler lazily so the core SDK stays usable
    # when langchain-core isn't installed.
    if name == "EasyObsCallbackHandler":
        from easyobs_agent.callbacks.langchain import EasyObsCallbackHandler

        return EasyObsCallbackHandler
    raise AttributeError(f"module 'easyobs_agent' has no attribute {name!r}")


if TYPE_CHECKING:
    from easyobs_agent.callbacks.langchain import (  # noqa: F401
        EasyObsCallbackHandler,
    )
