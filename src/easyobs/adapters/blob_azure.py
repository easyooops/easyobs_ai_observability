"""Azure Blob Storage Parquet blob store.

Writes trace spans as Parquet files to Azure Blob Storage with
hive-style partitioning. DuckDB's azure extension can read from
``az://container/prefix/**/*.parquet``.

Credentials: when account_key is empty, falls through to
DefaultAzureCredential (managed identity, CLI login, etc.).
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from easyobs.ingest.parquet_schema import SPAN_SCHEMA, span_dicts_to_arrow_table
from easyobs.services.app_settings import BlobConfig


class AzureParquetBlobStore:
    """Parquet blob store backed by Azure Blob Storage."""

    def __init__(self, cfg: BlobConfig) -> None:
        self._account_name = cfg.azure_account_name
        self._container = cfg.azure_container or cfg.bucket
        self._prefix = (cfg.prefix or "traces").strip("/")
        self._account_key = cfg.azure_account_key or None
        self._client = self._make_client()

    def _make_client(self):
        from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]

        account_url = f"https://{self._account_name}.blob.core.windows.net"
        if self._account_key:
            svc = BlobServiceClient(account_url=account_url, credential=self._account_key)
        else:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

            svc = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        return svc.get_container_client(self._container)

    @property
    def root(self) -> Path:
        return Path(f"az://{self._container}/{self._prefix}")

    @property
    def storage_format(self) -> str:
        return "parquet"

    def _trace_shard(self, trace_id_hex: str) -> str:
        return trace_id_hex[:2] if len(trace_id_hex) >= 2 else "00"

    def _date_partition(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _blob_name(self, relpath: str) -> str:
        return f"{self._prefix}/{relpath}"

    # ------------------------------------------------------------------
    # Parquet write
    # ------------------------------------------------------------------

    def write_trace_parquet(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        dt = self._date_partition()
        shard = self._trace_shard(trace_id_hex)
        batch_name = f"batch_{uuid.uuid4().hex}.parquet"
        relpath = f"dt={dt}/shard={shard}/{batch_name}"

        table = span_dicts_to_arrow_table(lines, dt=dt)

        buf = io.BytesIO()
        pq.write_table(
            table,
            buf,
            compression="snappy",
            use_dictionary=["service_name", "status", "kind", "model", "vendor"],
        )
        buf.seek(0)

        blob_name = self._blob_name(relpath)
        self._client.upload_blob(name=blob_name, data=buf.getvalue(), overwrite=True)

        return relpath

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        return self.write_trace_parquet(trace_id_hex=trace_id_hex, lines=lines)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]:
        blob_name = self._blob_name(batch_relpath)
        try:
            blob_client = self._client.get_blob_client(blob_name)
            data = blob_client.download_blob().readall()
        except Exception:
            return []

        if batch_relpath.endswith(".parquet"):
            return self._read_parquet_bytes(data)
        return self._read_ndjson_bytes(data)

    def _read_parquet_bytes(self, data: bytes) -> list[dict[str, Any]]:
        buf = io.BytesIO(data)
        pf = pq.ParquetFile(buf)
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
                        span[_parquet_col_to_span_key(col_name)] = val
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
    def _read_ndjson_bytes(data: bytes) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    # ------------------------------------------------------------------
    # DuckDB scan URI
    # ------------------------------------------------------------------

    def scan_uri(self, pattern: str = "**/*.parquet") -> str:
        return f"az://{self._container}/{self._prefix}/{pattern}"


def _parquet_col_to_span_key(col_name: str) -> str:
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
