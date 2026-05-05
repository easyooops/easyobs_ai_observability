from __future__ import annotations

from fastapi import APIRouter

from easyobs.api.deps import CallerScope, QuerySvc

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(query: QuerySvc, scope: CallerScope):
    return await query.dashboard_summary(service_ids=scope)
