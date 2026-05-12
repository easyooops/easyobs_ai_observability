"""DuckDB-powered trace query service.

Provides fast trace listing, filtering, and summary via DuckDB SQL over
Parquet. For trace detail (full span tree) it falls back to reading
the individual batch file because detail requests need the full
attributes/events payload which is stored as JSON inside Parquet.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from easyobs.ports.blob import TraceBlobStore
from easyobs.ports.catalog import TraceCatalog
from easyobs.services.llm_attrs import SpanLLM, summarise_trace
from easyobs.services.query_engine import QueryEngine
from easyobs.services.trace_query import resolve_range

_log = logging.getLogger("easyobs.trace_query_duckdb")


class DuckDBTraceQueryService:
    """Trace query service powered by DuckDB for list/filter operations.

    Falls back to catalog + blob for trace detail (full span tree) since
    that needs the full attributes/events JSON.

    When a hybrid blob store is active, two engines are available:
    - ``engine``: local hot store (last N days, default 7)
    - ``archive_engine``: S3 cold archive (all data)
    Preset windows (1h/6h/24h/7d) always use the hot engine.
    Custom ranges that extend beyond the hot window use the archive engine.
    """

    def __init__(
        self,
        *,
        engine: QueryEngine,
        blob: TraceBlobStore,
        catalog: TraceCatalog,
        archive_engine: QueryEngine | None = None,
        hot_retention_days: int = 7,
    ) -> None:
        self._engine = engine
        self._archive_engine = archive_engine
        self._hot_retention_days = hot_retention_days
        self._blob = blob
        self._catalog = catalog

    def _select_engine(
        self,
        window_hours: int | None,
        from_ts: datetime | None,
        to_ts: datetime | None,
    ) -> QueryEngine:
        """Pick the appropriate query engine based on time range.

        Rules:
        - If no archive engine exists, always use primary.
        - Preset windows (window_hours set) always use primary (hot store).
        - Custom range: if from_ts is older than hot_retention_days, use archive.
        """
        if self._archive_engine is None:
            return self._engine
        if window_hours is not None:
            return self._engine
        if from_ts is not None:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._hot_retention_days)
            if from_ts < cutoff:
                _log.info(
                    "routing query to S3 archive engine (custom range beyond hot window)",
                    extra={"from_ts": from_ts.isoformat(), "cutoff": cutoff.isoformat()},
                )
                return self._archive_engine
        return self._engine

    async def _resolve_service_names(
        self, service_ids: list[str] | None
    ) -> list[str] | None:
        """Convert service IDs (from CallerScope) to service_name values for DuckDB filtering.

        Returns None if no filtering is needed (super admin / no scope).
        """
        if service_ids is None:
            return None
        if not service_ids:
            return []
        names = await self._catalog.get_service_names_by_ids(service_ids)
        return names if names else []

    async def list_traces(
        self,
        *,
        service_ids: list[str] | None,
        limit: int,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        with_llm: bool = False,
    ):
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        now = datetime.now(tz=timezone.utc)
        effective_from = lo or (now - timedelta(hours=window_hours or 720))
        effective_to = hi or now

        engine = self._select_engine(window_hours, lo, hi)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return []

        rows = engine.list_traces(
            from_ts=effective_from,
            to_ts=effective_to,
            service_names=service_names,
            session_id=session_id,
            user_id=user_id,
            limit=limit,
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            started_ns = r.get("started_ns")
            ended_ns = r.get("ended_ns")
            tok_in = r.get("tokens_in", 0) or 0
            tok_out = r.get("tokens_out", 0) or 0

            trace = {
                "traceId": r.get("trace_id", ""),
                "serviceId": "",
                "startedAt": _ns_to_iso(started_ns),
                "endedAt": _ns_to_iso(ended_ns),
                "rootName": r.get("root_name") or "",
                "status": r.get("status", "UNSET"),
                "serviceName": r.get("service_name", "unknown"),
                "spanCount": r.get("span_count", 0) or 0,
            }

            if with_llm:
                trace["session"] = r.get("session_id")
                trace["user"] = r.get("user_id")
                trace["tokensIn"] = tok_in
                trace["tokensOut"] = tok_out
                trace["tokensTotal"] = tok_in + tok_out
                trace["price"] = round(r.get("price", 0.0) or 0.0, 6)
                trace["model"] = r.get("model")
                trace["models"] = [r["model"]] if r.get("model") else []

            out.append(trace)
        return out

    async def trace_detail(
        self,
        trace_id: str,
        *,
        allowed_service_ids: list[str] | None,
    ) -> dict | None:
        """Full trace detail with span tree. Uses catalog + blob read.

        DuckDB is not used here because the detail view needs full
        attributes/events JSON which is more efficient to read from a
        single targeted file.
        """
        meta = await self._catalog.get_trace_row(trace_id)
        if meta is None:
            return None
        if allowed_service_ids is not None and meta.service_id not in allowed_service_ids:
            return None
        spans = self._blob.read_batch_lines(meta.batch_relpath)
        spans_enriched = []
        for sp in spans:
            llm = SpanLLM.from_span(sp).to_public()
            enriched = dict(sp)
            enriched["llm"] = llm
            spans_enriched.append(enriched)
        return {
            "traceId": meta.trace_id,
            "serviceId": meta.service_id,
            "startedAt": meta.started_at.astimezone(timezone.utc).isoformat(),
            "endedAt": meta.ended_at.astimezone(timezone.utc).isoformat() if meta.ended_at else None,
            "rootName": meta.root_name,
            "status": meta.status,
            "serviceName": meta.service_name,
            "spanCount": meta.span_count,
            "spans": spans_enriched,
            "llmSummary": summarise_trace(spans),
        }

    async def dashboard_summary(
        self, *, service_ids: list[str] | None
    ) -> dict:
        now = datetime.now(tz=timezone.utc)
        from_ts = now - timedelta(hours=24)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return {"traceCount24h": 0, "errorTraces": 0, "note": "no accessible services"}

        kpi = self._engine.overview_kpi(
            from_ts=from_ts, to_ts=now, service_names=service_names
        )
        return {
            "traceCount24h": kpi.get("total_traces", 0) or 0,
            "errorTraces": kpi.get("error_traces", 0) or 0,
            "note": "DuckDB-powered summary over last 24h Parquet data",
        }


def _ns_to_iso(ns: int | None) -> str:
    if not ns:
        return ""
    try:
        dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        return dt.isoformat()
    except (OSError, OverflowError, ValueError):
        return ""
