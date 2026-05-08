"""DuckDB-based analytical query engine for EasyObs.

Provides vectorized SQL execution over Parquet files stored locally or on
cloud object stores (S3, Azure Blob, GCS). Results are returned as Polars
DataFrames for efficient downstream processing in the analytics / trace
query services.

Design:
- Single in-process DuckDB connection (no server needed).
- S3/Azure/GCS access via DuckDB's built-in httpfs extension.
- Thread-safe: DuckDB handles internal locking; we serialize writes to
  the connection via a reentrant lock for safety in asyncio contexts.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

import duckdb
import polars as pl

from easyobs.services.app_settings import BlobConfig

_log = logging.getLogger("easyobs.query_engine")


class QueryEngine:
    """DuckDB-backed OLAP query engine for Parquet trace data."""

    def __init__(
        self,
        *,
        blob_cfg: BlobConfig,
        scan_base_uri: str,
    ) -> None:
        self._lock = threading.RLock()
        self._scan_base_uri = scan_base_uri
        self._con = duckdb.connect(database=":memory:")
        self._configure(blob_cfg)

    def _configure(self, cfg: BlobConfig) -> None:
        """Install and configure extensions based on the storage provider."""
        with self._lock:
            if cfg.provider == "s3":
                self._con.execute("INSTALL httpfs; LOAD httpfs;")
                if cfg.region:
                    self._con.execute(f"SET s3_region='{cfg.region}';")
                if cfg.s3_access_key_id:
                    self._con.execute(f"SET s3_access_key_id='{cfg.s3_access_key_id}';")
                if cfg.s3_secret_access_key:
                    self._con.execute(f"SET s3_secret_access_key='{cfg.s3_secret_access_key}';")
            elif cfg.provider == "azure":
                self._con.execute("INSTALL azure; LOAD azure;")
                if cfg.azure_account_name:
                    self._con.execute(
                        f"SET azure_storage_account_name='{cfg.azure_account_name}';"
                    )
                if cfg.azure_account_key:
                    self._con.execute(
                        f"SET azure_storage_account_key='{cfg.azure_account_key}';"
                    )
            elif cfg.provider == "gcs":
                self._con.execute("INSTALL httpfs; LOAD httpfs;")
                self._con.execute("SET s3_endpoint='storage.googleapis.com';")
                self._con.execute("SET s3_url_style='path';")

    @property
    def scan_base_uri(self) -> str:
        return self._scan_base_uri

    # ------------------------------------------------------------------
    # Core query methods
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str) -> pl.DataFrame:
        """Execute arbitrary SQL and return results as a Polars DataFrame."""
        with self._lock:
            result = self._con.execute(sql)
            arrow_table = result.fetch_arrow_table()
            return pl.from_arrow(arrow_table)

    def query_parquet(self, sql: str) -> pl.DataFrame:
        """Execute SQL with ``{SCAN}`` placeholder replaced by the base scan URI.

        Usage::

            engine.query_parquet('''
                SELECT model, count(*) as cnt
                FROM read_parquet('{SCAN}')
                WHERE dt = '2026-05-08'
                GROUP BY model
            ''')
        """
        resolved = sql.replace("{SCAN}", self._scan_base_uri)
        return self.execute_sql(resolved)

    def query_parquet_raw(self, sql: str) -> list[dict[str, Any]]:
        """Same as ``query_parquet`` but returns list of dicts."""
        df = self.query_parquet(sql)
        return df.to_dicts()

    # ------------------------------------------------------------------
    # Pre-built analytical queries
    # ------------------------------------------------------------------

    def overview_kpi(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        service_name: str | None = None,
        service_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compute high-level KPIs (counts, error rate, latency percentiles)."""
        where_clauses = self._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                count(DISTINCT trace_id) as total_traces,
                count(*) as total_spans,
                count(CASE WHEN status = 'ERROR' THEN 1 END) as error_spans,
                count(DISTINCT CASE WHEN status = 'ERROR' THEN trace_id END) as error_traces,
                count(DISTINCT service_name) as unique_services,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) as p50_ms,
                percentile_cont(0.90) WITHIN GROUP (ORDER BY duration_ms) as p90_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms,
                percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99_ms,
                sum(tokens_in) as total_tokens_in,
                sum(tokens_out) as total_tokens_out,
                sum(price) as total_price
            FROM read_parquet('{{SCAN}}')
            {where_sql}
        """
        rows = self.query_parquet_raw(sql)
        return rows[0] if rows else {}

    def time_series(
        self,
        *,
        from_ts: datetime,
        to_ts: datetime,
        bucket_count: int = 24,
        service_name: str | None = None,
        service_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate time-bucketed series for charts."""
        span_ns = int((to_ts - from_ts).total_seconds() * 1e9)
        bucket_ns = max(1, span_ns // bucket_count)
        from_ns = int(from_ts.timestamp() * 1e9)

        where_clauses = self._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                ((start_time_unix_nano - {from_ns}) / {bucket_ns}) as bucket_idx,
                count(DISTINCT trace_id) as trace_count,
                count(CASE WHEN status = 'ERROR' THEN 1 END) as error_count,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY bucket_idx
            ORDER BY bucket_idx
        """
        return self.query_parquet_raw(sql)

    def top_models(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Aggregate LLM model usage stats."""
        where_clauses = self._build_where(from_ts=from_ts, to_ts=to_ts)
        where_clauses.append("model IS NOT NULL")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT
                model,
                count(*) as call_count,
                sum(tokens_in) as total_tokens_in,
                sum(tokens_out) as total_tokens_out,
                sum(price) as total_price,
                avg(duration_ms) as avg_latency_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY model
            ORDER BY call_count DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    def top_services(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Aggregate service-level stats."""
        where_clauses = self._build_where(from_ts=from_ts, to_ts=to_ts)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                service_name,
                count(DISTINCT trace_id) as trace_count,
                count(*) as span_count,
                count(CASE WHEN status = 'ERROR' THEN 1 END) as error_count,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) as p50_ms,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95_ms
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY service_name
            ORDER BY trace_count DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    def list_traces(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        service_name: str | None = None,
        service_names: list[str] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List distinct traces with summary stats."""
        where_clauses = self._build_where(
            from_ts=from_ts, to_ts=to_ts, service_name=service_name,
            service_names=service_names,
        )
        if session_id:
            where_clauses.append(f"session_id = '{session_id}'")
        if user_id:
            where_clauses.append(f"user_id = '{user_id}'")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                trace_id,
                min(service_name) as service_name,
                min(start_time_unix_nano) as started_ns,
                max(end_time_unix_nano) as ended_ns,
                count(*) as span_count,
                max(CASE WHEN status = 'ERROR' THEN 'ERROR'
                         WHEN status = 'OK' THEN 'OK'
                         ELSE 'UNSET' END) as status,
                any_value(session_id) as session_id,
                any_value(user_id) as user_id,
                sum(tokens_in) as tokens_in,
                sum(tokens_out) as tokens_out,
                sum(price) as price,
                any_value(model) FILTER (WHERE model IS NOT NULL) as model,
                any_value(name) FILTER (WHERE parent_span_id IS NULL OR parent_span_id = '') as root_name
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY trace_id
            ORDER BY started_ns DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    def session_aggregates(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Aggregate sessions from span-level session_id."""
        where_clauses = self._build_where(from_ts=from_ts, to_ts=to_ts)
        where_clauses.append("session_id IS NOT NULL")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT
                session_id,
                min(service_name) as service_name,
                any_value(user_id) FILTER (WHERE user_id IS NOT NULL) as user_id,
                count(DISTINCT trace_id) as trace_count,
                count(CASE WHEN status = 'ERROR' THEN 1 END) as error_count,
                min(start_time_unix_nano) as first_seen_ns,
                max(end_time_unix_nano) as last_seen_ns,
                sum(tokens_in) as tokens_in,
                sum(tokens_out) as tokens_out,
                sum(price) as price
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY session_id
            ORDER BY last_seen_ns DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    def user_aggregates(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Aggregate per-user activity."""
        where_clauses = self._build_where(from_ts=from_ts, to_ts=to_ts)
        where_clauses.append("user_id IS NOT NULL")
        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        sql = f"""
            SELECT
                user_id,
                min(service_name) as service_name,
                count(DISTINCT session_id) as session_count,
                count(DISTINCT trace_id) as trace_count,
                count(CASE WHEN status = 'ERROR' THEN 1 END) as error_count,
                sum(tokens_in) as tokens_in,
                sum(tokens_out) as tokens_out,
                sum(price) as price,
                min(start_time_unix_nano) as first_seen_ns,
                max(end_time_unix_nano) as last_seen_ns
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            GROUP BY user_id
            ORDER BY last_seen_ns DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    def span_list(
        self,
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        service_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Flat list of spans for the Spans tab."""
        where_clauses = self._build_where(from_ts=from_ts, to_ts=to_ts, service_name=service_name)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                trace_id, span_id, parent_span_id, name,
                service_name, status, duration_ms,
                start_time_unix_nano, end_time_unix_nano,
                kind, step, model,
                tokens_in + tokens_out as tokens_total,
                price
            FROM read_parquet('{{SCAN}}')
            {where_sql}
            ORDER BY start_time_unix_nano DESC
            LIMIT {limit}
        """
        return self.query_parquet_raw(sql)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_where(
        *,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        service_name: str | None = None,
        service_names: list[str] | None = None,
    ) -> list[str]:
        clauses: list[str] = []
        if from_ts:
            ns = int(from_ts.timestamp() * 1e9)
            clauses.append(f"start_time_unix_nano >= {ns}")
        if to_ts:
            ns = int(to_ts.timestamp() * 1e9)
            clauses.append(f"start_time_unix_nano <= {ns}")
        if service_name:
            clauses.append(f"service_name = '{service_name}'")
        elif service_names is not None:
            escaped = [n.replace("'", "''") for n in service_names]
            in_list = ", ".join(f"'{n}'" for n in escaped)
            clauses.append(f"service_name IN ({in_list})")
        return clauses

    def close(self) -> None:
        """Cleanly shut down the DuckDB connection."""
        with self._lock:
            try:
                self._con.close()
            except Exception:
                pass
