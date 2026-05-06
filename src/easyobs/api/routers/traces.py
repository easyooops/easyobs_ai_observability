from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from easyobs.api.deps import CallerScope, QuerySvc

router = APIRouter(prefix="/v1/traces", tags=["traces"])
# (touch: nudge uvicorn --reload after the multi-file edit so the router
# below — including the new session_id / with_llm query params — gets
# picked up. Removing this comment is harmless.)


@router.get("")
async def traces_list(
    query: QuerySvc,
    scope: CallerScope,
    limit: Annotated[int, Query(le=2000)] = 50,
    window_hours: Annotated[int | None, Query(ge=1, le=168)] = None,
    from_ts: Annotated[datetime | None, Query()] = None,
    to_ts: Annotated[datetime | None, Query()] = None,
    session_id: Annotated[
        str | None,
        Query(
            description=(
                "Restrict to traces whose spans carry the given o.sess "
                "attribute. Drives the Sessions → Traces drill-down."
            ),
        ),
    ] = None,
    user_id: Annotated[
        str | None,
        Query(
            description=(
                "Restrict to traces whose spans carry the given o.user "
                "attribute. Drives the Users → Traces drill-down."
            ),
        ),
    ] = None,
    with_llm: Annotated[
        bool,
        Query(
            description=(
                "When true, each row is enriched with tokens / price / "
                "model / session derived from span blobs. Adds one blob "
                "read per trace; safe up to a few hundred rows."
            ),
        ),
    ] = False,
):
    items = await query.list_traces(
        service_ids=scope,
        limit=limit,
        window_hours=window_hours,
        from_ts=from_ts,
        to_ts=to_ts,
        session_id=session_id,
        user_id=user_id,
        with_llm=with_llm,
    )
    return {"items": items}


@router.get("/{trace_id}")
async def trace_detail(trace_id: str, query: QuerySvc, scope: CallerScope):
    detail = await query.trace_detail(trace_id, allowed_service_ids=scope)
    if detail is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return detail
