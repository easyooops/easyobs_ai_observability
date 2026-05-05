"""EasyObs agent bootstrap + runtime configuration flags.

``init(...)`` is the single entry point an agent service needs to call.
Two opt-in switches turn ``@traced`` into "truly automatic" observation:

- ``capture_io=True``    — serialise decorator args/return into ``o.q`` / ``o.r``
- ``auto_langchain=True`` — register a process-wide LangChain callback
  handler so every ``llm.invoke / chain.invoke / retriever.invoke`` is
  captured without explicit ``callbacks=[handler]`` wiring

``auto=True`` is shorthand for both.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log = logging.getLogger("easyobs_agent")


@dataclass
class RuntimeConfig:
    """Runtime behaviour flags read by the rest of the SDK."""

    capture_io: bool = False
    capture_io_limit: int = 2048
    auto_langchain: bool = False


_CONFIG = RuntimeConfig()
_CONFIGURED = False
_LC_HANDLER: Any | None = None


def init(
    base_url: str,
    *,
    token: str,
    service: str = "agent",
    auto: bool = False,
    capture_io: bool | None = None,
    capture_io_limit: int = 2048,
    auto_langchain: bool | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """
    Connect to EasyObs OTLP HTTP ingest (standard OpenTelemetry exporter).

    :param base_url: e.g. ``http://127.0.0.1:8787`` (no trailing slash)
    :param token: service-scoped EasyObs ingest token (``eobs_…``); minted in
        the console under *Setup > Organizations > <org> > Services* and sent
        as the HTTP ``Authorization: Bearer …`` header.
    :param service: ``service.name`` resource attribute
    :param auto: shorthand — when True, enables ``capture_io`` and
        ``auto_langchain`` together.  Individual flags still win if passed.
    :param capture_io: when True, ``@traced`` records each function's
        arguments as ``o.q`` and the return value as ``o.r`` (truncated).
    :param capture_io_limit: max character length for captured values.
    :param auto_langchain: when True and ``langchain-core`` is installed,
        registers a process-global callback so every LangChain invoke
        emits EasyObs spans without explicit ``callbacks=[...]`` wiring.
    :param session_id / user_id: optional defaults stamped on auto-LC spans.
    """
    global _CONFIGURED
    root = base_url.rstrip("/")
    endpoint = f"{root}/otlp/v1/traces"
    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={"Authorization": f"Bearer {token}"},
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _CONFIG.capture_io = capture_io if capture_io is not None else auto
    _CONFIG.capture_io_limit = int(capture_io_limit)
    _CONFIG.auto_langchain = (
        auto_langchain if auto_langchain is not None else auto
    )
    _CONFIGURED = True

    if _CONFIG.auto_langchain:
        _install_langchain_global(session_id=session_id, user_id=user_id)


def _install_langchain_global(*, session_id: str | None, user_id: str | None) -> None:
    """Register our callback handler as a LangChain-wide tracing hook.

    Uses ``langchain_core.tracers.context.register_configure_hook`` which
    adds the callback to every ``CallbackManager.configure(...)`` call —
    the same mechanism LangSmith uses for auto-tracing.
    """
    global _LC_HANDLER
    try:
        from contextvars import ContextVar

        from langchain_core.tracers.context import register_configure_hook  # type: ignore
    except ImportError:
        log.warning(
            "easyobs_agent: auto_langchain=True but langchain-core is not "
            "installed; skipping global registration."
        )
        return

    from easyobs_agent.callbacks.langchain import EasyObsCallbackHandler

    handler = EasyObsCallbackHandler(session_id=session_id, user_id=user_id)
    _LC_HANDLER = handler

    # The ContextVar itself only needs to exist; register_configure_hook
    # will observe whatever value is set and inject it as a callback.
    var: ContextVar = ContextVar("easyobs_langchain_handler", default=handler)
    try:
        register_configure_hook(var, inheritable=True)
    except TypeError:
        # Older langchain-core signatures don't accept kwargs.
        register_configure_hook(var, True)  # type: ignore[arg-type]


def is_configured() -> bool:
    return _CONFIGURED


def config() -> RuntimeConfig:
    """Snapshot of the active runtime flags."""
    return _CONFIG
