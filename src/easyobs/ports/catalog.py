from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TraceSummaryRecord:
    trace_id: str
    service_id: str
    started_at: datetime
    ended_at: datetime | None
    root_name: str
    status: str
    service_name: str
    span_count: int
    batch_relpath: str


@runtime_checkable
class TraceCatalog(Protocol):
    """Trace index metadata. Defaults to SQLite for local dev; swap the URL to Postgres for production."""

    async def upsert_trace(
        self,
        *,
        trace_id: str,
        service_id: str,
        started_at: datetime,
        ended_at: datetime | None,
        root_name: str,
        status: str,
        service_name: str,
        span_count: int,
        batch_relpath: str,
    ) -> None: ...

    async def list_traces(
        self,
        *,
        service_ids: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    async def get_trace_row(self, trace_id: str) -> TraceSummaryRecord | None: ...
