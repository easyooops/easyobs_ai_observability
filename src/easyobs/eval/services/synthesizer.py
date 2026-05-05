"""LLM-driven Golden Set generator (12 §10).

Two strategies live behind one job entry-point:

- ``rag_aware``  — sample documents (per ``source_policy``) and ask the
  judge LLM to author L1 query + L2 doc-id + L3 expected-answer triples
  grounded in each document.
- ``trace_driven`` — cluster operational traces, surface uncovered or
  high-failure or new-intent patterns, and convert representative
  traces into L1 + (optional L3) candidates.

Generated items always land in ``status=candidate`` and
``review_state=unreviewed`` — operators must review before the items
are usable in a Run. The worker streams progress via the
:class:`ProgressBroker` so the *Synthesizer Hub* in the UI can survive
browser closes (12 §5).

The MVP implementation does not require a real document store: when the
operator does not pass a ``source_spec.docs[]`` list, ``rag_aware`` mode
falls back to using the operational traces (treating the response text
as the "document") so the feature is testable end-to-end on a fresh
install.
"""

from __future__ import annotations

import asyncio
import hashlib
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
    EvalGoldenSetRow,
    EvalSynthJobRow,
)
from easyobs.eval.judge.providers import (
    JudgeModelSpec,
    JudgeProviderError,
    JudgeRequest,
    get_provider,
)
from easyobs.eval.services.dtos import SynthJobDTO
from easyobs.eval.services.progress import ProgressBroker
from easyobs.eval.services.synth_prompts import (
    RAG_USER_TEMPLATE,
    SYSTEM_PROMPT,
    TRACE_USER_TEMPLATE,
    build_system_prompt,
)
from easyobs.eval.types import (
    GoldenItemReviewState,
    SourceKind,
    SynthJobMode,
    SynthJobSourcePolicy,
    SynthJobStatus,
)

if TYPE_CHECKING:
    from easyobs.eval.services.judge_models import JudgeModelService

_log = logging.getLogger("easyobs.eval.synthesizer")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SynthJobRequest:
    org_id: str
    project_id: str | None
    project_scope: list[str] | None
    set_id: str
    mode: str
    source_policy: str
    source_spec: dict[str, Any]
    judge_model_id: str | None
    target_count: int
    triggered_by: str | None
    custom_prompt: str | None = None


# Prompts live in ``synth_prompts.py`` so wording changes can be reviewed
# without touching the worker logic. See 12 §10 for the licensing
# rationale (no overlap with Phoenix ``run_experiment``, Langfuse Judge
# built-ins, or OpenLIT evaluator library).


