from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any


def _attr_string(attrs: list[dict[str, Any]] | None, key: str) -> str:
    if not attrs:
        return ""
    for a in attrs:
        if a.get("key") != key:
            continue
        v = a.get("value") or {}
        if "stringValue" in v:
            return str(v["stringValue"])
    return ""


def _hex_from_otlp_field(val: str | None) -> str:
    if not val:
        return ""
    s = val.strip()
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) in (16, 32):
        return s.lower()
    try:
        raw = base64.b64decode(s, validate=False)
        return raw.hex()
    except Exception:
        return s


def _ns_from_unix_nano(obj: dict[str, Any], key: str) -> int | None:
    v = obj.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _summarise(
    *,
    trace_id: str,
    starts: list[int],
    ends: list[int],
    statuses: list[str],
    root_candidates: list[tuple[int, int, str]],
    service_names: list[str],
    span_count: int,
) -> dict[str, Any]:
    """Collapse the per-trace per-span lists into the index summary row.

    Pure helper — kept private so the JSON and proto flatteners share the
    exact same rules for ``status`` priority (ERROR > OK > UNSET) and root
    pick (least-deep then earliest start).
    """
    min_start = min(starts) if starts else 0
    max_end = max(ends) if ends else None
    root_name = ""
    if root_candidates:
        root_candidates.sort(key=lambda x: (x[0], x[1]))
        root_name = root_candidates[0][2]

    worst = "UNSET"
    if any(s == "ERROR" for s in statuses):
        worst = "ERROR"
    elif any(s == "OK" for s in statuses):
        worst = "OK"

    started_at = datetime.fromtimestamp(min_start / 1e9, tz=timezone.utc)
    ended_at = (
        datetime.fromtimestamp(max_end / 1e9, tz=timezone.utc) if max_end is not None else None
    )

    return {
        "trace_id": trace_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "root_name": root_name,
        "status": worst,
        "service_name": service_names[0] if service_names else "",
        "span_count": span_count,
    }


def flatten_from_dict(
    body: dict[str, Any],
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Map an OTLP/JSON ``ExportTraceServiceRequest`` to one
    ``(span_lines, summary)`` tuple **per distinct trace_id** in the export.

    Why per trace_id:
        OpenTelemetry's standard ``BatchSpanProcessor`` (Python, JS, Java,
        Go, .NET, …) groups spans by export window — *not* by trace — so a
        single OTLP request very commonly carries spans from several
        different traces. EasyObs stores one blob batch per trace_id, so we
        bucket here and let ``TraceIngestService.ingest`` write each bucket
        separately. This is a hard requirement for any non-Python OTel SDK
        (we used to reject multi-trace batches, which broke the JS exporter
        the moment two requests overlapped).
    """
    resource_spans = body.get("resourceSpans") or body.get("resource_spans") or []

    # trace_id -> aggregator
    buckets: dict[str, dict[str, Any]] = {}

    for rs in resource_spans:
        res = rs.get("resource") or {}
        res_attrs = res.get("attributes") or []
        svc = _attr_string(res_attrs, "service.name") or "unknown"

        scopes = rs.get("scopeSpans") or rs.get("scope_spans") or []
        for scope in scopes:
            for sp in scope.get("spans") or []:
                tid = _hex_from_otlp_field(sp.get("traceId") or sp.get("trace_id"))
                sid = _hex_from_otlp_field(sp.get("spanId") or sp.get("span_id"))
                psid = _hex_from_otlp_field(sp.get("parentSpanId") or sp.get("parent_span_id"))
                if not tid or not sid:
                    continue

                start_ns = _ns_from_unix_nano(sp, "startTimeUnixNano") or _ns_from_unix_nano(
                    sp, "start_time_unix_nano"
                )
                end_ns = _ns_from_unix_nano(sp, "endTimeUnixNano") or _ns_from_unix_nano(
                    sp, "end_time_unix_nano"
                )

                st = (sp.get("status") or {}).get("code", "STATUS_CODE_UNSET")
                if isinstance(st, int):
                    st = {0: "UNSET", 1: "OK", 2: "ERROR"}.get(st, "UNSET")
                else:
                    st = str(st).replace("STATUS_CODE_", "")

                name = str(sp.get("name") or "")
                attrs = sp.get("attributes") or []
                events = sp.get("events") or []

                bucket = buckets.setdefault(
                    tid,
                    {
                        "lines": [],
                        "starts": [],
                        "ends": [],
                        "statuses": [],
                        "root_candidates": [],
                        "service_names": [],
                    },
                )
                if start_ns is not None:
                    bucket["starts"].append(start_ns)
                if end_ns is not None:
                    bucket["ends"].append(end_ns)
                bucket["statuses"].append(st)
                depth = 0 if not psid or all(c == "0" for c in psid) else 1
                if start_ns is not None:
                    bucket["root_candidates"].append((depth, start_ns, name))
                bucket["service_names"].append(svc)
                bucket["lines"].append(
                    {
                        "traceId": tid,
                        "spanId": sid,
                        "parentSpanId": psid,
                        "name": name,
                        "serviceName": svc,
                        "startTimeUnixNano": start_ns,
                        "endTimeUnixNano": end_ns,
                        "status": st,
                        "attributes": attrs,
                        "events": events,
                    }
                )

    if not buckets:
        raise ValueError("no spans")

    out: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for tid, b in buckets.items():
        summary = _summarise(
            trace_id=tid,
            starts=b["starts"],
            ends=b["ends"],
            statuses=b["statuses"],
            root_candidates=b["root_candidates"],
            service_names=b["service_names"],
            span_count=len(b["lines"]),
        )
        out.append((b["lines"], summary))
    return out
