"""FastAPI dependencies that decode the session JWT and translate it into a
caller context the routers can branch on.

Three flavours are exported:

- ``CurrentUser`` — user must be authenticated; their ``current_org`` (if any)
  is exposed but not validated.
- ``RequireSA``   — caller must be super admin.
- ``RequireOrgAdmin(org_id)`` / ``RequireOrgMember(org_id)`` — caller must
  hold the appropriate role in ``org_id`` (SA always passes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from easyobs.logging_setup import org_id_var, user_id_var
from easyobs.services.auth import JwtCodec, TokenClaims
from easyobs.services.directory import (
    DirectoryService,
    MembershipDTO,
    ROLE_PO,
    STATUS_APPROVED,
)

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CallerContext:
    user_id: str
    is_super_admin: bool
    current_org: str | None
    email: str
    display_name: str
    # ``is_platform_admin`` / ``is_platform_member`` are derived per-request
    # from the caller's membership in the default ``administrator`` org.
    # SA implies both flags. ``platform_admin`` (=admin/PO) gets effective-SA
    # privileges across every org; ``platform_member`` (=admin/DV) gets read
    # access across every org but cannot mutate other orgs.
    is_platform_admin: bool = False
    is_platform_member: bool = False


def _codec(request: Request) -> JwtCodec:
    return request.app.state.jwt


def _directory(request: Request) -> DirectoryService:
    return request.app.state.directory


async def _decode_caller(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CallerContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    claims: TokenClaims | None = _codec(request).decode(creds.credentials)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token")
    directory = _directory(request)
    user = await directory.get_user(claims.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="account no longer exists")
    user_id_var.set(user.id)
    if claims.current_org:
        org_id_var.set(claims.current_org)
    if user.is_super_admin:
        platform_admin = True
        platform_member = True
    else:
        platform_admin = await directory.is_platform_admin(user.id)
        platform_member = (
            platform_admin or await directory.is_platform_member(user.id)
        )
    return CallerContext(
        user_id=user.id,
        is_super_admin=user.is_super_admin,
        current_org=claims.current_org,
        email=user.email,
        display_name=user.display_name,
        is_platform_admin=platform_admin,
        is_platform_member=platform_member,
    )


CurrentUser = Annotated[CallerContext, Depends(_decode_caller)]


async def _require_sa(caller: CurrentUser) -> CallerContext:
    if not caller.is_super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="super admin required")
    return caller


RequireSA = Annotated[CallerContext, Depends(_require_sa)]


async def get_membership(
    caller: CurrentUser,
    request: Request,
    org_id: str,
) -> MembershipDTO | None:
    if caller.is_super_admin or caller.is_platform_admin:
        return None
    return await _directory(request).membership_for(user_id=caller.user_id, org_id=org_id)


async def _require_platform_admin(caller: CurrentUser) -> CallerContext:
    if not (caller.is_super_admin or caller.is_platform_admin):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="platform admin required"
        )
    return caller


RequirePlatformAdmin = Annotated[CallerContext, Depends(_require_platform_admin)]


def require_org_admin(org_id_param: str = "org_id"):
    """Allow SA, platform admin (=admin/PO), or PO of the specified org.
    Used for management routes (member approvals, service CRUD)."""

    async def _dep(
        request: Request,
        caller: CurrentUser,
    ) -> CallerContext:
        org_id = request.path_params.get(org_id_param)
        if not org_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing org_id")
        if caller.is_super_admin or caller.is_platform_admin:
            return caller
        membership = await _directory(request).membership_for(
            user_id=caller.user_id, org_id=org_id
        )
        if (
            membership is None
            or membership.status != STATUS_APPROVED
            or membership.role != ROLE_PO
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="organization admin required")
        return caller

    return _dep


def require_org_member(org_id_param: str = "org_id"):
    """Allow SA, platform member (=any approved admin/* member), or any
    approved member of the org. Used for read routes (member list,
    service list)."""

    async def _dep(
        request: Request,
        caller: CurrentUser,
    ) -> CallerContext:
        org_id = request.path_params.get(org_id_param)
        if not org_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing org_id")
        if caller.is_super_admin or caller.is_platform_member:
            return caller
        membership = await _directory(request).membership_for(
            user_id=caller.user_id, org_id=org_id
        )
        if membership is None or membership.status != STATUS_APPROVED:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="organization access required")
        return caller

    return _dep


async def require_service_access(
    request: Request,
    caller: CurrentUser,
    write: bool = False,
) -> tuple[CallerContext, str]:
    """Confirm the caller can read (or, when ``write=True``, manage) the
    service identified by ``service_id`` in the path. Returns the caller
    plus the resolved service id for downstream use.

    Platform-admin (admin/PO) bypasses both read and write checks across
    every org; platform-member (admin/DV) bypasses *read* only — write
    still requires PO of the target org or platform-admin / SA.
    """
    service_id = request.path_params.get("service_id")
    if not service_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="missing service_id")
    directory: DirectoryService = _directory(request)
    service = await directory.get_service(service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="service not found")
    if caller.is_super_admin or caller.is_platform_admin:
        return caller, service_id
    if not write and caller.is_platform_member:
        return caller, service_id
    membership = await directory.membership_for(
        user_id=caller.user_id, org_id=service.org_id
    )
    if membership is None or membership.status != STATUS_APPROVED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="service access denied")
    if write and membership.role != ROLE_PO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="service write requires PO/SA")
    if not write and membership.role != ROLE_PO:
        # DV must be explicitly assigned to the service to read it.
        assigned = await directory.list_service_assignments_for_user(
            user_id=caller.user_id, org_id=service.org_id
        )
        if service_id not in assigned:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="service access denied")
    return caller, service_id


ServiceReadAccess = Annotated[
    tuple[CallerContext, str], Depends(require_service_access)
]


async def _require_service_write(
    request: Request,
    caller: CurrentUser,
):
    return await require_service_access(request, caller, write=True)


ServiceWriteAccess = Annotated[
    tuple[CallerContext, str], Depends(_require_service_write)
]
