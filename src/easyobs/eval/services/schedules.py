"""Cron-style schedule for Judge profiles.

The MVP only implements interval scheduling (``every N hours``); a full
cron parser would be needed for the cron column. Schedules are listed by
the UI; an external worker (or the test scheduler) reads ``next_run_at``
and triggers the run service. We do not start a background loop here so
the platform stays import-side-effect-free.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalScheduleRow
from easyobs.eval.services.dtos import ScheduleDTO


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self,
        *,
        org_id: str,
        project_ids: list[str] | None,
    ) -> list[ScheduleDTO]:
        async with self._sf() as s:
            stmt = select(EvalScheduleRow).where(EvalScheduleRow.org_id == org_id)
            if project_ids is not None:
                stmt = stmt.where(EvalScheduleRow.project_id.in_(list(project_ids) or [""]))
            rows = (await s.execute(stmt.order_by(EvalScheduleRow.created_at))).scalars().all()
            return [_to_dto(r) for r in rows]

    async def create(
        self,
        *,
        org_id: str,
        project_id: str,
        profile_id: str,
        name: str,
        interval_hours: int,
        sample_size: int,
        enabled: bool,
        actor: str | None,
    ) -> ScheduleDTO:
        async with self._sf() as s:
            row = EvalScheduleRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                project_id=project_id,
                profile_id=profile_id,
                name=name,
                interval_hours=max(1, int(interval_hours)),
                sample_size=max(1, int(sample_size)),
                enabled=enabled,
                last_run_at=None,
                next_run_at=_now() + timedelta(hours=max(1, int(interval_hours))),
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def update(
        self,
        *,
        org_id: str,
        schedule_id: str,
        **fields,
    ) -> ScheduleDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalScheduleRow, schedule_id)
            if row is None or row.org_id != org_id:
                return None
            for k, v in fields.items():
                if v is None:
                    continue
                if hasattr(row, k):
                    setattr(row, k, v)
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def delete(self, *, org_id: str, schedule_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalScheduleRow, schedule_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def mark_run(self, *, schedule_id: str) -> None:
        async with self._sf() as s:
            row = await s.get(EvalScheduleRow, schedule_id)
            if row is None:
                return
            now = _now()
            row.last_run_at = now
            row.next_run_at = now + timedelta(hours=max(1, row.interval_hours))
            await s.commit()


def _to_dto(row: EvalScheduleRow) -> ScheduleDTO:
    return ScheduleDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        profile_id=row.profile_id,
        name=row.name,
        interval_hours=row.interval_hours,
        cron=row.cron,
        sample_size=row.sample_size,
        enabled=row.enabled,
        last_run_at=row.last_run_at,
        next_run_at=row.next_run_at,
        created_at=row.created_at,
    )
