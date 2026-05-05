"""Ingest-token lifecycle bound to a service: create, list, revoke, verify.

Design:
- The plaintext ``eobs_<24 chars>`` secret is **only ever returned once** (at
  creation). Storage uses ``sha256(secret).hexdigest()`` plus a short preview
  (``eobs_abcd••••wxyz``) so the UI can distinguish tokens without exposing them.
- Every token is bound to a ``service_id``. OTLP ingestion derives the
  service tenancy from the bearer the agent presents.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import IngestTokenRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _preview(secret: str) -> str:
    """``eobs_abcd••••wxyz`` style preview for UI display."""
    if len(secret) <= 12:
        return secret[:2] + "••••"
    return f"{secret[:8]}••••{secret[-4:]}"


@dataclass
class IngestToken:
    id: int
    service_id: str
    label: str
    preview: str
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool

    @classmethod
    def from_row(cls, row: IngestTokenRow) -> "IngestToken":
        return cls(
            id=row.id,
            service_id=row.service_id,
            label=row.label or "",
            preview=row.preview or "",
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            revoked=row.revoked_at is not None,
        )


@dataclass
class IssuedToken:
    """Only returned from ``create()`` — contains the plaintext secret once."""

    meta: IngestToken
    secret: str


class TokenService:
    """Persistence + creation/revocation of service-scoped ingest tokens."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list_for_service(self, service_id: str) -> list[IngestToken]:
        async with self._sf() as s:
            rows = (
                await s.execute(
                    select(IngestTokenRow)
                    .where(IngestTokenRow.service_id == service_id)
                    .order_by(IngestTokenRow.created_at.desc())
                )
            ).scalars().all()
            return [IngestToken.from_row(r) for r in rows]

    async def create(
        self, *, service_id: str, label: str = "", actor_user_id: str | None = None
    ) -> IssuedToken:
        secret = "eobs_" + secrets.token_urlsafe(18)
        row = IngestTokenRow(
            service_id=service_id,
            label=label.strip()[:128],
            secret_hash=_hash(secret),
            preview=_preview(secret),
            created_at=_now(),
            created_by=actor_user_id,
        )
        async with self._sf() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return IssuedToken(meta=IngestToken.from_row(row), secret=secret)

    async def get(self, token_id: int) -> IngestToken | None:
        async with self._sf() as s:
            row = await s.get(IngestTokenRow, token_id)
            return IngestToken.from_row(row) if row else None

    async def revoke(self, token_id: int) -> bool:
        async with self._sf() as s:
            row = await s.get(IngestTokenRow, token_id)
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = _now()
            await s.commit()
            return True
