"""Persistence for human-authored labels used in ``human_label`` evaluation runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import HumanLabelAnnotationRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_ctx(row: HumanLabelAnnotationRow) -> dict[str, Any]:
    return {
        "traceId": row.trace_id,
        "expectedResponse": row.expected_response or None,
        "humanVerdict": row.human_verdict or None,
        "notes": row.notes or None,
        "source": "registry",
        "annotationId": row.id,
    }


class HumanLabelService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def upsert(
        self,
        *,
        org_id: str,
        trace_id: str,
        project_id: str | None,
        expected_response: str,
        human_verdict: str,
        notes: str,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        tid = trace_id.strip()
        if not tid:
            raise ValueError("trace_id required")
        async with self._sf() as s:
            stmt = select(HumanLabelAnnotationRow).where(
                HumanLabelAnnotationRow.org_id == org_id,
                HumanLabelAnnotationRow.trace_id == tid,
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            now = _now()
            if row is None:
                row = HumanLabelAnnotationRow(
                    id=uuid.uuid4().hex,
                    org_id=org_id,
                    project_id=project_id,
                    trace_id=tid,
                    expected_response=expected_response,
                    human_verdict=human_verdict,
                    notes=notes,
                    created_at=now,
                    updated_at=now,
                    created_by=actor_user_id,
                )
                s.add(row)
            else:
                row.project_id = project_id
                row.expected_response = expected_response
                row.human_verdict = human_verdict
                row.notes = notes
                row.updated_at = now
            await s.commit()
            await s.refresh(row)
            return _row_to_ctx(row)

    async def delete(self, *, org_id: str, annotation_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(HumanLabelAnnotationRow, annotation_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def list_for_org(
        self, *, org_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with self._sf() as s:
            stmt = (
                select(HumanLabelAnnotationRow)
                .where(HumanLabelAnnotationRow.org_id == org_id)
                .order_by(HumanLabelAnnotationRow.updated_at.desc())
                .limit(min(max(1, limit), 500))
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "traceId": r.trace_id,
                    "projectId": r.project_id,
                    "expectedResponse": r.expected_response,
                    "humanVerdict": r.human_verdict,
                    "notes": r.notes,
                    "updatedAt": r.updated_at.isoformat(),
                }
                for r in rows
            ]

    async def batch_get_for_traces(
        self, *, org_id: str, trace_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not trace_ids:
            return {}
        async with self._sf() as s:
            stmt = select(HumanLabelAnnotationRow).where(
                HumanLabelAnnotationRow.org_id == org_id,
                HumanLabelAnnotationRow.trace_id.in_(list(dict.fromkeys(trace_ids))),
            )
            rows = (await s.execute(stmt)).scalars().all()
            return {r.trace_id: _row_to_ctx(r) for r in rows}
