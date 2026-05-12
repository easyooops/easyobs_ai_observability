"""Persisted storage settings (catalog + blob + retention).

The Settings UI lets a platform admin override the env-defined defaults at
runtime. We persist those overrides in a JSON file (``<data_dir>/app_settings.json``)
so the storage config — including the *catalog DB URL itself* — does not
depend on which catalog backend happens to be active. On next API boot
``http_app.lifespan`` reads the JSON and applies the override on top of the
env values (env wins for anything the UI hasn't explicitly set, so a
freshly cloned ``.env`` keeps working).

Design notes:

* Hot-swap of the active blob/catalog adapter is intentionally *not* done
  here. Switching backends mid-flight orphans existing data and complicates
  in-flight ingest, so the UI saves the override and surfaces a "Restart
  required" notice to the operator.
* Cloud blob writes (S3 / Azure Blob / GCS) are validated end-to-end by the
  test endpoints (real network call) but the actual ingest writer is still
  the local NDJSON store; cloud writers will be wired in a follow-up.
* Catalog Postgres swap *does* take effect after restart — SQLAlchemy handles
  it transparently as long as the URL is valid.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SETTINGS_KEY_STORAGE = "storage"

BlobProvider = Literal["local", "s3", "azure", "gcs", "hybrid"]
CatalogProvider = Literal["sqlite", "postgres"]


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass
class BlobConfig:
    """Where raw OTLP NDJSON batches are written."""

    provider: BlobProvider = "local"

    # local
    path: str = ""

    # s3 / azure / gcs share these
    bucket: str = ""
    prefix: str = ""
    region: str = ""

    # s3 — credentials are optional. When blank, boto3 falls back to its
    # default credential chain (env vars → ~/.aws/credentials → IAM role).
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # azure
    azure_account_name: str = ""
    azure_account_key: str = ""
    azure_container: str = ""

    # gcs
    gcs_service_account_json: str = ""

    # hybrid (local + s3)
    hot_retention_days: int = 7

    def public_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict with secrets masked, for the GET endpoint."""
        masked = asdict(self)
        for k in ("s3_secret_access_key", "azure_account_key", "gcs_service_account_json"):
            v = masked.get(k) or ""
            masked[k] = "••• set •••" if v else ""
        return masked


@dataclass
class CatalogConfig:
    """Metadata catalog (trace_index + auth tables)."""

    provider: CatalogProvider = "sqlite"

    # sqlite
    sqlite_path: str = ""

    # postgres
    pg_host: str = ""
    pg_port: int = 5432
    pg_database: str = ""
    pg_user: str = ""
    pg_password: str = ""
    pg_sslmode: str = "prefer"  # disable | allow | prefer | require | verify-full

    def public_dict(self) -> dict[str, Any]:
        masked = asdict(self)
        if masked.get("pg_password"):
            masked["pg_password"] = "••• set •••"
        return masked

    def to_async_url(self) -> str:
        """Build the SQLAlchemy async URL the engine should use."""
        if self.provider == "postgres":
            from urllib.parse import quote_plus

            user = quote_plus(self.pg_user or "")
            pw = quote_plus(self.pg_password or "")
            auth = f"{user}:{pw}@" if user else ""
            host = self.pg_host or "127.0.0.1"
            port = self.pg_port or 5432
            db = self.pg_database or "easyobs"
            return f"postgresql+asyncpg://{auth}{host}:{port}/{db}"
        # sqlite_path is a filesystem path; asyncio uses aiosqlite
        path = self.sqlite_path or "./data/catalog.sqlite3"
        return f"sqlite+aiosqlite:///{path}"


@dataclass
class RetentionConfig:
    """Trace data retention policy.

    Background note: EasyObs currently keeps every trace indefinitely. The
    fields below are persisted so the policy can be inspected/changed from
    the UI, but no automatic cleaner job is wired in yet — operators must run
    ``easyobs reset-data`` (or a custom script) to actually delete data.
    """

    enabled: bool = False
    days: int = 30

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StorageConfig:
    blob: BlobConfig = field(default_factory=BlobConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    def to_storable_json(self) -> str:
        return json.dumps(
            {
                "blob": asdict(self.blob),
                "catalog": asdict(self.catalog),
                "retention": asdict(self.retention),
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_storable_json(cls, raw: str) -> "StorageConfig":
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        return cls(
            blob=BlobConfig(**(data.get("blob") or {})),
            catalog=CatalogConfig(**(data.get("catalog") or {})),
            retention=RetentionConfig(**(data.get("retention") or {})),
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AppSettingsService:
    """File-backed key/value store for runtime-tunable platform settings.

    Storage lives at ``<data_dir>/app_settings.json``. We deliberately keep
    this off the catalog DB so an operator can flip the catalog backend
    without having to first manually copy the override row across DBs.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "app_settings.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_all(self, data: dict[str, Any]) -> None:
        # Atomic write so a crashed save can never leave a half-written file
        # that breaks the next boot.
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), prefix=".app_settings.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # Tighten POSIX file mode so cloud creds aren't world-readable. On
        # Windows os.chmod's permission bits are largely ignored by the OS
        # but the call is still safe.
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def get_storage_sync(self) -> StorageConfig:
        """Synchronous read for the boot path. Routers should use the async
        ``get_storage`` instead so future implementations can do real I/O."""
        data = self._read_all()
        raw = data.get(SETTINGS_KEY_STORAGE)
        if raw is None:
            return StorageConfig()
        return StorageConfig.from_storable_json(json.dumps(raw))

    async def get_storage(self) -> StorageConfig:
        return self.get_storage_sync()

    async def save_storage(
        self, cfg: StorageConfig, *, updated_by: str | None
    ) -> StorageConfig:
        data = self._read_all()
        data[SETTINGS_KEY_STORAGE] = json.loads(cfg.to_storable_json())
        data[f"{SETTINGS_KEY_STORAGE}.meta"] = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
        }
        self._write_all(data)
        return cfg
