from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from easyobs.api.deps import IngestSvc
from easyobs.settings import Settings

_log = logging.getLogger("easyobs.ingest")

# Standard OTLP/HTTP path. The OpenTelemetry spec mandates that exporters
# append ``/v1/traces`` to the configured ``OTEL_EXPORTER_OTLP_ENDPOINT``
# base, so registering this alias lets any stock OTel SDK (JS, Java, Go,
# .NET, …) talk to EasyObs without overriding the per-signal URL — they
# only need to set the base URL and the Bearer token header.
_OTLP_STANDARD_PATH = "/v1/traces"


def build_otlp_router(settings: Settings) -> APIRouter:
    r = APIRouter(tags=["ingest"])

    async def _otlp_traces(
        request: Request,
        ingest: IngestSvc,
        authorization: Annotated[str | None, Header()] = None,
    ):
        if not authorization or not authorization.startswith("Bearer "):
            _log.warning("otlp ingest rejected: missing bearer")
            raise HTTPException(status_code=401, detail="missing ingest token")
        presented = authorization.removeprefix("Bearer ").strip()
        # Tokens are bound to a specific service; the directory looks them
        # up by hash and returns the owning ``(service_id, org_id)`` tuple.
        ctx = await request.app.state.directory.resolve_ingest_token(presented)
        if ctx is None:
            _log.warning(
                "otlp ingest rejected: token not recognised",
                extra={"token_prefix": presented[:8]},
            )
            raise HTTPException(status_code=401, detail="invalid ingest token")
        ct = request.headers.get("content-type")
        raw = await request.body()
        try:
            if raw and "json" in (ct or "").lower():
                payload: dict[str, Any] | bytes = json.loads(raw.decode("utf-8"))
            else:
                payload = raw
            written = await ingest.ingest(payload, ct, service_id=ctx.service_id)
        except json.JSONDecodeError as e:
            _log.exception("otlp payload not valid json")
            raise HTTPException(status_code=400, detail="invalid json") from e
        except ValueError as e:
            _log.warning("otlp payload rejected: %s", e)
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception:
            _log.exception(
                "otlp ingest failed",
                extra={"service_id": ctx.service_id, "org_id": ctx.org_id},
            )
            raise

        _log.info(
            "otlp ingested",
            extra={
                "service_id": ctx.service_id,
                "org_id": ctx.org_id,
                "bytes": len(raw),
                "content_type": ct,
                "traces_written": written,
                "path": request.url.path,
            },
        )
        return JSONResponse({"partialSuccess": {}}, status_code=200)

    # Primary path — kept for the bundled ``easyobs_agent`` Python SDK and
    # any operator that has hard-coded the historical URL.
    r.add_api_route(
        settings.otlp_http_path, _otlp_traces, methods=["POST"], name="otlp_traces"
    )

    # Standard OTel exporter path. Registered under a different name so
    # FastAPI doesn't reject the duplicate route. Skipped if the operator
    # has already pointed ``otlp_http_path`` here.
    if settings.otlp_http_path != _OTLP_STANDARD_PATH:
        r.add_api_route(
            _OTLP_STANDARD_PATH,
            _otlp_traces,
            methods=["POST"],
            name="otlp_traces_standard_alias",
        )

    return r
