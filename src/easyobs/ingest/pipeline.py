from __future__ import annotations

from typing import Any

from easyobs.ingest.flatten_json import flatten_from_dict
from easyobs.ingest.flatten_proto import flatten_from_proto_bytes


def flatten_otlp_payload(
    body: dict[str, Any] | bytes, *, content_type: str | None
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Normalize an OTLP/JSON or OTLP/protobuf body into one
    ``(span_lines, summary)`` tuple per distinct trace_id in the export.

    The list shape (rather than a single tuple) is what makes EasyObs's
    ingest endpoint compatible with stock OpenTelemetry SDKs in any
    language — their ``BatchSpanProcessor`` groups by export window, not
    trace, so a single request commonly carries spans from many traces.
    """
    if isinstance(body, dict):
        return flatten_from_dict(body)
    ct = (content_type or "").lower()
    if "json" in ct:
        raise ValueError("JSON ingest must pass a decoded dict body, not raw bytes with json CT")
    if "protobuf" in ct or "octet-stream" in ct or ct == "":
        return flatten_from_proto_bytes(bytes(body))
    raise ValueError(f"unsupported content-type: {content_type!r}")
