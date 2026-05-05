from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TraceBlobStore(Protocol):
    """Read/write trace batches (NDJSON); local disk or S3-backed implementations."""

    @property
    def root(self) -> Path: ...

    def write_trace_batch(self, *, trace_id_hex: str, lines: list[dict[str, Any]]) -> str: ...

    def read_batch_lines(self, batch_relpath: str) -> list[dict[str, Any]]: ...
