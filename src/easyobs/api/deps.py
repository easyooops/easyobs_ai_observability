from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from easyobs.api.security import CurrentUser
from easyobs.services.directory import DirectoryService
from easyobs.services.trace_ingest import TraceIngestService
from easyobs.services.trace_query import TraceQueryService


def get_ingest(request: Request) -> TraceIngestService:
    return request.app.state.trace_ingest


def get_query(request: Request) -> TraceQueryService:
    return request.app.state.trace_query


def get_directory(request: Request) -> DirectoryService:
    return request.app.state.directory


IngestSvc = Annotated[TraceIngestService, Depends(get_ingest)]
QuerySvc = Annotated[TraceQueryService, Depends(get_query)]
DirectorySvc = Annotated[DirectoryService, Depends(get_directory)]


async def resolve_caller_scope(
    caller: CurrentUser,
    directory: DirectorySvc,
) -> list[str] | None:
    """Compute the catalog filter for the caller in their currently selected
    organization.

    - Returns ``None`` → "no scope filter" (catalog skips the WHERE clause).
      Only allowed for SA / platform admin / platform member without an
      active org context.
    - Returns ``[]`` → "no accessible services" — the caller has
      authenticated but has no rights yet (e.g. pending approval).
    """
    elevated = (
        caller.is_super_admin
        or caller.is_platform_admin
        or caller.is_platform_member
    )
    if not elevated and not caller.current_org:
        # Regular tenants must select an organization first via
        # /v1/auth/select-org after approval.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="select an organization first"
        )
    return await directory.accessible_service_ids(
        user_id=caller.user_id,
        is_super_admin=caller.is_super_admin,
        org_id=caller.current_org,
        is_platform_admin=caller.is_platform_admin,
        is_platform_member=caller.is_platform_member,
    )


CallerScope = Annotated[list[str] | None, Depends(resolve_caller_scope)]