class SynthesizerService:
    """Long-running worker that creates ``candidate`` golden items."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        judge_models: JudgeModelService,
        progress: ProgressBroker,
        trace_query=None,
    ) -> None:
        self._sf = session_factory
        self._judge_models = judge_models
        self._progress = progress
        self._traces = trace_query
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_job(self, req: SynthJobRequest) -> SynthJobDTO:
        if req.mode not in {m.value for m in SynthJobMode}:
            raise ValueError(f"unsupported synth mode {req.mode!r}")
        if req.source_policy not in {p.value for p in SynthJobSourcePolicy}:
            raise ValueError(f"unsupported source policy {req.source_policy!r}")
        target = max(1, min(int(req.target_count), 200))

        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, req.set_id)
            if base is None or base.org_id != req.org_id:
                raise LookupError("golden set not found")
            if (
                req.project_scope is not None
                and base.project_id is not None
                and base.project_id not in req.project_scope
            ):
                raise PermissionError("project access denied")

        job_id = uuid.uuid4().hex
        now = _now()
        async with self._sf() as s:
            job = EvalSynthJobRow(
                id=job_id,
                org_id=req.org_id,
                project_id=req.project_id,
                set_id=req.set_id,
                revision_id=None,
                mode=req.mode,
                source_policy=req.source_policy,
                source_spec_json=json.dumps(
                    req.source_spec or {}, ensure_ascii=False, default=str
                ),
                judge_model_id=req.judge_model_id,
                target_count=target,
                generated_count=0,
                status=SynthJobStatus.QUEUED.value,
                progress=0,
                cost_estimate_usd=0.0,
                cost_actual_usd=0.0,
                error_log_json="[]",
                started_at=None,
                finished_at=None,
                triggered_by=req.triggered_by,
                created_at=now,
                updated_at=now,
            )
            s.add(job)
            # Update set's last_synth_job_id pointer.
            base = await s.get(EvalGoldenSetRow, req.set_id)
            if base is not None:
                base.last_synth_job_id = job_id
            await s.commit()
            await s.refresh(job)
            dto = _job_dto(job)

        self._cancel_flags[job_id] = False
        task = asyncio.create_task(
            self._worker(req, job_id, target),
            name=f"easyobs-synth-{job_id[:8]}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        self._progress.publish(
            kind="synth_job",
            ident=job_id,
            event={
                "jobId": job_id,
                "status": SynthJobStatus.QUEUED.value,
                "progress": 0,
                "targetCount": target,
            },
        )
        return dto

    async def cancel_job(self, *, org_id: str, job_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalSynthJobRow, job_id)
            if row is None or row.org_id != org_id:
                return False
            if row.status in {
                SynthJobStatus.DONE.value,
                SynthJobStatus.FAILED.value,
                SynthJobStatus.CANCELLED.value,
            }:
                return False
        self._cancel_flags[job_id] = True
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        return True

    async def get_job(
        self, *, org_id: str, job_id: str, project_ids: list[str] | None
    ) -> SynthJobDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalSynthJobRow, job_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            return _job_dto(row)

    async def list_jobs(
        self, *, org_id: str, set_id: str, project_ids: list[str] | None
    ) -> list[SynthJobDTO]:
        async with self._sf() as s:
            stmt = (
                select(EvalSynthJobRow)
                .where(
                    EvalSynthJobRow.org_id == org_id,
                    EvalSynthJobRow.set_id == set_id,
                )
                .order_by(EvalSynthJobRow.created_at.desc())
            )
            rows = (await s.execute(stmt)).scalars().all()
            if project_ids is not None:
                allowed = set(project_ids)
                rows = [
                    r for r in rows if r.project_id is None or r.project_id in allowed
                ]
            return [_job_dto(r) for r in rows]

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self, req: SynthJobRequest, job_id: str, target: int) -> None:
        await self._mark_status(job_id, SynthJobStatus.RUNNING.value, started=True)
        self._progress.publish(
            kind="synth_job",
            ident=job_id,
            event={
                "jobId": job_id,
                "status": SynthJobStatus.RUNNING.value,
                "progress": 0,
                "targetCount": target,
            },
        )
        try:
            spec = await self._resolve_judge_spec(req)
            if spec is None:
                raise RuntimeError(
                    "synthesizer requires a judge model — none registered or selected"
                )
            sources = await self._collect_sources(req, target)
            generated = 0
            errors: list[dict[str, Any]] = []
            actual_cost = 0.0
            for idx, doc in enumerate(sources, start=1):
                if self._cancel_flags.get(job_id):
                    await self._mark_status(job_id, SynthJobStatus.CANCELLED.value, finished=True)
                    self._progress.publish(
                        kind="synth_job",
                        ident=job_id,
                        event={"jobId": job_id, "status": SynthJobStatus.CANCELLED.value},
                    )
                    return
                if generated >= target:
                    break
                try:
                    candidate, cost = await self._synth_one(spec, doc, req.custom_prompt)
                    actual_cost = round(actual_cost + cost, 6)
                    await self._persist_candidate(req, candidate, doc.get("id") or "")
                    generated += 1
                except JudgeProviderError as exc:
                    errors.append(
                        {
                            "errorType": exc.error_type,
                            "message": exc.detail,
                            "modelId": exc.model_id,
                        }
                    )
                    _log.warning(
                        "synth call failed; skipping",
                        extra={"job_id": job_id, "errorType": exc.error_type},
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        {
                            "errorType": "unknown",
                            "message": f"{exc.__class__.__name__}: {exc}"[:200],
                        }
                    )
                    _log.exception("synth iteration failed", extra={"job_id": job_id})
                progress = int(min(100, round(generated / target * 100)))
                await self._mark_progress(
                    job_id,
                    generated=generated,
                    progress=progress,
                    cost=actual_cost,
                    errors=errors,
                )
                self._progress.publish(
                    kind="synth_job",
                    ident=job_id,
                    event={
                        "jobId": job_id,
                        "status": SynthJobStatus.RUNNING.value,
                        "progress": progress,
                        "generatedCount": generated,
                        "targetCount": target,
                        "costUsd": actual_cost,
                    },
                )
            await self._mark_status(
                job_id,
                SynthJobStatus.DONE.value,
                finished=True,
            )
            self._progress.publish(
                kind="synth_job",
                ident=job_id,
                event={
                    "jobId": job_id,
                    "status": SynthJobStatus.DONE.value,
                    "progress": 100,
                    "generatedCount": generated,
                    "targetCount": target,
                    "costUsd": actual_cost,
                },
            )
        except asyncio.CancelledError:
            await self._mark_status(job_id, SynthJobStatus.CANCELLED.value, finished=True)
            raise
        except Exception as exc:  # noqa: BLE001
            _log.exception("synth job crashed", extra={"job_id": job_id})
            await self._mark_status(
                job_id,
                SynthJobStatus.FAILED.value,
                finished=True,
                error_msg=f"{exc.__class__.__name__}: {exc}"[:200],
            )
            self._progress.publish(
                kind="synth_job",
                ident=job_id,
                event={
                    "jobId": job_id,
                    "status": SynthJobStatus.FAILED.value,
                    "errorType": exc.__class__.__name__,
                    "errorMessage": str(exc)[:200],
                },
            )

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------

    async def _collect_sources(
        self, req: SynthJobRequest, target: int
    ) -> list[dict[str, Any]]:
        """Resolve up to ``target`` 'documents' (each is a dict with id +
        text) according to the configured policy. The MVP uses traces as
        the document substrate when no document store is wired yet — see
        12 §10 for why this is acceptable for the trace-driven branch."""

        spec = req.source_spec or {}
        if req.mode == SynthJobMode.RAG_AWARE.value:
            if req.source_policy == SynthJobSourcePolicy.EXPLICIT.value:
                docs_in = spec.get("docs") or []
                return [_normalise_doc(d) for d in docs_in if isinstance(d, dict)][:target]
            # Other RAG policies (collection / tag / trace_freq / random)
            # need a document store; we degrade to trace-driven sampling
            # rather than failing so the feature works on a fresh install.
            return await self._sample_traces_as_docs(req, target)
        return await self._sample_traces_as_docs(req, target)

    async def _sample_traces_as_docs(
        self, req: SynthJobRequest, target: int
    ) -> list[dict[str, Any]]:
        if self._traces is None:
            return []
        scope = req.project_scope
        try:
            traces = await self._traces.list_traces(
                service_ids=scope, limit=max(target * 3, 30), with_llm=True
            )
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for t in traces:
            summary = t.get("llmSummary") or {}
            text = summary.get("response") or summary.get("query") or ""
            if not text:
                continue
            out.append(
                {
                    "id": t.get("traceId") or hashlib.sha1(text.encode()).hexdigest()[:12],
                    "text": text,
                    "query": summary.get("query"),
                    "trace_id": t.get("traceId"),
                }
            )
            if len(out) >= target:
                break
        return out

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _resolve_judge_spec(
        self, req: SynthJobRequest
    ) -> JudgeModelSpec | None:
        if not req.judge_model_id:
            # Fall back to the first enabled judge model in the org.
            models = await self._judge_models.list(
                org_id=req.org_id, include_disabled=False
            )
            if not models:
                return None
            specs = await self._judge_models.resolve_specs(
                org_id=req.org_id, refs=[(models[0].id, 1.0)]
            )
            return specs[0] if specs else None
        specs = await self._judge_models.resolve_specs(
            org_id=req.org_id, refs=[(req.judge_model_id, 1.0)]
        )
        return specs[0] if specs else None

    async def _synth_one(
        self, spec: JudgeModelSpec, doc: dict[str, Any], custom_prompt: str | None = None
    ) -> tuple[dict[str, Any], float]:
        provider = get_provider(spec.provider) or get_provider("mock")
        assert provider is not None
        doc_id = str(doc.get("id") or "")
        doc_text = str(doc.get("text") or "")
        # Trace-driven sampler emits docs with ``trace_id`` and ``query``;
        # rag-aware path emits docs with ``id`` + ``text``. Switching on
        # ``trace_id`` therefore picks the right prompt template without
        # threading the request mode all the way down here.
        trace_id = doc.get("trace_id") or doc.get("traceId")
        if trace_id:
            user_message = TRACE_USER_TEMPLATE.format(
                trace_id=str(trace_id),
                query_text=str(doc.get("query") or "")[:2000],
                response_text=doc_text[:4000],
            )
        else:
            user_message = RAG_USER_TEMPLATE.format(
                doc_id=doc_id, doc_text=doc_text[:4000]
            )
        system = build_system_prompt(custom_prompt)
        request = JudgeRequest(
            rubric_id=f"synth.{spec.id}",
            prompt=system,
            context={"docId": doc_id, "docText": doc_text[:4000]},
            system_prompt=system,
            user_message=user_message,
        )
        # Reuse the JudgeProvider surface — providers parse JSON for us.
        # The "score" / "verdict" coming back is irrelevant for synthesis;
        # we recover the candidate from the raw payload (mock returns the
        # template fallback so demos stay populated).
        resp = await provider.evaluate(spec, request)
        candidate = _extract_candidate_from_response(resp.raw, doc=doc, reason=resp.reason)
        return candidate, resp.cost_usd

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_candidate(
        self, req: SynthJobRequest, candidate: dict[str, Any], doc_id: str
    ) -> None:
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, req.set_id)
            if base is None:
                return
            row = EvalGoldenItemRow(
                id=uuid.uuid4().hex,
                set_id=req.set_id,
                org_id=req.org_id,
                project_id=base.project_id,
                layer=base.layer,
                source_kind=SourceKind.AUTO.value,
                status="candidate",
                payload_json=json.dumps(candidate, ensure_ascii=False, default=str),
                source_trace_id=str(candidate.get("traceId") or "") or None,
                review_state=GoldenItemReviewState.UNREVIEWED.value,
                created_at=_now(),
                created_by=req.triggered_by,
            )
            s.add(row)
            await s.commit()

    async def _mark_status(
        self,
        job_id: str,
        status: str,
        *,
        started: bool = False,
        finished: bool = False,
        error_msg: str | None = None,
    ) -> None:
        async with self._sf() as s:
            row = await s.get(EvalSynthJobRow, job_id)
            if row is None:
                return
            row.status = status
            row.updated_at = _now()
            if started:
                row.started_at = row.started_at or _now()
            if finished:
                row.finished_at = _now()
            if error_msg:
                try:
                    log = json.loads(row.error_log_json or "[]")
                except Exception:
                    log = []
                if not isinstance(log, list):
                    log = []
                log.append({"errorMessage": error_msg, "at": _now().isoformat()})
                row.error_log_json = json.dumps(log[-50:], ensure_ascii=False)
            await s.commit()

    async def _mark_progress(
        self,
        job_id: str,
        *,
        generated: int,
        progress: int,
        cost: float,
        errors: list[dict[str, Any]],
    ) -> None:
        async with self._sf() as s:
            row = await s.get(EvalSynthJobRow, job_id)
            if row is None:
                return
            row.generated_count = generated
            row.progress = progress
            row.cost_actual_usd = cost
            row.error_log_json = json.dumps(errors[-50:], ensure_ascii=False, default=str)
            row.updated_at = _now()
            await s.commit()


def _normalise_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["id"] = str(doc.get("id") or doc.get("docId") or "")
    out["text"] = str(doc.get("text") or doc.get("content") or "")
    return out


def _extract_candidate_from_response(
    raw: dict[str, Any] | None,
    *,
    doc: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Best-effort extraction of a candidate dict from a judge response.

    Real LLM providers return the parsed JSON object verbatim; the mock
    returns nothing useful (the mock is hash-deterministic) so we fall
    back to a templated candidate that at least keeps the schema filled
    out for end-to-end demos."""

    if isinstance(raw, dict) and isinstance(raw.get("candidate"), dict):
        return dict(raw["candidate"])
    # The mock provider does not produce a candidate; fall back to
    # synthesising from the source document so the operator can still
    # exercise the worker end-to-end on an air-gapped install.
    text = str(doc.get("text") or "")
    snippet = text[:200].strip()
    return {
        "queryText": (doc.get("query") or snippet[:80] or "What does this document say?"),
        "intent": "auto.synth.demo",
        "expectedAnswer": snippet,
        "mustInclude": [],
        "citationsExpected": [doc.get("id") or ""],
        "difficulty": "medium",
        "synthesizerNote": reason[:200],
        "traceId": doc.get("trace_id"),
    }


def _job_dto(row: EvalSynthJobRow) -> SynthJobDTO:
    try:
        spec = json.loads(row.source_spec_json or "{}")
    except Exception:
        spec = {}
    if not isinstance(spec, dict):
        spec = {}
    try:
        errors = json.loads(row.error_log_json or "[]")
    except Exception:
        errors = []
    if not isinstance(errors, list):
        errors = []
    return SynthJobDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        set_id=row.set_id,
        revision_id=row.revision_id,
        mode=row.mode,
        source_policy=row.source_policy,
        source_spec=spec,
        judge_model_id=row.judge_model_id,
        target_count=int(row.target_count or 0),
        generated_count=int(row.generated_count or 0),
        status=row.status,
        progress=int(row.progress or 0),
        cost_estimate_usd=float(row.cost_estimate_usd or 0.0),
        cost_actual_usd=float(row.cost_actual_usd or 0.0),
        error_log=errors,
        started_at=row.started_at,
        finished_at=row.finished_at,
        triggered_by=row.triggered_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
