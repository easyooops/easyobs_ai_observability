"""Connectivity probes for the Settings > Storage UI.

Each ``probe_*`` returns ``(ok, message)``. They never raise — every probe
catches its own exceptions and reports a short, human-readable reason that
the UI surfaces verbatim. The cloud SDKs are imported lazily so the API
keeps booting on machines that didn't ``pip install`` the optional extras.

These probes only validate connectivity (auth + reachability) — they do
*not* read or write user data. We deliberately keep the side-effect surface
small (a single ``HEAD``/``list-with-limit-1`` call) so a stray Test click
never costs the operator real money or pollutes the bucket.
"""

from __future__ import annotations

import os
from pathlib import Path

from easyobs.services.app_settings import BlobConfig, CatalogConfig


# Public helper for the missing-dependency hint we surface to the UI.
def _missing(pkg: str, install: str) -> str:
    return (
        f"missing optional dependency '{pkg}'. Install on the API host with: "
        f"`pip install {install}` and restart."
    )


# ---------------------------------------------------------------------------
# Blob probes
# ---------------------------------------------------------------------------


def probe_blob(cfg: BlobConfig) -> tuple[bool, str]:
    p = cfg.provider
    if p == "local":
        return _probe_local(cfg)
    if p == "s3":
        return _probe_s3(cfg)
    if p == "azure":
        return _probe_azure(cfg)
    if p == "gcs":
        return _probe_gcs(cfg)
    return False, f"unknown provider '{p}'"


def _probe_local(cfg: BlobConfig) -> tuple[bool, str]:
    raw = (cfg.path or "").strip() or "./data/blobs"
    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"cannot create directory '{path}': {e}"
    # Probe with a tmp file write/delete so we catch read-only mounts.
    probe = path / ".easyobs-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return False, f"cannot write to '{path}': {e}"
    return True, f"local path is writable ({path.resolve()})"


def _probe_s3(cfg: BlobConfig) -> tuple[bool, str]:
    if not cfg.bucket:
        return False, "bucket is required"
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-not-found]
    except ImportError:
        return False, _missing("boto3", "boto3")
    kwargs: dict[str, object] = {}
    if cfg.region:
        kwargs["region_name"] = cfg.region
    # Credentials are optional. When omitted, boto3 uses its standard
    # credential chain (env vars → ~/.aws/credentials → IAM role / IRSA).
    if cfg.s3_access_key_id and cfg.s3_secret_access_key:
        kwargs["aws_access_key_id"] = cfg.s3_access_key_id
        kwargs["aws_secret_access_key"] = cfg.s3_secret_access_key
    try:
        s3 = boto3.client("s3", **kwargs)  # type: ignore[arg-type]
        s3.head_bucket(Bucket=cfg.bucket)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        msg = e.response.get("Error", {}).get("Message", str(e))
        return False, f"S3 {code}: {msg}"
    except BotoCoreError as e:
        return False, f"S3 connection failed: {e}"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"S3 connection failed: {e}"
    return True, f"bucket '{cfg.bucket}' is reachable"


def _probe_azure(cfg: BlobConfig) -> tuple[bool, str]:
    container = cfg.azure_container or cfg.bucket
    if not cfg.azure_account_name:
        return False, "azure_account_name is required"
    if not container:
        return False, "azure_container (or bucket) is required"
    try:
        from azure.core.exceptions import AzureError, HttpResponseError  # type: ignore[import-not-found]
        from azure.storage.blob import BlobServiceClient  # type: ignore[import-not-found]
    except ImportError:
        return False, _missing("azure-storage-blob", "azure-storage-blob")
    account_url = f"https://{cfg.azure_account_name}.blob.core.windows.net"
    try:
        if cfg.azure_account_key:
            svc = BlobServiceClient(account_url=account_url, credential=cfg.azure_account_key)
        else:
            try:
                from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
            except ImportError:
                return False, _missing("azure-identity", "azure-identity")
            svc = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        container_client = svc.get_container_client(container)
        container_client.get_container_properties()
    except HttpResponseError as e:
        return False, f"Azure {e.status_code}: {e.reason or e.message}"
    except AzureError as e:
        return False, f"Azure connection failed: {e}"
    except Exception as e:  # pragma: no cover
        return False, f"Azure connection failed: {e}"
    return True, f"container '{container}' is reachable"


