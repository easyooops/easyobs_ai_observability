"""LangChain callback handler that emits EasyObs ``o.*`` spans automatically.

Attach to any LangChain runnable so model calls, retriever hits, and tool
invocations show up in EasyObs without manual ``record_llm`` / ``record_*``
boilerplate::

    from easyobs_agent import init
    from easyobs_agent.callbacks import EasyObsCallbackHandler

    init("http://127.0.0.1:8787", token="eobs_…", service="rag-bot")
    handler = EasyObsCallbackHandler(session_id="sess-abc", user_id="alice")

    llm = ChatOpenAI(model="gpt-4o-mini", callbacks=[handler])
    llm.invoke("Hello")

The handler only captures **LangChain** events.  For chains of pure
Python code keep using ``@traced`` / ``span_block`` / ``record_llm`` — the
two approaches compose (manual helpers attach to whatever span is current
when callbacks fire).
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

from opentelemetry import context, trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from easyobs_agent.tags import SpanTag, _set

try:
    from langchain_core.callbacks import BaseCallbackHandler

    _HAS_LC = True
except ImportError:  # pragma: no cover - optional dep
    _HAS_LC = False

    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Stub used only when langchain-core is not installed."""


__all__ = ["EasyObsCallbackHandler"]

_VENDOR_HINTS = (
    "openai",
    "azure",
    "anthropic",
    "google",
    "gemini",
    "vertex",
    "mistral",
    "ollama",
    "cohere",
    "bedrock",
    "fireworks",
    "groq",
    "together",
)


