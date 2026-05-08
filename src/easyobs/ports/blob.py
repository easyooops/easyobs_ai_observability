from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TraceBlobStore(Protocol):
    """Read/write trace batches; local disk or cloud-backed implementations.

    The legacy NDJSON methods (``write_trace_batch`` / ``read_batch_lines``)
    are kept for backward compatibility. Parquet-capable stores additionally
    implement ``write_trace_parquet`` and ``scan_uri`` so the DuckDB query
    engine can scan them directly.
    """

    @property
    def root(self) -> Path: ...

    # --- Legacy NDJSON interface (kept for migration period) ----------------
    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str: ...

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]: ...

    # --- Parquet interface --------------------------------------------------
    def write_trace_parquet(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str:
        """Write spans as a Parquet file. Returns the relative path / object key."""
        ...

    def scan_uri(self, pattern: str = "**/*.parquet") -> str:
        """Return the URI or glob path that DuckDB can use to scan Parquet files.

        Examples:
          - Local: ``/data/blob/traces/**/*.parquet``
          - S3:    ``s3://bucket/prefix/traces/**/*.parquet``
        """
        ...

    @property
    def storage_format(self) -> str:
        """Return 'ndjson' or 'parquet' to indicate the active write format."""
        ...
