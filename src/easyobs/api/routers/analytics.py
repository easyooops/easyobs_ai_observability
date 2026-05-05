from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request

from easyobs.api.deps import CallerScope
from easyobs.services.analytics import AnalyticsService

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


def _svc(request: Request) -> AnalyticsService:
    return request.app.state.analytics


@router.get("/overview")
async def overview(
    request: Request,
    scope: CallerScope,
    window_hours: Annotated[int | None, Query(ge=1, le=168)] = 24,
    buckets: Annotated[int, Query(ge=6, le=96)] = 24,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
):
    svc = _svc(request)
    return await svc.overview(
        service_ids=scope,
        window_hours=window_hours,
        bucket_count=buckets,
        from_ts=from_ts,
        to_ts=to_ts,
    )


@router.get("/sessions")
async def sessions(
    request: Request,
    scope: CallerScope,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    window_hours: Annotated[int | None, Query(ge=1, le=168)] = None,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
):
    svc = _svc(request)
    rows = await svc.sessions(
        service_ids=scope,
        limit=limit,
        window_hours=window_hours,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {"items": rows}


@router.get("/spans")
async def spans(
    request: Request,
    scope: CallerScope,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    window_hours: Annotated[int | None, Query(ge=1, le=168)] = None,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
):
    svc = _svc(request)
    rows = await svc.spans(
        service_ids=scope,
        limit=limit,
        window_hours=window_hours,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {"items": rows}
