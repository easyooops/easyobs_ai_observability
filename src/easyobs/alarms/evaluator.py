"""Threshold evaluator + alarm dispatch loop.

The evaluator wakes up every ``interval_seconds`` seconds, walks every
enabled alarm rule, computes the observed value of the rule's signal
over the last ``window_minutes`` and decides whether to:

  - open a new firing event (and dispatch to channels)
  - keep an existing firing event open (silent)
  - resolve a previously firing event (and dispatch the resolution)
  - record ``insufficient_data`` (silent)

The reads happen against the live catalog tables (``trace_index``,
``eval_run``, ``eval_result``, ``eval_improvement``, ``eval_cost_daily``)
and against the LLM analytics aggregator when needed. Every query is
scoped by the rule's ``org_id`` (and ``service_id`` when set) so the
existing access matrix is preserved automatically — alarms cannot
"see" data from organizations they do not belong to.

The dispatcher itself runs ``await self._dispatcher.send(...)`` in
sequence per channel so a single bad webhook cannot stall the others
through resource exhaustion. Each delivery is wrapped in a try/except
that records the outcome on ``alarm_event``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.alarms.catalog import AlarmSignalKind
from easyobs.alarms.dispatcher import AlarmDispatcher
from easyobs.alarms.dtos import AlarmChannelDTO, AlarmEventDTO, AlarmRuleDTO
from easyobs.alarms.services import (
    AlarmChannelService,
    AlarmEventService,
    AlarmRuleService,
)
from easyobs.db.models import (
    EvalCostDailyRow,
    EvalImprovementRow,
    EvalResultRow,
    EvalRunRow,
    ServiceRow,
    TraceIndexRow,
)

_log = logging.getLogger("easyobs.alarms.eval")


@dataclass
class _Eval:
    observed_value: float
    sample_count: int
    is_violating: bool


class AlarmEvaluator:
    """Runs the threshold-evaluation loop in the background.

    The class is wired in ``http_app.lifespan`` with the four alarm
    services and an optional ``AnalyticsService`` for LLM cost / token
    totals (those signals require iterating spans, so the evaluator
    delegates rather than re-implementing the aggregation).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        rules: AlarmRuleService,
        channels: AlarmChannelService,
        events: AlarmEventService,
        dispatcher: AlarmDispatcher,
        analytics: Any | None = None,
        interval_seconds: int = 60,
    ) -> None:
        self._sf = session_factory
        self._rules = rules
        self._channels = channels
        self._events = events
        self._dispatcher = dispatcher
        self._analytics = analytics
        self._interval = max(15, int(interval_seconds))
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run_forever(), name="easyobs-alarm-eval")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    async def _run_forever(self) -> None:
        # Tiny initial delay so the API is fully booted (lifespan continues
        # past start()) before the first sweep hits the DB.
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=5.0)
            return
        except asyncio.TimeoutError:
            pass
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                _log.exception("alarm evaluator sweep failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
                return  # stop() flagged us
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    async def run_once(self) -> None:
        """Single evaluation pass — exposed publicly so the unit / smoke
        tests and the ``obsctl`` CLI can drive a deterministic sweep."""
        rules = await self._rules.list_all_enabled()
        if not rules:
            return
        # Pre-load channels per org to avoid N+1 queries.
        channels_by_org: dict[str, dict[str, AlarmChannelDTO]] = {}
        for rule in rules:
            if rule.org_id not in channels_by_org:
                channels = await self._channels.list(org_id=rule.org_id)
                channels_by_org[rule.org_id] = {c.id: c for c in channels}
        for rule in rules:
            try:
                await self._evaluate_rule(rule, channels_by_org[rule.org_id])
            except Exception:  # noqa: BLE001
                _log.exception("alarm rule evaluation failed", extra={"rule_id": rule.id})

    async def _evaluate_rule(
        self,
        rule: AlarmRuleDTO,
        channels: dict[str, AlarmChannelDTO],
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        window_start = now - timedelta(minutes=rule.window_minutes)
        ev = await self._compute(rule, window_start=window_start, now=now)
        if ev is None:
            await self._rules.update_evaluation_state(
                rule_id=rule.id,
                last_state="insufficient_data",
                last_observed_value=None,
            )
            return

        if ev.sample_count < rule.min_samples:
            await self._rules.update_evaluation_state(
                rule_id=rule.id,
                last_state="insufficient_data",
                last_observed_value=ev.observed_value,
            )
            return

        active = await self._events.find_active_firing(rule_id=rule.id)
        new_state = "firing" if ev.is_violating else "ok"
        await self._rules.update_evaluation_state(
            rule_id=rule.id,
            last_state=new_state,
            last_observed_value=ev.observed_value,
        )

        if new_state == "firing" and active is None:
            # Respect dedup window: do not re-open if the last firing was
            # recent enough that operators are still acknowledging it.
            if rule.dedup_minutes > 0:
                latest = await self._events.list(
                    org_id=rule.org_id, rule_id=rule.id, limit=1
                )
                if latest and (
                    latest[0].started_at
                    and latest[0].started_at
                    >= now - timedelta(minutes=rule.dedup_minutes)
                ):
                    return
            event = await self._events.open_firing(
                rule_id=rule.id,
                org_id=rule.org_id,
                service_id=rule.service_id,
                severity=rule.severity,
                observed_value=ev.observed_value,
                threshold=rule.threshold,
                context={
                    "signal_kind": rule.signal_kind,
                    "window_minutes": rule.window_minutes,
                    "sample_count": ev.sample_count,
                    "comparator": rule.comparator,
                },
            )
            await self._fanout(rule, event, channels)
            return

        if new_state == "ok" and active is not None:
            await self._events.close_firing(
                event_id=active.id,
                observed_value=ev.observed_value,
                context={"resolved_sample_count": ev.sample_count},
            )
            resolved = AlarmEventDTO(
                id=active.id,
                rule_id=active.rule_id,
                rule_name=active.rule_name,
                org_id=active.org_id,
                service_id=active.service_id,
                state="resolved",
                severity=active.severity,
                observed_value=ev.observed_value,
                threshold=active.threshold,
                started_at=active.started_at,
                ended_at=now,
                context={**active.context, "resolved_sample_count": ev.sample_count},
                delivery_attempts=active.delivery_attempts,
                delivery_failures=active.delivery_failures,
                last_delivery_error=active.last_delivery_error,
            )
            await self._fanout(rule, resolved, channels)

    async def _fanout(
        self,
        rule: AlarmRuleDTO,
        event: AlarmEventDTO,
        channels: dict[str, AlarmChannelDTO],
    ) -> None:
        targets = [channels[c] for c in rule.channel_ids if c in channels]
        if not targets:
            return
        for ch in targets:
            outcome = await self._dispatcher.send(rule=rule, event=event, channel=ch)
            await self._events.record_delivery(
                event_id=event.id, ok=outcome.ok, error=outcome.detail
            )

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    async def _compute(
        self,
        rule: AlarmRuleDTO,
        *,
        window_start: datetime,
        now: datetime,
    ) -> _Eval | None:
        sk = rule.signal_kind
        try:
            if sk == AlarmSignalKind.TRACE_VOLUME.value:
                return await self._signal_trace_volume(rule, window_start)
            if sk == AlarmSignalKind.ERROR_RATE.value:
                return await self._signal_error_rate(rule, window_start)
            if sk == AlarmSignalKind.LATENCY_P95.value:
                return await self._signal_latency(rule, window_start, percentile=95)
            if sk == AlarmSignalKind.LATENCY_P99.value:
                return await self._signal_latency(rule, window_start, percentile=99)
            if sk == AlarmSignalKind.LLM_COST_USD.value:
                return await self._signal_llm_cost(rule, window_start)
            if sk == AlarmSignalKind.LLM_TOKENS_TOTAL.value:
                return await self._signal_llm_tokens(rule, window_start)
            if sk == AlarmSignalKind.QUALITY_PASS_RATE.value:
                return await self._signal_quality_pass_rate(rule, window_start)
            if sk == AlarmSignalKind.QUALITY_AVG_SCORE.value:
                return await self._signal_quality_avg_score(rule, window_start)
            if sk == AlarmSignalKind.JUDGE_DISAGREEMENT.value:
                return await self._signal_judge_disagreement(rule, window_start)
            if sk == AlarmSignalKind.IMPROVEMENT_OPEN_COUNT.value:
                return await self._signal_improvement_open(rule, window_start)
            if sk == AlarmSignalKind.JUDGE_COST_USD_DAILY.value:
                return await self._signal_judge_cost_daily(rule, now)
        except Exception:  # noqa: BLE001
            _log.exception(
                "signal compute failed",
                extra={"rule_id": rule.id, "signal_kind": sk},
            )
            return None
        return None

    # ---- helpers ------------------------------------------------------

    async def _service_filter(self, rule: AlarmRuleDTO) -> list[str]:
        """Resolve the rule's effective service-id scope.

        - rule.service_id != None → exactly that service.
        - rule.service_id == None → every service inside rule.org_id.
        """
        if rule.service_id is not None:
            return [rule.service_id]
        async with self._sf() as s:
            stmt = select(ServiceRow.id).where(ServiceRow.org_id == rule.org_id)
            return [r for r in (await s.execute(stmt)).scalars().all()]

    def _is_violating(
        self, *, comparator: str, observed: float, threshold: float
    ) -> bool:
        if comparator == "gt":
            return observed > threshold
        if comparator == "gte":
            return observed >= threshold
        if comparator == "lt":
            return observed < threshold
        if comparator == "lte":
            return observed <= threshold
        if comparator == "eq":
            return observed == threshold
        return False

    def _eval(
        self,
        *,
        rule: AlarmRuleDTO,
        observed: float,
        samples: int,
    ) -> _Eval:
        return _Eval(
            observed_value=float(observed),
            sample_count=int(samples),
            is_violating=self._is_violating(
                comparator=rule.comparator,
                observed=float(observed),
                threshold=float(rule.threshold),
            ),
        )

    # ---- Observe signals ----------------------------------------------

    async def _signal_trace_volume(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        services = await self._service_filter(rule)
        if not services:
            return _Eval(observed_value=0.0, sample_count=0, is_violating=False)
        async with self._sf() as s:
            stmt = (
                select(func.count(TraceIndexRow.trace_id))
                .where(
                    TraceIndexRow.service_id.in_(services),
                    TraceIndexRow.started_at >= window_start,
                )
            )
            count = (await s.execute(stmt)).scalar_one() or 0
        return self._eval(rule=rule, observed=count, samples=count)

    async def _signal_error_rate(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        services = await self._service_filter(rule)
        if not services:
            return None
        async with self._sf() as s:
            error_expr = func.sum(
                case((TraceIndexRow.status == "ERROR", 1), else_=0)
            )
            stmt = (
                select(
                    func.count(TraceIndexRow.trace_id),
                    error_expr,
                )
                .where(
                    TraceIndexRow.service_id.in_(services),
                    TraceIndexRow.started_at >= window_start,
                )
            )
            total, errors = (await s.execute(stmt)).one()
        total = total or 0
        errors = errors or 0
        if total == 0:
            return _Eval(observed_value=0.0, sample_count=0, is_violating=False)
        rate = (errors / total) * 100.0
        return self._eval(rule=rule, observed=rate, samples=total)

    async def _signal_latency(
        self, rule: AlarmRuleDTO, window_start: datetime, *, percentile: int
    ) -> _Eval | None:
        services = await self._service_filter(rule)
        if not services:
            return None
        async with self._sf() as s:
            stmt = select(
                TraceIndexRow.started_at, TraceIndexRow.ended_at
            ).where(
                TraceIndexRow.service_id.in_(services),
                TraceIndexRow.started_at >= window_start,
                TraceIndexRow.ended_at.is_not(None),
            )
            rows = (await s.execute(stmt)).all()
        durations: list[float] = []
        for started, ended in rows:
            if started and ended and ended >= started:
                durations.append((ended - started).total_seconds() * 1000.0)
        if not durations:
            return _Eval(observed_value=0.0, sample_count=0, is_violating=False)
        p = _percentile(durations, percentile)
        return self._eval(rule=rule, observed=p, samples=len(durations))

    async def _signal_llm_cost(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        if self._analytics is None:
            return None
        services = await self._service_filter(rule)
        if not services:
            return _Eval(observed_value=0.0, sample_count=0, is_violating=False)
        window_minutes = max(1, int(rule.window_minutes))
        result = await self._analytics.overview(
            service_ids=services,
            window_hours=max(1, window_minutes // 60),
            bucket_count=4,
        )
        llm = result.get("llm") or {}
        traces = (result.get("kpi") or {}).get("totalTraces") or 0
        return self._eval(
            rule=rule, observed=float(llm.get("price") or 0.0), samples=int(traces)
        )

    async def _signal_llm_tokens(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        if self._analytics is None:
            return None
        services = await self._service_filter(rule)
        if not services:
            return _Eval(observed_value=0.0, sample_count=0, is_violating=False)
        window_minutes = max(1, int(rule.window_minutes))
        result = await self._analytics.overview(
            service_ids=services,
            window_hours=max(1, window_minutes // 60),
            bucket_count=4,
        )
        llm = result.get("llm") or {}
        traces = (result.get("kpi") or {}).get("totalTraces") or 0
        return self._eval(
            rule=rule,
            observed=float(llm.get("tokensTotal") or 0),
            samples=int(traces),
        )

    # ---- Quality signals ----------------------------------------------

    async def _signal_quality_pass_rate(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        async with self._sf() as s:
            stmt = (
                select(func.avg(EvalRunRow.pass_rate), func.count(EvalRunRow.id))
                .where(
                    EvalRunRow.org_id == rule.org_id,
                    EvalRunRow.started_at >= window_start,
                    EvalRunRow.status == "succeeded",
                )
            )
            if rule.service_id is not None:
                stmt = stmt.where(EvalRunRow.project_id == rule.service_id)
            avg, count = (await s.execute(stmt)).one()
        avg = float(avg or 0.0)
        return self._eval(rule=rule, observed=avg, samples=int(count or 0))

    async def _signal_quality_avg_score(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        async with self._sf() as s:
            stmt = (
                select(func.avg(EvalRunRow.avg_score), func.count(EvalRunRow.id))
                .where(
                    EvalRunRow.org_id == rule.org_id,
                    EvalRunRow.started_at >= window_start,
                    EvalRunRow.status == "succeeded",
                )
            )
            if rule.service_id is not None:
                stmt = stmt.where(EvalRunRow.project_id == rule.service_id)
            avg, count = (await s.execute(stmt)).one()
        avg = float(avg or 0.0)
        return self._eval(rule=rule, observed=avg, samples=int(count or 0))

    async def _signal_judge_disagreement(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        async with self._sf() as s:
            stmt = (
                select(
                    func.avg(EvalResultRow.judge_disagreement),
                    func.count(EvalResultRow.id),
                )
                .where(
                    EvalResultRow.org_id == rule.org_id,
                    EvalResultRow.created_at >= window_start,
                    EvalResultRow.judge_disagreement.is_not(None),
                )
            )
            if rule.service_id is not None:
                stmt = stmt.where(EvalResultRow.project_id == rule.service_id)
            avg, count = (await s.execute(stmt)).one()
        avg = float(avg or 0.0)
        return self._eval(rule=rule, observed=avg, samples=int(count or 0))

    async def _signal_improvement_open(
        self, rule: AlarmRuleDTO, window_start: datetime
    ) -> _Eval | None:
        async with self._sf() as s:
            stmt = (
                select(func.count(EvalImprovementRow.id))
                .where(
                    EvalImprovementRow.org_id == rule.org_id,
                    EvalImprovementRow.status == "open",
                    EvalImprovementRow.created_at >= window_start,
                )
            )
            if rule.service_id is not None:
                stmt = stmt.where(EvalImprovementRow.project_id == rule.service_id)
            count = (await s.execute(stmt)).scalar_one() or 0
        return self._eval(rule=rule, observed=count, samples=count)

    async def _signal_judge_cost_daily(
        self, rule: AlarmRuleDTO, now: datetime
    ) -> _Eval | None:
        day_str = now.strftime("%Y-%m-%d")
        async with self._sf() as s:
            stmt = (
                select(func.sum(EvalCostDailyRow.judge_cost_usd))
                .where(
                    EvalCostDailyRow.org_id == rule.org_id,
                    EvalCostDailyRow.day == day_str,
                )
            )
            if rule.service_id is not None:
                stmt = stmt.where(EvalCostDailyRow.project_id == rule.service_id)
            total = (await s.execute(stmt)).scalar_one() or 0.0
        return self._eval(
            rule=rule, observed=float(total), samples=1 if total > 0 else 0
        )


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[k])
