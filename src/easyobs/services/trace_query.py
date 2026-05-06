from __future__ import annotations

from datetime import datetime, timedelta, timezone

from easyobs.ports.blob import TraceBlobStore
from easyobs.ports.catalog import TraceCatalog
from easyobs.services.llm_attrs import SpanLLM, summarise_trace


def resolve_range(
    window_hours: int | None,
    from_ts: datetime | None,
    to_ts: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Translate the UI time selector into an absolute (from, to) pair.

    Precedence: explicit from/to > rolling window > no filter.
    """
    if from_ts is not None or to_ts is not None:
        return from_ts, to_ts
    if window_hours:
        return (
            datetime.now(tz=timezone.utc) - timedelta(hours=window_hours),
            None,
        )
    return None, None


def _started_in_range(
    row: dict, from_ts: datetime | None, to_ts: datetime | None
) -> bool:
    ts = datetime.fromisoformat(row["startedAt"])
    if from_ts is not None and ts < from_ts:
        return False
    if to_ts is not None and ts > to_ts:
        return False
    return True


class TraceQueryService:
    """Read paths for UI and external APIs (trace list, detail, summaries)."""

    def __init__(self, *, blob: TraceBlobStore, catalog: TraceCatalog):
        self._blob = blob
        self._catalog = catalog

    async def list_traces(
        self,
        *,
        service_ids: list[str] | None,
        limit: int,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        with_llm: bool = False,
    ):
        """List recent traces filtered to ``service_ids`` (None = SA-wide).

        The catalog port does not expose a time / session filter today, so
        we over-fetch and trim in the service layer. That keeps the port
        surface small while still letting the UI honour:

        - Workspace > Window / Custom range
        - Session ID filter (drives the Sessions → Traces drill-down and
          the Tracing page's left-rail "Session" filter)
        - User ID filter (drives the Users → Traces drill-down)
        - ``with_llm`` enrichment (adds tokens / price / models / session
          on each row so the trace table can show those columns without a
          second round-trip per row)

        Filtering by ``session_id`` or ``user_id`` always implies reading
        span blobs (the attribute lives there, not in the catalog index),
        so we transparently flip ``with_llm`` on in that case to avoid
        scanning the same blobs twice.
        """
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        has_time_filter = lo is not None or hi is not None
        needs_blob = with_llm or session_id is not None or user_id is not None
        # When we'll filter by session_id/user_id we cannot trust ``limit``
        # until after the per-row scan, so over-fetch generously.
        fetch_limit = max(limit, 1000) if (has_time_filter or session_id or user_id) else limit
        rows = await self._catalog.list_traces(
            service_ids=service_ids, limit=fetch_limit
        )
        if has_time_filter:
            rows = [r for r in rows if _started_in_range(r, lo, hi)]

        if not needs_blob:
            return rows[:limit]

        out: list[dict] = []
        for r in rows:
            detail = await self._catalog.get_trace_row(r["traceId"])
            if detail is None:
                continue
            try:
                spans = self._blob.read_batch_lines(detail.batch_relpath)
            except Exception:
                spans = []
            summary = summarise_trace(spans)
            if session_id is not None and summary.get("session") != session_id:
                continue
            if user_id is not None and summary.get("user") != user_id:
                continue
            enriched = dict(r)
            if with_llm:
                # Flat fields keep the trace table render simple — no nested
                # access in the JSX. ``llmSummary`` carries the full object
                # for callers that want everything.
                enriched["session"] = summary.get("session")
                enriched["user"] = summary.get("user")
                enriched["tokensIn"] = summary.get("tokensIn", 0)
                enriched["tokensOut"] = summary.get("tokensOut", 0)
                enriched["tokensTotal"] = summary.get("tokensTotal", 0)
                enriched["price"] = summary.get("price", 0.0)
                enriched["model"] = (summary.get("models") or [None])[0]
                enriched["models"] = summary.get("models", [])
                enriched["llmSummary"] = summary
            out.append(enriched)
            if len(out) >= limit:
                break
        return out

    async def trace_detail(
        self,
        trace_id: str,
        *,
        allowed_service_ids: list[str] | None,
    ) -> dict | None:
        meta = await self._catalog.get_trace_row(trace_id)
        if meta is None:
            return None
        if allowed_service_ids is not None and meta.service_id not in allowed_service_ids:
            return None
        spans = self._blob.read_batch_lines(meta.batch_relpath)
        spans_enriched = []
        for sp in spans:
            llm = SpanLLM.from_span(sp).to_public()
            enriched = dict(sp)
            enriched["llm"] = llm
            spans_enriched.append(enriched)
        return {
            "traceId": meta.trace_id,
            "serviceId": meta.service_id,
            "startedAt": meta.started_at.astimezone(timezone.utc).isoformat(),
            "endedAt": meta.ended_at.astimezone(timezone.utc).isoformat() if meta.ended_at else None,
            "rootName": meta.root_name,
            "status": meta.status,
            "serviceName": meta.service_name,
            "spanCount": meta.span_count,
            "spans": spans_enriched,
            "llmSummary": summarise_trace(spans),
        }

    async def dashboard_summary(
        self, *, service_ids: list[str] | None
    ) -> dict:
        items = await self._catalog.list_traces(service_ids=service_ids, limit=500)
        errors = sum(1 for i in items if i.get("status") == "ERROR")
        return {
            "traceCount24h": len(items),
            "errorTraces": errors,
            "note": "summary over the most recent stored traces",
        }
