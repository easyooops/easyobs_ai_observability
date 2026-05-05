"""Service-scoped ingest token management.

- ``GET    /v1/services/{service_id}/tokens``         — list (preview-only).
- ``POST   /v1/services/{service_id}/tokens``         — issue; secret returned ONCE.
- ``DELETE /v1/services/{service_id}/tokens/{id}``    — revoke.

Authorization is delegated to the [security helpers](../security.py):
read access requires SA or an approved member of the service's org with
either PO role or an explicit assignment; write access requires SA or PO.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from easyobs.api.security import ServiceReadAccess, ServiceWriteAccess

router = APIRouter(prefix="/v1/services", tags=["tokens"])


class TokenOut(BaseModel):
    id: int
    serviceId: str
    label: str
    preview: str
    createdAt: str
    lastUsedAt: str | None
    revoked: bool


class CreateTokenIn(BaseModel):
    label: str = Field(default="", max_length=128)


class CreateTokenOut(BaseModel):
    token: TokenOut
    secret: str = Field(description="Plaintext secret — shown exactly once.")


def _to_out(meta) -> TokenOut:
    return TokenOut(
        id=meta.id,
        serviceId=meta.service_id,
        label=meta.label,
        preview=meta.preview,
        createdAt=meta.created_at.isoformat(),
        lastUsedAt=meta.last_used_at.isoformat() if meta.last_used_at else None,
        revoked=meta.revoked,
    )


@router.get("/{service_id}/tokens", response_model=list[TokenOut])
async def list_tokens(
    request: Request, access: ServiceReadAccess
) -> list[TokenOut]:
    _caller, service_id = access
    svc = request.app.state.tokens
    return [_to_out(m) for m in await svc.list_for_service(service_id)]


@router.post("/{service_id}/tokens", response_model=CreateTokenOut, status_code=201)
async def create_token(
    body: CreateTokenIn, request: Request, access: ServiceWriteAccess
) -> CreateTokenOut:
    caller, service_id = access
    svc = request.app.state.tokens
    issued = await svc.create(
        service_id=service_id, label=body.label, actor_user_id=caller.user_id
    )
    return CreateTokenOut(token=_to_out(issued.meta), secret=issued.secret)


@router.delete("/{service_id}/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: int, request: Request, access: ServiceWriteAccess
) -> None:
    _caller, service_id = access
    svc = request.app.state.tokens
    existing = await svc.get(token_id)
    if existing is None or existing.service_id != service_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="token not found")
    ok = await svc.revoke(token_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="token already revoked")
