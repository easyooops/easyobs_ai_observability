from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from easyobs.ports.blob import TraceBlobStore
from easyobs.ports.catalog import TraceCatalog
from easyobs.services.llm_attrs import SpanLLM
from easyobs.services.trace_query import resolve_range


def _percentile(values: list[float], p: float) -> float:
    """Simple nearest-rank percentile (P in [0, 100])."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[k])


def _duration_ms(row: dict[str, Any]) -> float | None:
    start = row.get("startedAt")
    end = row.get("endedAt")
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max(0.0, (e - s).total_seconds() * 1000.0)


class AnalyticsService:
    """Read-side analytics derived from the trace_index catalog and NDJSON blobs.

    The implementation is intentionally data-driven (no hardcoded demo values);
    it computes KPIs, percentiles, trends, distributions, service activity,
    and session aggregates from actual ingested traces on every request.
    """

    def __init__(self, *, blob: TraceBlobStore, catalog: TraceCatalog) -> None:
        self._blob = blob
        self._catalog = catalog

    async def overview(
        self,
        *,
        service_ids: list[str] | None,
        window_hours: int | None = 24,
        bucket_count: int,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Compute the Overview payload over a rolling window OR a custom
        (from, to) range. When a range is supplied the bucket math uses the
        range duration so the chart x-axis stays proportional.
        """
        items = await self._catalog.list_traces(service_ids=service_ids, limit=1000)
        now = datetime.now(tz=timezone.utc)
        lo, hi = resolve_range(window_hours, from_ts, to_ts)

        # Anchor the chart to the explicit window when present, otherwise to
        # "now" for a rolling one.
        series_end = hi or now
        series_start = lo or (series_end - timedelta(hours=window_hours or 24))
        total_span_sec = max(1.0, (series_end - series_start).total_seconds())
        effective_window_hours = total_span_sec / 3600.0

        recent = [
            i
            for i in items
            if (lo is None or datetime.fromisoformat(i["startedAt"]) >= lo)
            and (hi is None or datetime.fromisoformat(i["startedAt"]) <= hi)
        ]

        durations = [d for d in (_duration_ms(i) for i in recent) if d is not None]
        error_rows = [i for i in recent if i.get("status") == "ERROR"]
        ok_rows = [i for i in recent if i.get("status") == "OK"]
        unset_rows = [i for i in recent if i.get("status") == "UNSET"]

        bucket_span = max(1, int(total_span_sec // bucket_count))
        series_total: list[int] = [0] * bucket_count
        series_err: list[int] = [0] * bucket_count
        lat_by_bucket: list[list[float]] = [[] for _ in range(bucket_count)]
        t0 = series_end - timedelta(seconds=bucket_span * bucket_count)
        for i in recent:
            ts = datetime.fromisoformat(i["startedAt"])
            idx = int((ts - t0).total_seconds() // bucket_span)
            if 0 <= idx < bucket_count:
                series_total[idx] += 1
                if i.get("status") == "ERROR":
                    series_err[idx] += 1
                d = _duration_ms(i)
                if d is not None:
                    lat_by_bucket[idx].append(d)

        latency_series_p95 = [
            round(_percentile(bucket, 95), 2) for bucket in lat_by_bucket
        ]

        service_counter: Counter[str] = Counter()
        service_err: Counter[str] = Counter()
        service_lat: defaultdict[str, list[float]] = defaultdict(list)
        for i in recent:
            svc = i.get("serviceName") or "unknown"
            service_counter[svc] += 1
            if i.get("status") == "ERROR":
                service_err[svc] += 1
            d = _duration_ms(i)
            if d is not None:
                service_lat[svc].append(d)

        services = [
            {
                "name": name,
                "count": count,
                "errorCount": service_err[name],
                "errorRate": round(
                    (service_err[name] / count) * 100 if count else 0.0, 2
                ),
                "p50": round(_percentile(service_lat[name], 50), 2),
                "p95": round(_percentile(service_lat[name], 95), 2),
            }
            for name, count in service_counter.most_common(10)
        ]

        root_counter: Counter[str] = Counter()
        root_lat: defaultdict[str, list[float]] = defaultdict(list)
        for i in recent:
            name = i.get("rootName") or "(unnamed)"
            root_counter[name] += 1
            d = _duration_ms(i)
            if d is not None:
                root_lat[name].append(d)
        top_ops = [
            {
                "name": n,
                "count": c,
                "p50": round(_percentile(root_lat[n], 50), 2),
                "p95": round(_percentile(root_lat[n], 95), 2),
            }
            for n, c in root_counter.most_common(8)
        ]

        llm_stats = await self._llm_stats(recent)

        return {
            "windowHours": round(effective_window_hours, 4),
            "generatedAt": now.isoformat(),
            "kpi": {
                "totalTraces": len(recent),
                "errorTraces": len(error_rows),
                "okTraces": len(ok_rows),
                "unsetTraces": len(unset_rows),
                "errorRate": round(
                    (len(error_rows) / len(recent)) * 100 if recent else 0.0, 2
                ),
                "p50LatencyMs": round(_percentile(durations, 50), 2),
                "p90LatencyMs": round(_percentile(durations, 90), 2),
                "p95LatencyMs": round(_percentile(durations, 95), 2),
                "p99LatencyMs": round(_percentile(durations, 99), 2),
                "uniqueServices": len(service_counter),
            },
            "series": {
                "bucketCount": bucket_count,
                "bucketSpanSec": bucket_span,
                "startedAt": t0.isoformat(),
                "total": series_total,
                "errors": series_err,
                "p95Ms": latency_series_p95,
            },
            "statusMix": {
                "OK": len(ok_rows),
                "ERROR": len(error_rows),
                "UNSET": len(unset_rows),
            },
            "latencyBands": self._latency_bands(durations),
            "services": services,
            "topOperations": top_ops,
            "llm": llm_stats,
        }

    async def _llm_stats(self, recent: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate LLM-level KPIs across recent traces' span attributes."""
        total_in = total_out = 0
        total_price = 0.0
        model_counter: Counter[str] = Counter()
        vendor_counter: Counter[str] = Counter()
        step_counter: Counter[str] = Counter()
        llm_calls = retrieve_calls = tool_calls = 0
        traces_with_llm = 0
        sessions: set[str] = set()

        for meta in recent:
            detail = await self._catalog.get_trace_row(meta["traceId"])
            if detail is None:
                continue
            try:
                spans = self._blob.read_batch_lines(detail.batch_relpath)
            except Exception:
                continue
            trace_has_llm = False
            for sp in spans:
                info = SpanLLM.from_span(sp)
                total_in += info.tokens_in
                total_out += info.tokens_out
                total_price += info.price
                if info.model:
                    model_counter[info.model] += 1
                if info.vendor:
                    vendor_counter[info.vendor] += 1
                if info.step:
                    step_counter[info.step] += 1
                if info.session:
                    sessions.add(info.session)
                kind = (info.kind or "").lower()
                if kind == "llm":
                    llm_calls += 1
                    trace_has_llm = True
                elif kind == "retrieve":
                    retrieve_calls += 1
                elif kind == "tool":
                    tool_calls += 1
            if trace_has_llm:
                traces_with_llm += 1

        return {
            "tokensIn": total_in,
            "tokensOut": total_out,
            "tokensTotal": total_in + total_out,
            "price": round(total_price, 6),
            "llmCalls": llm_calls,
            "retrieveCalls": retrieve_calls,
            "toolCalls": tool_calls,
            "tracesWithLlm": traces_with_llm,
            "uniqueSessions": len(sessions),
            "topModels": [
                {"name": n, "count": c} for n, c in model_counter.most_common(8)
            ],
            "topVendors": [
                {"name": n, "count": c} for n, c in vendor_counter.most_common(8)
            ],
            "topSteps": [
                {"name": n, "count": c} for n, c in step_counter.most_common(8)
            ],
        }

    @staticmethod
    def _latency_bands(durations: list[float]) -> list[dict[str, Any]]:
        bands = [
            ("<100ms", 0.0, 100.0),
            ("100-300ms", 100.0, 300.0),
            ("300-800ms", 300.0, 800.0),
            ("0.8-2s", 800.0, 2000.0),
            ("2-5s", 2000.0, 5000.0),
            (">5s", 5000.0, float("inf")),
        ]
        out = []
        for label, lo, hi in bands:
            n = sum(1 for d in durations if lo <= d < hi)
            out.append({"label": label, "count": n})
        return out

    async def sessions(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate **conversational sessions** from ingested traces.

        A session is defined as the set of traces that share the same
        ``o.sess`` attribute — i.e. the same conversational context as
        produced by the agent (``span_tag(SpanTag.SESSION, sid)`` in
        Python, ``span.setAttribute("o.sess", sid)`` in TS/JS). Traces
        without an ``o.sess`` value are intentionally **excluded** from
        the session list — they belong to the trace explorer, not here.
        The previous "service × hour-bucket" fallback was a useful
        proxy for demos but conflated unrelated traces and is removed.

        Hierarchy: ``session > traces > spans``. The returned row carries
        ``traceIds`` so the UI can pivot directly into the trace list
        for the session without a fragile time-window match.
        """
        items = await self._catalog.list_traces(service_ids=service_ids, limit=1000)
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        if lo is not None or hi is not None:
            def _ok(i: dict[str, Any]) -> bool:
                ts = datetime.fromisoformat(i["startedAt"])
                if lo is not None and ts < lo:
                    return False
                if hi is not None and ts > hi:
                    return False
                return True
            items = [i for i in items if _ok(i)]

        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        # Per-session derived fields (last-seen wins for user / service so
        # the row reflects the most recent conversational context).
        meta: dict[str, dict[str, Any]] = {}

        for i in items:
            detail = await self._catalog.get_trace_row(i["traceId"])
            if detail is None:
                continue
            try:
                spans = self._blob.read_batch_lines(detail.batch_relpath)
            except Exception:
                continue

            session_id: str | None = None
            user: str | None = None
            tokens_in = tokens_out = 0
            price = 0.0
            models: set[str] = set()
            for sp in spans:
                info = SpanLLM.from_span(sp)
                if info.session and not session_id:
                    session_id = info.session
                if info.user and not user:
                    user = info.user
                tokens_in += info.tokens_in
                tokens_out += info.tokens_out
                price += info.price
                if info.model:
                    models.add(info.model)

            # Skip traces that did not carry o.sess. This is the deliberate
            # change vs. the old hour-bucket fallback — only real sessions
            # show up here.
            if not session_id:
                continue

            enriched = dict(i)
            enriched["_tokensIn"] = tokens_in
            enriched["_tokensOut"] = tokens_out
            enriched["_price"] = price
            enriched["_models"] = models
            groups[session_id].append(enriched)

            m = meta.setdefault(
                session_id,
                {
                    "serviceName": i.get("serviceName") or "unknown",
                    "user": user,
                },
            )
            # Keep the most recent service/user observation per session
            # (sessions can hop services in theory; in practice this
            # collapses to a stable value because o.sess is bound to a
            # single agent process).
            m["serviceName"] = i.get("serviceName") or m["serviceName"]
            if user:
                m["user"] = user

        out: list[dict[str, Any]] = []
        for key, group in groups.items():
            m = meta.get(key, {})
            first_seen = min(g["startedAt"] for g in group)
            ended_vals = [g.get("endedAt") for g in group if g.get("endedAt")]
            last_seen = (
                max(ended_vals)
                if ended_vals
                else max(g["startedAt"] for g in group)
            )
            err = sum(1 for g in group if g.get("status") == "ERROR")
            tok_in = sum(g.get("_tokensIn", 0) for g in group)
            tok_out = sum(g.get("_tokensOut", 0) for g in group)
            price = round(sum(g.get("_price", 0.0) for g in group), 6)
            models: set[str] = set()
            for g in group:
                models |= g.get("_models") or set()
            # traceIds in chronological order — drawer renders them as a
            # mini turn list.
            ordered = sorted(group, key=lambda g: g["startedAt"])
            trace_ids = [g["traceId"] for g in ordered]
            out.append(
                {
                    "sessionId": key,
                    "serviceName": m.get("serviceName", "unknown"),
                    "user": m.get("user"),
                    "traceCount": len(group),
                    "errorCount": err,
                    "firstSeenAt": first_seen,
                    "lastSeenAt": last_seen,
                    # Kept for the drawer / a possible compact metric column,
                    # but the main table no longer surfaces these (trace-level
                    # concern).
                    "tokensIn": tok_in,
                    "tokensOut": tok_out,
                    "tokensTotal": tok_in + tok_out,
                    "price": price,
                    "models": sorted(models),
                    "traceIds": trace_ids,
                }
            )
        out.sort(key=lambda s: s["lastSeenAt"], reverse=True)
        return out[:limit]

    async def users(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate per-user activity from ingested traces.

        A user is identified by the ``o.user`` span attribute
        (``span_tag(SpanTag.USER, uid)`` in Python). Traces without
        ``o.user`` are excluded. The returned row summarises sessions,
        traces, tokens and cost per user.
        """
        items = await self._catalog.list_traces(service_ids=service_ids, limit=1000)
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        if lo is not None or hi is not None:
            def _ok(i: dict[str, Any]) -> bool:
                ts = datetime.fromisoformat(i["startedAt"])
                if lo is not None and ts < lo:
                    return False
                if hi is not None and ts > hi:
                    return False
                return True
            items = [i for i in items if _ok(i)]

        user_data: dict[str, dict[str, Any]] = {}

        for i in items:
            detail = await self._catalog.get_trace_row(i["traceId"])
            if detail is None:
                continue
            try:
                spans = self._blob.read_batch_lines(detail.batch_relpath)
            except Exception:
                continue

            user_id: str | None = None
            session_id: str | None = None
            tokens_in = tokens_out = 0
            price = 0.0
            models: set[str] = set()
            for sp in spans:
                info = SpanLLM.from_span(sp)
                if info.user and not user_id:
                    user_id = info.user
                if info.session and not session_id:
                    session_id = info.session
                tokens_in += info.tokens_in
                tokens_out += info.tokens_out
                price += info.price
                if info.model:
                    models.add(info.model)

            if not user_id:
                continue

            if user_id not in user_data:
                user_data[user_id] = {
                    "userId": user_id,
                    "serviceName": i.get("serviceName") or "unknown",
                    "sessions": set(),
                    "traceCount": 0,
                    "errorCount": 0,
                    "tokensIn": 0,
                    "tokensOut": 0,
                    "price": 0.0,
                    "models": set(),
                    "firstSeenAt": i["startedAt"],
                    "lastSeenAt": i.get("endedAt") or i["startedAt"],
                }

            ud = user_data[user_id]
            ud["traceCount"] += 1
            if i.get("status") == "ERROR":
                ud["errorCount"] += 1
            ud["tokensIn"] += tokens_in
            ud["tokensOut"] += tokens_out
            ud["price"] += price
            ud["models"] |= models
            if session_id:
                ud["sessions"].add(session_id)
            ud["serviceName"] = i.get("serviceName") or ud["serviceName"]
            if i["startedAt"] < ud["firstSeenAt"]:
                ud["firstSeenAt"] = i["startedAt"]
            ended = i.get("endedAt") or i["startedAt"]
            if ended > ud["lastSeenAt"]:
                ud["lastSeenAt"] = ended

        out: list[dict[str, Any]] = []
        for ud in user_data.values():
            out.append({
                "userId": ud["userId"],
                "serviceName": ud["serviceName"],
                "sessionCount": len(ud["sessions"]),
                "traceCount": ud["traceCount"],
                "errorCount": ud["errorCount"],
                "tokensIn": ud["tokensIn"],
                "tokensOut": ud["tokensOut"],
                "tokensTotal": ud["tokensIn"] + ud["tokensOut"],
                "price": round(ud["price"], 6),
                "models": sorted(ud["models"]),
                "firstSeenAt": ud["firstSeenAt"],
                "lastSeenAt": ud["lastSeenAt"],
            })
        out.sort(key=lambda u: u["lastSeenAt"], reverse=True)
        return out[:limit]

    async def spans(
        self,
        *,
        service_ids: list[str] | None,
        limit: int = 200,
        window_hours: int | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Flatten NDJSON span rows across recent traces for the Spans tab."""
        items = await self._catalog.list_traces(service_ids=service_ids, limit=1000)
        lo, hi = resolve_range(window_hours, from_ts, to_ts)
        if lo is not None or hi is not None:
            def _ok(i: dict[str, Any]) -> bool:
                ts = datetime.fromisoformat(i["startedAt"])
                if lo is not None and ts < lo:
                    return False
                if hi is not None and ts > hi:
                    return False
                return True
            items = [i for i in items if _ok(i)]
        rows: list[dict[str, Any]] = []
        for meta in items:
            detail = await self._catalog.get_trace_row(meta["traceId"])
            if detail is None:
                continue
            spans = self._blob.read_batch_lines(detail.batch_relpath)
            for sp in spans:
                start = sp.get("startTimeUnixNano")
                end = sp.get("endTimeUnixNano")
                duration = 0.0
                if isinstance(start, int) and isinstance(end, int) and end > start:
                    duration = round((end - start) / 1e6, 2)
                info = SpanLLM.from_span(sp)
                rows.append(
                    {
                        "traceId": sp.get("traceId"),
                        "spanId": sp.get("spanId"),
                        "parentSpanId": sp.get("parentSpanId"),
                        "name": sp.get("name"),
                        "serviceName": sp.get("serviceName"),
                        "status": sp.get("status"),
                        "durationMs": duration,
                        "startTimeUnixNano": start,
                        "endTimeUnixNano": end,
                        "kind": info.kind,
                        "step": info.step,
                        "model": info.model,
                        "tokensTotal": info.tokens_total,
                        "price": info.price,
                    }
                )
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break
        rows.sort(
            key=lambda r: (r.get("startTimeUnixNano") or 0), reverse=True
        )
        return rows[:limit]
