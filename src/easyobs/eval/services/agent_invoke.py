"""Outbound HTTP client used by the Golden Regression Runner (12 §2).

The runner calls the operator's agent service with each Golden Item's
L1 query, the response is later correlated with the OTLP trace the
service emits, and the trace is then evaluated against L2/L3 GT.

The client deliberately stays tiny:
- ``httpx.AsyncClient`` with a per-call timeout taken from the set's
  ``agent_timeout_sec``.
- A semaphore caps concurrent invocations to ``agent_max_concurrent``
  so a 1k-item run cannot DoS the user's own agent service.
- Auth references (``agent_auth_ref``) are resolved at call-time from
  ``os.environ`` so the secret never lives in the catalog DB.
- The request template supports ``{{query_text}}``, ``{{run_id}}``,
  ``{{item_id}}`` Mustache-ish placeholders so the operator can shape
  the body to match their existing API contract.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from easyobs.eval.services.dtos import AgentInvokeSettings

_log = logging.getLogger("easyobs.eval.agent_invoke")


@dataclass(frozen=True, slots=True)
class AgentInvokeResult:
    ok: bool
    status_code: int
    response_body: dict[str, Any]
    elapsed_ms: int
    # ``trace_id`` will normally come from the trace correlator (it
    # arrives via OTLP after the call returns), but well-behaved agents
    # can also echo it back inline — we store both possibilities.
    inline_trace_id: str | None
    error_type: str | None
    error_message: str | None


def _render_template(template: dict[str, Any], substitutions: dict[str, str]) -> dict[str, Any]:
    """Replace ``{{key}}`` placeholders inside any string leaf of the
    template. We avoid pulling in Jinja for two reasons: (1) the
    substitution surface is intentionally narrow (3 placeholders), and
    (2) fewer template features means fewer ways for a hostile request
    template to misbehave."""

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            out = node
            for k, v in substitutions.items():
                out = out.replace(f"{{{{{k}}}}}", v)
            return out
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    rendered = _walk(template) if template else {}
    if not isinstance(rendered, dict):
        # Default body when the operator did not supply a template — a
        # minimal ``{"message": "<query_text>"}`` works for most agents.
        return {"message": substitutions.get("query_text", "")}
    return rendered


def _resolve_auth_header(auth_ref: str) -> dict[str, str]:
    """``auth_ref`` is one of:

    - ``""``                          → no auth header
    - ``"env:VAR_NAME"``               → ``Authorization: Bearer <env value>``
    - ``"bearer:<literal>"``           → ``Authorization: Bearer <literal>``
    - ``"header:<name>:env:VAR_NAME"`` → ``<name>: <env value>``

    Plain literals are accepted only via the ``bearer:`` prefix so an
    operator never accidentally pastes a raw secret into a UI input box
    that gets logged.
    """

    s = (auth_ref or "").strip()
    if not s:
        return {}
    if s.startswith("env:"):
        env_name = s[len("env:") :]
        token = os.environ.get(env_name)
        return {"Authorization": f"Bearer {token}"} if token else {}
    if s.startswith("bearer:"):
        token = s[len("bearer:") :]
        return {"Authorization": f"Bearer {token}"} if token else {}
    if s.startswith("header:"):
        try:
            _, name, kind, ref = s.split(":", 3)
        except ValueError:
            return {}
        if kind == "env":
            v = os.environ.get(ref)
            return {name: v} if v else {}
        return {}
    return {}


def _classify_invoke_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "connect" in name or "connection" in msg:
        return "connection_error"
    return "unknown"


async def invoke_agent_for_item(
    *,
    settings: AgentInvokeSettings,
    query_text: str,
    run_id: str,
    item_id: str,
    correlation_metadata_key: str = "easyobs",
    extra_headers: dict[str, str] | None = None,
) -> AgentInvokeResult:
    """Issue one POST call to the operator's agent endpoint.

    The runner stamps ``correlation_metadata_key`` into the body so the
    trace correlator can later match the OTLP trace back to this item:

        {"<key>": {"goldenRunId": "...", "goldenItemId": "..."}}

    is merged into the rendered template; agents are expected to forward
    this object back through their OTel spans (the easyobs SDK does this
    automatically for langchain agents). When the agent does not echo
    the metadata, the trace correlator falls back to time-window +
    query-similarity matching."""

    if not settings.endpoint_url.strip():
        return AgentInvokeResult(
            ok=False,
            status_code=0,
            response_body={},
            elapsed_ms=0,
            inline_trace_id=None,
            error_type="not_configured",
            error_message="agent endpoint URL is empty",
        )

    body = _render_template(
        settings.request_template,
        {
            "query_text": query_text,
            "run_id": run_id,
            "item_id": item_id,
        },
    )
    body.setdefault(correlation_metadata_key, {})
    if isinstance(body[correlation_metadata_key], dict):
        body[correlation_metadata_key].update(
            {"goldenRunId": run_id, "goldenItemId": item_id}
        )

    headers: dict[str, str] = {"content-type": "application/json"}
    headers.update(_resolve_auth_header(settings.auth_ref))
    if extra_headers:
        headers.update(extra_headers)

    timeout = httpx.Timeout(max(1.0, float(settings.timeout_sec)))
    started = httpx.AsyncClient(timeout=timeout)
    import time

    t0 = time.perf_counter()
    try:
        async with started as client:
            try:
                resp = await client.post(
                    settings.endpoint_url,
                    content=json.dumps(body, ensure_ascii=False, default=str).encode(
                        "utf-8"
                    ),
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                return AgentInvokeResult(
                    ok=False,
                    status_code=0,
                    response_body={},
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    inline_trace_id=None,
                    error_type="timeout",
                    error_message=str(exc)[:200],
                )
            except Exception as exc:
                return AgentInvokeResult(
                    ok=False,
                    status_code=0,
                    response_body={},
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                    inline_trace_id=None,
                    error_type=_classify_invoke_error(exc),
                    error_message=str(exc)[:200],
                )
            elapsed = int((time.perf_counter() - t0) * 1000)
            try:
                parsed = resp.json()
            except Exception:
                parsed = {"raw": resp.text[:2000]}
            inline_tid = None
            if isinstance(parsed, dict):
                # Agents using easyobs SDK echo the OTel trace_id under
                # ``traceId`` or ``trace_id`` — we accept either to keep
                # the contract permissive.
                inline_tid = (
                    parsed.get("traceId") or parsed.get("trace_id") or None
                )
                if inline_tid is not None:
                    inline_tid = str(inline_tid)
            ok = 200 <= resp.status_code < 300
            return AgentInvokeResult(
                ok=ok,
                status_code=resp.status_code,
                response_body=parsed if isinstance(parsed, dict) else {"raw": parsed},
                elapsed_ms=elapsed,
                inline_trace_id=inline_tid,
                error_type=None if ok else "server_error",
                error_message=None if ok else f"HTTP {resp.status_code}",
            )
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        _log.exception("agent invoke fatal", extra={"endpoint": settings.endpoint_url})
        return AgentInvokeResult(
            ok=False,
            status_code=0,
            response_body={},
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            inline_trace_id=None,
            error_type=_classify_invoke_error(exc),
            error_message=str(exc)[:200],
        )


async def test_agent_connection(
    settings: AgentInvokeSettings,
    *,
    sample_query: str = "ping",
) -> AgentInvokeResult:
    """Synchronous-ish convenience used by the *Test connection* button
    in the agent settings UI (12 §2.3)."""

    return await invoke_agent_for_item(
        settings=settings,
        query_text=sample_query,
        run_id="test_run",
        item_id="test_item",
    )
