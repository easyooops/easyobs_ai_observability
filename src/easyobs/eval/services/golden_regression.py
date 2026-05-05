"""Regression Run for Golden Sets (12 §2).

Flow:
    queued → invoking → collecting → evaluating → done

1. **invoking** — for each Golden Item (status=active, layer=L1) we POST
   the item's ``query_text`` to the configured agent endpoint with
   ``goldenRunId`` / ``goldenItemId`` correlation metadata.
2. **collecting** — once every invocation has either responded or
   errored, we wait up to ``settings.regression_collect_timeout_sec``
   for the OTLP traces to arrive. Items whose trace never arrives
   transition to ``timeout`` and are recorded as ``verdict=error``.
3. **evaluating** — collected traces are handed to :class:`RunService`
   in ``golden_gt`` mode so the existing rule + judge layer kicks in.
4. **done** — the run row is finalised; pass-rate / avg-score exclude
   ``Verdict.ERROR`` rows.

The whole worker runs as a long-lived ``asyncio.Task``; the launcher
returns the run id immediately so the API stays non-blocking. Status is
mirrored to ``eval_run.status`` for replay after server restart and to
the ``ProgressBroker`` for live SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import (
    EvalGoldenItemRow,
    EvalGoldenRunTraceMapRow,
    EvalRunRow,
)
from easyobs.eval.services.agent_invoke import (
    AgentInvokeResult,
    invoke_agent_for_item,
)
from easyobs.eval.services.dtos import (
    AgentInvokeSettings,
    GoldenRunInvokeDTO,
    RunDTO,
)
from easyobs.eval.services.progress import ProgressBroker
from easyobs.eval.types import (
    EvalRunMode,
    GoldenRunInvokeStatus,
    GoldenSetMode,
    RunStatus,
    TriggerLane,
)

if TYPE_CHECKING:
    from easyobs.eval.services.goldensets import GoldenSetService
    from easyobs.eval.services.runs import RunService

_log = logging.getLogger("easyobs.eval.golden_regression")


@dataclass(slots=True)
class GoldenRegressionRequest:
    org_id: str
    project_id: str | None
    project_scope: list[str] | None
    set_id: str
    profile_id: str
    triggered_by: str | None
    notes: str = ""
    # Operator override for the per-item correlation timeout. Falls back
    # to the global setting when ``None``.
    collect_timeout_sec: int | None = None
    # Operator override for max concurrent invocations; falls back to the
    # set's ``agent_max_concurrent``.
    max_concurrent: int | None = None


class GoldenRegressionService:
    """Worker-style service that drives the Regression Run lifecycle."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        runs: RunService,
        goldensets: GoldenSetService,
        progress: ProgressBroker,
        collect_timeout_sec: int = 60,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self._sf = session_factory
        self._runs = runs
        self._goldens = goldensets
        self._progress = progress
        self._collect_timeout = max(5, int(collect_timeout_sec))
        self._poll = max(0.5, float(poll_interval_sec))
        # Track active worker tasks so the API can request cancellation
        # by run_id even after a process restart-replay swaps the task
        # reference. Worker functions check this dict's status flag too.
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, bool] = {}

    async def start_regression_run(
        self, req: GoldenRegressionRequest
    ) -> RunDTO:
        """Persist a placeholder run row, lock the active revision, and
        kick off the background worker. Returns the run DTO immediately
        so the API stays non-blocking."""

        # Validate set + access first so the API returns 4xx for obvious
        # misuse before we spawn anything.
        gset = await self._load_set(req.org_id, req.set_id, req.project_scope)
        if gset is None:
            raise LookupError("golden set not found")
        if gset.mode != GoldenSetMode.REGRESSION.value:
            raise ValueError(
                f"regression run requires mode=regression (got {gset.mode!r})"
            )
        if not gset.agent_endpoint_url.strip():
            raise ValueError(
                "agent endpoint not configured — set agent_endpoint_url first"
            )

        # 12 §3.2: lock a revision so the Run pins immutable inputs.
        revision = await self._goldens.lock_revision_for_run(
            org_id=req.org_id,
            set_id=req.set_id,
            actor=req.triggered_by,
        )

        # Pick L1 items only — Regression invokes the agent with the
        # query, so non-L1 items are skipped (they belong to L2/L3 GT).
        active_items = await self._select_invocation_items(
            org_id=req.org_id,
            set_id=req.set_id,
            revision_id=revision.id,
        )
        if not active_items:
            raise ValueError(
                "golden set has no active L1 items eligible for regression"
            )

        run_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        async with self._sf() as s:
            run_row = EvalRunRow(
                id=run_id,
                org_id=req.org_id,
                project_id=req.project_id,
                profile_id=req.profile_id,
                schedule_id=None,
                trigger_lane=TriggerLane.GOLDEN_REGRESSION.value,
                triggered_by=req.triggered_by,
                status=RunStatus.QUEUED.value,
                subject_count=len(active_items),
                completed_count=0,
                failed_count=0,
                cost_estimate_usd=0.0,
                cost_actual_usd=0.0,
                pass_rate=0.0,
                avg_score=0.0,
                notes=req.notes or f"regression for {gset.name}",
                run_mode=EvalRunMode.GOLDEN_GT.value,
                golden_set_id=req.set_id,
                run_context_json=json.dumps(
                    {
                        "goldenRevisionId": revision.id,
                        "goldenRevisionNo": revision.revision_no,
                        "agent": {
                            "endpoint": gset.agent_endpoint_url,
                            "timeoutSec": gset.agent_timeout_sec,
                            "maxConcurrent": gset.agent_max_concurrent,
                        },
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                started_at=now,
                finished_at=None,
            )
            s.add(run_row)
            for item_id in active_items:
                s.add(
                    EvalGoldenRunTraceMapRow(
                        id=uuid.uuid4().hex,
                        run_id=run_id,
                        golden_item_id=item_id,
                        org_id=req.org_id,
                        trace_id=None,
                        invoke_status=GoldenRunInvokeStatus.PENDING.value,
                        invoke_started_at=None,
                        invoke_finished_at=None,
                        agent_response_json="{}",
                        error_detail_json="{}",
                        created_at=now,
                    )
                )
            await s.commit()

        self._cancel_flags[run_id] = False
        task = asyncio.create_task(
            self._run_worker(req, gset, run_id, active_items),
            name=f"easyobs-golden-regression-{run_id[:8]}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        # Surface the QUEUED state immediately so SSE clients get a tick.
        self._progress.publish(
            kind="golden_run",
            ident=run_id,
            event={
                "runId": run_id,
                "status": RunStatus.QUEUED.value,
                "phase": "queued",
                "subjectCount": len(active_items),
                "elapsedMs": 0,
            },
        )
        run_dto = await self._runs.get_run(
            org_id=req.org_id, run_id=run_id, project_ids=req.project_scope
        )
        if run_dto is None:
            raise LookupError("run vanished mid-launch")
        return run_dto

    async def cancel_regression_run(self, *, org_id: str, run_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None or row.org_id != org_id:
                return False
            if row.status in {
                RunStatus.COMPLETED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                return False
        self._cancel_flags[run_id] = True
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return True

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run_worker(
        self,
        req: GoldenRegressionRequest,
        gset_initial,
        run_id: str,
        item_ids: list[str],
    ) -> None:
        """The long-running task that drives the four phases."""

        loop_started = datetime.now(timezone.utc)
        try:
            await self._set_run_status(run_id, RunStatus.INVOKING.value, phase="invoking")
            invocation_results = await self._phase_invoke(
                req=req, run_id=run_id, item_ids=item_ids,
                agent_settings=AgentInvokeSettings(
                    endpoint_url=gset_initial.agent_endpoint_url,
                    request_template=dict(gset_initial.agent_invoke.request_template),
                    auth_ref=gset_initial.agent_invoke.auth_ref,
                    timeout_sec=gset_initial.agent_invoke.timeout_sec,
                    max_concurrent=req.max_concurrent or gset_initial.agent_invoke.max_concurrent,
                ),
            )
            if self._cancel_flags.get(run_id):
                await self._set_run_status(run_id, RunStatus.CANCELLED.value, phase="cancelled")
                return

            await self._set_run_status(run_id, RunStatus.COLLECTING.value, phase="collecting")
            collected_trace_ids = await self._phase_collect(
                run_id=run_id,
                invocation_results=invocation_results,
                timeout_sec=req.collect_timeout_sec or self._collect_timeout,
            )
            if self._cancel_flags.get(run_id):
                await self._set_run_status(run_id, RunStatus.CANCELLED.value, phase="cancelled")
                return

            await self._set_run_status(run_id, RunStatus.EVALUATING.value, phase="evaluating")
            run_dto = await self._phase_evaluate(
                req=req,
                run_id=run_id,
                trace_ids=collected_trace_ids,
            )

            self._progress.publish(
                kind="golden_run",
                ident=run_id,
                event={
                    "runId": run_id,
                    "status": "done",
                    "phase": "done",
                    "passRate": run_dto.pass_rate,
                    "avgScore": run_dto.avg_score,
                    "completedCount": run_dto.completed_count,
                    "failedCount": run_dto.failed_count,
                    "elapsedMs": _elapsed_ms(loop_started),
                },
            )
        except asyncio.CancelledError:
            await self._set_run_status(run_id, RunStatus.CANCELLED.value, phase="cancelled")
            self._progress.publish(
                kind="golden_run",
                ident=run_id,
                event={"runId": run_id, "status": "cancelled", "phase": "cancelled"},
            )
            raise
        except Exception as exc:  # noqa: BLE001
            _log.exception("golden regression worker crashed", extra={"run_id": run_id})
            await self._set_run_status(
                run_id,
                RunStatus.FAILED.value,
                phase="failed",
                notes_append=f" · worker error: {exc.__class__.__name__}",
            )
            self._progress.publish(
                kind="golden_run",
                ident=run_id,
                event={
                    "runId": run_id,
                    "status": "failed",
                    "phase": "failed",
                    "errorType": exc.__class__.__name__,
                    "errorMessage": str(exc)[:200],
                },
            )

    # -- phase 1: invoke ------------------------------------------------

    async def _phase_invoke(
        self,
        *,
        req: GoldenRegressionRequest,
        run_id: str,
        item_ids: list[str],
        agent_settings: AgentInvokeSettings,
    ) -> dict[str, AgentInvokeResult]:
        sem = asyncio.Semaphore(max(1, agent_settings.max_concurrent))
        items_by_id = await self._load_items(req.org_id, item_ids)
        results: dict[str, AgentInvokeResult] = {}
        invoked_count = 0
        total = len(item_ids)

        async def _one(item_id: str) -> None:
            nonlocal invoked_count
            if self._cancel_flags.get(run_id):
                return
            async with sem:
                if self._cancel_flags.get(run_id):
                    return
                payload = items_by_id.get(item_id) or {}
                query_text = str(payload.get("query") or payload.get("query_text") or "")
                await self._mark_invoke_started(run_id, item_id)
                result = await invoke_agent_for_item(
                    settings=agent_settings,
                    query_text=query_text,
                    run_id=run_id,
                    item_id=item_id,
                )
                results[item_id] = result
                await self._record_invoke_result(run_id, item_id, result)
                invoked_count += 1
                self._progress.publish(
                    kind="golden_run",
                    ident=run_id,
                    event={
                        "runId": run_id,
                        "status": "running",
                        "phase": "invoking",
                        "invokedCount": invoked_count,
                        "subjectCount": total,
                        "lastItemId": item_id,
                        "lastOk": result.ok,
                        "lastErrorType": result.error_type,
                    },
                )

        await asyncio.gather(*(_one(i) for i in item_ids))
        return results

    # -- phase 2: collect (wait for OTLP traces) ------------------------

    async def _phase_collect(
        self,
        *,
        run_id: str,
        invocation_results: dict[str, AgentInvokeResult],
        timeout_sec: int,
    ) -> list[str]:
        # Items the agent already echoed a trace_id for can shortcut.
        shortcut_pairs: list[tuple[str, str]] = []
        for item_id, res in invocation_results.items():
            if res.ok and res.inline_trace_id:
                shortcut_pairs.append((item_id, res.inline_trace_id))
        if shortcut_pairs:
            await self._record_collected_traces(run_id, shortcut_pairs)

        # Poll-based correlation: the post-write hook updates the row by
        # ``goldenRunId`` correlation; we just sit here until either every
        # row collects or the timeout expires.
        deadline = asyncio.get_event_loop().time() + timeout_sec
        last_progress_emit = 0.0
        while True:
            collected, pending = await self._collect_status_counts(run_id)
            now = asyncio.get_event_loop().time()
            if now - last_progress_emit > 1.5:
                self._progress.publish(
                    kind="golden_run",
                    ident=run_id,
                    event={
                        "runId": run_id,
                        "status": "running",
                        "phase": "collecting",
                        "collectedCount": collected,
                        "pendingCount": pending,
                    },
                )
                last_progress_emit = now
            if pending == 0:
                break
            if now >= deadline or self._cancel_flags.get(run_id):
                # Mark stragglers as timed out so the eval phase can
                # cleanly skip them.
                await self._timeout_pending_invocations(run_id)
                break
            await asyncio.sleep(self._poll)

        return await self._collected_trace_ids(run_id)

    # -- phase 3: evaluate ----------------------------------------------

    async def _phase_evaluate(
        self,
        *,
        req: GoldenRegressionRequest,
        run_id: str,
        trace_ids: list[str],
    ) -> RunDTO:
        """Hand the collected trace_ids over to the existing RunService.

        We do not call ``runs.execute`` recursively here — that would
        create a *second* eval_run row. Instead we delegate the
        evaluation work but reuse our own run row by patching the
        results onto it. The cleanest way to achieve that without
        duplicating code is to run the evaluation inline, then copy the
        finalised summary fields into our row."""

        # Empty case: every invocation timed out / errored. Mark done
        # with zero subjects evaluated; the count of error rows is
        # already in the trace_map table for the UI.
        if not trace_ids:
            return await self._finalise_empty_run(run_id)

        eval_dto = await self._runs.execute(
            org_id=req.org_id,
            profile_id=req.profile_id,
            profile=None,
            project_id=req.project_id,
            trace_ids=trace_ids,
            trigger_lane=TriggerLane.GOLDEN_REGRESSION.value,
            triggered_by=req.triggered_by,
            project_scope=req.project_scope,
            run_mode=EvalRunMode.GOLDEN_GT.value,
            golden_set_id=req.set_id,
            run_context={
                "goldenRunId": run_id,
                "goldenSetId": req.set_id,
            },
        )
        # Copy the inline-eval summary onto our pre-created run row so
        # the API still returns one stable run id throughout.
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None:
                raise LookupError("run vanished mid-evaluate")
            row.status = RunStatus.COMPLETED.value
            row.completed_count = eval_dto.completed_count
            row.failed_count = eval_dto.failed_count
            row.cost_actual_usd = eval_dto.cost_actual_usd
            row.cost_estimate_usd = eval_dto.cost_estimate_usd
            row.pass_rate = eval_dto.pass_rate
            row.avg_score = eval_dto.avg_score
            row.finished_at = datetime.now(timezone.utc)
            await s.commit()
            await s.refresh(row)
            return _run_dto_from_row(row)

    async def _finalise_empty_run(self, run_id: str) -> RunDTO:
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None:
                raise LookupError("run vanished mid-finalise")
            row.status = RunStatus.COMPLETED.value
            row.completed_count = 0
            row.failed_count = row.subject_count
            row.finished_at = datetime.now(timezone.utc)
            row.notes = (row.notes or "") + " · no traces collected"
            await s.commit()
            await s.refresh(row)
            return _run_dto_from_row(row)

    # ------------------------------------------------------------------
    # Read helpers (used by the API to render Run Status Hub)
    # ------------------------------------------------------------------

    async def list_invokes(
        self, *, org_id: str, run_id: str, project_scope: list[str] | None
    ) -> list[GoldenRunInvokeDTO]:
        """Return the per-item invocation map for a given Regression Run.
        Used by the API to drive the Run Status Hub drill-down (12 §2.5)
        — each row tells the operator whether a single Golden Item has
        been invoked, whether its trace has landed, or why it failed."""

        async with self._sf() as s:
            run = await s.get(EvalRunRow, run_id)
            if run is None or run.org_id != org_id:
                raise LookupError("run not found")
            if (
                project_scope is not None
                and run.project_id is not None
                and run.project_id not in project_scope
            ):
                raise PermissionError("project access denied")
            stmt = select(EvalGoldenRunTraceMapRow).where(
                EvalGoldenRunTraceMapRow.run_id == run_id,
            ).order_by(EvalGoldenRunTraceMapRow.created_at)
            rows = (await s.execute(stmt)).scalars().all()
        out: list[GoldenRunInvokeDTO] = []
        for row in rows:
            try:
                err = json.loads(row.error_detail_json or "null")
            except Exception:
                err = None
            try:
                resp = json.loads(row.agent_response_json or "null")
            except Exception:
                resp = None
            out.append(
                GoldenRunInvokeDTO(
                    id=row.id,
                    run_id=row.run_id,
                    golden_item_id=row.golden_item_id,
                    trace_id=row.trace_id,
                    invoke_status=row.invoke_status,
                    invoke_started_at=row.invoke_started_at,
                    invoke_finished_at=row.invoke_finished_at,
                    agent_response=resp if isinstance(resp, dict) else {},
                    error_detail=err if isinstance(err, dict) else {},
                )
            )
        return out

    # ------------------------------------------------------------------
    # Trace correlation hook (called from ingest)
    # ------------------------------------------------------------------

    async def correlate_trace_attribute(
        self, trace_id: str, attributes: dict[str, Any]
    ) -> None:
        """Called by the ingest path after each trace lands. If the
        trace carries ``easyobs.golden_run_id`` and ``...item_id`` (or
        equivalents) we update the matching trace_map row.

        Called via ``register_post_write_hook`` so failure here can
        never break ingest; defensive try/except is therefore the rule."""

        try:
            run_id = (
                attributes.get("easyobs.golden_run_id")
                or attributes.get("easyobs.goldenRunId")
                or attributes.get("goldenRunId")
            )
            item_id = (
                attributes.get("easyobs.golden_item_id")
                or attributes.get("easyobs.goldenItemId")
                or attributes.get("goldenItemId")
            )
            if not run_id or not item_id:
                return
            await self._record_collected_traces(str(run_id), [(str(item_id), trace_id)])
        except Exception:
            _log.exception("trace correlation failed", extra={"trace_id": trace_id})

    # ------------------------------------------------------------------
    # DB helpers (small read/write methods kept tight so the worker is
    # easy to read)
    # ------------------------------------------------------------------

    async def _load_set(self, org_id: str, set_id: str, project_scope):
        return await self._goldens.get_set(
            org_id=org_id, set_id=set_id, project_ids=project_scope
        )

    async def _select_invocation_items(
        self, *, org_id: str, set_id: str, revision_id: str
    ) -> list[str]:
        async with self._sf() as s:
            stmt = (
                select(EvalGoldenItemRow.id, EvalGoldenItemRow.layer)
                .where(
                    EvalGoldenItemRow.set_id == set_id,
                    EvalGoldenItemRow.org_id == org_id,
                    EvalGoldenItemRow.status == "active",
                )
                .order_by(EvalGoldenItemRow.created_at)
            )
            rows = (await s.execute(stmt)).all()
        # We invoke for every active item — the agent itself will route
        # by layer if needed. But L1 items always have a query, so
        # prefer them when present.
        l1 = [r[0] for r in rows if str(r[1]).upper() == "L1"]
        return l1 or [r[0] for r in rows]

    async def _load_items(
        self, org_id: str, item_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not item_ids:
            return {}
        async with self._sf() as s:
            stmt = select(EvalGoldenItemRow).where(
                EvalGoldenItemRow.id.in_(item_ids),
                EvalGoldenItemRow.org_id == org_id,
            )
            rows = (await s.execute(stmt)).scalars().all()
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                try:
                    out[r.id] = json.loads(r.payload_json or "{}")
                except Exception:
                    out[r.id] = {}
            return out

    async def _set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        phase: str | None = None,
        notes_append: str = "",
    ) -> None:
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None:
                return
            row.status = status
            if notes_append:
                row.notes = (row.notes or "") + notes_append
            if status in {RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}:
                row.finished_at = datetime.now(timezone.utc)
            await s.commit()
        if phase:
            self._progress.publish(
                kind="golden_run",
                ident=run_id,
                event={"runId": run_id, "status": status, "phase": phase},
            )

    async def _mark_invoke_started(self, run_id: str, item_id: str) -> None:
        async with self._sf() as s:
            stmt = select(EvalGoldenRunTraceMapRow).where(
                EvalGoldenRunTraceMapRow.run_id == run_id,
                EvalGoldenRunTraceMapRow.golden_item_id == item_id,
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                return
            row.invoke_status = GoldenRunInvokeStatus.INVOKED.value
            row.invoke_started_at = datetime.now(timezone.utc)
            await s.commit()

    async def _record_invoke_result(
        self, run_id: str, item_id: str, result: AgentInvokeResult
    ) -> None:
        async with self._sf() as s:
            stmt = select(EvalGoldenRunTraceMapRow).where(
                EvalGoldenRunTraceMapRow.run_id == run_id,
                EvalGoldenRunTraceMapRow.golden_item_id == item_id,
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                return
            row.invoke_finished_at = datetime.now(timezone.utc)
            row.agent_response_json = json.dumps(
                {
                    "ok": result.ok,
                    "statusCode": result.status_code,
                    "elapsedMs": result.elapsed_ms,
                    "body": _truncate(result.response_body, 2000),
                },
                ensure_ascii=False,
                default=str,
            )
            if not result.ok:
                row.invoke_status = GoldenRunInvokeStatus.ERROR.value
                row.error_detail_json = json.dumps(
                    {
                        "errorType": result.error_type or "unknown",
                        "errorMessage": result.error_message or "",
                    },
                    ensure_ascii=False,
                    default=str,
                )
            await s.commit()

    async def _record_collected_traces(
        self, run_id: str, pairs: list[tuple[str, str]]
    ) -> None:
        if not pairs:
            return
        async with self._sf() as s:
            for item_id, trace_id in pairs:
                stmt = select(EvalGoldenRunTraceMapRow).where(
                    EvalGoldenRunTraceMapRow.run_id == run_id,
                    EvalGoldenRunTraceMapRow.golden_item_id == item_id,
                )
                row = (await s.execute(stmt)).scalar_one_or_none()
                if row is None or row.invoke_status == GoldenRunInvokeStatus.COLLECTED.value:
                    continue
                row.trace_id = trace_id
                row.invoke_status = GoldenRunInvokeStatus.COLLECTED.value
            await s.commit()

    async def _collect_status_counts(self, run_id: str) -> tuple[int, int]:
        async with self._sf() as s:
            stmt = select(
                EvalGoldenRunTraceMapRow.invoke_status
            ).where(EvalGoldenRunTraceMapRow.run_id == run_id)
            statuses = [row[0] for row in (await s.execute(stmt)).all()]
        collected = sum(
            1 for v in statuses if v == GoldenRunInvokeStatus.COLLECTED.value
        )
        terminal = collected + sum(
            1
            for v in statuses
            if v in {
                GoldenRunInvokeStatus.TIMEOUT.value,
                GoldenRunInvokeStatus.ERROR.value,
            }
        )
        pending = len(statuses) - terminal
        return collected, pending

    async def _timeout_pending_invocations(self, run_id: str) -> None:
        async with self._sf() as s:
            stmt = select(EvalGoldenRunTraceMapRow).where(
                EvalGoldenRunTraceMapRow.run_id == run_id,
                EvalGoldenRunTraceMapRow.invoke_status.in_(
                    [
                        GoldenRunInvokeStatus.PENDING.value,
                        GoldenRunInvokeStatus.INVOKED.value,
                    ]
                ),
            )
            rows = (await s.execute(stmt)).scalars().all()
            for row in rows:
                row.invoke_status = GoldenRunInvokeStatus.TIMEOUT.value
                if not row.error_detail_json or row.error_detail_json == "{}":
                    row.error_detail_json = json.dumps(
                        {
                            "errorType": "collecting_timeout",
                            "errorMessage": "OTLP trace did not arrive in time",
                        },
                        ensure_ascii=False,
                    )
            await s.commit()

    async def _collected_trace_ids(self, run_id: str) -> list[str]:
        async with self._sf() as s:
            stmt = select(EvalGoldenRunTraceMapRow.trace_id).where(
                EvalGoldenRunTraceMapRow.run_id == run_id,
                EvalGoldenRunTraceMapRow.invoke_status
                == GoldenRunInvokeStatus.COLLECTED.value,
            )
            rows = (await s.execute(stmt)).all()
        return [r[0] for r in rows if r[0]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(obj: Any, limit: int) -> Any:
    if isinstance(obj, str) and len(obj) > limit:
        return obj[:limit] + "…"
    if isinstance(obj, dict):
        return {k: _truncate(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(x, limit) for x in obj]
    return obj


def _elapsed_ms(start: datetime) -> int:
    return int((datetime.now(timezone.utc) - start).total_seconds() * 1000)


def _run_dto_from_row(row: EvalRunRow) -> RunDTO:
    # Local lift to avoid an import cycle with runs.py.
    try:
        rc = json.loads(getattr(row, "run_context_json", None) or "{}")
    except Exception:
        rc = {}
    if not isinstance(rc, dict):
        rc = {}
    return RunDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        profile_id=row.profile_id,
        schedule_id=row.schedule_id,
        trigger_lane=row.trigger_lane,
        triggered_by=row.triggered_by,
        status=row.status,
        subject_count=row.subject_count,
        completed_count=row.completed_count,
        failed_count=row.failed_count,
        cost_estimate_usd=row.cost_estimate_usd,
        cost_actual_usd=row.cost_actual_usd,
        pass_rate=row.pass_rate,
        avg_score=row.avg_score,
        notes=row.notes,
        started_at=row.started_at,
        finished_at=row.finished_at,
        run_mode=getattr(row, "run_mode", None) or EvalRunMode.GOLDEN_GT.value,
        golden_set_id=getattr(row, "golden_set_id", None),
        run_context=rc,
    )
