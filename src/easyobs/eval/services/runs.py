"""Evaluation run orchestrator.

A run binds a profile to a list of trace IDs and produces one
:class:`ResultDTO` per trace. The orchestrator is responsible for:

1. Resolving the profile (and its judge model specs).
2. Enforcing the cost guard *before* any judge call.
3. Running the rule layer inline (cheap, deterministic).
4. Running the judge layer per trace with multi-judge consensus when
   enabled.
5. Persisting results, daily cost roll-ups and the run summary.

The run service is **async-safe** to call from a fire-and-forget task
(used by the auto-rule trigger on ingest) and from a regular request
handler.
"""

from __future__ import annotations

import difflib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalResultRow, EvalRunRow
from easyobs.eval.catalog.catalog_loader import (
    human_metric_ids,
    judge_metric_ids,
    metric_row_by_id,
)
from easyobs.eval.judge.dimensions_meta import build_evaluation_hints
from easyobs.eval.judge.providers import JudgeRequest
from easyobs.eval.judge.runner import estimate_judge_cost, run_judges
from easyobs.eval.rules import RuleContext
from easyobs.eval.rules.builtin import run_evaluator
from easyobs.eval.services.cost import CostGuard, CostService
from easyobs.eval.services.dtos import (
    FindingDTO,
    ProfileDTO,
    ResultDTO,
    RunDTO,
)
from easyobs.eval.services.improvements import (
    ImprovementService,
    derive_proposals,
    fallback_proposals_for_non_pass,
)
from easyobs.eval.services.judge_models import JudgeModelService
from easyobs.eval.services.profiles import ProfileService
from easyobs.eval.types import EvalRunMode, RunStatus, TriggerLane, Verdict

if TYPE_CHECKING:
    from easyobs.eval.services.goldensets import GoldenSetService
    from easyobs.eval.services.human_labels import HumanLabelService

_log = logging.getLogger("easyobs.eval.runs")