def _safe(value: Any, limit: int) -> str:
    try:
        if value is None:
            return ""
        if isinstance(value, str):
            return value[:limit]
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _infer_model(
    serialized: dict[str, Any] | None,
    invocation_params: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Return ``(model, vendor)`` from LangChain's serialized + params blobs."""
    model: str | None = None
    vendor: str | None = None

    params = invocation_params or {}
    for key in ("model", "model_name", "deployment_name", "model_id"):
        if params.get(key):
            model = str(params[key])
            break

    kwargs = (serialized or {}).get("kwargs") or {}
    if not model:
        for key in ("model", "model_name", "deployment_name"):
            if kwargs.get(key):
                model = str(kwargs[key])
                break

    ident = (serialized or {}).get("id")
    if isinstance(ident, list):
        lowered = [str(x).lower() for x in ident]
        for hint in _VENDOR_HINTS:
            if any(hint in part for part in lowered):
                vendor = hint
                break
    return model, vendor


class EasyObsCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
    """LangChain callback → EasyObs ``o.*`` span emitter.

    The handler is intentionally side-effect only: it never mutates the
    LangChain response and it handles missing optional metadata silently.
    When ``langchain-core`` isn't installed instantiating the class raises
    :class:`ImportError`.
    """

    def __init__(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        request_id: str | None = None,
        extra_tags: dict[str, Any] | None = None,
        capture_io: bool = True,
        io_limit: int = 2000,
    ) -> None:
        if not _HAS_LC:
            raise ImportError(
                "EasyObsCallbackHandler requires langchain-core. "
                "Install the extra: `pip install 'easyobs[langchain]'`"
            )
        super().__init__()
        self._session = session_id
        self._user = user_id
        self._req = request_id
        self._extra = dict(extra_tags or {})
        self._capture = capture_io
        self._limit = int(io_limit)
        self._tracer = trace.get_tracer("easyobs_agent.langchain", "0.1.0")
        self._runs: dict[UUID, dict[str, Any]] = {}

    # ------------------------------------------------------------------ state
    def _parent_ctx(self, parent_run_id: UUID | None):
        if parent_run_id is not None and parent_run_id in self._runs:
            span = self._runs[parent_run_id]["span"]
            return trace.set_span_in_context(span)
        return context.get_current()

    def _open(
        self,
        *,
        run_id: UUID,
        parent_run_id: UUID | None,
        name: str,
        kind: str,
        step: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> Span:
        base: dict[str, Any] = {SpanTag.KIND: kind}
        if step:
            base[SpanTag.STEP] = step
        if self._session:
            base[SpanTag.SESSION] = self._session
        if self._user:
            base[SpanTag.USER] = self._user
        if self._req:
            base[SpanTag.REQUEST] = self._req
        base.update(self._extra)
        if attrs:
            for key, value in attrs.items():
                if value is not None:
                    base[key] = value
        span = self._tracer.start_span(
            name,
            context=self._parent_ctx(parent_run_id),
            kind=SpanKind.INTERNAL,
            attributes={
                k: v if isinstance(v, (str, int, float, bool)) else str(v)
                for k, v in base.items()
                if v is not None
            },
        )
        self._runs[run_id] = {"span": span, "started_at": time.time()}
        return span

    def _close(self, run_id: UUID, *, error: BaseException | None = None) -> None:
        entry = self._runs.pop(run_id, None)
        if entry is None:
            return
        span: Span = entry["span"]
        if error is not None:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, str(error)[:200]))
        span.end()

    def _get(self, run_id: UUID) -> Span | None:
        entry = self._runs.get(run_id)
        return entry["span"] if entry else None

    # =============================================================== LLM / Chat
    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        model, vendor = _infer_model(serialized, invocation_params)
        attrs: dict[str, Any] = {SpanTag.MODEL: model, SpanTag.VENDOR: vendor}
        if self._capture and prompts:
            attrs[SpanTag.QUERY] = _safe(prompts[-1], self._limit)
        self._open(
            run_id=run_id,
            parent_run_id=parent_run_id,
            name=f"llm.{model or vendor or 'call'}",
            kind="llm",
            step="generate",
            attrs=attrs,
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        model, vendor = _infer_model(serialized, invocation_params)
        attrs: dict[str, Any] = {SpanTag.MODEL: model, SpanTag.VENDOR: vendor}
        if self._capture and messages:
            last_turn = messages[-1] if messages else []
            pieces: list[str] = []
            for msg in last_turn[-4:]:
                role = getattr(msg, "type", None) or getattr(msg, "role", None) or "msg"
                content = getattr(msg, "content", msg)
                pieces.append(f"[{role}] {content}")
            attrs[SpanTag.QUERY] = _safe("\n".join(pieces), self._limit)
        self._open(
            run_id=run_id,
            parent_run_id=parent_run_id,
            name=f"llm.{model or vendor or 'chat'}",
            kind="llm",
            step="chat",
            attrs=attrs,
        )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ARG002
        span = self._get(run_id)
        if span is None:
            return

        generations = getattr(response, "generations", None) or []
        text: str | None = None
        gen: Any = None
        if generations and generations[0]:
            gen = generations[0][0]
            text = getattr(gen, "text", None) or None
            if not text:
                message = getattr(gen, "message", None)
                if message is not None:
                    text = getattr(message, "content", None) or None
        if self._capture and text:
            _set(span, SpanTag.RESPONSE, _safe(text, self._limit))

        llm_output = getattr(response, "llm_output", None) or {}
        model = llm_output.get("model_name") or llm_output.get("model")
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}

        # Some integrations (e.g. Anthropic, newer LangChain) tuck usage
        # inside generation.message.response_metadata instead of llm_output.
        if (not usage or not model) and gen is not None:
            message = getattr(gen, "message", None)
            if message is not None:
                meta = getattr(message, "response_metadata", None) or {}
                usage = usage or meta.get("token_usage") or meta.get("usage") or {}
                model = model or meta.get("model_name") or meta.get("model")
                usage_meta = getattr(message, "usage_metadata", None) or {}
                if not usage and usage_meta:
                    usage = {
                        "prompt_tokens": usage_meta.get("input_tokens"),
                        "completion_tokens": usage_meta.get("output_tokens"),
                    }

        if model:
            _set(span, SpanTag.MODEL, str(model))

        t_in = usage.get("prompt_tokens") or usage.get("input_tokens")
        t_out = usage.get("completion_tokens") or usage.get("output_tokens")
        if t_in is not None:
            _set(span, SpanTag.TOKENS_IN, int(t_in))
        if t_out is not None:
            _set(span, SpanTag.TOKENS_OUT, int(t_out))
        if t_in is not None and t_out is not None:
            _set(span, SpanTag.TOKENS_TOTAL, int(t_in) + int(t_out))
        # ``o.price`` is intentionally not computed here — the EasyObs
        # collector fills it at ingest time so pricing tables stay server-side.

        self._close(run_id)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self._close(run_id, error=error)

    # =============================================================== Retriever
    def on_retriever_start(
        self,
        serialized: dict[str, Any],  # noqa: ARG002
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        attrs: dict[str, Any] = {}
        if self._capture:
            attrs[SpanTag.QUERY] = _safe(query, self._limit)
        self._open(
            run_id=run_id,
            parent_run_id=parent_run_id,
            name="retrieve",
            kind="retrieve",
            step="vector.lookup",
            attrs=attrs,
        )

    def on_retriever_end(
        self,
        documents: list[Any],
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        span = self._get(run_id)
        if span is None:
            return
        if documents:
            compact: list[dict[str, Any]] = []
            for idx, doc in enumerate(documents[:10]):
                meta = getattr(doc, "metadata", {}) or {}
                compact.append(
                    {
                        "id": meta.get("id")
                        or meta.get("source")
                        or meta.get("doc_id")
                        or f"doc-{idx + 1}",
                        "score": meta.get("score") or meta.get("relevance_score"),
                        "snippet": _safe(getattr(doc, "page_content", doc), 180),
                    }
                )
            _set(span, SpanTag.DOCS, json.dumps(compact, ensure_ascii=False))
            _set(span, SpanTag.DOCS_COUNT, len(documents))
            scores = [
                c["score"]
                for c in compact
                if isinstance(c.get("score"), (int, float))
            ]
            if scores:
                _set(span, SpanTag.DOCS_TOP_SCORE, float(max(scores)))
        self._close(run_id)

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self._close(run_id, error=error)

    # ==================================================================== Tool
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        tool_name = (serialized or {}).get("name") or "tool"
        attrs: dict[str, Any] = {SpanTag.TOOL: tool_name}
        if self._capture:
            attrs[SpanTag.TOOL_INPUT] = _safe(input_str, self._limit)
        self._open(
            run_id=run_id,
            parent_run_id=parent_run_id,
            name=f"tool.{tool_name}",
            kind="tool",
            step="tool.call",
            attrs=attrs,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        span = self._get(run_id)
        if span is None:
            return
        if self._capture and output is not None:
            _set(span, SpanTag.TOOL_OUTPUT, _safe(output, self._limit))
        self._close(run_id)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        self._close(run_id, error=error)
