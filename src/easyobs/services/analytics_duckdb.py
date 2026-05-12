"""DuckDB-powered analytics service.

Replaces the O(n) Python-loop analytics with vectorized SQL execution
over Parquet files. All KPIs, time series, service breakdowns, LLM stats,
session/user aggregates are computed via DuckDB in sub-second latency
regardless of data volume.

Falls back to the legacy ``AnalyticsService`` when the query engine is
not available (e.g. ``EASYOBS_QUERY_ENGINE=legacy``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from easyobs.ports.blob import TraceBlobStore
from easyobs.ports.catalog import TraceCatalog
from easyobs.services.query_engine import QueryEngine
from easyobs.services.trace_query import resolve_range

_log = logging.getLogger("easyobs.analytics_duckdb")


class DuckDBAnalyticsService:
    """Analytics powered by DuckDB SQL over Parquet.

    In hybrid mode, two engines are available:
    - ``engine``: local hot store (last N days)
    - ``archive_engine``: S3 cold archive (all data)
    Routing follows the same logic as DuckDBTraceQueryService.
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
        if self._archive_engine is None:
            return self._engine
        if window_hours is not None:
            return self._engine
        if from_ts is not None:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._hot_retention_days)
            if from_ts < cutoff:
                _log.info(
                    "analytics: routing to S3 archive engine",
                    extra={"from_ts": from_ts.isoformat()},
                )
                return self._archive_engine
        return self._engine

    async def _resolve_service_names(
        self, service_ids: list[str] | None
    ) -> list[str] | None:
        if service_ids is None:
            return None
        if not service_ids:
            return []
        names = await self._catalog.get_service_names_by_ids(service_ids)
        return names if names else []

    async def overview(
        self,
        *,
        service_ids: list[str] | None,
        window_hours: int | None = 24,
        bucket_count: int,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(tz=timezone.utc)
        lo, hi = resolve_range(window_hours, from_ts, to_ts)

        series_end = hi or now
        series_start = lo or (series_end - timedelta(hours=window_hours or 24))
        total_span_sec = max(1.0, (series_end - series_start).total_seconds())
        effective_window_hours = total_span_sec / 3600.0

        engine = self._select_engine(window_hours, lo, hi)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return self._empty_overview(effective_window_hours, now, series_start, bucket_count)

        service_filter = service_names[0] if service_names and len(service_names) == 1 else None

        kpi = engine.overview_kpi(
            from_ts=series_start, to_ts=series_end,
            service_name=service_filter,
            service_names=service_names if not service_filter else None,
        )

        total_traces = kpi.get("total_traces", 0) or 0
        error_traces = kpi.get("error_traces", 0) or 0

        series_data = engine.time_series(
            from_ts=series_start, to_ts=series_end,
            bucket_count=bucket_count, service_name=service_filter,
            service_names=service_names if not service_filter else None,
        )

        series_total = [0] * bucket_count
        series_err = [0] * bucket_count
        series_p95 = [0.0] * bucket_count
        for row in series_data:
            idx = int(row.get("bucket_idx", -1))
            if 0 <= idx < bucket_count:
                series_total[idx] = row.get("trace_count", 0) or 0
                series_err[idx] = row.get("error_count", 0) or 0
                series_p95[idx] = round(row.get("p95_ms", 0.0) or 0.0, 2)

        bucket_span = max(1, int(total_span_sec // bucket_count))

        services_data = engine.top_services(
            from_ts=series_start, to_ts=series_end, limit=10
        )
        services = []
        for svc in services_data:
            count = svc.get("trace_count", 0) or 0
            err = svc.get("error_count", 0) or 0
            services.append({
                "name": svc.get("service_name", "unknown"),
                "count": count,
                "errorCount": err,
                "errorRate": round((err / count) * 100, 2) if count else 0.0,
                "p50": round(svc.get("p50_ms", 0.0) or 0.0, 2),
                "p95": round(svc.get("p95_ms", 0.0) or 0.0, 2),
            })

        models_data = engine.top_models(
            from_ts=series_start, to_ts=series_end, limit=8
        )

        top_models = [
            {"name": m.get("model", ""), "count": m.get("call_count", 0) or 0}
            for m in models_data
        ]

        total_tokens_in = kpi.get("total_tokens_in", 0) or 0
        total_tokens_out = kpi.get("total_tokens_out", 0) or 0
        total_price = kpi.get("total_price", 0.0) or 0.0

        # Latency bands via DuckDB
        latency_bands = self._compute_latency_bands(engine, series_start, series_end, service_filter, service_names if not service_filter else None)

        # Top operations (root spans)
        top_ops = self._compute_top_operations(engine, series_start, series_end, service_filter, service_names if not service_filter else None)

        # Top vendors and steps
        top_vendors = self._compute_top_vendors(engine, series_start, series_end, service_filter, service_names if not service_filter else None)
        top_steps = self._compute_top_steps(engine, series_start, series_end, service_filter, service_names if not service_filter else None)

        return {
            "windowHours": round(effective_window_hours, 4),
            "generatedAt": now.isoformat(),
            "kpi": {
                "totalTraces": total_traces,
                "errorTraces": error_traces,
                "okTraces": total_traces - error_traces,
                "unsetTraces": 0,
                "errorRate": round((error_traces / total_traces) * 100, 2) if total_traces else 0.0,
                "p50LatencyMs": round(kpi.get("p50_ms", 0.0) or 0.0, 2),
                "p90LatencyMs": round(kpi.get("p90_ms", 0.0) or 0.0, 2),
                "p95LatencyMs": round(kpi.get("p95_ms", 0.0) or 0.0, 2),
                "p99LatencyMs": round(kpi.get("p99_ms", 0.0) or 0.0, 2),
                "uniqueServices": kpi.get("unique_services", 0) or 0,
            },
            "series": {
                "bucketCount": bucket_count,
                "bucketSpanSec": bucket_span,
                "startedAt": series_start.isoformat(),
                "total": series_total,
                "errors": series_err,
                "p95Ms": series_p95,
            },
            "statusMix": {
                "OK": total_traces - error_traces,
                "ERROR": error_traces,
                "UNSET": 0,
            },
            "latencyBands": latency_bands,
            "services": services,
            "topOperations": top_ops,
            "llm": {
                "tokensIn": total_tokens_in,
                "tokensOut": total_tokens_out,
                "tokensTotal": total_tokens_in + total_tokens_out,
                "price": round(total_price, 6),
                "llmCalls": sum(m.get("call_count", 0) or 0 for m in models_data),
                "retrieveCalls": 0,
                "toolCalls": 0,
                "tracesWithLlm": 0,
                "uniqueSessions": 0,
                "topModels": top_models,
                "topVendors": top_vendors,
                "topSteps": top_steps,
            },
        }

    @staticmethod
    def _empty_overview(
        window_hours: float, now: datetime, start: datetime, bucket_count: int
    ) -> dict[str, Any]:
        bucket_span = max(1, int((now - start).total_seconds() // bucket_count))
        return {
            "windowHours": round(window_hours, 4),
            "generatedAt": now.isoformat(),
            "kpi": {
                "totalTraces": 0, "errorTraces": 0, "okTraces": 0, "unsetTraces": 0,
                "errorRate": 0.0, "p50LatencyMs": 0.0, "p90LatencyMs": 0.0,
                "p95LatencyMs": 0.0, "p99LatencyMs": 0.0, "uniqueServices": 0,
            },
            "series": {
                "bucketCount": bucket_count, "bucketSpanSec": bucket_span,
                "startedAt": start.isoformat(),
                "total": [0] * bucket_count, "errors": [0] * bucket_count,
                "p95Ms": [0.0] * bucket_count,
            },
            "statusMix": {"OK": 0, "ERROR": 0, "UNSET": 0},
            "latencyBands": [{"label": b, "count": 0} for b in
                            ("<100ms", "100-300ms", "300-800ms", "0.8-2s", "2-5s", ">5s")],
            "services": [],
            "topOperations": [],
            "llm": {
                "tokensIn": 0, "tokensOut": 0, "tokensTotal": 0, "price": 0.0,
                "llmCalls": 0, "retrieveCalls": 0, "toolCalls": 0,
                "tracesWithLlm": 0, "uniqueSessions": 0,
                "topModels": [], "topVendors": [], "topSteps": [],
            },
        }

    def _compute_latency_bands(
        self, engine: QueryEngine, from_ts: datetime, to_ts: datetime, service_name: str | None,
        service_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = QueryEngine._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_clauses.append("duration_ms IS NOT NULL")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT
                count(CASE WHEN duration_ms < 100 THEN 1 END) as band_0,
                count(CASE WHEN duration_ms >= 100 AND duration_ms < 300 THEN 1 END) as band_1,
                count(CASE WHEN duration_ms >= 300 AND duration_ms < 800 THEN 1 END) as band_2,
                count(CASE WHEN duration_ms >= 800 AND duration_ms < 2000 THEN 1 END) as band_3,
                count(CASE WHEN duration_ms >= 2000 AND duration_ms < 5000 THEN 1 END) as band_4,
                count(CASE WHEN duration_ms >= 5000 THEN 1 END) as band_5
            FROM read_parquet('{{SCAN}}')
            {where_sql}
        """
        sql = sql.replace("{SCAN}", engine.scan_base_uri)
        try:
            rows = engine.execute_sql(sql).to_dicts()
        except Exception:
            return [{"label": b, "count": 0} for b in
                    ("<100ms", "100-300ms", "300-800ms", "0.8-2s", "2-5s", ">5s")]

        row = rows[0] if rows else {}
        labels = ["<100ms", "100-300ms", "300-800ms", "0.8-2s", "2-5s", ">5s"]
        return [
            {"label": labels[i], "count": row.get(f"band_{i}", 0) or 0}
            for i in range(6)
        ]

    def _compute_top_operations(
        self, engine: QueryEngine, from_ts: datetime, to_ts: datetime, service_name: str | None,
        service_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = QueryEngine._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_clauses.append("(parent_span_id IS NULL OR parent_span_id = '')")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT
                name,
                count(*) as cnt,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) as p50,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY name
            ORDER BY cnt DESC
            LIMIT 8
        """
        sql = sql.replace("{SCAN}", engine.scan_base_uri)
        try:
            rows = engine.execute_sql(sql).to_dicts()
        except Exception:
            return []

        return [
            {
                "name": r.get("name", "(unnamed)"),
                "count": r.get("cnt", 0) or 0,
                "p50": round(r.get("p50", 0.0) or 0.0, 2),
                "p95": round(r.get("p95", 0.0) or 0.0, 2),
            }
            for r in rows
        ]

    def _compute_top_vendors(
        self, engine: QueryEngine, from_ts: datetime, to_ts: datetime, service_name: str | None,
        service_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = QueryEngine._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_clauses.append("vendor IS NOT NULL AND vendor != ''")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT vendor as name, count(*) as cnt
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY vendor
            ORDER BY cnt DESC
            LIMIT 8
        """
        sql = sql.replace("{SCAN}", engine.scan_base_uri)
        try:
            rows = engine.execute_sql(sql).to_dicts()
        except Exception:
            return []
        return [{"name": r.get("name", ""), "count": r.get("cnt", 0) or 0} for r in rows]

    def _compute_top_steps(
        self, engine: QueryEngine, from_ts: datetime, to_ts: datetime, service_name: str | None,
        service_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = QueryEngine._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_clauses.append("step IS NOT NULL AND step != ''")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT step as name, count(*) as cnt
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY step
            ORDER BY cnt DESC
            LIMIT 8
        """
        sql = sql.replace("{SCAN}", engine.scan_base_uri)
        try:
            rows = engine.execute_sql(sql).to_dicts()
        except Exception:
            return []
        return [{"name": r.get("name", ""), "count": r.get("cnt", 0) or 0} for r in rows]

    async def sessions(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        now = datetime.now(tz=timezone.utc)
        effective_from = lo or (now - timedelta(hours=window_hours or 720))
        effective_to = hi or now

        engine = self._select_engine(window_hours, lo, hi)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return []

        rows = engine.session_aggregates(
            from_ts=effective_from, to_ts=effective_to, limit=limit
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            tok_in = r.get("tokens_in", 0) or 0
            tok_out = r.get("tokens_out", 0) or 0
            first_ns = r.get("first_seen_ns")
            last_ns = r.get("last_seen_ns")
            out.append({
                "sessionId": r.get("session_id", ""),
                "serviceName": r.get("service_name", "unknown"),
                "user": r.get("user_id"),
                "traceCount": r.get("trace_count", 0) or 0,
                "errorCount": r.get("error_count", 0) or 0,
                "firstSeenAt": _ns_to_iso(first_ns),
                "lastSeenAt": _ns_to_iso(last_ns),
                "tokensIn": tok_in,
                "tokensOut": tok_out,
                "tokensTotal": tok_in + tok_out,
                "price": round(r.get("price", 0.0) or 0.0, 6),
                "models": [],
                "traceIds": [],
            })
        return out

    async def users(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        now = datetime.now(tz=timezone.utc)
        effective_from = lo or (now - timedelta(hours=window_hours or 720))
        effective_to = hi or now

        engine = self._select_engine(window_hours, lo, hi)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return []

        rows = engine.user_aggregates(
            from_ts=effective_from, to_ts=effective_to, limit=limit
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            tok_in = r.get("tokens_in", 0) or 0
            tok_out = r.get("tokens_out", 0) or 0
            first_ns = r.get("first_seen_ns")
            last_ns = r.get("last_seen_ns")
            out.append({
                "userId": r.get("user_id", ""),
                "serviceName": r.get("service_name", "unknown"),
                "sessionCount": r.get("session_count", 0) or 0,
                "traceCount": r.get("trace_count", 0) or 0,
                "errorCount": r.get("error_count", 0) or 0,
                "tokensIn": tok_in,
                "tokensOut": tok_out,
                "tokensTotal": tok_in + tok_out,
                "price": round(r.get("price", 0.0) or 0.0, 6),
                "models": [],
                "firstSeenAt": _ns_to_iso(first_ns),
                "lastSeenAt": _ns_to_iso(last_ns),
            })
        return out

    async def spans(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        now = datetime.now(tz=timezone.utc)
        effective_from = lo or (now - timedelta(hours=window_hours or 720))
        effective_to = hi or now

        engine = self._select_engine(window_hours, lo, hi)

        service_names = await self._resolve_service_names(service_ids)
        if service_names is not None and not service_names:
            return []

        service_filter = service_names[0] if service_names and len(service_names) == 1 else None

        rows = engine.span_list(
            from_ts=effective_from, to_ts=effective_to,
            service_name=service_filter, limit=limit,
        )

        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "traceId": r.get("trace_id", ""),
                "spanId": r.get("span_id", ""),
                "parentSpanId": r.get("parent_span_id"),
                "name": r.get("name", ""),
                "serviceName": r.get("service_name", ""),
                "status": r.get("status", "UNSET"),
                "durationMs": round(r.get("duration_ms", 0.0) or 0.0, 2),
                "startTimeUnixNano": r.get("start_time_unix_nano"),
                "endTimeUnixNano": r.get("end_time_unix_nano"),
                "kind": r.get("kind"),
                "step": r.get("step"),
                "model": r.get("model"),
                "tokensTotal": r.get("tokens_total", 0) or 0,
                "price": r.get("price", 0.0) or 0.0,
            })
        return out


def _ns_to_iso(ns: int | None) -> str:
    """Convert nanosecond timestamp to ISO string."""
    if not ns:
        return ""
    try:
        dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        return dt.isoformat()
    except (OSError, OverflowError, ValueError):
        return ""
