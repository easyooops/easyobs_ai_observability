"""Parquet schema definition for trace span storage.

The schema mirrors the flattened OTLP span structure produced by
``flatten_json`` / ``flatten_proto`` with pre-computed analytics columns
(duration_ms, LLM token counts, price) so DuckDB queries can run directly
on the parquet files without post-processing.
"""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa

SPAN_SCHEMA = pa.schema(
    [
        pa.field("trace_id", pa.string(), nullable=False),
        pa.field("span_id", pa.string(), nullable=False),
        pa.field("parent_span_id", pa.string(), nullable=True),
        pa.field("name", pa.string(), nullable=False),
        pa.field("service_name", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("start_time_unix_nano", pa.int64(), nullable=True),
        pa.field("end_time_unix_nano", pa.int64(), nullable=True),
        pa.field("duration_ms", pa.float64(), nullable=True),
        pa.field("kind", pa.string(), nullable=True),
        pa.field("model", pa.string(), nullable=True),
        pa.field("vendor", pa.string(), nullable=True),
        pa.field("tokens_in", pa.int32(), nullable=False),
        pa.field("tokens_out", pa.int32(), nullable=False),
        pa.field("price", pa.float64(), nullable=False),
        pa.field("session_id", pa.string(), nullable=True),
        pa.field("user_id", pa.string(), nullable=True),
        pa.field("step", pa.string(), nullable=True),
        pa.field("attributes_json", pa.string(), nullable=True),
        pa.field("events_json", pa.string(), nullable=True),
        # Hive partition key written inline for DuckDB predicate pushdown
        pa.field("dt", pa.string(), nullable=False),
    ]
)


def span_dicts_to_arrow_table(
    lines: list[dict[str, Any]],
    *,
    dt: str,
) -> pa.Table:
    """Convert the NDJSON-style span dicts (as produced by flatten_*) into
    a PyArrow Table conforming to ``SPAN_SCHEMA``.

    ``dt`` is the date partition key (ISO date, e.g. "2026-05-08").
    LLM attributes are extracted inline via the same logic as ``SpanLLM``.
    """
    from easyobs.services.llm_attrs import SpanLLM

    rows: dict[str, list[Any]] = {field.name: [] for field in SPAN_SCHEMA}

    for sp in lines:
        info = SpanLLM.from_span(sp)

        start_ns = sp.get("startTimeUnixNano")
        end_ns = sp.get("endTimeUnixNano")
        duration_ms: float | None = None
        if isinstance(start_ns, int) and isinstance(end_ns, int) and end_ns > start_ns:
            duration_ms = round((end_ns - start_ns) / 1e6, 3)

        attrs = sp.get("attributes")
        events = sp.get("events")

        rows["trace_id"].append(sp.get("traceId") or "")
        rows["span_id"].append(sp.get("spanId") or "")
        rows["parent_span_id"].append(sp.get("parentSpanId") or None)
        rows["name"].append(sp.get("name") or "")
        rows["service_name"].append(sp.get("serviceName") or "")
        rows["status"].append(sp.get("status") or "UNSET")
        rows["start_time_unix_nano"].append(start_ns)
        rows["end_time_unix_nano"].append(end_ns)
        rows["duration_ms"].append(duration_ms)
        rows["kind"].append(info.kind or None)
        rows["model"].append(info.model or None)
        rows["vendor"].append(info.vendor or None)
        rows["tokens_in"].append(info.tokens_in)
        rows["tokens_out"].append(info.tokens_out)
        rows["price"].append(info.price)
        rows["session_id"].append(info.session or None)
        rows["user_id"].append(info.user or None)
        rows["step"].append(info.step or None)
        rows["attributes_json"].append(
            json.dumps(attrs, ensure_ascii=False) if attrs else None
        )
        rows["events_json"].append(
            json.dumps(events, ensure_ascii=False) if events else None
        )
        rows["dt"].append(dt)

    return pa.table(rows, schema=SPAN_SCHEMA)
