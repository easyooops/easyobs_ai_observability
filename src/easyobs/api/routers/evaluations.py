"""HTTP surface for the Quality (evaluation) module.

Every route in here gates on the (org_id × project_id) access matrix the
operational tracing path already enforces. We do this through the existing
:func:`resolve_caller_scope` dependency: anything that is not in the
caller's accessible service set is dropped *before* the service layer
sees the request, so the eval domain inherits the same tenancy model
without re-implementing it.

The router stays opt-in — the lifespan only mounts it when
``settings.eval_enabled`` is true, so deployments that haven't enabled
Quality see neither the routes nor any state attached to them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from easyobs.api.deps import CallerScope
from easyobs.api.security import CurrentUser
from easyobs.eval.judge.dimensions_meta import list_judge_dimension_catalog
from easyobs.eval.judge.providers import get_provider
from easyobs.eval.services import (
    AgentInvokeSettings,
    EvaluatorCatalogService,
    GoldenRegressionRequest,
    GoldenRegressionService,
    GoldenSetService,
    HumanLabelService,
    ImprovementService,
    JudgeModelService,
    ProfileService,
    ProgressBroker,
    RunService,
    ScheduleService,
    SynthJobRequest,
    SynthesizerService,
    TrustService,
)
from easyobs.eval.services.agent_invoke import test_agent_connection
from easyobs.eval.services.cost import CostService
from easyobs.eval.services.golden_upload import (
    SUPPORTED_GOLDEN_PATHS,
    UploadError,
    validate_upload,
)
from easyobs.eval.services.improvements import list_improvement_pack_meta
from easyobs.eval.types import (
    EvalRunMode,
    GoldenLayer,
    GoldenSetMode,
    SynthJobMode,
    SynthJobSourcePolicy,
    TriggerLane,
)

router = APIRouter(prefix="/v1/evaluations", tags=["evaluations"])


# ---------------------------------------------------------------------------
# Service accessors (kept as module-local helpers so dependency typing stays
# obvious in the route signatures below).
# ---------------------------------------------------------------------------


def _services(request: Request) -> dict[str, Any]:
    bag = getattr(request.app.state, "eval_services", None)
    if bag is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="evaluation module disabled",
        )
    return bag


def _profile_svc(request: Request) -> ProfileService:
    return _services(request)["profiles"]


def _judge_model_svc(request: Request) -> JudgeModelService:
    return _services(request)["judge_models"]


def _run_svc(request: Request) -> RunService:
    return _services(request)["runs"]


def _golden_svc(request: Request) -> GoldenSetService:
    return _services(request)["goldensets"]


def _improve_svc(request: Request) -> ImprovementService:
    return _services(request)["improvements"]


def _schedule_svc(request: Request) -> ScheduleService:
    return _services(request)["schedules"]


def _cost_svc(request: Request) -> CostService:
    return _services(request)["cost"]


def _evaluator_svc(request: Request) -> EvaluatorCatalogService:
    return _services(request)["evaluators"]


def _human_label_svc(request: Request) -> HumanLabelService:
    return _services(request)["human_labels"]


def _golden_regression_svc(request: Request) -> GoldenRegressionService:
    return _services(request)["golden_regression"]


def _synth_svc(request: Request) -> SynthesizerService:
    return _services(request)["synthesizer"]


def _trust_svc(request: Request) -> TrustService:
    return _services(request)["trust"]


def _progress_broker(request: Request) -> ProgressBroker:
    return _services(request)["progress"]


def _require_org(caller: CurrentUser) -> str:
    if not caller.current_org:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="select an organization first")
    return caller.current_org


def _require_write(caller: CurrentUser) -> None:
    """Eval mutation requires PO/SA-equivalent powers within the active
    org. Platform-member (admin/DV) is read-only across orgs, matching the
    pattern used by the trace routes."""
    if caller.is_super_admin or caller.is_platform_admin:
        return
    if caller.is_platform_member:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="evaluation write requires PO of the active org",
        )
    # Fall through — the per-route service layer also rejects org/project
    # mismatch; this gate just keeps cross-org reads honest.


def _project_allowed(project_id: str | None, scope: list[str] | None) -> bool:
    if project_id is None:
        return True
    if scope is None:
        return True
    return project_id in set(scope)


# ---------------------------------------------------------------------------
# Pydantic input models (router-level validation only)
# ---------------------------------------------------------------------------


class EvaluatorRefIn(BaseModel):
    evaluator_id: str
    weight: float = 1.0
    threshold: float = 0.6
    params: dict[str, Any] = Field(default_factory=dict)


class JudgeRefIn(BaseModel):
    model_id: str
    weight: float = 1.0


class CostGuardIn(BaseModel):
    max_cost_usd_per_run: float = 5.0
    max_cost_usd_per_subject: float = 0.05
    monthly_budget_usd: float = 100.0
    on_exceed: str = "block"


class ProfileIn(BaseModel):
    project_id: str | None = None
    name: str
    description: str = ""
    evaluators: list[EvaluatorRefIn] = Field(default_factory=list)
    judge_models: list[JudgeRefIn] = Field(default_factory=list)
    consensus: str = "single"
    auto_run: bool = False
    cost_guard: CostGuardIn = Field(default_factory=CostGuardIn)
    enabled: bool = True
    judge_rubric_text: str = ""
    judge_rubric_mode: str = "append"
    judge_system_prompt: str = ""
    judge_user_message_template: str = ""
    improvement_pack: str = "easyobs_standard"
    judge_dimension_prompts: dict[str, dict[str, str]] = Field(default_factory=dict)
    improvement_content_locale: str = "en"


class JudgeModelIn(BaseModel):
    name: str
    provider: str = "mock"
    model: str = ""
    temperature: float = 0.0
    weight: float = 1.0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    connection_config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class JudgeModelPatch(BaseModel):
    name: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    weight: float | None = None
    cost_per_1k_input: float | None = None
    cost_per_1k_output: float | None = None
    connection_config: dict[str, Any] | None = None
    enabled: bool | None = None


class JudgePromptIn(BaseModel):
    dimension_id: str
    system_prompt: str = ""
    user_message_template: str = ""
    description: str = ""


class HumanLabelIn(BaseModel):
    trace_id: str
    expected_response: str | None = None
    human_verdict: str | None = None
    notes: str = ""


class RunRequestIn(BaseModel):
    profile_id: str
    project_id: str | None = None
    trace_ids: list[str]
    trigger_lane: str = TriggerLane.JUDGE_MANUAL.value
    notes: str = ""
    run_mode: str = EvalRunMode.TRACE.value
    golden_set_id: str | None = None
    run_context: dict[str, Any] = Field(default_factory=dict)
    human_labels: list[HumanLabelIn] = Field(default_factory=list)


class EstimateIn(BaseModel):
    profile_id: str
    subject_count: int
    project_id: str | None = None


class GoldenSetIn(BaseModel):
    project_id: str | None = None
    name: str
    layer: str
    description: str = ""
    mode: str = GoldenSetMode.REGRESSION.value
    expand_query: dict[str, Any] = Field(default_factory=dict)


class AgentInvokeSettingsIn(BaseModel):
    """12 §2.3 — agent connection settings for Regression Run."""

    endpoint_url: str = ""
    request_template: dict[str, Any] = Field(default_factory=dict)
    auth_ref: str = ""
    timeout_sec: int = 30
    max_concurrent: int = 5


class RegressionRunIn(BaseModel):
    profile_id: str
    notes: str = ""
    collect_timeout_sec: int | None = None
    max_concurrent: int | None = None


class SynthJobIn(BaseModel):
    mode: str = SynthJobMode.RAG_AWARE.value
    source_policy: str = SynthJobSourcePolicy.TRACE_FREQ.value
    source_spec: dict[str, Any] = Field(default_factory=dict)
    judge_model_id: str | None = None
    target_count: int = 20
    custom_prompt: str | None = None


class GoldenItemReviewPatch(BaseModel):
    review_state: str | None = None
    label_kind: str | None = None
    dispute_reason: str | None = None


class DisputeIn(BaseModel):
    reason: str


class GoldenItemIn(BaseModel):
    payload: dict[str, Any]


class GoldenItemFromTraceIn(BaseModel):
    trace_id: str
    labels: dict[str, Any] = Field(default_factory=dict)


class GoldenItemAutoIn(BaseModel):
    project_id: str
    sample_size: int = 20


class GoldenItemPatch(BaseModel):
    status: str


class ScheduleIn(BaseModel):
    project_id: str
    profile_id: str
    name: str
    interval_hours: int = 24
    sample_size: int = 50
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = None
    interval_hours: int | None = None
    sample_size: int | None = None
    enabled: bool | None = None


class ImprovementPatch(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Evaluator catalog (read-only)
# ---------------------------------------------------------------------------


@router.get("/evaluators")
async def list_evaluators(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
):
    _ = scope  # caller must be authenticated; catalog is org-agnostic
    return {"items": _evaluator_svc(request).list()}


@router.get("/improvement-packs")
async def list_improvement_packs(
    caller: CurrentUser,
    scope: CallerScope,
):
    _ = scope
    _ = _require_org(caller)
    return {"items": list_improvement_pack_meta()}


@router.get("/judge-dimensions")
async def judge_dimensions_catalog(
    caller: CurrentUser,
    scope: CallerScope,
):
    _ = scope
    _ = _require_org(caller)
    return {"items": list_judge_dimension_catalog()}


# ---------------------------------------------------------------------------
# Human label registry (separate from ad-hoc JSON on manual runs)
# ---------------------------------------------------------------------------


class HumanLabelBody(BaseModel):
    trace_id: str
    project_id: str | None = None
    expected_response: str = ""
    human_verdict: str = ""
    notes: str = ""


@router.get("/human-labels")
async def list_human_labels(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    limit: Annotated[int, Query()] = 80,
):
    _ = scope
    org_id = _require_org(caller)
    return {"items": await _human_label_svc(request).list_for_org(org_id=org_id, limit=limit)}


@router.post("/human-labels", status_code=status.HTTP_201_CREATED)
async def upsert_human_label(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: HumanLabelBody,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    row = await _human_label_svc(request).upsert(
        org_id=org_id,
        trace_id=body.trace_id,
        project_id=body.project_id,
        expected_response=body.expected_response,
        human_verdict=body.human_verdict,
        notes=body.notes,
        actor_user_id=caller.user_id,
    )
    return row


@router.delete("/human-labels/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_human_label(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    annotation_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _human_label_svc(request).delete(
        org_id=org_id, annotation_id=annotation_id
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")


# ---------------------------------------------------------------------------
# Judge models
# ---------------------------------------------------------------------------


@router.get("/judge-models")
async def list_judge_models(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    include_disabled: Annotated[bool, Query()] = False,
):
    _ = scope
    org_id = _require_org(caller)
    rows = await _judge_model_svc(request).list(
        org_id=org_id, include_disabled=include_disabled
    )
    return {"items": [_judge_model_to_json(m) for m in rows]}


@router.post("/judge-models", status_code=status.HTTP_201_CREATED)
async def create_judge_model(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: JudgeModelIn,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    if get_provider(body.provider) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unknown provider {body.provider!r}")
    row = await _judge_model_svc(request).create(
        org_id=org_id,
        name=body.name,
        provider=body.provider,
        model=body.model,
        temperature=body.temperature,
        weight=body.weight,
        cost_per_1k_input=body.cost_per_1k_input,
        cost_per_1k_output=body.cost_per_1k_output,
        enabled=body.enabled,
        actor=caller.user_id,
        connection_config=body.connection_config,
    )
    return _judge_model_to_json(row)


@router.patch("/judge-models/{model_id}")
async def patch_judge_model(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    model_id: str,
    body: JudgeModelPatch,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    patch = body.model_dump(exclude_none=True)
    if "connection_config" in patch:
        patch["connection_config_json"] = json.dumps(patch.pop("connection_config"))
    row = await _judge_model_svc(request).update(
        org_id=org_id, model_id=model_id, **patch
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="judge model not found")
    return _judge_model_to_json(row)


@router.delete("/judge-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_judge_model(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    model_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _judge_model_svc(request).delete(org_id=org_id, model_id=model_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="judge model not found")


# ---------------------------------------------------------------------------
# Judge prompts (versioned per dimension)
# ---------------------------------------------------------------------------


@router.get("/judge-prompts")
async def list_judge_prompts(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    dimension_id: Annotated[str | None, Query()] = None,
):
    _ = scope
    org_id = _require_org(caller)
    from easyobs.db.models import EvalJudgePromptRow

    session = _services(request).get("db_session")
    if session is None:
        from easyobs.db.session import session_scope

        session = session_scope()
    from sqlalchemy import select

    async with session() as db:
        stmt = select(EvalJudgePromptRow).where(
            EvalJudgePromptRow.org_id == org_id
        ).order_by(EvalJudgePromptRow.dimension_id, EvalJudgePromptRow.version.desc())
        if dimension_id:
            stmt = stmt.where(EvalJudgePromptRow.dimension_id == dimension_id)
        rows = (await db.execute(stmt)).scalars().all()
    return {"items": [_judge_prompt_to_json(r) for r in rows]}


@router.post("/judge-prompts", status_code=status.HTTP_201_CREATED)
async def create_judge_prompt(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: JudgePromptIn,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    from easyobs.eval.judge.dimensions_meta import JUDGE_DIMENSION_IDS

    if body.dimension_id not in JUDGE_DIMENSION_IDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unknown dimension: {body.dimension_id!r}",
        )
    from easyobs.db.models import EvalJudgePromptRow

    session = _services(request).get("db_session")
    if session is None:
        from easyobs.db.session import session_scope

        session = session_scope()
    import uuid
    from sqlalchemy import select, func

    async with session() as db:
        max_ver = (
            await db.execute(
                select(func.coalesce(func.max(EvalJudgePromptRow.version), 0)).where(
                    EvalJudgePromptRow.org_id == org_id,
                    EvalJudgePromptRow.dimension_id == body.dimension_id,
                )
            )
        ).scalar() or 0
        new_ver = max_ver + 1
        # Deactivate previous versions
        from sqlalchemy import update

        await db.execute(
            update(EvalJudgePromptRow)
            .where(
                EvalJudgePromptRow.org_id == org_id,
                EvalJudgePromptRow.dimension_id == body.dimension_id,
            )
            .values(is_active=False)
        )
        row = EvalJudgePromptRow(
            id=str(uuid.uuid4()),
            org_id=org_id,
            dimension_id=body.dimension_id,
            version=new_ver,
            system_prompt=body.system_prompt,
            user_message_template=body.user_message_template,
            is_active=True,
            description=body.description,
            created_at=datetime.now(timezone.utc),
            created_by=caller.user_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return _judge_prompt_to_json(row)


@router.post("/judge-prompts/{prompt_id}:activate")
async def activate_judge_prompt(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    prompt_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    from easyobs.db.models import EvalJudgePromptRow

    session = _services(request).get("db_session")
    if session is None:
        from easyobs.db.session import session_scope

        session = session_scope()
    from sqlalchemy import select, update

    async with session() as db:
        row = (
            await db.execute(
                select(EvalJudgePromptRow).where(
                    EvalJudgePromptRow.id == prompt_id,
                    EvalJudgePromptRow.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="prompt not found")
        await db.execute(
            update(EvalJudgePromptRow)
            .where(
                EvalJudgePromptRow.org_id == org_id,
                EvalJudgePromptRow.dimension_id == row.dimension_id,
            )
            .values(is_active=False)
        )
        row.is_active = True
        await db.commit()
        await db.refresh(row)
    return _judge_prompt_to_json(row)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@router.get("/profiles")
async def list_profiles(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    include_disabled: Annotated[bool, Query()] = False,
):
    org_id = _require_org(caller)
    rows = await _profile_svc(request).list(
        org_id=org_id, project_ids=scope, include_disabled=include_disabled
    )
    return {"items": [_profile_to_json(p) for p in rows]}


@router.get("/profiles/{profile_id}")
async def get_profile(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    profile_id: str,
):
    org_id = _require_org(caller)
    row = await _profile_svc(request).get(
        org_id=org_id, profile_id=profile_id, project_ids=scope
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    return _profile_to_json(row)


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: ProfileIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    row = await _profile_svc(request).upsert(
        org_id=org_id,
        profile_id=None,
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        evaluators=[e.model_dump() for e in body.evaluators],
        judge_models=[j.model_dump() for j in body.judge_models],
        consensus=body.consensus,
        auto_run=body.auto_run,
        cost_guard=body.cost_guard.model_dump(),
        enabled=body.enabled,
        actor=caller.user_id,
        judge_rubric_text=body.judge_rubric_text,
        judge_rubric_mode=body.judge_rubric_mode,
        judge_system_prompt=body.judge_system_prompt,
        judge_user_message_template=body.judge_user_message_template,
        improvement_pack=body.improvement_pack,
        judge_dimension_prompts=body.judge_dimension_prompts,
        improvement_content_locale=body.improvement_content_locale,
    )
    return _profile_to_json(row)


@router.put("/profiles/{profile_id}")
async def replace_profile(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    profile_id: str,
    body: ProfileIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    try:
        row = await _profile_svc(request).upsert(
            org_id=org_id,
            profile_id=profile_id,
            project_id=body.project_id,
            name=body.name,
            description=body.description,
            evaluators=[e.model_dump() for e in body.evaluators],
            judge_models=[j.model_dump() for j in body.judge_models],
            consensus=body.consensus,
            auto_run=body.auto_run,
            cost_guard=body.cost_guard.model_dump(),
            enabled=body.enabled,
            actor=caller.user_id,
            judge_rubric_text=body.judge_rubric_text,
            judge_rubric_mode=body.judge_rubric_mode,
            judge_system_prompt=body.judge_system_prompt,
            judge_user_message_template=body.judge_user_message_template,
            improvement_pack=body.improvement_pack,
            judge_dimension_prompts=body.judge_dimension_prompts,
            improvement_content_locale=body.improvement_content_locale,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    return _profile_to_json(row)


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    profile_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _profile_svc(request).delete(org_id=org_id, profile_id=profile_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post("/runs:estimate")
async def estimate_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: EstimateIn,
):
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    try:
        est = await _run_svc(request).estimate(
            org_id=org_id,
            profile_id=body.profile_id,
            subject_count=max(1, body.subject_count),
            project_ids=scope,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="profile not found")
    return {
        "subjectCount": est.subject_count,
        "judgeCalls": est.judge_calls,
        "costEstimateUsd": est.cost_estimate_usd,
        "ruleOnly": est.rule_only,
        "monthlySpentUsd": est.monthly_spent_usd,
        "costGuard": {
            "allowed": est.cost_guard_allowed,
            "downgrade": est.cost_guard_downgrade,
            "note": est.cost_guard_note,
        },
    }


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: RunRequestIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    if body.trigger_lane in {TriggerLane.RULE_AUTO.value, TriggerLane.JUDGE_SCHEDULE.value}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="trigger_lane reserved for system use",
        )
    rm = (body.run_mode or EvalRunMode.TRACE.value).lower()
    if rm not in {m.value for m in EvalRunMode}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid run_mode")
    if rm in (EvalRunMode.GOLDEN_GT.value, EvalRunMode.GOLDEN_JUDGE.value):
        if not (body.golden_set_id or "").strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="golden_set_id required for golden_gt and golden_judge",
            )
        g = await _golden_svc(request).get_set(
            org_id=org_id,
            set_id=body.golden_set_id,
            project_ids=scope,
        )
        if g is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    run_ctx = dict(body.run_context or {})
    if body.human_labels:
        run_ctx["humanLabels"] = [
            {
                "traceId": h.trace_id,
                "expectedResponse": h.expected_response,
                "humanVerdict": h.human_verdict,
                "notes": h.notes,
            }
            for h in body.human_labels
        ]
    try:
        run = await _run_svc(request).execute(
            org_id=org_id,
            profile_id=body.profile_id,
            profile=None,
            project_id=body.project_id,
            trace_ids=list(body.trace_ids),
            trigger_lane=body.trigger_lane,
            triggered_by=caller.user_id,
            notes=body.notes,
            project_scope=scope,
            run_mode=rm,
            golden_set_id=(body.golden_set_id.strip() if body.golden_set_id else None),
            run_context=run_ctx,
        )
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc))
    return _run_to_json(run)


@router.get("/runs")
async def list_runs(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    org_id = _require_org(caller)
    rows = await _run_svc(request).list_runs(
        org_id=org_id, project_ids=scope, limit=limit
    )
    return {"items": [_run_to_json(r) for r in rows]}


@router.get("/runs/{run_id}")
async def get_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    run_id: str,
):
    org_id = _require_org(caller)
    row = await _run_svc(request).get_run(
        org_id=org_id, run_id=run_id, project_ids=scope
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    return _run_to_json(row)


@router.get("/runs/{run_id}/results")
async def list_results(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    run_id: str,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
):
    org_id = _require_org(caller)
    rows = await _run_svc(request).list_results(
        org_id=org_id, run_id=run_id, project_ids=scope, limit=limit
    )
    return {"items": [_result_to_json(r) for r in rows]}


@router.get("/results/{result_id}")
async def get_result(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    result_id: str,
):
    org_id = _require_org(caller)
    row = await _run_svc(request).get_result(
        org_id=org_id, result_id=result_id, project_ids=scope
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="result not found")
    return _result_to_json(row)


# ---------------------------------------------------------------------------
# Golden sets
# ---------------------------------------------------------------------------


@router.get("/golden-sets")
async def list_golden_sets(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
):
    org_id = _require_org(caller)
    rows = await _golden_svc(request).list_sets(org_id=org_id, project_ids=scope)
    return {"items": [_golden_set_to_json(g) for g in rows]}


@router.post("/golden-sets", status_code=status.HTTP_201_CREATED)
async def create_golden_set(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: GoldenSetIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    if body.layer not in {gl.value for gl in GoldenLayer}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid layer")
    if body.mode not in {m.value for m in GoldenSetMode}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid mode")
    row = await _golden_svc(request).create_set(
        org_id=org_id,
        project_id=body.project_id,
        name=body.name,
        layer=body.layer,
        description=body.description,
        actor=caller.user_id,
        mode=body.mode,
        expand_query=body.expand_query,
    )
    return _golden_set_to_json(row)


@router.put("/golden-sets/{set_id}/agent-settings")
async def update_agent_settings(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: AgentInvokeSettingsIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    settings = AgentInvokeSettings(
        endpoint_url=body.endpoint_url,
        request_template=body.request_template,
        auth_ref=body.auth_ref,
        timeout_sec=body.timeout_sec,
        max_concurrent=body.max_concurrent,
    )
    row = await _golden_svc(request).update_agent_settings(
        org_id=org_id, set_id=set_id, settings=settings, project_ids=scope
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    return _golden_set_to_json(row)


@router.post("/golden-sets/{set_id}/agent-settings:test")
async def test_agent_settings(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: AgentInvokeSettingsIn | None = None,
):
    """Run a single test invocation against the configured agent
    endpoint without persisting anything. The caller may supply a draft
    body to test settings before saving them."""

    _require_write(caller)
    org_id = _require_org(caller)
    gset = await _golden_svc(request).get_set(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    if gset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    if body is not None:
        settings = AgentInvokeSettings(
            endpoint_url=body.endpoint_url,
            request_template=body.request_template,
            auth_ref=body.auth_ref,
            timeout_sec=body.timeout_sec,
            max_concurrent=body.max_concurrent,
        )
    else:
        settings = gset.agent_invoke
    result = await test_agent_connection(settings)
    return {
        "ok": result.ok,
        "statusCode": result.status_code,
        "elapsedMs": result.elapsed_ms,
        "errorType": result.error_type,
        "errorMessage": result.error_message,
        "responseBody": result.response_body,
        "inlineTraceId": result.inline_trace_id,
    }


@router.get("/golden-sets/{set_id}/revisions")
async def list_revisions(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
):
    org_id = _require_org(caller)
    rows = await _golden_svc(request).list_revisions(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    return {"items": [_revision_to_json(r) for r in rows]}


@router.get("/golden-sets/{set_id}/revisions/{revision_no}/trust")
async def revision_trust(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    revision_no: int,
    refresh: Annotated[bool, Query()] = False,
):
    """12 §11 — return the cached trust summary for this revision; pass
    ``refresh=true`` to recompute on demand and update the daily row."""

    org_id = _require_org(caller)
    rev = await _golden_svc(request).get_revision(
        org_id=org_id, set_id=set_id, revision_no=revision_no, project_ids=scope
    )
    if rev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="revision not found")
    if refresh:
        await _trust_svc(request).persist_daily(
            org_id=org_id, set_id=set_id, revision_id=rev.id
        )
        # Re-read revision to pick up the freshly mirrored summary.
        rev = await _golden_svc(request).get_revision(
            org_id=org_id,
            set_id=set_id,
            revision_no=revision_no,
            project_ids=scope,
        )
    return {
        "revision": _revision_to_json(rev),
        "trustSummary": rev.trust_summary if rev else {},
    }


# ---------------------------------------------------------------------------
# Regression Runs (12 §2)
# ---------------------------------------------------------------------------


@router.post(
    "/golden-sets/{set_id}/regression-runs", status_code=status.HTTP_201_CREATED
)
async def start_regression_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: RegressionRunIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    gset = await _golden_svc(request).get_set(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    if gset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    req = GoldenRegressionRequest(
        org_id=org_id,
        project_id=gset.project_id,
        project_scope=scope,
        set_id=set_id,
        profile_id=body.profile_id,
        triggered_by=caller.user_id,
        notes=body.notes,
        collect_timeout_sec=body.collect_timeout_sec,
        max_concurrent=body.max_concurrent,
    )
    try:
        run = await _golden_regression_svc(request).start_regression_run(req)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    return _run_to_json(run)


@router.post(
    "/golden-sets/{set_id}/regression-runs/{run_id}:cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_regression_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    run_id: str,
):
    _ = scope, set_id
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _golden_regression_svc(request).cancel_regression_run(
        org_id=org_id, run_id=run_id
    )
    if not ok:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="run already finished or not found"
        )


@router.get("/golden-sets/{set_id}/regression-runs/{run_id}/stream")
async def stream_regression_run(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    run_id: str,
):
    """SSE stream of progress events for a Regression Run. Survives
    browser closes — every reconnect replays the last 32 events before
    going live."""

    from fastapi.responses import StreamingResponse

    _ = set_id  # path scoping is enforced via the run lookup below.
    org_id = _require_org(caller)
    run = await _run_svc(request).get_run(
        org_id=org_id, run_id=run_id, project_ids=scope
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    broker = _progress_broker(request)

    async def _gen():
        async for event in broker.stream(kind="golden_run", ident=run_id):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/golden-sets/{set_id}/regression-runs/{run_id}/invokes")
async def list_regression_invokes(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    run_id: str,
):
    """Per-Golden-Item invocation map (12 §2.5). The UI uses this to
    render a status grid alongside the SSE-driven progress bar so
    operators can see *which* item is stuck on what phase."""

    _ = set_id
    org_id = _require_org(caller)
    try:
        rows = await _golden_regression_svc(request).list_invokes(
            org_id=org_id, run_id=run_id, project_scope=scope
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    return {"items": [_run_invoke_to_json(r) for r in rows]}


# ---------------------------------------------------------------------------
# Synthesizer jobs (12 §10)
# ---------------------------------------------------------------------------


@router.post(
    "/golden-sets/{set_id}/synth-jobs", status_code=status.HTTP_201_CREATED
)
async def start_synth_job(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: SynthJobIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    gset = await _golden_svc(request).get_set(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    if gset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    req = SynthJobRequest(
        org_id=org_id,
        project_id=gset.project_id,
        project_scope=scope,
        set_id=set_id,
        mode=body.mode,
        source_policy=body.source_policy,
        source_spec=body.source_spec,
        judge_model_id=body.judge_model_id,
        target_count=body.target_count,
        triggered_by=caller.user_id,
        custom_prompt=body.custom_prompt,
    )
    try:
        job = await _synth_svc(request).start_job(req)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    return _synth_job_to_json(job)


@router.get("/golden-sets/{set_id}/synth-jobs")
async def list_synth_jobs(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
):
    org_id = _require_org(caller)
    rows = await _synth_svc(request).list_jobs(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    return {"items": [_synth_job_to_json(r) for r in rows]}


@router.post(
    "/synth-jobs/{job_id}:cancel", status_code=status.HTTP_204_NO_CONTENT
)
async def cancel_synth_job(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    job_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _synth_svc(request).cancel_job(org_id=org_id, job_id=job_id)
    if not ok:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="job already finished or not found"
        )


@router.get("/synth-jobs/{job_id}/stream")
async def stream_synth_job(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    job_id: str,
):
    from fastapi.responses import StreamingResponse

    org_id = _require_org(caller)
    job = await _synth_svc(request).get_job(
        org_id=org_id, job_id=job_id, project_ids=scope
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found")
    broker = _progress_broker(request)

    async def _gen():
        async for event in broker.stream(kind="synth_job", ident=job_id):
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Item review / dispute (12 §8.2)
# ---------------------------------------------------------------------------


@router.patch("/golden-items/{item_id}/review")
async def patch_golden_item_review(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    item_id: str,
    body: GoldenItemReviewPatch,
):
    _require_write(caller)
    org_id = _require_org(caller)
    try:
        row = await _golden_svc(request).update_item_review(
            org_id=org_id,
            item_id=item_id,
            review_state=body.review_state,
            label_kind=body.label_kind,
            dispute_reason=body.dispute_reason,
            project_ids=scope,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden item not found")
    return _golden_item_to_json(row)


@router.post("/golden-items/{item_id}:dispute")
async def dispute_golden_item(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    item_id: str,
    body: DisputeIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    row = await _golden_svc(request).update_item_review(
        org_id=org_id,
        item_id=item_id,
        review_state="disputed",
        dispute_reason=body.reason,
        project_ids=scope,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden item not found")
    return _golden_item_to_json(row)


@router.delete("/golden-sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_golden_set(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _golden_svc(request).delete_set(org_id=org_id, set_id=set_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")


@router.get("/golden-sets/{set_id}/items")
async def list_golden_items(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    org_id = _require_org(caller)
    rows = await _golden_svc(request).list_items(
        org_id=org_id,
        set_id=set_id,
        project_ids=scope,
        status=status_filter,
        limit=limit,
    )
    return {"items": [_golden_item_to_json(i) for i in rows]}


@router.post("/golden-sets/{set_id}/items", status_code=status.HTTP_201_CREATED)
async def add_golden_item(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: GoldenItemIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    try:
        row = await _golden_svc(request).add_manual_item(
            org_id=org_id,
            set_id=set_id,
            payload=body.payload,
            project_ids=scope,
            actor=caller.user_id,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    return _golden_item_to_json(row)


@router.post(
    "/golden-sets/{set_id}/items/from-trace", status_code=status.HTTP_201_CREATED
)
async def add_golden_item_from_trace(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: GoldenItemFromTraceIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    try:
        row = await _golden_svc(request).add_from_trace(
            org_id=org_id,
            set_id=set_id,
            trace_id=body.trace_id,
            labels=body.labels,
            project_ids=scope,
            actor=caller.user_id,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    return _golden_item_to_json(row)


@router.post("/golden-sets/{set_id}/auto-discover")
async def auto_discover_items(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    body: GoldenItemAutoIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    rows = await _golden_svc(request).auto_discover(
        org_id=org_id,
        set_id=set_id,
        service_ids=[body.project_id],
        sample_size=max(1, min(body.sample_size, 200)),
        actor=caller.user_id,
    )
    return {"items": [_golden_item_to_json(i) for i in rows]}


@router.patch("/golden-items/{item_id}")
async def patch_golden_item(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    item_id: str,
    body: GoldenItemPatch,
):
    _require_write(caller)
    org_id = _require_org(caller)
    try:
        row = await _golden_svc(request).update_item_status(
            org_id=org_id,
            item_id=item_id,
            status=body.status,
            project_ids=scope,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden item not found")
    return _golden_item_to_json(row)


# ---------------------------------------------------------------------------
# Golden Set upload (CSV / JSONL / xlsx) — 12 §9
# ---------------------------------------------------------------------------


@router.get("/golden-sets/upload-schema")
async def golden_upload_schema(_: CurrentUser):
    """Return the closed list of golden-path keys the UI may pin a CSV
    column to. Used by the upload mapper so the operator gets a closed
    dropdown rather than free text."""

    return {
        "paths": list(SUPPORTED_GOLDEN_PATHS),
        "fileKinds": ["csv", "jsonl", "xlsx"],
    }


def _upload_settings(request: Request) -> tuple[int, int, int]:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return 25 * 1024 * 1024, 50_000, 32
    max_mb = int(getattr(settings, "eval_upload_max_mb", 25))
    max_rows = int(getattr(settings, "eval_upload_max_rows", 50_000))
    max_cols = int(getattr(settings, "eval_upload_max_cols", 32))
    return max_mb * 1024 * 1024, max_rows, max_cols


def _parse_mapping(raw: str) -> dict[str, str]:
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mapping is required")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"mapping is not valid JSON: {exc}"
        )
    if not isinstance(parsed, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="mapping must be a JSON object {csv_col: golden_path}",
        )
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(value, str):
            continue
        if value not in SUPPORTED_GOLDEN_PATHS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"unsupported golden path: {value!r}",
            )
        out[str(key)] = value
    if not out:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="mapping must include at least one supported golden path",
        )
    return out


async def _read_upload(file: UploadFile, max_size: int) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty upload payload")
    if max_size > 0 and len(data) > max_size:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file too large: {len(data)} bytes > {max_size}",
        )
    return data


@router.post("/golden-sets/{set_id}/upload:validate")
async def validate_golden_upload(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    file: Annotated[UploadFile, File(description="CSV / JSONL / xlsx file")],
    mapping: Annotated[str, Form(description="JSON {csv_col: golden_path}")],
    has_header: Annotated[bool, Form()] = True,
    redact_pii: Annotated[bool, Form()] = False,
):
    """Parse + sanitise the upload, return preview + validation result
    without persisting anything. Same parser the consume route uses, so
    what the operator sees is exactly what would land."""

    _require_write(caller)
    org_id = _require_org(caller)
    # Touch the set so the caller hits a 404 before we open the file.
    target_set = await _golden_svc(request).get_set(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    if target_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")

    max_size, max_rows, max_cols = _upload_settings(request)
    parsed_mapping = _parse_mapping(mapping)
    data = await _read_upload(file, max_size)
    try:
        result = validate_upload(
            filename=file.filename or "",
            data=data,
            mapping=parsed_mapping,
            has_header=has_header,
            redact_pii=redact_pii,
            max_size_bytes=max_size,
            max_rows=max_rows,
            max_cols=max_cols,
        )
    except UploadError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        )
    return {
        "fileKind": result.file_kind,
        "headers": result.headers,
        "validCount": result.valid_count,
        "skippedCount": result.skipped_count,
        "truncated": result.truncated,
        "issues": result.issues,
        "sampleRows": result.sample_rows,
        "limits": {
            "maxRows": max_rows,
            "maxCols": max_cols,
            "maxSizeBytes": max_size,
        },
    }


@router.post(
    "/golden-sets/{set_id}/upload:consume", status_code=status.HTTP_201_CREATED
)
async def consume_golden_upload(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    set_id: str,
    file: Annotated[UploadFile, File()],
    mapping: Annotated[str, Form()],
    has_header: Annotated[bool, Form()] = True,
    redact_pii: Annotated[bool, Form()] = False,
):
    """Parse + sanitise the upload then bulk-insert golden items as
    ``candidate``. Operators must promote to ``active`` afterwards."""

    _require_write(caller)
    org_id = _require_org(caller)
    target_set = await _golden_svc(request).get_set(
        org_id=org_id, set_id=set_id, project_ids=scope
    )
    if target_set is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")

    max_size, max_rows, max_cols = _upload_settings(request)
    parsed_mapping = _parse_mapping(mapping)
    data = await _read_upload(file, max_size)
    try:
        result = validate_upload(
            filename=file.filename or "",
            data=data,
            mapping=parsed_mapping,
            has_header=has_header,
            redact_pii=redact_pii,
            max_size_bytes=max_size,
            max_rows=max_rows,
            max_cols=max_cols,
        )
    except UploadError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        )
    if not result.payloads:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="no rows mapped to a golden item — check the column mapping",
        )
    try:
        rows = await _golden_svc(request).bulk_add_from_upload(
            org_id=org_id,
            set_id=set_id,
            payloads=result.payloads,
            project_ids=scope,
            actor=caller.user_id,
        )
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden set not found")
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    return {
        "fileKind": result.file_kind,
        "inserted": len(rows),
        "skippedCount": result.skipped_count,
        "issues": result.issues,
        "items": [_golden_item_to_json(r) for r in rows],
    }


@router.delete("/golden-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_golden_item(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    item_id: str,
):
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _golden_svc(request).delete_item(
        org_id=org_id, item_id=item_id, project_ids=scope
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="golden item not found")


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@router.get("/schedules")
async def list_schedules(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
):
    org_id = _require_org(caller)
    rows = await _schedule_svc(request).list(org_id=org_id, project_ids=scope)
    return {"items": [_schedule_to_json(s) for s in rows]}


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    body: ScheduleIn,
):
    _require_write(caller)
    org_id = _require_org(caller)
    if not _project_allowed(body.project_id, scope):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="project access denied")
    row = await _schedule_svc(request).create(
        org_id=org_id,
        project_id=body.project_id,
        profile_id=body.profile_id,
        name=body.name,
        interval_hours=body.interval_hours,
        sample_size=body.sample_size,
        enabled=body.enabled,
        actor=caller.user_id,
    )
    return _schedule_to_json(row)


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    schedule_id: str,
    body: SchedulePatch,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    row = await _schedule_svc(request).update(
        org_id=org_id, schedule_id=schedule_id, **body.model_dump(exclude_none=True)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="schedule not found")
    return _schedule_to_json(row)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    schedule_id: str,
):
    _ = scope
    _require_write(caller)
    org_id = _require_org(caller)
    ok = await _schedule_svc(request).delete(org_id=org_id, schedule_id=schedule_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="schedule not found")


# ---------------------------------------------------------------------------
# Improvements
# ---------------------------------------------------------------------------


@router.get("/improvements")
async def list_improvements(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    org_id = _require_org(caller)
    rows = await _improve_svc(request).list(
        org_id=org_id, project_ids=scope, status=status_filter, limit=limit
    )
    return {"items": [_improvement_to_json(i) for i in rows]}


@router.patch("/improvements/{improvement_id}")
async def patch_improvement(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    improvement_id: str,
    body: ImprovementPatch,
):
    _require_write(caller)
    org_id = _require_org(caller)
    try:
        row = await _improve_svc(request).update_status(
            org_id=org_id,
            improvement_id=improvement_id,
            status=body.status,
            project_ids=scope,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="improvement not found")
    return _improvement_to_json(row)


# ---------------------------------------------------------------------------
# Cost dashboard
# ---------------------------------------------------------------------------


@router.get("/cost/overview")
async def cost_overview(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
):
    org_id = _require_org(caller)
    return await _cost_svc(request).overview(org_id=org_id, project_ids=scope)


@router.get("/cost/daily")
async def cost_daily(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
    days: Annotated[int, Query(ge=1, le=180)] = 30,
):
    org_id = _require_org(caller)
    return {
        "items": await _cost_svc(request).daily(
            org_id=org_id, project_ids=scope, days=days
        )
    }


# ---------------------------------------------------------------------------
# Aggregated dashboard
# ---------------------------------------------------------------------------


@router.get("/overview")
async def quality_overview(
    request: Request,
    caller: CurrentUser,
    scope: CallerScope,
):
    org_id = _require_org(caller)
    runs = await _run_svc(request).list_runs(
        org_id=org_id, project_ids=scope, limit=20
    )
    cost_overview = await _cost_svc(request).overview(
        org_id=org_id, project_ids=scope
    )
    profile_count = len(
        await _profile_svc(request).list(
            org_id=org_id, project_ids=scope, include_disabled=True
        )
    )
    judge_count = len(await _judge_model_svc(request).list(org_id=org_id))
    golden_sets = await _golden_svc(request).list_sets(
        org_id=org_id, project_ids=scope
    )
    open_improvements = await _improve_svc(request).list(
        org_id=org_id, project_ids=scope, status="open", limit=200
    )
    avg_score = round(
        sum(r.avg_score for r in runs) / len(runs), 4
    ) if runs else 0.0
    pass_rate = round(
        sum(r.pass_rate for r in runs) / len(runs), 4
    ) if runs else 0.0
    auto_runs = sum(1 for r in runs if r.trigger_lane == TriggerLane.RULE_AUTO.value)
    return {
        "kpi": {
            "profileCount": profile_count,
            "judgeModelCount": judge_count,
            "goldenSetCount": len(golden_sets),
            "openImprovements": len(open_improvements),
            "avgScore": avg_score,
            "passRate": pass_rate,
            "autoRuleRunsLast20": auto_runs,
        },
        "recentRuns": [_run_to_json(r) for r in runs[:10]],
        "cost": cost_overview,
    }


# ---------------------------------------------------------------------------
# JSON serialisers — keep keys camelCase so the front-end can dot into them
# directly.
# ---------------------------------------------------------------------------


def _judge_model_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "name": row.name,
        "provider": row.provider,
        "model": row.model,
        "temperature": row.temperature,
        "weight": row.weight,
        "costPer1kInput": row.cost_per_1k_input,
        "costPer1kOutput": row.cost_per_1k_output,
        "connectionConfig": row.connection_config,
        "enabled": row.enabled,
        "createdAt": _iso(row.created_at),
    }


def _profile_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "name": row.name,
        "description": row.description,
        "evaluators": [
            {
                "evaluatorId": e.evaluator_id,
                "weight": e.weight,
                "threshold": e.threshold,
                "params": e.params,
            }
            for e in row.evaluators
        ],
        "judgeModels": [
            {"modelId": j.model_id, "weight": j.weight} for j in row.judge_models
        ],
        "consensus": row.consensus,
        "autoRun": row.auto_run,
        "costGuard": {
            "maxCostUsdPerRun": row.cost_guard.max_cost_usd_per_run,
            "maxCostUsdPerSubject": row.cost_guard.max_cost_usd_per_subject,
            "monthlyBudgetUsd": row.cost_guard.monthly_budget_usd,
            "onExceed": row.cost_guard.on_exceed,
        },
        "enabled": row.enabled,
        "createdAt": _iso(row.created_at),
        "judgeRubricText": row.judge_rubric_text or "",
        "judgeRubricMode": row.judge_rubric_mode or "append",
        "judgeSystemPrompt": row.judge_system_prompt or "",
        "judgeUserMessageTemplate": row.judge_user_message_template or "",
        "improvementPack": row.improvement_pack or "easyobs_standard",
        "judgeDimensionPrompts": dict(row.judge_dimension_prompts or {}),
        "improvementContentLocale": row.improvement_content_locale or "en",
    }


def _run_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "profileId": row.profile_id,
        "scheduleId": row.schedule_id,
        "triggerLane": row.trigger_lane,
        "triggeredBy": row.triggered_by,
        "status": row.status,
        "subjectCount": row.subject_count,
        "completedCount": row.completed_count,
        "failedCount": row.failed_count,
        "costEstimateUsd": row.cost_estimate_usd,
        "costActualUsd": row.cost_actual_usd,
        "passRate": row.pass_rate,
        "avgScore": row.avg_score,
        "notes": row.notes,
        "startedAt": _iso(row.started_at),
        "finishedAt": _iso(row.finished_at) if row.finished_at else None,
        "runMode": row.run_mode,
        "goldenSetId": row.golden_set_id,
        "runContext": row.run_context or {},
    }


def _result_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "runId": row.run_id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "traceId": row.trace_id,
        "sessionId": row.session_id,
        "score": row.score,
        "verdict": row.verdict,
        "ruleScore": row.rule_score,
        "judgeScore": row.judge_score,
        "judgeDisagreement": row.judge_disagreement,
        "judgeInputTokens": row.judge_input_tokens,
        "judgeOutputTokens": row.judge_output_tokens,
        "judgeCostUsd": row.judge_cost_usd,
        "findings": [
            {
                "evaluatorId": f.evaluator_id,
                "kind": f.kind,
                "score": f.score,
                "verdict": f.verdict,
                "reason": f.reason,
                "details": f.details,
            }
            for f in row.findings
        ],
        "judgePerModel": row.judge_per_model,
        "triggerLane": row.trigger_lane,
        "createdAt": _iso(row.created_at),
        "judgeErrorDetail": getattr(row, "judge_error_detail", {}) or {},
    }


def _golden_set_to_json(row) -> dict[str, Any]:
    invoke = row.agent_invoke
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "name": row.name,
        "layer": row.layer,
        "description": row.description,
        "itemCount": row.item_count,
        "createdAt": _iso(row.created_at),
        "mode": row.mode,
        "expandQuery": row.expand_query or {},
        "lastSynthJobId": row.last_synth_job_id,
        "agentInvoke": {
            "endpointUrl": invoke.endpoint_url,
            "requestTemplate": invoke.request_template or {},
            "authRef": invoke.auth_ref,
            "timeoutSec": invoke.timeout_sec,
            "maxConcurrent": invoke.max_concurrent,
        },
    }


def _golden_item_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "setId": row.set_id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "layer": row.layer,
        "sourceKind": row.source_kind,
        "status": row.status,
        "payload": row.payload,
        "sourceTraceId": row.source_trace_id,
        "createdAt": _iso(row.created_at),
        "revisionId": row.revision_id,
        "labelKind": row.label_kind,
        "reviewState": row.review_state,
        "disputeReason": row.dispute_reason,
    }


def _revision_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "setId": row.set_id,
        "orgId": row.org_id,
        "revisionNo": row.revision_no,
        "immutable": row.immutable,
        "itemCount": row.item_count,
        "notes": row.notes,
        "trustSummary": row.trust_summary or {},
        "createdAt": _iso(row.created_at),
        "lockedAt": _iso(row.locked_at) if row.locked_at else None,
    }


def _synth_job_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "setId": row.set_id,
        "revisionId": row.revision_id,
        "mode": row.mode,
        "sourcePolicy": row.source_policy,
        "sourceSpec": row.source_spec or {},
        "judgeModelId": row.judge_model_id,
        "targetCount": row.target_count,
        "generatedCount": row.generated_count,
        "status": row.status,
        "progress": row.progress,
        "costEstimateUsd": row.cost_estimate_usd,
        "costActualUsd": row.cost_actual_usd,
        "errorLog": row.error_log or [],
        "startedAt": _iso(row.started_at) if row.started_at else None,
        "finishedAt": _iso(row.finished_at) if row.finished_at else None,
        "triggeredBy": row.triggered_by,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }


def _run_invoke_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "runId": row.run_id,
        "goldenItemId": row.golden_item_id,
        "traceId": row.trace_id,
        "invokeStatus": row.invoke_status,
        "invokeStarted": _iso(row.invoke_started_at) if row.invoke_started_at else None,
        "invokeFinished": _iso(row.invoke_finished_at) if row.invoke_finished_at else None,
        "agentResponse": row.agent_response or {},
        "errorDetail": row.error_detail or {},
    }


def _schedule_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "profileId": row.profile_id,
        "name": row.name,
        "intervalHours": row.interval_hours,
        "cron": row.cron,
        "sampleSize": row.sample_size,
        "enabled": row.enabled,
        "lastRunAt": _iso(row.last_run_at) if row.last_run_at else None,
        "nextRunAt": _iso(row.next_run_at) if row.next_run_at else None,
        "createdAt": _iso(row.created_at),
    }


def _improvement_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "projectId": row.project_id,
        "resultId": row.result_id,
        "traceId": row.trace_id,
        "summary": row.summary,
        "proposals": row.proposals,
        "judgeModels": row.judge_models,
        "consensusPolicy": row.consensus_policy,
        "agreementRatio": row.agreement_ratio,
        "judgeCostUsd": row.judge_cost_usd,
        "status": row.status,
        "createdAt": _iso(row.created_at),
        "improvementPack": row.improvement_pack,
        "improvementContentLocale": row.improvement_content_locale,
    }


def _iso(value: datetime) -> str:
    # Always emit UTC ISO -- bare ``astimezone()`` (no arg) converts to the
    # server's *local* timezone, which on a KST host shifts every timestamp
    # by +9h before it reaches the UI. The frontend assumes UTC everywhere
    # (chart axes, range filters, formatTime), so we must too.
    if value.tzinfo is None:
        # ``UtcDateTime`` already re-tags loaded values, but some callers
        # forward DTOs that wrapped the raw row before that hook ran;
        # treat naive as UTC since every store path writes UTC.
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


def _judge_prompt_to_json(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "orgId": row.org_id,
        "dimensionId": row.dimension_id,
        "version": row.version,
        "systemPrompt": row.system_prompt,
        "userMessageTemplate": row.user_message_template,
        "isActive": row.is_active,
        "description": row.description or "",
        "createdAt": _iso(row.created_at),
        "createdBy": row.created_by,
    }
