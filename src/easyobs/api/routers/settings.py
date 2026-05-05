"""Platform-admin Settings router.

Currently exposes the **Storage** group (catalog DB + blob backend +
retention). Reads and mutations require platform-admin (SA or any PO of the
default ``administrator`` org). The probes ("Test connection") are also
restricted to platform-admin so a leaked DV token can't fingerprint the
operator's S3 bucket from outside.

The catalog/blob mutation does *not* hot-swap the active connection —
operators must restart the API to pick up the new config. The UI surfaces
this via a "Restart required" banner driven by the ``restartRequired`` flag
on the ``GET`` response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from easyobs.api.security import RequirePlatformAdmin
from easyobs.services.app_settings import (
    AppSettingsService,
    BlobConfig,
    CatalogConfig,
    RetentionConfig,
    StorageConfig,
)
from easyobs.services.storage_probe import probe_blob, probe_catalog


def _norm_path(p: str | None) -> str:
    """Normalise a filesystem path so a relative override like ``./data/blobs``
    compares equal to the absolute path the running server resolved at boot."""
    if not p:
        return ""
    try:
        return str(Path(p).expanduser().resolve())
    except OSError:
        return p


router = APIRouter(prefix="/v1/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Pydantic in/out schemas
# ---------------------------------------------------------------------------


class BlobIn(BaseModel):
    provider: Literal["local", "s3", "azure", "gcs"] = "local"
    path: str = ""
    bucket: str = ""
    prefix: str = ""
    region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    azure_account_name: str = ""
    azure_account_key: str = ""
    azure_container: str = ""
    gcs_service_account_json: str = ""

    def to_dc(self) -> BlobConfig:
        return BlobConfig(**self.model_dump())


class CatalogIn(BaseModel):
    provider: Literal["sqlite", "postgres"] = "sqlite"
    sqlite_path: str = ""
    pg_host: str = ""
    pg_port: int = Field(default=5432, ge=1, le=65535)
    pg_database: str = ""
    pg_user: str = ""
    pg_password: str = ""
    pg_sslmode: Literal["disable", "allow", "prefer", "require", "verify-full"] = "prefer"

    def to_dc(self) -> CatalogConfig:
        return CatalogConfig(**self.model_dump())


class RetentionIn(BaseModel):
    enabled: bool = False
    days: int = Field(default=30, ge=1, le=3650)

    def to_dc(self) -> RetentionConfig:
        return RetentionConfig(**self.model_dump())


class StorageIn(BaseModel):
    blob: BlobIn = Field(default_factory=BlobIn)
    catalog: CatalogIn = Field(default_factory=CatalogIn)
    retention: RetentionIn = Field(default_factory=RetentionIn)


class StorageOut(BaseModel):
    blob: dict[str, Any]
    catalog: dict[str, Any]
    retention: dict[str, Any]
    # Active values currently in effect on the running server (env defaults
    # plus any DB override that was loaded at boot). Helps the UI explain
    # the "saved override doesn't match active" case.
    active: dict[str, Any]
    restartRequired: bool


class TestResult(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(request: Request) -> AppSettingsService:
    svc: AppSettingsService | None = getattr(request.app.state, "app_settings", None)
    if svc is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="settings service not initialised"
        )
    return svc


def _active_dict(request: Request) -> dict[str, Any]:
    """Snapshot of what the running server is actually using right now."""
    settings = request.app.state.settings
    blob_root = getattr(request.app.state, "blob", None)
    blob_path = ""
    if blob_root is not None:
        try:
            blob_path = str(blob_root.root)
        except Exception:  # pragma: no cover - defensive
            blob_path = ""
    return {
        "blob": {
            "provider": "local",
            "path": blob_path or str(settings.blob_root),
        },
        "catalog": {
            "provider": "postgres" if settings.database_url.startswith("postgresql") else "sqlite",
            "url": settings.database_url,
        },
    }


def _is_dirty(saved: StorageConfig, active: dict[str, Any]) -> bool:
    """Return True when the saved override differs from the active config so
    the UI can show the "restart required" banner. Paths are normalised so a
    relative override like ``./data/blobs`` doesn't false-positive against
    the absolute path the server resolved at boot."""
    a_blob = active.get("blob", {})
    if saved.blob.provider != a_blob.get("provider"):
        return True
    if saved.blob.provider == "local":
        # Empty saved.path means "use whatever is active" → never dirty.
        if saved.blob.path and _norm_path(saved.blob.path) != _norm_path(a_blob.get("path")):
            return True
    else:
        return True  # any cloud blob = always pending until adapter wired

    a_cat = active.get("catalog", {})
    if saved.catalog.provider != a_cat.get("provider"):
        return True
    if saved.catalog.provider == "postgres":
        if saved.catalog.to_async_url() != a_cat.get("url"):
            return True
    else:
        if saved.catalog.sqlite_path:
            saved_url = saved.catalog.to_async_url()
            active_url = a_cat.get("url") or ""
            # SQLAlchemy URL paths are absolute; compare via Path normalisation
            # by stripping the scheme prefix.
            saved_p = _norm_path(saved_url.split("///", 1)[-1])
            active_p = _norm_path(active_url.split("///", 1)[-1])
            if saved_p != active_p:
                return True
    return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/storage", response_model=StorageOut)
async def get_storage(
    request: Request, _admin: RequirePlatformAdmin
) -> StorageOut:
    saved = await _service(request).get_storage()
    active = _active_dict(request)
    return StorageOut(
        blob=saved.blob.public_dict(),
        catalog=saved.catalog.public_dict(),
        retention=saved.retention.public_dict(),
        active=active,
        restartRequired=_is_dirty(saved, active),
    )


@router.put("/storage", response_model=StorageOut)
async def put_storage(
    request: Request, body: StorageIn, admin: RequirePlatformAdmin
) -> StorageOut:
    cfg = StorageConfig(
        blob=body.blob.to_dc(),
        catalog=body.catalog.to_dc(),
        retention=body.retention.to_dc(),
    )
    saved = await _service(request).save_storage(cfg, updated_by=admin.user_id)
    active = _active_dict(request)
    return StorageOut(
        blob=saved.blob.public_dict(),
        catalog=saved.catalog.public_dict(),
        retention=saved.retention.public_dict(),
        active=active,
        restartRequired=_is_dirty(saved, active),
    )


@router.post("/storage/blob/test", response_model=TestResult)
async def test_blob(
    body: BlobIn, _admin: RequirePlatformAdmin
) -> TestResult:
    ok, msg = probe_blob(body.to_dc())
    return TestResult(ok=ok, message=msg)


@router.post("/storage/catalog/test", response_model=TestResult)
async def test_catalog(
    body: CatalogIn, _admin: RequirePlatformAdmin
) -> TestResult:
    ok, msg = probe_catalog(body.to_dc())
    return TestResult(ok=ok, message=msg)
