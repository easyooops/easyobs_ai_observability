"""Google Cloud Storage Parquet blob store.

Writes trace spans as Parquet files to GCS with hive-style partitioning.
DuckDB's httpfs extension reads from ``gs://bucket/prefix/**/*.parquet``.

Credentials: when gcs_service_account_json is empty, falls through to
Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS env var,
gcloud auth, GCE metadata service).
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


class GCSParquetBlobStore:
    """Parquet blob store backed by Google Cloud Storage."""

    def __init__(self, cfg: BlobConfig) -> None:
        self._bucket_name = cfg.bucket
        self._prefix = (cfg.prefix or "traces").strip("/")
        self._sa_json = cfg.gcs_service_account_json or None
        self._bucket = self._make_bucket()

    def _make_bucket(self):
        from google.cloud import storage  # type: ignore[import-not-found]

        if self._sa_json:
            from google.oauth2 import service_account  # type: ignore[import-not-found]

            info = json.loads(self._sa_json)
            creds = service_account.Credentials.from_service_account_info(info)
            client = storage.Client(credentials=creds, project=info.get("project_id"))
        else:
            client = storage.Client()
        return client.bucket(self._bucket_name)

    @property
    def root(self) -> Path:
        return Path(f"gs://{self._bucket_name}/{self._prefix}")

    @property
    def storage_format(self) -> str:
        return "parquet"

    def _trace_shard(self, trace_id_hex: str) -> str:
        return trace_id_hex[:2] if len(trace_id_hex) >= 2 else "00"

    def _date_partition(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _blob_path(self, relpath: str) -> str:
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

        blob_path = self._blob_path(relpath)
        blob = self._bucket.blob(blob_path)
        blob.upload_from_file(buf, content_type="application/octet-stream")

        return relpath

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        return self.write_trace_parquet(trace_id_hex=trace_id_hex, lines=lines)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]:
        blob_path = self._blob_path(batch_relpath)
        try:
            blob = self._bucket.blob(blob_path)
            data = blob.download_as_bytes()
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
        return f"gs://{self._bucket_name}/{self._prefix}/{pattern}"


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