_JUDGE_METRIC_DIMENSIONS: dict[str, str] = {
    "metric.d1_relevance": "answer_relevance",
    "metric.d2_correct": "answer_correctness",
    "metric.d3_faith": "faithfulness",
    "metric.d4_hallu": "faithfulness",
    "metric.d5_complete": "answer_correctness",
    "metric.d7_tone": "coherence",
    "metric.d8_policy": "harmfulness_safety",
    "metric.d11_langq": "coherence",
    "metric.d12_refusal": "harmfulness_safety",
    "metric.b7_chunk_rel": "context_precision",
    "metric.b8_coverage": "context_recall",
    "metric.c1_noise": "noise_sensitivity",
    "metric.c4_evidence": "context_utilization",
    "metric.c5_order": "context_utilization",
    "metric.e5_overcall": "tool_use_quality",
    "metric.e6_plan": "tool_use_quality",
    "metric.e8_synth": "tool_use_quality",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_q(text: str) -> str:
    return " ".join(text.lower().split())


def _run_mode_rubric_line(run_mode: str) -> str | None:
    if run_mode == EvalRunMode.HUMAN_LABEL.value:
        return (
            "[Run mode: human_label] Human-provided labels / expected outputs are "
            "in context.humanLabel — treat them as authoritative supervision signals."
        )
    if run_mode == EvalRunMode.GOLDEN_GT.value:
        return (
            "[Run mode: golden_gt] Ground-truth golden payload is in context.golden — "
            "score factual alignment, coverage, and (when retrieval fields exist) citation faithfulness."
        )
    if run_mode == EvalRunMode.GOLDEN_JUDGE.value:
        return (
            "[Run mode: golden_judge] Golden payload is a reference only — judge holistic "
            "helpfulness, clarity, and safety; do not require verbatim match."
        )
    return None


def _golden_gt_finding(expected: str, actual: str) -> FindingDTO:
    exp = (expected or "").strip()
    act = (actual or "").strip()
    if not exp:
        return FindingDTO(
            evaluator_id="rule.golden.gt",
            kind="rule",
            score=0.5,
            verdict=Verdict.WARN.value,
            reason="golden: no expected text in matched item payload",
            details={},
        )
    if not act:
        return FindingDTO(
            evaluator_id="rule.golden.gt",
            kind="rule",
            score=0.0,
            verdict=Verdict.FAIL.value,
            reason="golden: trace response empty",
            details={},
        )
    ratio = round(difflib.SequenceMatcher(a=exp.lower(), b=act.lower()).ratio(), 4)
    if ratio >= 0.88:
        v = Verdict.PASS
    elif ratio >= 0.55:
        v = Verdict.WARN
    else:
        v = Verdict.FAIL
    return FindingDTO(
        evaluator_id="rule.golden.gt",
        kind="rule",
        score=ratio,
        verdict=v.value,
        reason=f"golden response similarity={ratio:.3f}",
        details={"expectedChars": len(exp), "actualChars": len(act)},
    )


def _mean_rule_scores(findings: list[FindingDTO]) -> float:
    if not findings:
        return 0.0
    return round(sum(f.score for f in findings) / len(findings), 4)


def _pick_golden_blob(
    trace_id: str,
    summary: dict[str, Any],
    by_trace: dict[str, dict[str, Any]],
    by_query: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if trace_id in by_trace:
        return by_trace[trace_id]
    q = summary.get("query") if isinstance(summary, dict) else None
    if q and str(q).strip():
        return by_query.get(_norm_q(str(q)))
    return None


def _expected_from_golden_payload(payload: dict[str, Any]) -> str:
    for key in (
        "expectedResponse",
        "expected_response",
        "goldResponse",
        "gold_response",
        "referenceAnswer",
        "response",
    ):
        v = payload.get(key)
        if v is not None and str(v).strip():
            return str(v)
    return ""


@dataclass(frozen=True, slots=True)
class RunEstimate:
    """Outcome of ``POST /v1/evaluations/runs:estimate`` — UI shows it
    inline before the operator confirms a manual run."""

    subject_count: int
    judge_calls: int
    cost_estimate_usd: float
    rule_only: bool
    monthly_spent_usd: float
    cost_guard_allowed: bool
    cost_guard_note: str
    cost_guard_downgrade: bool


# Type alias so the run service can be unit-tested without pulling in the
# real trace query.
TraceLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


class RunService:
    """Orchestrates evaluation runs across rule + judge layers."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        profiles: ProfileService,
        judge_models: JudgeModelService,
        cost: CostService,
        improvements: ImprovementService | None = None,
        load_trace: TraceLoader | None = None,
        goldensets: GoldenSetService | None = None,
        human_labels: "HumanLabelService | None" = None,
    ) -> None:
        self._sf = session_factory
        self._profiles = profiles
        self._judge_models = judge_models
        self._cost = cost
        self._improvements = improvements
        self._load_trace = load_trace
        self._goldens = goldensets
        self._human_labels = human_labels

    # ------------------------------------------------------------------
    # Estimation
    # ------------------------------------------------------------------

    async def estimate(
        self,
        *,
        org_id: str,
        profile_id: str,
        subject_count: int,
        project_ids: list[str] | None,
    ) -> RunEstimate:
        profile = await self._profiles.get(
            org_id=org_id, profile_id=profile_id, project_ids=project_ids
        )
        if profile is None:
            raise LookupError("profile not found")
        rule_only = not profile.judge_models
        cost_per_subject = 0.0
        if not rule_only:
            specs = await self._judge_models.resolve_specs(
                org_id=org_id,
                refs=[(j.model_id, j.weight) for j in profile.judge_models],
            )
            cost_per_subject = estimate_judge_cost(specs)
        total = round(cost_per_subject * subject_count, 6)
        monthly = await self._cost.monthly_spend(
            org_id=org_id, project_ids=project_ids, profile_id=profile_id
        )
        decision = CostGuard.decide(
            profile.cost_guard,
            projected_usd=total,
            subject_count=subject_count,
            monthly_spent_usd=monthly,
        )
        judge_calls = 0 if rule_only else len(profile.judge_models) * subject_count
        return RunEstimate(
            subject_count=subject_count,
            judge_calls=judge_calls,
            cost_estimate_usd=total,
            rule_only=rule_only,
            monthly_spent_usd=monthly,
            cost_guard_allowed=decision.allowed,
            cost_guard_note=decision.note,
            cost_guard_downgrade=decision.downgrade_judges,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        org_id: str,
        profile_id: str | None,
        profile: ProfileDTO | None,
        project_id: str | None,
        trace_ids: list[str],
        trigger_lane: str,
        triggered_by: str | None,
        schedule_id: str | None = None,
        notes: str = "",
        project_scope: list[str] | None = None,
        run_mode: str | None = None,
        golden_set_id: str | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> RunDTO:
        """Synchronously run all evaluations for the supplied ``trace_ids``.

        The trigger_lane controls accounting and UI badging; cost-guard is
        applied for any lane that involves judge models. Rule-auto runs
        skip the guard entirely (rules are free)."""

        # --------------------------------------------------------------
        # Resolve profile (allow caller to supply it directly to avoid an
        # extra DB hit when we already have it — used by the auto-rule path)
        # --------------------------------------------------------------
        if profile is None:
            if profile_id is None:
                raise LookupError("profile not provided")
            profile = await self._profiles.get(
                org_id=org_id, profile_id=profile_id, project_ids=project_scope
            )
            if profile is None:
                raise LookupError("profile not found")
        profile_id = profile.id

        rm = (run_mode or EvalRunMode.TRACE.value).lower()
        if rm not in {m.value for m in EvalRunMode}:
            rm = EvalRunMode.TRACE.value
        ctx_blob: dict[str, Any] = dict(run_context or {})
        if rm in (EvalRunMode.GOLDEN_GT.value, EvalRunMode.GOLDEN_JUDGE.value):
            if not (golden_set_id or "").strip():
                raise LookupError("golden_set_id is required for golden_gt and golden_judge runs")
            if self._goldens is None:
                raise LookupError("golden set service is not configured")

        rule_only_lane = trigger_lane in {TriggerLane.RULE_AUTO.value, TriggerLane.RULE_REPLAY.value}
        wants_judges = (not rule_only_lane) and bool(profile.judge_models)

        # --------------------------------------------------------------
        # Cost guard pre-flight
        # --------------------------------------------------------------
        downgrade = False
        cost_estimate = 0.0
        guard_note = ""
        if wants_judges:
            specs = await self._judge_models.resolve_specs(
                org_id=org_id,
                refs=[(j.model_id, j.weight) for j in profile.judge_models],
            )
            est_per_subject = estimate_judge_cost(specs)
            cost_estimate = round(est_per_subject * len(trace_ids), 6)
            monthly = await self._cost.monthly_spend(
                org_id=org_id,
                project_ids=[project_id] if project_id else None,
                profile_id=profile_id,
            )
            decision = CostGuard.decide(
                profile.cost_guard,
                projected_usd=cost_estimate,
                subject_count=max(1, len(trace_ids)),
                monthly_spent_usd=monthly,
            )
            if not decision.allowed:
                raise PermissionError(f"cost guard blocked run: {decision.note}")
            downgrade = decision.downgrade_judges
            guard_note = decision.note
            if downgrade:
                wants_judges = False
                cost_estimate = 0.0

        # --------------------------------------------------------------
        # Persist run row in `running` state up front so the UI sees it
        # --------------------------------------------------------------
        run_id = uuid.uuid4().hex
        async with self._sf() as s:
            row = EvalRunRow(
                id=run_id,
                org_id=org_id,
                project_id=project_id,
                profile_id=profile_id,
                schedule_id=schedule_id,
                trigger_lane=trigger_lane,
                triggered_by=triggered_by,
                status=RunStatus.RUNNING.value,
                subject_count=len(trace_ids),
                completed_count=0,
                failed_count=0,
                cost_estimate_usd=cost_estimate,
                cost_actual_usd=0.0,
                pass_rate=0.0,
                avg_score=0.0,
                notes=guard_note or notes,
                run_mode=rm,
                golden_set_id=(golden_set_id.strip() if golden_set_id else None),
                run_context_json=json.dumps(ctx_blob, ensure_ascii=False, default=str),
                started_at=_now(),
                finished_at=None,
            )
            s.add(row)
            await s.commit()

        human_by_trace: dict[str, Any] = {}
        if self._human_labels is not None:
            reg = await self._human_labels.batch_get_for_traces(
                org_id=org_id, trace_ids=trace_ids
            )
            human_by_trace.update(reg)
        for h in ctx_blob.get("humanLabels") or []:
            if not isinstance(h, dict):
                continue
            tid = str(h.get("traceId") or h.get("trace_id") or "").strip()
            if tid:
                prev = human_by_trace.get(tid) if isinstance(
                    human_by_trace.get(tid), dict
                ) else {}
                if not isinstance(prev, dict):
                    prev = {}
                merged = {**prev, **h}
                human_by_trace[tid] = merged

        by_tr_g: dict[str, dict[str, Any]] = {}
        by_q_g: dict[str, dict[str, Any]] = {}
        if (
            golden_set_id
            and self._goldens is not None
            and rm in (EvalRunMode.GOLDEN_GT.value, EvalRunMode.GOLDEN_JUDGE.value)
        ):
            items = await self._goldens.list_items(
                org_id=org_id,
                set_id=golden_set_id,
                project_ids=project_scope,
                status=None,
                limit=500,
            )
            for it in items:
                pl = it.payload or {}
                blob = {
                    "itemId": it.id,
                    "layer": it.layer,
                    "payload": pl,
                    "sourceTraceId": it.source_trace_id,
                }
                if it.source_trace_id:
                    by_tr_g[it.source_trace_id] = blob
                q = pl.get("query")
                if q and str(q).strip():
                    by_q_g.setdefault(_norm_q(str(q)), blob)

        # --------------------------------------------------------------
        # Iterate subjects
        # --------------------------------------------------------------
        completed = 0
        failed = 0
        # 12 §4: subjects whose judges all failed are tracked separately
        # so they can be excluded from the pass-rate / avg-score
        # aggregates while still being persisted for the error breakdown.
        error_count = 0
        score_sum = 0.0
        pass_count = 0
        actual_cost = 0.0
        judge_in_tokens_total = 0
        judge_out_tokens_total = 0
        judge_calls_total = 0

        judge_specs = []
        if wants_judges:
            judge_specs = await self._judge_models.resolve_specs(
                org_id=org_id,
                refs=[(j.model_id, j.weight) for j in profile.judge_models],
            )

        # Load active versioned prompts from DB for dimension-level overrides.
        from easyobs.eval.judge.defaults import resolve_active_prompts

        active_dim_prompts = await resolve_active_prompts(org_id) if wants_judges else {}

        for trace_id in trace_ids:
            try:
                trace = await self._fetch_trace(trace_id, project_scope)
                if trace is None:
                    failed += 1
                    continue
                summary = trace.get("llmSummary") or {}
                extra: dict[str, Any] = {}
                hl = human_by_trace.get(trace_id)
                if hl:
                    extra["humanLabel"] = hl
                gblob = None
                if rm in (EvalRunMode.GOLDEN_GT.value, EvalRunMode.GOLDEN_JUDGE.value):
                    gblob = _pick_golden_blob(trace_id, summary, by_tr_g, by_q_g)
                    if gblob:
                        extra["golden"] = gblob
                ctx = RuleContext(
                    trace=trace,
                    summary=summary,
                    spans=trace.get("spans") or [],
                    extra=extra,
                )
                rule_findings, rule_score = self._run_rule_layer(profile, ctx)
                if rm == EvalRunMode.GOLDEN_GT.value and gblob:
                    exp = _expected_from_golden_payload(gblob.get("payload") or {})
                    act = str(summary.get("response") or "")
                    rule_findings.append(_golden_gt_finding(exp, act))
                    rule_score = _mean_rule_scores(rule_findings)
                judge_findings: list[FindingDTO] = []
                judge_per_model: list[dict[str, Any]] = []
                judge_score: float | None = None
                judge_disagreement: float | None = None
                judge_in_tokens = 0
                judge_out_tokens = 0
                judge_cost = 0.0
                judge_error_detail: dict[str, Any] = {}
                judge_total_failure = False
                if wants_judges and judge_specs:
                    request = self._build_judge_request(
                        profile, ctx, run_mode=rm, active_dim_prompts=active_dim_prompts
                    )
                    outcome = await run_judges(
                        models=judge_specs,
                        request=request,
                        consensus_policy=profile.consensus,
                    )
                    judge_score = outcome.consensus.score
                    judge_disagreement = outcome.consensus.disagreement
                    judge_per_model = outcome.per_model
                    judge_in_tokens = outcome.total_input_tokens
                    judge_out_tokens = outcome.total_output_tokens
                    judge_cost = outcome.total_cost_usd
                    judge_calls_total += outcome.judge_calls
                    judge_in_tokens_total += judge_in_tokens
                    judge_out_tokens_total += judge_out_tokens
                    actual_cost = round(actual_cost + judge_cost, 6)
                    judge_error_detail = dict(outcome.judge_error_detail or {})
                    judge_total_failure = bool(judge_error_detail.get("totalFailure"))
                    judge_findings.append(
                        FindingDTO(
                            evaluator_id="judge.consensus",
                            kind="judge",
                            score=outcome.consensus.score,
                            verdict=outcome.consensus.verdict.value,
                            reason=outcome.consensus.reason,
                            details={
                                "agreementRatio": outcome.consensus.agreement_ratio,
                                "perModel": outcome.per_model,
                                **(
                                    {"judgeErrorDetail": judge_error_detail}
                                    if judge_error_detail
                                    else {}
                                ),
                            },
                        )
                    )
                # Combine score: weighted mean of rule + judge (when present).
                # 12 §4: when every judge model failed we mark the result
                # ``Verdict.ERROR`` so it can be excluded from pass-rate /
                # avg-score aggregates (see ``_finalise_run``).
                if judge_total_failure:
                    final_score, final_verdict = 0.0, Verdict.ERROR
                else:
                    final_score, final_verdict = self._combine(
                        rule_score=rule_score, judge_score=judge_score
                    )
                if final_verdict == Verdict.ERROR:
                    error_count += 1
                else:
                    score_sum += final_score
                    if final_verdict == Verdict.PASS:
                        pass_count += 1
                    completed += 1
                session_key = None
                if isinstance(trace.get("llmSummary"), dict):
                    session_key = trace["llmSummary"].get("session")
                elif trace.get("session"):
                    session_key = trace.get("session")
                result_row_id = await self._persist_result(
                    run_id=run_id,
                    org_id=org_id,
                    project_id=project_id,
                    trace_id=trace_id,
                    session_id=str(session_key) if session_key else None,
                    rule_score=rule_score,
                    judge_score=judge_score,
                    judge_disagreement=judge_disagreement,
                    judge_in_tokens=judge_in_tokens,
                    judge_out_tokens=judge_out_tokens,
                    judge_cost=judge_cost,
                    score=final_score,
                    verdict=final_verdict,
                    findings=rule_findings + judge_findings,
                    judge_per_model=judge_per_model,
                    trigger_lane=trigger_lane,
                    judge_error_detail=judge_error_detail,
                )
                # 12 §4: skip improvement pack generation for ERROR rows —
                # there is nothing to improve when the verdict was excluded
                # because the judge call itself failed.
                if (
                    self._improvements is not None
                    and final_verdict not in (Verdict.PASS, Verdict.ERROR)
                ):
                    imp_loc = str(
                        ctx_blob.get("uiLocale")
                        or profile.improvement_content_locale
                        or "en"
                    ).lower()
                    if imp_loc not in {"en", "ko"}:
                        imp_loc = "en"
                    proposals = derive_proposals(
                        findings=[
                            {
                                "evaluator_id": f.evaluator_id,
                                "score": f.score,
                                "verdict": f.verdict,
                                "reason": f.reason,
                            }
                            for f in (rule_findings + judge_findings)
                        ],
                        pack_id=profile.improvement_pack,
                        locale=imp_loc,
                    )
                    if not proposals:
                        proposals = fallback_proposals_for_non_pass(
                            final_score=final_score,
                            final_verdict=final_verdict.value,
                            pack_id=profile.improvement_pack,
                            locale=imp_loc,
                        )
                    await self._improvements.create(
                        org_id=org_id,
                        project_id=project_id,
                        result_id=result_row_id,
                        trace_id=trace_id,
                        summary=f"Auto-pack · trace {trace_id[:8]} · run {run_id[:8]}",
                        proposals=proposals,
                        judge_models=[m.id for m in judge_specs],
                        consensus_policy=profile.consensus,
                        agreement_ratio=judge_findings[0].details.get(
                            "agreementRatio", 1.0
                        )
                        if judge_findings
                        else 1.0,
                        judge_cost_usd=judge_cost,
                        actor=triggered_by,
                        improvement_pack=profile.improvement_pack,
                        improvement_content_locale=imp_loc,
                    )
            except Exception:
                _log.exception("trace evaluation failed", extra={"trace_id": trace_id})
                failed += 1

        # --------------------------------------------------------------
        # Cost roll-up + run summary
        # --------------------------------------------------------------
        await self._cost.record(
            org_id=org_id,
            project_id=project_id,
            profile_id=profile_id,
            judge_calls=judge_calls_total,
            judge_input_tokens=judge_in_tokens_total,
            judge_output_tokens=judge_out_tokens_total,
            judge_cost_usd=actual_cost,
            rule_evals=completed * len(profile.evaluators),
        )

        run_dto = await self._finalise_run(
            run_id=run_id,
            completed=completed,
            failed=failed,
            error_count=error_count,
            score_sum=score_sum,
            pass_count=pass_count,
            actual_cost=actual_cost,
        )
        return run_dto

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_runs(
        self,
        *,
        org_id: str,
        project_ids: list[str] | None,
        limit: int = 100,
    ) -> list[RunDTO]:
        async with self._sf() as s:
            stmt = select(EvalRunRow).where(EvalRunRow.org_id == org_id)
            rows = (
                await s.execute(stmt.order_by(desc(EvalRunRow.started_at)).limit(limit))
            ).scalars().all()
            if project_ids is not None:
                allowed = set(project_ids)
                rows = [r for r in rows if r.project_id is None or r.project_id in allowed]
            return [_run_dto(r) for r in rows]

    async def get_run(
        self, *, org_id: str, run_id: str, project_ids: list[str] | None
    ) -> RunDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            return _run_dto(row)

    async def list_results(
        self,
        *,
        org_id: str,
        run_id: str,
        project_ids: list[str] | None,
        limit: int = 500,
    ) -> list[ResultDTO]:
        async with self._sf() as s:
            run = await s.get(EvalRunRow, run_id)
            if run is None or run.org_id != org_id:
                return []
            if (
                project_ids is not None
                and run.project_id is not None
                and run.project_id not in project_ids
            ):
                return []
            stmt = (
                select(EvalResultRow)
                .where(EvalResultRow.run_id == run_id)
                .order_by(EvalResultRow.score)
                .limit(limit)
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [_result_dto(r) for r in rows]

    async def get_result(
        self,
        *,
        org_id: str,
        result_id: str,
        project_ids: list[str] | None,
    ) -> ResultDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalResultRow, result_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            return _result_dto(row)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_trace(
        self, trace_id: str, project_scope: list[str] | None
    ) -> dict[str, Any] | None:
        if self._load_trace is None:
            return None
        try:
            return await self._load_trace(trace_id)
        except Exception:
            _log.warning("trace loader failed for %s", trace_id, exc_info=True)
            return None

    def _run_rule_layer(
        self, profile: ProfileDTO, ctx: RuleContext
    ) -> tuple[list[FindingDTO], float]:
        findings: list[FindingDTO] = []
        weighted_sum = 0.0
        weight_total = 0.0
        skip_rule = judge_metric_ids() | human_metric_ids()
        for ref in profile.evaluators:
            if ref.evaluator_id in skip_rule:
                continue
            outcome = run_evaluator(ref.evaluator_id, ctx, ref.params)
            if outcome.details.get("catalog_stub"):
                # Catalog placeholders / judge-only rows — no finding noise.
                continue
            findings.append(
                FindingDTO(
                    evaluator_id=ref.evaluator_id,
                    kind="rule",
                    score=outcome.score,
                    verdict=outcome.verdict.value,
                    reason=outcome.reason,
                    details=outcome.details,
                )
            )
            if outcome.verdict == Verdict.UNSET:
                continue
            weighted_sum += outcome.score * ref.weight
            weight_total += ref.weight
        rule_score = round(weighted_sum / weight_total, 4) if weight_total else 0.0
        return findings, rule_score

    def _build_judge_request(
        self,
        profile: ProfileDTO,
        ctx: RuleContext,
        *,
        run_mode: str,
        active_dim_prompts: dict[str, dict[str, str]] | None = None,
    ) -> JudgeRequest:
        lines: list[str] = []
        mode_line = _run_mode_rubric_line(run_mode)
        if mode_line:
            lines.append(mode_line)
        lines.extend(
            [
                f"Profile: {profile.name}",
                f"Description: {profile.description or '—'}",
                "Evaluators (rule layer summary): "
                + ", ".join(e.evaluator_id for e in profile.evaluators[:6])
                + ("…" if len(profile.evaluators) > 6 else ""),
            ]
        )
        mmeta = metric_row_by_id()
        judge_lines: list[str] = []
        for e in profile.evaluators:
            row = mmeta.get(e.evaluator_id)
            if not row or str(row.get("kind")) != "judge":
                continue
            code = str(row.get("code") or "")
            title = str(row.get("name") or e.evaluator_id)
            cause = str(row.get("causeCode") or "")
            jd = row.get("judgeDimension")
            dim = f" Align with checklist dimension `{jd}`." if jd else ""
            judge_lines.append(
                f"- {code} {title}: holistic 0..1 score; failure cause `{cause}`.{dim}"
            )
        if judge_lines:
            lines.append("Profile-selected LLM-as-a-Judge metrics (score each implicitly):")
            lines.extend(judge_lines)
        for e in profile.evaluators:
            hint = (e.params or {}).get("judgeHint") or (e.params or {}).get(
                "judge_hint"
            )
            hint_ko = (e.params or {}).get("judgeHintKo") or (e.params or {}).get(
                "judge_hint_ko"
            )
            if hint:
                lines.append(f"Evaluator guidance ({e.evaluator_id}): {hint}")
            if hint_ko:
                lines.append(
                    f"Evaluator guidance (KO) ({e.evaluator_id}): {hint_ko}"
                )
        auto_prompt = "\n".join(lines)
        extra = (profile.judge_rubric_text or "").strip()
        rubric_mode = (profile.judge_rubric_mode or "append").lower()
        if rubric_mode == "replace" and extra:
            prompt = ((mode_line + "\n\n") if mode_line else "") + extra
        elif rubric_mode == "replace" and not extra:
            prompt = auto_prompt
        else:
            prompt = auto_prompt
            if extra:
                prompt = prompt + "\n\nOperator rubric:\n" + extra
        context: dict[str, Any] = {
            "query": ctx.summary.get("query"),
            "response": ctx.summary.get("response"),
            "session": ctx.summary.get("session"),
            "models": ctx.summary.get("models"),
            "tokens": ctx.summary.get("tokensTotal"),
            "runMode": run_mode,
        }
        if ctx.extra.get("humanLabel"):
            context["humanLabel"] = ctx.extra["humanLabel"]
        if ctx.extra.get("golden"):
            context["golden"] = ctx.extra["golden"]
        # Control for judge metrics that require GT (e.g. correctness/completeness).
        has_l3_gt = bool(
            (ctx.extra.get("golden") or {}).get("expected_response")
            or (ctx.extra.get("humanLabel") or {}).get("expectedResponse")
        )
        gt_required: list[str] = []
        gt_missing: list[str] = []
        for e in profile.evaluators:
            row = mmeta.get(e.evaluator_id)
            if not row or str(row.get("kind")) != "judge":
                continue
            gt = str(row.get("gt") or "—")
            if gt in {"—", "없음", ""}:
                continue
            code = str(row.get("code") or e.evaluator_id)
            gt_required.append(code)
            if gt == "L3" and not has_l3_gt:
                gt_missing.append(code)
        if gt_required:
            context["judgeMetricControls"] = {
                "gtRequiredMetrics": gt_required,
                "missingGtMetrics": gt_missing,
                "hasL3Gt": has_l3_gt,
            }
        selected_dim_ids = {
            _JUDGE_METRIC_DIMENSIONS[e.evaluator_id]
            for e in profile.evaluators
            if e.evaluator_id in _JUDGE_METRIC_DIMENSIONS
        }
        # RAGAS-inspired checklist (metadata + profile per-dimension criterion overrides).
        context["evaluationHints"] = build_evaluation_hints(
            profile_overrides=dict(profile.judge_dimension_prompts or {}),
            include_ids=selected_dim_ids if selected_dim_ids else None,
        )
        sys_override = (profile.judge_system_prompt or "").strip() or None
        from easyobs.eval.judge.defaults import build_profile_user_message

        # Merge DB versioned prompts: if active_dim_prompts are available for selected
        # dimensions and the profile doesn't already override, use them.
        merged_sys_parts: list[str] = []
        merged_user_template: str | None = profile.judge_user_message_template or None
        if active_dim_prompts and selected_dim_ids:
            for dim_id in selected_dim_ids:
                dp = active_dim_prompts.get(dim_id)
                if dp:
                    if dp.get("system_prompt") and not sys_override:
                        merged_sys_parts.append(dp["system_prompt"])
                    if dp.get("user_message_template") and not merged_user_template:
                        merged_user_template = dp["user_message_template"]
        if merged_sys_parts and not sys_override:
            sys_override = "\n\n".join(merged_sys_parts)

        user_msg = build_profile_user_message(
            rubric_id=profile.id,
            rubric=prompt,
            context=context,
            template_override=merged_user_template,
        )
        return JudgeRequest(
            rubric_id=profile.id,
            prompt=prompt,
            context=context,
            system_prompt=sys_override,
            user_message=user_msg,
        )

    def _combine(
        self, *, rule_score: float, judge_score: float | None
    ) -> tuple[float, Verdict]:
        if judge_score is None:
            score = rule_score
        else:
            # 50/50 weighting matches the design doc default. The Judge has
            # the final say on tie-breaks because it sees the full text.
            score = round(rule_score * 0.5 + judge_score * 0.5, 4)
        if score >= 0.7:
            verdict = Verdict.PASS
        elif score >= 0.4:
            verdict = Verdict.WARN
        else:
            verdict = Verdict.FAIL
        return score, verdict

    async def _persist_result(
        self,
        *,
        run_id: str,
        org_id: str,
        project_id: str | None,
        trace_id: str,
        session_id: str | None,
        rule_score: float,
        judge_score: float | None,
        judge_disagreement: float | None,
        judge_in_tokens: int,
        judge_out_tokens: int,
        judge_cost: float,
        score: float,
        verdict: Verdict,
        findings: list[FindingDTO],
        judge_per_model: list[dict[str, Any]],
        trigger_lane: str,
        judge_error_detail: dict[str, Any] | None = None,
    ) -> str:
        result_id = uuid.uuid4().hex
        async with self._sf() as s:
            row = EvalResultRow(
                id=result_id,
                run_id=run_id,
                org_id=org_id,
                project_id=project_id,
                trace_id=trace_id,
                session_id=session_id,
                score=score,
                verdict=verdict.value,
                rule_score=rule_score,
                judge_score=judge_score,
                judge_disagreement=judge_disagreement,
                judge_input_tokens=judge_in_tokens,
                judge_output_tokens=judge_out_tokens,
                judge_cost_usd=judge_cost,
                findings_json=json.dumps(
                    [_finding_to_json(f) for f in findings],
                    ensure_ascii=False,
                    default=str,
                ),
                judge_per_model_json=json.dumps(
                    judge_per_model, ensure_ascii=False, default=str
                ),
                trigger_lane=trigger_lane,
                judge_error_detail_json=json.dumps(
                    judge_error_detail or {}, ensure_ascii=False, default=str
                ),
                created_at=_now(),
            )
            s.add(row)
            await s.commit()
        return result_id

    async def _finalise_run(
        self,
        *,
        run_id: str,
        completed: int,
        failed: int,
        error_count: int,
        score_sum: float,
        pass_count: int,
        actual_cost: float,
    ) -> RunDTO:
        # 12 §4: pass-rate / avg-score are computed over the **evaluated**
        # population (success + soft fail) — Judge ERROR rows are
        # tracked separately so their absence does not skew the score
        # but the count still surfaces in the run summary.
        avg_score = round(score_sum / completed, 4) if completed else 0.0
        pass_rate = round(pass_count / completed, 4) if completed else 0.0
        notes_suffix = ""
        if error_count > 0:
            notes_suffix = f" · judge_error={error_count}"
        async with self._sf() as s:
            row = await s.get(EvalRunRow, run_id)
            if row is None:
                raise LookupError("run vanished mid-execution")
            row.status = RunStatus.COMPLETED.value
            row.completed_count = completed
            # ``failed_count`` keeps its original meaning ("could not
            # evaluate at all") while error_count rolls up under the
            # same column so existing dashboards do not break.
            row.failed_count = failed + error_count
            row.avg_score = avg_score
            row.pass_rate = pass_rate
            row.cost_actual_usd = actual_cost
            row.finished_at = _now()
            if notes_suffix and notes_suffix not in (row.notes or ""):
                row.notes = (row.notes or "") + notes_suffix
            await s.commit()
            await s.refresh(row)
            return _run_dto(row)


# ---------------------------------------------------------------------------
# DTO builders
# ---------------------------------------------------------------------------


def _run_dto(row: EvalRunRow) -> RunDTO:
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
        run_mode=getattr(row, "run_mode", None) or EvalRunMode.TRACE.value,
        golden_set_id=getattr(row, "golden_set_id", None),
        run_context=rc,
    )


def _result_dto(row: EvalResultRow) -> ResultDTO:
    try:
        findings_raw = json.loads(row.findings_json or "[]")
    except Exception:
        findings_raw = []
    findings = [
        FindingDTO(
            evaluator_id=str(f.get("evaluatorId") or f.get("evaluator_id") or ""),
            kind=str(f.get("kind") or ""),
            score=float(f.get("score") or 0.0),
            verdict=str(f.get("verdict") or "unset"),
            reason=str(f.get("reason") or ""),
            details=dict(f.get("details") or {}),
        )
        for f in findings_raw
    ]
    try:
        per_model = json.loads(row.judge_per_model_json or "[]")
    except Exception:
        per_model = []
    try:
        judge_error_detail = json.loads(getattr(row, "judge_error_detail_json", None) or "{}")
    except Exception:
        judge_error_detail = {}
    if not isinstance(judge_error_detail, dict):
        judge_error_detail = {}
    return ResultDTO(
        id=row.id,
        run_id=row.run_id,
        org_id=row.org_id,
        project_id=row.project_id,
        trace_id=row.trace_id,
        session_id=getattr(row, "session_id", None),
        score=row.score,
        verdict=row.verdict,
        rule_score=row.rule_score,
        judge_score=row.judge_score,
        judge_disagreement=row.judge_disagreement,
        judge_input_tokens=row.judge_input_tokens,
        judge_output_tokens=row.judge_output_tokens,
        judge_cost_usd=row.judge_cost_usd,
        findings=findings,
        judge_per_model=per_model,
        trigger_lane=row.trigger_lane,
        created_at=row.created_at,
        judge_error_detail=judge_error_detail,
    )


def _finding_to_json(finding: FindingDTO) -> dict[str, Any]:
    return {
        "evaluatorId": finding.evaluator_id,
        "kind": finding.kind,
        "score": finding.score,
        "verdict": finding.verdict,
        "reason": finding.reason,
        "details": finding.details,
    }
