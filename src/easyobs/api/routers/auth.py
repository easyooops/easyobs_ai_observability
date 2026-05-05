"""Authentication endpoints: sign-up, sign-in, org selection, current-user info.

The first sign-up bootstraps the super admin and the ``administrator`` org;
subsequent sign-ups must include an ``orgId`` + ``requestedRole`` and land
in ``pending`` status until an SA/PO approves them.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from easyobs.api.security import CurrentUser
from easyobs.logging_setup import user_id_var
from easyobs.services.auth import JwtCodec
from easyobs.services.directory import (
    DirectoryService,
    MembershipDTO,
    OrganizationDTO,
    UserDTO,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
_log = logging.getLogger("easyobs.auth")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    id: str
    email: str
    displayName: str
    isSuperAdmin: bool


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    isDefault: bool


class MembershipOut(BaseModel):
    orgId: str
    orgName: str
    role: Literal["PO", "DV"]
    status: Literal["pending", "approved", "rejected"]
    requestedAt: str
    approvedAt: str | None


class SignUpIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)
    displayName: str = Field(default="", max_length=120)
    orgId: str | None = None
    requestedRole: Literal["PO", "DV"] | None = None


class SignUpOut(BaseModel):
    token: str
    user: UserOut
    isFirstUser: bool
    membership: MembershipOut | None
    currentOrg: OrganizationOut | None
    # See ``MeOut`` for the meaning of these flags.
    isPlatformAdmin: bool = False
    isPlatformMember: bool = False


class SignInIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class SignInOut(BaseModel):
    token: str
    user: UserOut
    approvedMemberships: list[MembershipOut]
    currentOrg: OrganizationOut | None
    isPlatformAdmin: bool = False
    isPlatformMember: bool = False


class SelectOrgIn(BaseModel):
    orgId: str


class SelectOrgOut(BaseModel):
    token: str
    currentOrg: OrganizationOut
    role: Literal["SA", "PO", "DV"]
    accessibleServiceIds: list[str]
    isPlatformAdmin: bool = False
    isPlatformMember: bool = False


class MeOut(BaseModel):
    user: UserOut
    currentOrg: OrganizationOut | None
    role: Literal["SA", "PO", "DV"] | None
    approvedMemberships: list[MembershipOut]
    pendingMemberships: list[MembershipOut]
    accessibleServiceIds: list[str] | None
    # ``isPlatformAdmin`` is true for the bootstrapped SA *and* for any
    # approved PO of the default ``administrator`` org. Such accounts can
    # manage every other org from the UI.
    # ``isPlatformMember`` is true for SA, platform admins and any approved
    # member of the default org (including DV) — they get cross-org read.
    isPlatformAdmin: bool = False
    isPlatformMember: bool = False


class PublicOrganizationsOut(BaseModel):
    organizations: list[OrganizationOut]
    hasUsers: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_out(u: UserDTO) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        displayName=u.display_name,
        isSuperAdmin=u.is_super_admin,
    )


def _org_out(o: OrganizationDTO) -> OrganizationOut:
    return OrganizationOut(
        id=o.id, name=o.name, slug=o.slug, isDefault=o.is_default
    )


def _membership_out(m: MembershipDTO, org_name: str) -> MembershipOut:
    return MembershipOut(
        orgId=m.org_id,
        orgName=org_name,
        role=m.role,
        status=m.status,
        requestedAt=m.requested_at.isoformat(),
        approvedAt=m.approved_at.isoformat() if m.approved_at else None,
    )


def _directory(request: Request) -> DirectoryService:
    return request.app.state.directory


def _codec(request: Request) -> JwtCodec:
    return request.app.state.jwt


async def _build_membership_outs(
    directory: DirectoryService,
    memberships: list[MembershipDTO],
) -> list[MembershipOut]:
    if not memberships:
        return []
    org_ids = {m.org_id for m in memberships}
    orgs = {o.id: o for o in await directory.list_organizations() if o.id in org_ids}
    return [
        _membership_out(m, orgs[m.org_id].name if m.org_id in orgs else m.org_id)
        for m in memberships
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/public-organizations", response_model=PublicOrganizationsOut)
async def public_organizations(request: Request) -> PublicOrganizationsOut:
    directory = _directory(request)
    has_users = await directory.has_any_user()
    orgs = await directory.list_organizations_for_signup() if has_users else []
    return PublicOrganizationsOut(
        organizations=[_org_out(o) for o in orgs],
        hasUsers=has_users,
    )


@router.post("/sign-up", response_model=SignUpOut, status_code=201)
async def sign_up(body: SignUpIn, request: Request) -> SignUpOut:
    directory = _directory(request)
    try:
        result = await directory.sign_up(
            email=body.email,
            password=body.password,
            display_name=body.displayName,
            org_id=body.orgId,
            requested_role=body.requestedRole,
        )
    except ValueError as e:
        _log.warning(
            "sign-up rejected: %s",
            e,
            extra={"email": body.email, "org_id": body.orgId},
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception:
        _log.exception(
            "sign-up failed unexpectedly",
            extra={"email": body.email, "org_id": body.orgId},
        )
        raise

    user_id_var.set(result.user.id)
    _log.info(
        "user signed up",
        extra={
            "user_id": result.user.id,
            "email": result.user.email,
            "is_super_admin": result.user.is_super_admin,
            "is_first_user": result.is_first_user,
            "org_id": result.membership.org_id if result.membership else None,
            "requested_role": result.membership.role if result.membership else None,
        },
    )

    current_org_id: str | None = None
    current_org_dto: OrganizationDTO | None = None
    membership_out: MembershipOut | None = None
    if result.is_first_user and result.membership is not None:
        current_org_id = result.membership.org_id
        current_org_dto = await directory.get_organization(current_org_id)
        membership_out = _membership_out(
            result.membership,
            current_org_dto.name if current_org_dto else current_org_id,
        )
    elif result.membership is not None:
        org = await directory.get_organization(result.membership.org_id)
        membership_out = _membership_out(
            result.membership, org.name if org else result.membership.org_id
        )

    token = _codec(request).issue(
        user_id=result.user.id,
        is_super_admin=result.user.is_super_admin,
        current_org=current_org_id,
    )
    is_platform_admin = result.user.is_super_admin or await directory.is_platform_admin(
        result.user.id
    )
    is_platform_member = is_platform_admin or await directory.is_platform_member(
        result.user.id
    )
    return SignUpOut(
        token=token,
        user=_user_out(result.user),
        isFirstUser=result.is_first_user,
        membership=membership_out,
        currentOrg=_org_out(current_org_dto) if current_org_dto else None,
        isPlatformAdmin=is_platform_admin,
        isPlatformMember=is_platform_member,
    )


@router.post("/sign-in", response_model=SignInOut)
async def sign_in(body: SignInIn, request: Request) -> SignInOut:
    directory = _directory(request)
    try:
        result = await directory.sign_in(email=body.email, password=body.password)
    except ValueError as e:
        _log.warning("sign-in failed: %s", e, extra={"email": body.email})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    user_id_var.set(result.user.id)
    _log.info("user signed in", extra={"user_id": result.user.id, "email": result.user.email})

    approved = result.approved_memberships
    is_platform_admin = result.user.is_super_admin or await directory.is_platform_admin(
        result.user.id
    )
    is_platform_member = is_platform_admin or await directory.is_platform_member(
        result.user.id
    )
    current_org_id: str | None = None
    current_org_dto: OrganizationDTO | None = None
    if result.user.is_super_admin or is_platform_admin:
        # SA / platform admin defaults to the administrator org for nicer
        # UX; they can still switch from the org picker.
        default = await directory.ensure_default_org()
        current_org_id = default.id
        current_org_dto = default
    elif len(approved) == 1:
        current_org_id = approved[0].org_id
        current_org_dto = await directory.get_organization(current_org_id)

    token = _codec(request).issue(
        user_id=result.user.id,
        is_super_admin=result.user.is_super_admin,
        current_org=current_org_id,
    )
    membership_outs = await _build_membership_outs(directory, approved)
    return SignInOut(
        token=token,
        user=_user_out(result.user),
        approvedMemberships=membership_outs,
        currentOrg=_org_out(current_org_dto) if current_org_dto else None,
        isPlatformAdmin=is_platform_admin,
        isPlatformMember=is_platform_member,
    )


@router.post("/select-org", response_model=SelectOrgOut)
async def select_org(
    body: SelectOrgIn, request: Request, caller: CurrentUser
) -> SelectOrgOut:
    directory = _directory(request)
    org = await directory.get_organization(body.orgId)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="organization not found")
    if caller.is_super_admin:
        role: str = "SA"
    elif caller.is_platform_admin:
        # admin/PO behaves as PO of any org; UI gates SA-only menus on the
        # ``isPlatformAdmin`` flag, not on this label.
        role = "PO"
    elif caller.is_platform_member:
        # admin/DV gets read access into any org; expose role as DV.
        role = "DV"
    else:
        membership = await directory.membership_for(
            user_id=caller.user_id, org_id=org.id
        )
        if membership is None or membership.status != "approved":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="no approved membership")
        role = membership.role
    accessible = await directory.accessible_service_ids(
        user_id=caller.user_id,
        is_super_admin=caller.is_super_admin,
        org_id=org.id,
        is_platform_admin=caller.is_platform_admin,
        is_platform_member=caller.is_platform_member,
    )
    token = _codec(request).issue(
        user_id=caller.user_id,
        is_super_admin=caller.is_super_admin,
        current_org=org.id,
    )
    return SelectOrgOut(
        token=token,
        currentOrg=_org_out(org),
        role=role,  # type: ignore[arg-type]
        accessibleServiceIds=accessible or [],
        isPlatformAdmin=caller.is_platform_admin,
        isPlatformMember=caller.is_platform_member,
    )


@router.get("/me", response_model=MeOut)
async def me(request: Request, caller: CurrentUser) -> MeOut:
    directory = _directory(request)
    user = await directory.get_user(caller.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    all_memberships = await directory.all_memberships(caller.user_id)
    approved = [m for m in all_memberships if m.status == "approved"]
    pending = [m for m in all_memberships if m.status == "pending"]

    current_org_dto: OrganizationDTO | None = None
    role: str | None = None
    if caller.current_org:
        current_org_dto = await directory.get_organization(caller.current_org)
        if current_org_dto is not None:
            if caller.is_super_admin:
                role = "SA"
            else:
                m = next(
                    (
                        m
                        for m in approved
                        if m.org_id == current_org_dto.id
                    ),
                    None,
                )
                if m is not None:
                    role = m.role
                elif caller.is_platform_admin:
                    role = "PO"
                elif caller.is_platform_member:
                    role = "DV"
    elif caller.is_super_admin:
        role = "SA"

    accessible = await directory.accessible_service_ids(
        user_id=caller.user_id,
        is_super_admin=caller.is_super_admin,
        org_id=current_org_dto.id if current_org_dto else None,
        is_platform_admin=caller.is_platform_admin,
        is_platform_member=caller.is_platform_member,
    )
    return MeOut(
        user=_user_out(user),
        currentOrg=_org_out(current_org_dto) if current_org_dto else None,
        role=role,  # type: ignore[arg-type]
        approvedMemberships=await _build_membership_outs(directory, approved),
        pendingMemberships=await _build_membership_outs(directory, pending),
        accessibleServiceIds=accessible,
        isPlatformAdmin=caller.is_platform_admin,
        isPlatformMember=caller.is_platform_member,
    )


class RequestAccessIn(BaseModel):
    orgId: str
    requestedRole: Literal["PO", "DV"]


@router.post("/request-access", response_model=MembershipOut, status_code=201)
async def request_access(
    body: RequestAccessIn, request: Request, caller: CurrentUser
) -> MembershipOut:
    """Allow an authenticated user without an approved membership in the
    target org to request one (used from the ``/pending`` page)."""
    directory = _directory(request)
    try:
        membership = await directory.request_membership(
            user_id=caller.user_id, org_id=body.orgId, requested_role=body.requestedRole
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    org = await directory.get_organization(body.orgId)
    return _membership_out(membership, org.name if org else body.orgId)
