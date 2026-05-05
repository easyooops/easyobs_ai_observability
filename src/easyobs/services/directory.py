"""User, organization, membership, and service domain operations.

This is a single service object so transactional operations (e.g. "first
sign-up = create user + create administrator org + auto-approve PO membership")
can share an async session.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import (
    IngestTokenRow,
    MembershipRow,
    OrganizationRow,
    ServiceAssignmentRow,
    ServiceRow,
    UserRow,
)
from easyobs.services.auth import hash_password, verify_password

DEFAULT_ORG_NAME = "administrator"
DEFAULT_ORG_SLUG = "administrator"
ROLE_PO = "PO"
ROLE_DV = "DV"
ROLES = {ROLE_PO, ROLE_DV}
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "x"


# ---------------------------------------------------------------------------
# Public read DTOs (kept JSON-friendly so routers can serialise directly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserDTO:
    id: str
    email: str
    display_name: str
    is_super_admin: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrganizationDTO:
    id: str
    name: str
    slug: str
    is_default: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MembershipDTO:
    user_id: str
    org_id: str
    role: str
    status: str
    requested_at: datetime
    approved_at: datetime | None
    user_email: str
    user_display_name: str
    # When the underlying user is the bootstrapped super admin we surface
    # this on every membership row so the UI (and other API consumers) can
    # render the row read-only — SA permissions and presence in the default
    # org must never be editable through the regular membership endpoints.
    user_is_super_admin: bool = False


@dataclass(frozen=True, slots=True)
class ServiceDTO:
    id: str
    org_id: str
    name: str
    slug: str
    description: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SignUpResult:
    user: UserDTO
    is_first_user: bool
    membership: MembershipDTO | None


@dataclass(frozen=True, slots=True)
class SignInResult:
    user: UserDTO
    approved_memberships: list[MembershipDTO]


@dataclass(frozen=True, slots=True)
class ServiceContext:
    """Resolved from an OTLP bearer token at ingest time."""

    service_id: str
    org_id: str


class DirectoryService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Bootstrap helpers
    # ------------------------------------------------------------------

    async def has_any_user(self) -> bool:
        async with self._sf() as s:
            row = (await s.execute(select(UserRow.id).limit(1))).scalar_one_or_none()
            return row is not None

    async def ensure_default_org(self) -> OrganizationDTO:
        """Idempotently create the ``administrator`` org used as the SA's
        default tenant. Returns the existing row when present."""
        async with self._sf() as s:
            row = (
                await s.execute(select(OrganizationRow).where(OrganizationRow.is_default.is_(True)))
            ).scalar_one_or_none()
            if row is None:
                row = OrganizationRow(
                    id=_new_id(),
                    name=DEFAULT_ORG_NAME,
                    slug=DEFAULT_ORG_SLUG,
                    is_default=True,
                    created_at=_now(),
                )
                s.add(row)
                await s.commit()
                await s.refresh(row)
            return _org_dto(row)

    # ------------------------------------------------------------------
    # User lifecycle
    # ------------------------------------------------------------------

    async def sign_up(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        org_id: str | None,
        requested_role: str | None,
    ) -> SignUpResult:
        """Create a user. The very first user in the system becomes SA and is
        auto-joined to the default organization with role PO (auto-approved).
        Subsequent users must pick an org + role; their membership is created
        as ``pending`` and awaits SA/PO approval."""
        email_norm = email.strip().lower()
        if not email_norm or "@" not in email_norm:
            raise ValueError("invalid email")
        if len(password) < 8:
            raise ValueError("password too short (min 8 chars)")
        async with self._sf() as s:
            existing = (
                await s.execute(select(UserRow).where(UserRow.email == email_norm))
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError("email already registered")

            is_first = (
                await s.execute(select(UserRow.id).limit(1))
            ).scalar_one_or_none() is None

            user = UserRow(
                id=_new_id(),
                email=email_norm,
                password_hash=hash_password(password),
                display_name=display_name.strip()[:120],
                is_super_admin=is_first,
                created_at=_now(),
            )
            s.add(user)

            membership_dto: MembershipDTO | None = None
            if is_first:
                # Bootstrap path: first ever user. Create the default org if
                # missing and attach an auto-approved PO membership purely so
                # the UI can show consistent "current org" context — SA's
                # access doesn't actually depend on this row.
                default_org = (
                    await s.execute(
                        select(OrganizationRow).where(OrganizationRow.is_default.is_(True))
                    )
                ).scalar_one_or_none()
                if default_org is None:
                    default_org = OrganizationRow(
                        id=_new_id(),
                        name=DEFAULT_ORG_NAME,
                        slug=DEFAULT_ORG_SLUG,
                        is_default=True,
                        created_at=_now(),
                    )
                    s.add(default_org)
                membership = MembershipRow(
                    user_id=user.id,
                    org_id=default_org.id,
                    role=ROLE_PO,
                    status=STATUS_APPROVED,
                    requested_at=_now(),
                    approved_at=_now(),
                    approved_by=user.id,
                )
                s.add(membership)
                await s.commit()
                await s.refresh(user)
                membership_dto = MembershipDTO(
                    user_id=user.id,
                    org_id=default_org.id,
                    role=ROLE_PO,
                    status=STATUS_APPROVED,
                    requested_at=membership.requested_at,
                    approved_at=membership.approved_at,
                    user_email=user.email,
                    user_display_name=user.display_name,
                )
            else:
                if not org_id:
                    raise ValueError("organization is required")
                if requested_role not in ROLES:
                    raise ValueError("requested_role must be PO or DV")
                org = await s.get(OrganizationRow, org_id)
                if org is None:
                    raise ValueError("organization not found")
                membership = MembershipRow(
                    user_id=user.id,
                    org_id=org_id,
                    role=requested_role,
                    status=STATUS_PENDING,
                    requested_at=_now(),
                )
                s.add(membership)
                await s.commit()
                await s.refresh(user)
                membership_dto = MembershipDTO(
                    user_id=user.id,
                    org_id=org_id,
                    role=requested_role,
                    status=STATUS_PENDING,
                    requested_at=membership.requested_at,
                    approved_at=None,
                    user_email=user.email,
                    user_display_name=user.display_name,
                )
            return SignUpResult(
                user=_user_dto(user),
                is_first_user=is_first,
                membership=membership_dto,
            )

    async def sign_in(self, *, email: str, password: str) -> SignInResult:
        async with self._sf() as s:
            user = (
                await s.execute(select(UserRow).where(UserRow.email == email.strip().lower()))
            ).scalar_one_or_none()
            if user is None or not verify_password(password, user.password_hash):
                raise ValueError("invalid credentials")
            memberships = await self._approved_memberships_for(s, user.id)
            return SignInResult(user=_user_dto(user), approved_memberships=memberships)

    async def get_user(self, user_id: str) -> UserDTO | None:
        async with self._sf() as s:
            row = await s.get(UserRow, user_id)
            return _user_dto(row) if row else None

    # ------------------------------------------------------------------
    # Membership / approval
    # ------------------------------------------------------------------

    async def request_membership(
        self, *, user_id: str, org_id: str, requested_role: str
    ) -> MembershipDTO:
        if requested_role not in ROLES:
            raise ValueError("requested_role must be PO or DV")
        async with self._sf() as s:
            user = await s.get(UserRow, user_id)
            org = await s.get(OrganizationRow, org_id)
            if user is None or org is None:
                raise ValueError("user or organization not found")
            existing = await s.get(MembershipRow, (user_id, org_id))
            if existing is not None and existing.status in {STATUS_PENDING, STATUS_APPROVED}:
                raise ValueError("membership already exists")
            if existing is not None:
                existing.role = requested_role
                existing.status = STATUS_PENDING
                existing.requested_at = _now()
                existing.approved_at = None
                existing.approved_by = None
                row = existing
            else:
                row = MembershipRow(
                    user_id=user_id,
                    org_id=org_id,
                    role=requested_role,
                    status=STATUS_PENDING,
                    requested_at=_now(),
                )
                s.add(row)
            await s.commit()
            return _membership_dto(row, user)

    async def list_org_members(self, org_id: str) -> list[MembershipDTO]:
        async with self._sf() as s:
            stmt = (
                select(MembershipRow, UserRow)
                .join(UserRow, MembershipRow.user_id == UserRow.id)
                .where(MembershipRow.org_id == org_id)
                .order_by(MembershipRow.requested_at.desc())
            )
            return [
                _membership_dto(m, u)
                for m, u in (await s.execute(stmt)).all()
            ]

    async def update_membership_status(
        self,
        *,
        org_id: str,
        user_id: str,
        status: str,
        actor_user_id: str,
    ) -> MembershipDTO:
        if status not in {STATUS_APPROVED, STATUS_REJECTED, STATUS_PENDING}:
            raise ValueError("invalid status")
        async with self._sf() as s:
            row = await s.get(MembershipRow, (user_id, org_id))
            if row is None:
                raise ValueError("membership not found")
            user = await s.get(UserRow, user_id)
            if user is not None and user.is_super_admin:
                raise ValueError("super admin membership cannot be modified")
            row.status = status
            if status == STATUS_APPROVED:
                row.approved_at = _now()
                row.approved_by = actor_user_id
            else:
                row.approved_at = None
                row.approved_by = None
            await s.commit()
            return _membership_dto(row, user)

    async def update_membership_role(
        self, *, org_id: str, user_id: str, role: str
    ) -> MembershipDTO:
        if role not in ROLES:
            raise ValueError("role must be PO or DV")
        async with self._sf() as s:
            row = await s.get(MembershipRow, (user_id, org_id))
            if row is None:
                raise ValueError("membership not found")
            user = await s.get(UserRow, user_id)
            if user is not None and user.is_super_admin:
                raise ValueError("super admin role cannot be changed")
            row.role = role
            await s.commit()
            return _membership_dto(row, user)

    async def remove_membership(self, *, org_id: str, user_id: str) -> None:
        async with self._sf() as s:
            row = await s.get(MembershipRow, (user_id, org_id))
            if row is None:
                return
            user = await s.get(UserRow, user_id)
            if user is not None and user.is_super_admin:
                raise ValueError("super admin membership cannot be removed")
            await s.delete(row)
            await s.commit()

    async def membership_for(self, *, user_id: str, org_id: str) -> MembershipDTO | None:
        async with self._sf() as s:
            row = await s.get(MembershipRow, (user_id, org_id))
            if row is None:
                return None
            user = await s.get(UserRow, user_id)
            return _membership_dto(row, user)

    async def approved_memberships(self, user_id: str) -> list[MembershipDTO]:
        async with self._sf() as s:
            return await self._approved_memberships_for(s, user_id)

    # ------------------------------------------------------------------
    # Platform-wide elevation via the default ``administrator`` org
    # ------------------------------------------------------------------
    #
    # The ``administrator`` org is the platform-admin tenant: any approved
    # member there is granted visibility across every other org, and
    # approved POs there can manage any org/service/token regardless of
    # whether they hold an explicit membership in the target org. This is
    # in addition to the bootstrapped super-admin flag — SA continues to
    # bypass every check unconditionally.

    async def is_platform_admin(self, user_id: str) -> bool:
        """True when the user is an approved PO of the default org.

        Combined with the ``is_super_admin`` flag at the call site this
        gives "effective SA" semantics for management routes.
        """
        async with self._sf() as s:
            stmt = (
                select(MembershipRow)
                .join(
                    OrganizationRow,
                    OrganizationRow.id == MembershipRow.org_id,
                )
                .where(
                    MembershipRow.user_id == user_id,
                    MembershipRow.status == STATUS_APPROVED,
                    MembershipRow.role == ROLE_PO,
                    OrganizationRow.is_default.is_(True),
                )
            )
            return (await s.execute(stmt)).first() is not None

    async def is_platform_member(self, user_id: str) -> bool:
        """True when the user has any approved membership in the default
        org. Used for cross-org *read* access (DV in administrator can see
        traces from any other org but cannot mutate them)."""
        async with self._sf() as s:
            stmt = (
                select(MembershipRow)
                .join(
                    OrganizationRow,
                    OrganizationRow.id == MembershipRow.org_id,
                )
                .where(
                    MembershipRow.user_id == user_id,
                    MembershipRow.status == STATUS_APPROVED,
                    OrganizationRow.is_default.is_(True),
                )
            )
            return (await s.execute(stmt)).first() is not None

    async def all_memberships(self, user_id: str) -> list[MembershipDTO]:
        async with self._sf() as s:
            stmt = (
                select(MembershipRow, UserRow)
                .join(UserRow, MembershipRow.user_id == UserRow.id)
                .where(MembershipRow.user_id == user_id)
                .order_by(MembershipRow.requested_at.desc())
            )
            return [_membership_dto(m, u) for m, u in (await s.execute(stmt)).all()]

    async def _approved_memberships_for(self, s, user_id: str) -> list[MembershipDTO]:
        stmt = (
            select(MembershipRow, UserRow)
            .join(UserRow, MembershipRow.user_id == UserRow.id)
            .where(MembershipRow.user_id == user_id, MembershipRow.status == STATUS_APPROVED)
            .order_by(MembershipRow.approved_at.desc().nullslast())
        )
        return [_membership_dto(m, u) for m, u in (await s.execute(stmt)).all()]

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    async def list_organizations(self) -> list[OrganizationDTO]:
        async with self._sf() as s:
            rows = (
                await s.execute(select(OrganizationRow).order_by(OrganizationRow.created_at))
            ).scalars().all()
            return [_org_dto(r) for r in rows]

    async def list_organizations_for_signup(self) -> list[OrganizationDTO]:
        # Same as ``list_organizations`` today, but kept as a separate seam
        # so we can later hide private orgs from the public sign-up picker.
        return await self.list_organizations()

    async def get_organization(self, org_id: str) -> OrganizationDTO | None:
        async with self._sf() as s:
            row = await s.get(OrganizationRow, org_id)
            return _org_dto(row) if row else None

    async def create_organization(self, name: str) -> OrganizationDTO:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        slug = _slugify(name)
        async with self._sf() as s:
            collision = (
                await s.execute(
                    select(OrganizationRow).where(
                        (OrganizationRow.name == name) | (OrganizationRow.slug == slug)
                    )
                )
            ).scalar_one_or_none()
            if collision is not None:
                raise ValueError("organization name or slug already exists")
            row = OrganizationRow(
                id=_new_id(),
                name=name,
                slug=slug,
                is_default=False,
                created_at=_now(),
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _org_dto(row)

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------

    async def list_services(self, *, org_id: str) -> list[ServiceDTO]:
        async with self._sf() as s:
            rows = (
                await s.execute(
                    select(ServiceRow)
                    .where(ServiceRow.org_id == org_id)
                    .order_by(ServiceRow.created_at)
                )
            ).scalars().all()
            return [_service_dto(r) for r in rows]

    async def list_all_services(self) -> list[ServiceDTO]:
        async with self._sf() as s:
            rows = (
                await s.execute(select(ServiceRow).order_by(ServiceRow.created_at))
            ).scalars().all()
            return [_service_dto(r) for r in rows]

    async def get_service(self, service_id: str) -> ServiceDTO | None:
        async with self._sf() as s:
            row = await s.get(ServiceRow, service_id)
            return _service_dto(row) if row else None

    async def create_service(
        self, *, org_id: str, name: str, description: str, actor_user_id: str
    ) -> ServiceDTO:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        slug = _slugify(name)
        async with self._sf() as s:
            org = await s.get(OrganizationRow, org_id)
            if org is None:
                raise ValueError("organization not found")
            collision = (
                await s.execute(
                    select(ServiceRow).where(
                        ServiceRow.org_id == org_id, ServiceRow.slug == slug
                    )
                )
            ).scalar_one_or_none()
            if collision is not None:
                raise ValueError("service slug already exists in this organization")
            row = ServiceRow(
                id=_new_id(),
                org_id=org_id,
                name=name,
                slug=slug,
                description=description.strip(),
                created_at=_now(),
                created_by=actor_user_id,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _service_dto(row)

    async def delete_service(self, service_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(ServiceRow, service_id)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # ------------------------------------------------------------------
    # Service assignments (DV scope)
    # ------------------------------------------------------------------

    async def list_service_assignments_for_user(
        self, *, user_id: str, org_id: str
    ) -> list[str]:
        async with self._sf() as s:
            stmt = (
                select(ServiceAssignmentRow.service_id)
                .join(ServiceRow, ServiceRow.id == ServiceAssignmentRow.service_id)
                .where(
                    ServiceAssignmentRow.user_id == user_id,
                    ServiceRow.org_id == org_id,
                )
            )
            return [r for r in (await s.execute(stmt)).scalars().all()]

    async def set_user_service_assignments(
        self,
        *,
        user_id: str,
        org_id: str,
        service_ids: Sequence[str],
        actor_user_id: str,
    ) -> list[str]:
        async with self._sf() as s:
            services = (
                await s.execute(
                    select(ServiceRow).where(
                        ServiceRow.id.in_(list(service_ids) or [""]),
                        ServiceRow.org_id == org_id,
                    )
                )
            ).scalars().all()
            kept = {svc.id for svc in services}
            existing = (
                await s.execute(
                    select(ServiceAssignmentRow)
                    .join(ServiceRow, ServiceRow.id == ServiceAssignmentRow.service_id)
                    .where(
                        ServiceAssignmentRow.user_id == user_id,
                        ServiceRow.org_id == org_id,
                    )
                )
            ).scalars().all()
            existing_ids = {a.service_id for a in existing}
            for a in existing:
                if a.service_id not in kept:
                    await s.delete(a)
            for sid in kept - existing_ids:
                s.add(
                    ServiceAssignmentRow(
                        user_id=user_id,
                        service_id=sid,
                        assigned_at=_now(),
                        assigned_by=actor_user_id,
                    )
                )
            await s.commit()
            return sorted(kept)

    async def accessible_service_ids(
        self,
        *,
        user_id: str,
        is_super_admin: bool,
        org_id: str | None,
        is_platform_admin: bool = False,
        is_platform_member: bool = False,
    ) -> list[str] | None:
        """Return the list of service IDs the user can read.

        Privilege ladder (highest first):

        - ``is_super_admin``      → bootstrapped SA, bypass everything.
        - ``is_platform_admin``   → approved PO of the default org → treated
          as SA for read scope (sees every service in every org).
        - ``is_platform_member``  → approved DV of the default org → can read
          every service in every org (cannot mutate; that is enforced
          separately at the route level).
        - approved PO of ``org_id`` → all services in ``org_id``.
        - approved DV of ``org_id`` → services explicitly assigned to them.

        Return value semantics:

        - ``None`` → no filter (only used for SA / platform-admin without an
          active org context).
        - ``[]`` → user is authenticated but has no readable services (e.g.
          pending approval, or DV with no assignments yet).
        """
        elevated = is_super_admin or is_platform_admin or is_platform_member
        if elevated and not org_id:
            return None
        if not org_id:
            return []
        async with self._sf() as s:
            if elevated:
                rows = (
                    await s.execute(
                        select(ServiceRow.id).where(ServiceRow.org_id == org_id)
                    )
                ).scalars().all()
                return list(rows)
            membership = await s.get(MembershipRow, (user_id, org_id))
            if membership is None or membership.status != STATUS_APPROVED:
                return []
            if membership.role == ROLE_PO:
                rows = (
                    await s.execute(
                        select(ServiceRow.id).where(ServiceRow.org_id == org_id)
                    )
                ).scalars().all()
                return list(rows)
            stmt = (
                select(ServiceAssignmentRow.service_id)
                .join(ServiceRow, ServiceRow.id == ServiceAssignmentRow.service_id)
                .where(
                    ServiceAssignmentRow.user_id == user_id,
                    ServiceRow.org_id == org_id,
                )
            )
            return list((await s.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Ingest tokens (service-scoped)
    # ------------------------------------------------------------------

    async def resolve_ingest_token(self, secret: str) -> ServiceContext | None:
        """Look up an ingest token by its plaintext value, returning the
        owning ``(service_id, org_id)`` so callers can store ingested data
        with full tenancy context."""
        from easyobs.services.tokens import _hash  # local import to avoid cycle

        h = _hash(secret)
        async with self._sf() as s:
            row = (
                await s.execute(
                    select(IngestTokenRow, ServiceRow)
                    .join(ServiceRow, ServiceRow.id == IngestTokenRow.service_id)
                    .where(IngestTokenRow.secret_hash == h)
                )
            ).first()
            if row is None:
                return None
            token, service = row
            if token.revoked_at is not None:
                return None
            token.last_used_at = _now()
            await s.commit()
            return ServiceContext(service_id=service.id, org_id=service.org_id)


# ---------------------------------------------------------------------------
# DTO builders
# ---------------------------------------------------------------------------


def _user_dto(row: UserRow) -> UserDTO:
    return UserDTO(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        is_super_admin=row.is_super_admin,
        created_at=row.created_at,
    )


def _org_dto(row: OrganizationRow) -> OrganizationDTO:
    return OrganizationDTO(
        id=row.id,
        name=row.name,
        slug=row.slug,
        is_default=row.is_default,
        created_at=row.created_at,
    )


def _membership_dto(row: MembershipRow, user: UserRow | None) -> MembershipDTO:
    return MembershipDTO(
        user_id=row.user_id,
        org_id=row.org_id,
        role=row.role,
        status=row.status,
        requested_at=row.requested_at,
        approved_at=row.approved_at,
        user_email=user.email if user else "",
        user_display_name=user.display_name if user else "",
        user_is_super_admin=bool(user.is_super_admin) if user else False,
    )


def _service_dto(row: ServiceRow) -> ServiceDTO:
    return ServiceDTO(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        slug=row.slug,
        description=row.description,
        created_at=row.created_at,
    )
