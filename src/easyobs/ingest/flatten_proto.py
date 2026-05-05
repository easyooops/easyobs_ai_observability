from __future__ import annotations

from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from easyobs.ingest.flatten_json import _summarise


def _anyvalue_to_jsonish(val: AnyValue) -> dict[str, Any]:
    which = val.WhichOneof("value")
    if which == "string_value":
        return {"stringValue": val.string_value}
    if which == "bool_value":
        return {"boolValue": val.bool_value}
    if which == "int_value":
        return {"intValue": val.int_value}
    if which == "double_value":
        return {"doubleValue": val.double_value}
    return {"stringValue": ""}


def _attrs_to_jsonish(attrs: list[KeyValue]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kv in attrs:
        out.append({"key": kv.key, "value": _anyvalue_to_jsonish(kv.value)})
    return out


def _service_name(rs) -> str:
    for kv in rs.resource.attributes:
        if kv.key == "service.name" and kv.value.HasField("string_value"):
            return kv.value.string_value
    return "unknown"


def _span_status_code(sp: Span) -> str:
    code = sp.status.code
    if code == Status.STATUS_CODE_ERROR:
        return "ERROR"
    if code == Status.STATUS_CODE_OK:
        return "OK"
    return "UNSET"


def _parent_hex(ps: bytes) -> str:
    if not ps or ps == b"\x00" * len(ps):
        return ""
    return ps.hex()


def flatten_from_proto_bytes(
    raw: bytes,
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Map an OTLP/protobuf ``ExportTraceServiceRequest`` to one
    ``(span_lines, summary)`` tuple per distinct trace_id.

    Mirrors the JSON flattener's bucketing semantics so the JS / Java / Go
    OTel exporters that batch multiple traces in one export work the same
    way regardless of wire format.
    """
    msg = ExportTraceServiceRequest()
    msg.ParseFromString(raw)

    buckets: dict[str, dict[str, Any]] = {}

    for rs in msg.resource_spans:
        svc = _service_name(rs)
        for ils in rs.scope_spans:
            for sp in ils.spans:
                tid = sp.trace_id.hex()
                sid = sp.span_id.hex()
                psid = _parent_hex(sp.parent_span_id)
                if not tid or not sid:
                    continue

                st = _span_status_code(sp)
                name = sp.name
                depth = 0 if not psid else 1

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
                bucket["starts"].append(sp.start_time_unix_nano)
                if sp.end_time_unix_nano:
                    bucket["ends"].append(sp.end_time_unix_nano)
                bucket["statuses"].append(st)
                bucket["root_candidates"].append((depth, sp.start_time_unix_nano, name))
                bucket["service_names"].append(svc)
                bucket["lines"].append(
                    {
                        "traceId": tid,
                        "spanId": sid,
                        "parentSpanId": psid,
                        "name": name,
                        "serviceName": svc,
                        "startTimeUnixNano": sp.start_time_unix_nano,
                        "endTimeUnixNano": sp.end_time_unix_nano or None,
                        "status": st,
                        "attributes": _attrs_to_jsonish(list(sp.attributes)),
                        "events": [],
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
