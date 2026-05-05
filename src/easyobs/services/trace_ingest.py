from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from easyobs.ingest.enrich import enrich_with_price
from easyobs.ingest.pipeline import flatten_otlp_payload
from easyobs.ports.blob import TraceBlobStore
from easyobs.ports.catalog import TraceCatalog

_log = logging.getLogger("easyobs.ingest")


# Optional callback invoked after each successful trace write. The signature
# is ``async (trace_id: str, service_id: str) -> None``. The hook is run as a
# fire-and-forget task and **must never** raise into the ingest path — every
# exception is swallowed and logged so a misconfigured evaluator cannot break
# trace collection.
TraceWriteHook = Callable[[str, str], Awaitable[None]]


class TraceIngestService:
    """Ingest OTLP payloads into blob storage and catalog.

    A single ingest call may carry spans for **multiple distinct
    trace_ids** because every standard OpenTelemetry SDK groups its
    ``BatchSpanProcessor`` exports by *time window*, not by trace. We
    therefore accept the bucketed output of ``flatten_otlp_payload`` and
    persist one blob batch + one catalog row per trace_id.
    """

    def __init__(self, *, blob: TraceBlobStore, catalog: TraceCatalog) -> None:
        self._blob = blob
        self._catalog = catalog
        self._post_write_hooks: list[TraceWriteHook] = []

    def register_post_write_hook(self, hook: TraceWriteHook) -> None:
        """Subscribe to ``(trace_id, service_id)`` events emitted right after
        every successful catalog upsert. Hooks are invoked as fire-and-forget
        background tasks; any exception they raise is logged and swallowed.

        Used by the Quality module to schedule auto-rule evaluations without
        coupling the ingest path to evaluation logic."""
        self._post_write_hooks.append(hook)

    async def ingest(
        self,
        payload: dict[str, Any] | bytes,
        content_type: str | None,
        *,
        service_id: str,
    ) -> int:
        """Persist every trace contained in ``payload`` and return the
        count of traces actually written. Returning the count makes the
        OTLP router's structured access log honest about what landed.
        """
        traces = flatten_otlp_payload(payload, content_type=content_type)
        written = 0
        for lines, summary in traces:
            # Operator-side enrichment: fill in derived fields (price etc.)
            # so SDKs don't need to know about pricing tables.
            lines = enrich_with_price(lines)
            batch_relpath = self._blob.write_trace_batch(
                trace_id_hex=summary["trace_id"],
                lines=lines,
            )
            await self._catalog.upsert_trace(
                trace_id=summary["trace_id"],
                service_id=service_id,
                started_at=summary["started_at"],
                ended_at=summary["ended_at"],
                root_name=summary["root_name"],
                status=summary["status"],
                service_name=summary["service_name"],
                span_count=summary["span_count"],
                batch_relpath=batch_relpath,
            )
            written += 1
            self._dispatch_hooks(summary["trace_id"], service_id)

        if written > 1:
            _log.info(
                "ingest split multi-trace export",
                extra={"traces_written": written, "service_id": service_id},
            )
        return written

    def _dispatch_hooks(self, trace_id: str, service_id: str) -> None:
        if not self._post_write_hooks:
            return
        for hook in list(self._post_write_hooks):
            try:
                asyncio.create_task(self._safe_hook(hook, trace_id, service_id))
            except RuntimeError:
                # Probably no running loop (sync test harness) — skip.
                _log.debug("no event loop for post-write hook; skipping")

    @staticmethod
    async def _safe_hook(
        hook: TraceWriteHook, trace_id: str, service_id: str
    ) -> None:
        try:
            await hook(trace_id, service_id)
        except Exception:
            _log.exception(
                "post-write hook failed", extra={"trace_id": trace_id}
            )
