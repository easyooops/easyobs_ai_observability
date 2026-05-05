"""Organization, membership, and service management routes.

Routes are gated by [security helpers](../security.py). Throughout this
module ``platform admin`` means SA *or* an approved PO of the default
``administrator`` org, and ``platform member`` adds approved DV of the
default org. Platform admin grants full read+write on every org;
platform member grants cross-org read.

- ``GET /v1/organizations``                 — platform member: all; others: own approved orgs.
- ``POST /v1/organizations``                — platform admin only.
- ``GET /v1/organizations/{org}/members``   — platform admin or org PO.
- ``PATCH /v1/organizations/{org}/members/{user}`` — platform admin or org PO; status / role.
- ``GET /v1/organizations/{org}/members/{user}/services`` — platform admin or org PO.
- ``PUT  /v1/organizations/{org}/members/{user}/services`` — platform admin or org PO.
- ``GET /v1/organizations/{org}/services``  — platform member or any approved member.
- ``POST /v1/organizations/{org}/services`` — platform admin or org PO.
- ``DELETE /v1/organizations/{org}/services/{service}`` — platform admin or org PO.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from easyobs.api.security import (
    CallerContext,
    CurrentUser,
    RequirePlatformAdmin,
    require_org_admin,
    require_org_member,
)
from easyobs.services.directory import (
    DirectoryService,
    MembershipDTO,
    OrganizationDTO,
    ServiceDTO,
)

router = APIRouter(prefix="/v1/organizations", tags=["organizations"])


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    isDefault: bool
    createdAt: str


class CreateOrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MembershipOut(BaseModel):
    userId: str
    orgId: str
    role: Literal["PO", "DV"]
    status: Literal["pending", "approved", "rejected"]
    requestedAt: str
    approvedAt: str | None
    userEmail: str
    userDisplayName: str
    # When true the row represents the bootstrapped super admin and the
    # client must render it read-only — the server also rejects PATCH /
    # DELETE on these memberships, but exposing the flag avoids unnecessary
    # 400 round-trips.
    userIsSuperAdmin: bool = False


class UpdateMembershipIn(BaseModel):
    status: Literal["pending", "approved", "rejected"] | None = None
    role: Literal["PO", "DV"] | None = None


class ServiceAssignmentsIn(BaseModel):
    serviceIds: list[str]


class ServiceOut(BaseModel):
    id: str
    orgId: str
    name: str
    slug: str
    description: str
    createdAt: str


class CreateServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


def _directory(request: Request) -> DirectoryService:
    return request.app.state.directory


def _org_out(o: OrganizationDTO) -> OrganizationOut:
    return OrganizationOut(
        id=o.id,
        name=o.name,
        slug=o.slug,
        isDefault=o.is_default,
        createdAt=o.created_at.isoformat(),
    )


def _membership_out(m: MembershipDTO) -> MembershipOut:
    return MembershipOut(
        userId=m.user_id,
        orgId=m.org_id,
        role=m.role,
        status=m.status,
        requestedAt=m.requested_at.isoformat(),
        approvedAt=m.approved_at.isoformat() if m.approved_at else None,
        userEmail=m.user_email,
        userDisplayName=m.user_display_name,
        userIsSuperAdmin=m.user_is_super_admin,
    )


def _service_out(s: ServiceDTO) -> ServiceOut:
    return ServiceOut(
        id=s.id,
        orgId=s.org_id,
        name=s.name,
        slug=s.slug,
        description=s.description,
        createdAt=s.created_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


@router.get("", response_model=list[OrganizationOut])
async def list_organizations(
    request: Request, caller: CurrentUser
) -> list[OrganizationOut]:
    directory = _directory(request)
    if (
        caller.is_super_admin
        or caller.is_platform_admin
        or caller.is_platform_member
    ):
        # SA + members of the administrator org see every other tenant.
        orgs = await directory.list_organizations()
    else:
        memberships = await directory.approved_memberships(caller.user_id)
        approved_ids = {m.org_id for m in memberships}
        orgs = [o for o in await directory.list_organizations() if o.id in approved_ids]
    return [_org_out(o) for o in orgs]


@router.post("", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: CreateOrgIn, request: Request, _admin: RequirePlatformAdmin
) -> OrganizationOut:
    try:
        org = await _directory(request).create_organization(name=body.name)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _org_out(org)


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/members",
    response_model=list[MembershipOut],
    dependencies=[Depends(require_org_admin())],
)
async def list_members(org_id: str, request: Request) -> list[MembershipOut]:
    members = await _directory(request).list_org_members(org_id)
    return [_membership_out(m) for m in members]


@router.patch(
    "/{org_id}/members/{user_id}",
    response_model=MembershipOut,
)
async def update_member(
    org_id: str,
    user_id: str,
    body: UpdateMembershipIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> MembershipOut:
    directory = _directory(request)
    try:
        if body.status is not None:
            await directory.update_membership_status(
                org_id=org_id,
                user_id=user_id,
                status=body.status,
                actor_user_id=caller.user_id,
            )
        if body.role is not None:
            await directory.update_membership_role(
                org_id=org_id, user_id=user_id, role=body.role
            )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    refreshed = await directory.membership_for(user_id=user_id, org_id=org_id)
    if refreshed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="membership not found")
    return _membership_out(refreshed)


@router.delete(
    "/{org_id}/members/{user_id}",
    status_code=204,
    dependencies=[Depends(require_org_admin())],
)
async def remove_member(org_id: str, user_id: str, request: Request) -> None:
    await _directory(request).remove_membership(org_id=org_id, user_id=user_id)


@router.get(
    "/{org_id}/members/{user_id}/services",
    response_model=list[str],
    dependencies=[Depends(require_org_admin())],
)
async def get_member_services(
    org_id: str, user_id: str, request: Request
) -> list[str]:
    return await _directory(request).list_service_assignments_for_user(
        user_id=user_id, org_id=org_id
    )


@router.put(
    "/{org_id}/members/{user_id}/services",
    response_model=list[str],
)
async def set_member_services(
    org_id: str,
    user_id: str,
    body: ServiceAssignmentsIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> list[str]:
    return await _directory(request).set_user_service_assignments(
        user_id=user_id,
        org_id=org_id,
        service_ids=body.serviceIds,
        actor_user_id=caller.user_id,
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/services",
    response_model=list[ServiceOut],
    dependencies=[Depends(require_org_member())],
)
async def list_services(org_id: str, request: Request) -> list[ServiceOut]:
    services = await _directory(request).list_services(org_id=org_id)
    return [_service_out(s) for s in services]


@router.post(
    "/{org_id}/services",
    response_model=ServiceOut,
    status_code=201,
)
async def create_service(
    org_id: str,
    body: CreateServiceIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> ServiceOut:
    try:
        svc = await _directory(request).create_service(
            org_id=org_id,
            name=body.name,
            description=body.description,
            actor_user_id=caller.user_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _service_out(svc)


@router.delete(
    "/{org_id}/services/{service_id}",
    status_code=204,
    dependencies=[Depends(require_org_admin())],
)
async def delete_service(org_id: str, service_id: str, request: Request) -> None:
    directory = _directory(request)
    svc = await directory.get_service(service_id)
    if svc is None or svc.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="service not found")
    await directory.delete_service(service_id)
