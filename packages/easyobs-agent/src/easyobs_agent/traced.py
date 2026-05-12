from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import Callable
from typing import Any, TypeVar, overload

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from easyobs_agent.boot import config, is_configured

F = TypeVar("F", bound=Callable[..., Any])


def _tracer():
    return trace.get_tracer("easyobs_agent", "0.1.0")


def _safe_json(value: Any, limit: int) -> str:
    """Truncate-safe JSON encode; never raises on the hot path."""
    if value is None:
        return ""
    try:
        if isinstance(value, str):
            return value[:limit]
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _bind_args(fn: Callable[..., Any], args: tuple, kwargs: dict) -> dict[str, Any]:
    """Best-effort (name -> value) map; skips ``self`` / ``cls``."""
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        out = dict(bound.arguments)
    except (TypeError, ValueError):
        out = {f"arg{i}": a for i, a in enumerate(args)}
        out.update(kwargs)
    out.pop("self", None)
    out.pop("cls", None)
    return out


def _record_inputs(span, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
    cfg = config()
    if not cfg.capture_io:
        return
    payload = _bind_args(fn, args, kwargs)
    if not payload:
        return
    # If the only arg is a plain string, treat it as the user query —
    # matches the common agent shape ``answer(user_query)``.
    if len(payload) == 1:
        (only,) = payload.values()
        if isinstance(only, str):
            span.set_attribute("o.q", only[: cfg.capture_io_limit])
            return
    span.set_attribute("o.q", _safe_json(payload, cfg.capture_io_limit))


def _record_output(span, value: Any) -> None:
    cfg = config()
    if not cfg.capture_io:
        return
    span.set_attribute("o.r", _safe_json(value, cfg.capture_io_limit))


def _wrap(fn: F, span_name: str, *, capture: bool | None) -> F:
    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            if not is_configured():
                raise RuntimeError("Call easyobs_agent.init(...) before using @traced.")
            with _tracer().start_as_current_span(span_name) as span:
                cap = capture if capture is not None else config().capture_io
                if cap:
                    _record_inputs(span, fn, args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
                if cap:
                    _record_output(span, result)
                return result

        return async_wrapped  # type: ignore[return-value]

    @functools.wraps(fn)
    def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_configured():
            raise RuntimeError("Call easyobs_agent.init(...) before using @traced.")
        with _tracer().start_as_current_span(span_name) as span:
            cap = capture if capture is not None else config().capture_io
            if cap:
                _record_inputs(span, fn, args, kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            if cap:
                _record_output(span, result)
            return result

    return sync_wrapped  # type: ignore[return-value]


@overload
def traced(fn: F) -> F: ...


@overload
def traced(
    name: str | None = None,
    *,
    capture: bool | None = None,
) -> Callable[[F], F]: ...


def traced(
    name_or_fn: str | F | None = None,
    *,
    capture: bool | None = None,
) -> Any:
    """
    Decorator: one OpenTelemetry span per function call.

    - ``@traced`` — span name defaults to the function name
    - ``@traced("custom_name")`` — fixed span name
    - ``@traced("custom_name", capture=True)`` — force-capture args/return
      regardless of the global ``init(capture_io=...)`` setting
    - ``@traced("custom_name", capture=False)`` — opt-out even when global
      capture is on (useful for functions with secrets / huge payloads)
    """
    if callable(name_or_fn):
        return _wrap(name_or_fn, name_or_fn.__name__, capture=capture)
    name = name_or_fn if isinstance(name_or_fn, str) else None

    def deco(fn: F) -> F:
        return _wrap(fn, name or fn.__name__, capture=capture)

    return deco
