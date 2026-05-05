from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from easyobs.db.models import TraceIndexRow
from easyobs.ports.catalog import TraceCatalog, TraceSummaryRecord


class SqliteTraceCatalog(TraceCatalog):
    """SQLite-backed trace_index; same schema targets Postgres with driver swap."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

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
    ) -> None:
        async with self._sf() as session:
            row = await session.get(TraceIndexRow, trace_id)
            if row is None:
                session.add(
                    TraceIndexRow(
                        trace_id=trace_id,
                        service_id=service_id,
                        started_at=started_at,
                        ended_at=ended_at,
                        root_name=root_name,
                        status=status,
                        service_name=service_name,
                        span_count=span_count,
                        batch_relpath=batch_relpath,
                    )
                )
            else:
                row.service_id = service_id or row.service_id
                row.started_at = min(row.started_at, started_at)
                if ended_at:
                    row.ended_at = max(row.ended_at or ended_at, ended_at)
                row.root_name = root_name or row.root_name
                row.status = status
                row.service_name = service_name or row.service_name
                row.span_count = max(row.span_count, span_count)
                row.batch_relpath = batch_relpath
            await session.commit()

    async def list_traces(
        self,
        *,
        service_ids: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # ``service_ids=None`` means "no scope filter" (super admin view).
        # ``service_ids=[]`` means "the caller has access to nothing"; we
        # short-circuit so query callers always see an empty result rather
        # than the global dataset.
        if service_ids is not None and not service_ids:
            return []
        async with self._sf() as session:
            stmt = select(TraceIndexRow).order_by(TraceIndexRow.started_at.desc()).limit(limit)
            if service_ids is not None:
                stmt = stmt.where(TraceIndexRow.service_id.in_(service_ids))
            rows = (await session.scalars(stmt)).all()
            out: list[dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "traceId": r.trace_id,
                        "serviceId": r.service_id,
                        "startedAt": r.started_at.astimezone(timezone.utc).isoformat(),
                        "endedAt": r.ended_at.astimezone(timezone.utc).isoformat()
                        if r.ended_at
                        else None,
                        "rootName": r.root_name,
                        "status": r.status,
                        "serviceName": r.service_name,
                        "spanCount": r.span_count,
                    }
                )
            return out

    async def get_trace_row(self, trace_id: str) -> TraceSummaryRecord | None:
        async with self._sf() as session:
            r = await session.get(TraceIndexRow, trace_id)
            if r is None:
                return None
            return TraceSummaryRecord(
                trace_id=r.trace_id,
                service_id=r.service_id,
                started_at=r.started_at,
                ended_at=r.ended_at,
                root_name=r.root_name,
                status=r.status,
                service_name=r.service_name,
                span_count=r.span_count,
                batch_relpath=r.batch_relpath,
            )