def _probe_gcs(cfg: BlobConfig) -> tuple[bool, str]:
    if not cfg.bucket:
        return False, "bucket is required"
    try:
        from google.api_core import exceptions as gex  # type: ignore[import-not-found]
        from google.cloud import storage  # type: ignore[import-not-found]
    except ImportError:
        return False, _missing("google-cloud-storage", "google-cloud-storage")
    try:
        if cfg.gcs_service_account_json:
            try:
                from google.oauth2 import service_account  # type: ignore[import-not-found]
            except ImportError:
                return False, _missing("google-auth", "google-auth")
            import json as _json

            info = _json.loads(cfg.gcs_service_account_json)
            creds = service_account.Credentials.from_service_account_info(info)
            client = storage.Client(credentials=creds, project=info.get("project_id"))
        else:
            client = storage.Client()
        bucket = client.lookup_bucket(cfg.bucket)
        if bucket is None:
            return False, f"bucket '{cfg.bucket}' not found or not accessible"
    except gex.GoogleAPICallError as e:
        return False, f"GCS {e.code}: {e.message}"
    except Exception as e:  # pragma: no cover
        return False, f"GCS connection failed: {e}"
    return True, f"bucket '{cfg.bucket}' is reachable"


# ---------------------------------------------------------------------------
# Catalog probes
# ---------------------------------------------------------------------------


def probe_catalog(cfg: CatalogConfig) -> tuple[bool, str]:
    if cfg.provider == "sqlite":
        return _probe_sqlite(cfg)
    if cfg.provider == "postgres":
        return _probe_postgres(cfg)
    return False, f"unknown provider '{cfg.provider}'"


def _probe_sqlite(cfg: CatalogConfig) -> tuple[bool, str]:
    raw = (cfg.sqlite_path or "").strip() or "./data/catalog.sqlite3"
    path = Path(raw)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"cannot create directory '{parent}': {e}"
    if path.exists() and not os.access(path, os.W_OK):
        return False, f"file '{path}' is not writable"
    return True, f"sqlite path is usable ({path.resolve()})"


def _probe_postgres(cfg: CatalogConfig) -> tuple[bool, str]:
    if not (cfg.pg_host and cfg.pg_database and cfg.pg_user):
        return False, "host / database / user are required"
    try:
        import asyncpg  # type: ignore[import-not-found]
    except ImportError:
        return False, _missing("asyncpg", "asyncpg")

    import asyncio

    async def _try() -> tuple[bool, str]:
        kwargs: dict[str, object] = {
            "host": cfg.pg_host,
            "port": cfg.pg_port or 5432,
            "user": cfg.pg_user,
            "password": cfg.pg_password or None,
            "database": cfg.pg_database,
        }
        # asyncpg's ``ssl`` is bool|str|SSLContext. Accept the common modes.
        if cfg.pg_sslmode and cfg.pg_sslmode not in ("disable", "allow"):
            kwargs["ssl"] = True
        try:
            conn = await asyncio.wait_for(asyncpg.connect(**kwargs), timeout=8.0)  # type: ignore[arg-type]
        except asyncio.TimeoutError:
            return False, "connection timed out after 8s"
        except Exception as e:
            return False, f"postgres connection failed: {e}"
        try:
            v = await conn.fetchval("SELECT version()")
        finally:
            await conn.close()
        return True, f"connected · {v}"

    try:
        return asyncio.run(_try())
    except RuntimeError:
        # We're already inside a running event loop (FastAPI). Fall back to a
        # threadpool-isolated runner.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _try())
            return future.result()
