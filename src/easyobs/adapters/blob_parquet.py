"""Local filesystem Parquet blob store.

Writes trace spans as Parquet files using hive-style partitioning
(``dt=YYYY-MM-DD/shard=XX/batch_<uuid>.parquet``) so DuckDB can leverage
partition pruning for time-range and shard-level pushdown.

Also retains the legacy NDJSON read path so existing ``read_batch_lines``
callers (trace detail endpoint) keep working during the migration period.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from easyobs.ingest.parquet_schema import SPAN_SCHEMA, span_dicts_to_arrow_table


class LocalParquetBlobStore:
    """Parquet-first local blob store with NDJSON backward-compat reads."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def storage_format(self) -> str:
        return "parquet"

    def _trace_shard(self, trace_id_hex: str) -> str:
        return trace_id_hex[:2] if len(trace_id_hex) >= 2 else "00"

    def _date_partition(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Parquet write (primary path)
    # ------------------------------------------------------------------

    def write_trace_parquet(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        dt = self._date_partition()
        shard = self._trace_shard(trace_id_hex)
        dir_path = self._root / f"dt={dt}" / f"shard={shard}"
        dir_path.mkdir(parents=True, exist_ok=True)

        batch_name = f"batch_{uuid.uuid4().hex}.parquet"
        file_path = dir_path / batch_name

        table = span_dicts_to_arrow_table(lines, dt=dt)
        pq.write_table(
            table,
            str(file_path),
            compression="snappy",
            use_dictionary=["service_name", "status", "kind", "model", "vendor"],
        )

        rel = file_path.relative_to(self._root)
        return str(rel).replace("\\", "/")

    # ------------------------------------------------------------------
    # Legacy NDJSON write (for backward compat / fallback)
    # ------------------------------------------------------------------

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        return self.write_trace_parquet(trace_id_hex=trace_id_hex, lines=lines)

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]:
        """Read spans from either Parquet or legacy NDJSON files."""
        path = self._root / batch_relpath
        if not path.is_file():
            return []

        if path.suffix == ".parquet":
            return self._read_parquet(path)
        return self._read_ndjson(path)

    def _read_parquet(self, path: Path) -> list[dict[str, Any]]:
        pf = pq.ParquetFile(str(path))
        table = pf.read()
        rows: list[dict[str, Any]] = []
        schema = table.schema
        for batch in table.to_batches():
            for row_idx in range(batch.num_rows):
                span: dict[str, Any] = {}
                attrs_json_val = None
                events_json_val = None
                for col_idx in range(batch.num_columns):
                    col_name = schema.field(col_idx).name
                    val = batch.column(col_idx)[row_idx].as_py()
                    if col_name == "attributes_json":
                        attrs_json_val = val
                    elif col_name == "events_json":
                        events_json_val = val
                    elif col_name == "dt":
                        continue
                    else:
                        span[self._parquet_col_to_span_key(col_name)] = val
                # Restore original attributes/events structure
                if attrs_json_val:
                    try:
                        span["attributes"] = json.loads(attrs_json_val)
                    except (json.JSONDecodeError, TypeError):
                        span["attributes"] = []
                else:
                    span["attributes"] = []
                if events_json_val:
                    try:
                        span["events"] = json.loads(events_json_val)
                    except (json.JSONDecodeError, TypeError):
                        span["events"] = []
                else:
                    span["events"] = []
                rows.append(span)
        return rows

    @staticmethod
    def _read_ndjson(path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    @staticmethod
    def _parquet_col_to_span_key(col_name: str) -> str:
        """Map parquet column names back to the original NDJSON span keys."""
        mapping = {
            "trace_id": "traceId",
            "span_id": "spanId",
            "parent_span_id": "parentSpanId",
            "name": "name",
            "service_name": "serviceName",
            "status": "status",
            "start_time_unix_nano": "startTimeUnixNano",
            "end_time_unix_nano": "endTimeUnixNano",
            "duration_ms": "durationMs",
            "kind": "kind",
            "model": "model",
            "vendor": "vendor",
            "tokens_in": "tokensIn",
            "tokens_out": "tokensOut",
            "price": "price",
            "session_id": "sessionId",
            "user_id": "userId",
            "step": "step",
        }
        return mapping.get(col_name, col_name)

    # ------------------------------------------------------------------
    # DuckDB scan URI
    # ------------------------------------------------------------------

    def scan_uri(self, pattern: str = "**/*.parquet") -> str:
        return str(self._root / pattern).replace("\\", "/")
