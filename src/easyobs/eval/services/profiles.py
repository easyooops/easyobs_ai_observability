"""Evaluation profile CRUD.

A profile is the unit a run pins itself to: it owns the evaluator list,
the judge model + consensus policy, the cost guard and the auto-run flag.
We persist evaluators / judge_models / cost_guard as JSON because the
catalog is open-ended (operators can add custom rule expressions and any
number of judge model rows) and SQLite makes JSON columns easy enough.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalProfileRow
from easyobs.eval.services.dtos import (
    CostGuardConfig,
    ProfileDTO,
    ProfileEvaluatorRef,
    ProfileJudgeRef,
)
from easyobs.eval.types import ConsensusPolicy, CostExceedAction, JudgeRubricMode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_evaluators(items: list[dict[str, Any]]) -> list[ProfileEvaluatorRef]:
    out: list[ProfileEvaluatorRef] = []
    for raw in items or []:
        eid = str(raw.get("evaluator_id") or raw.get("id") or "").strip()
        if not eid:
            continue
        out.append(
            ProfileEvaluatorRef(
                evaluator_id=eid,
                weight=float(raw.get("weight", 1.0) or 1.0),
                threshold=float(raw.get("threshold", 0.6) or 0.6),
                params=dict(raw.get("params") or {}),
            )
        )
    return out


def _normalise_judges(items: list[dict[str, Any]]) -> list[ProfileJudgeRef]:
    out: list[ProfileJudgeRef] = []
    for raw in items or []:
        mid = str(raw.get("model_id") or raw.get("id") or "").strip()
        if not mid:
            continue
        out.append(
            ProfileJudgeRef(
                model_id=mid,
                weight=float(raw.get("weight", 1.0) or 1.0),
            )
        )
    return out


def _normalise_cost_guard(raw: dict[str, Any] | None) -> CostGuardConfig:
    raw = raw or {}
    on_exceed = str(raw.get("on_exceed") or CostExceedAction.BLOCK.value).lower()
    if on_exceed not in {a.value for a in CostExceedAction}:
        on_exceed = CostExceedAction.BLOCK.value
    return CostGuardConfig(
        max_cost_usd_per_run=float(raw.get("max_cost_usd_per_run", 5.0) or 5.0),
        max_cost_usd_per_subject=float(raw.get("max_cost_usd_per_subject", 0.05) or 0.05),
        monthly_budget_usd=float(raw.get("monthly_budget_usd", 100.0) or 100.0),
        on_exceed=on_exceed,
    )


def _normalise_consensus(raw: str | None) -> str:
    value = (raw or ConsensusPolicy.SINGLE.value).lower()
    if value not in {p.value for p in ConsensusPolicy}:
        return ConsensusPolicy.SINGLE.value
    return value


def _normalise_judge_rubric_mode(raw: str | None) -> str:
    v = (raw or JudgeRubricMode.APPEND.value).lower()
    if v not in {m.value for m in JudgeRubricMode}:
        return JudgeRubricMode.APPEND.value
    return v


def _normalise_improvement_content_locale(raw: str | None) -> str:
    v = (raw or "en").lower().replace("_", "-")
    if v in {"ko", "kr", "ko-kr"}:
        return "ko"
    return "en"


def _normalise_dimension_prompts(raw: dict[str, Any] | None) -> str:
    if not raw:
        return "{}"
    out: dict[str, Any] = {}
    for k, v in raw.items():
        kid = str(k).strip()
        if not kid or not isinstance(v, dict):
            continue
        en = str(v.get("en") or "").strip()
        ko = str(v.get("ko") or "").strip()
        piece: dict[str, str] = {}
        if en:
            piece["en"] = en
        if ko:
            piece["ko"] = ko
        if piece:
            out[kid] = piece
    return json.dumps(out, ensure_ascii=False)


def _parse_dimension_prompts_json(raw: str | None) -> dict[str, dict[str, str]]:
    try:
        obj = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in obj.items():
        kid = str(k).strip()
        if not kid or not isinstance(v, dict):
            continue
        en = str(v.get("en") or "").strip()
        ko = str(v.get("ko") or "").strip()
        piece: dict[str, str] = {}
        if en:
            piece["en"] = en
        if ko:
            piece["ko"] = ko
        if piece:
            out[kid] = piece
    return out


class ProfileService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self,
        *,
        org_id: str,
        project_ids: list[str] | None,
        include_disabled: bool = False,
    ) -> list[ProfileDTO]:
        async with self._sf() as s:
            stmt = select(EvalProfileRow).where(EvalProfileRow.org_id == org_id)
            if project_ids is not None:
                allowed = set(project_ids)
                rows = (await s.execute(stmt)).scalars().all()
                # Profile may be org-wide (project_id is NULL) which we
                # always allow within the org; otherwise the project id
                # must intersect the caller's accessible services.
                rows = [r for r in rows if r.project_id is None or r.project_id in allowed]
            else:
                rows = (await s.execute(stmt)).scalars().all()
            if not include_disabled:
                rows = [r for r in rows if r.enabled]
            return [_to_dto(r) for r in rows]

    async def get(
        self,
        *,
        org_id: str,
        profile_id: str,
        project_ids: list[str] | None,
    ) -> ProfileDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalProfileRow, profile_id)
        if row is None or row.org_id != org_id:
            return None
        if project_ids is not None and row.project_id is not None and row.project_id not in project_ids:
            return None
        return _to_dto(row)

    async def upsert(
        self,
        *,
        org_id: str,
        profile_id: str | None,
        project_id: str | None,
        name: str,
        description: str,
        evaluators: list[dict[str, Any]],
        judge_models: list[dict[str, Any]],
        consensus: str,
        auto_run: bool,
        cost_guard: dict[str, Any] | None,
        enabled: bool,
        actor: str | None,
        judge_rubric_text: str = "",
        judge_rubric_mode: str | None = None,
        judge_system_prompt: str = "",
        judge_user_message_template: str = "",
        improvement_pack: str = "easyobs_standard",
        judge_dimension_prompts: dict[str, Any] | None = None,
        improvement_content_locale: str | None = None,
    ) -> ProfileDTO:
        normalised_evaluators = _normalise_evaluators(evaluators)
        normalised_judges = _normalise_judges(judge_models)
        normalised_consensus = _normalise_consensus(consensus)
        normalised_cost_guard = _normalise_cost_guard(cost_guard)
        rubric_mode = _normalise_judge_rubric_mode(judge_rubric_mode)
        rubric_body = (judge_rubric_text or "").strip()
        sys_prompt = (judge_system_prompt or "").strip()
        user_tpl = judge_user_message_template or ""
        pack = (improvement_pack or "easyobs_standard").strip() or "easyobs_standard"
        dim_json = _normalise_dimension_prompts(judge_dimension_prompts)
        imp_loc = _normalise_improvement_content_locale(improvement_content_locale)
        async with self._sf() as s:
            if profile_id is None:
                row = EvalProfileRow(
                    id=uuid.uuid4().hex,
                    org_id=org_id,
                    project_id=project_id,
                    name=name,
                    description=description,
                    evaluators_json=json.dumps([_eval_to_json(e) for e in normalised_evaluators]),
                    judge_models_json=json.dumps([_judge_to_json(j) for j in normalised_judges]),
                    consensus=normalised_consensus,
                    auto_run=auto_run,
                    cost_guard_json=json.dumps(_cost_guard_to_json(normalised_cost_guard)),
                    enabled=enabled,
                    judge_rubric_text=rubric_body,
                    judge_rubric_mode=rubric_mode,
                    judge_system_prompt=sys_prompt,
                    judge_user_message_template=user_tpl,
                    improvement_pack=pack,
                    judge_dimension_prompts_json=dim_json,
                    improvement_content_locale=imp_loc,
                    created_at=_now(),
                    created_by=actor,
                )
                s.add(row)
            else:
                row = await s.get(EvalProfileRow, profile_id)
                if row is None or row.org_id != org_id:
                    raise LookupError("profile not found")
                row.project_id = project_id
                row.name = name
                row.description = description
                row.evaluators_json = json.dumps(
                    [_eval_to_json(e) for e in normalised_evaluators]
                )
                row.judge_models_json = json.dumps(
                    [_judge_to_json(j) for j in normalised_judges]
                )
                row.consensus = normalised_consensus
                row.auto_run = auto_run
                row.cost_guard_json = json.dumps(_cost_guard_to_json(normalised_cost_guard))
                row.enabled = enabled
                row.judge_rubric_text = rubric_body
                row.judge_rubric_mode = rubric_mode
                row.judge_system_prompt = sys_prompt
                row.judge_user_message_template = user_tpl
                row.improvement_pack = pack
                row.judge_dimension_prompts_json = dim_json
                row.improvement_content_locale = imp_loc
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def delete(self, *, org_id: str, profile_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalProfileRow, profile_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def list_auto_run(
        self, *, org_id: str, project_id: str
    ) -> list[ProfileDTO]:
        """All enabled profiles that should fire on trace ingest for the
        given service. Org-wide profiles (project_id is NULL) and
        project-specific profiles both qualify."""
        async with self._sf() as s:
            stmt = select(EvalProfileRow).where(
                EvalProfileRow.org_id == org_id,
                EvalProfileRow.enabled.is_(True),
                EvalProfileRow.auto_run.is_(True),
            )
            rows = (await s.execute(stmt)).scalars().all()
        return [
            _to_dto(r)
            for r in rows
            if r.project_id is None or r.project_id == project_id
        ]


# ---------------------------------------------------------------------------
# JSON ↔ DTO helpers
# ---------------------------------------------------------------------------


def _eval_to_json(ref: ProfileEvaluatorRef) -> dict[str, Any]:
    return {
        "evaluator_id": ref.evaluator_id,
        "weight": ref.weight,
        "threshold": ref.threshold,
        "params": ref.params,
    }


def _judge_to_json(ref: ProfileJudgeRef) -> dict[str, Any]:
    return {"model_id": ref.model_id, "weight": ref.weight}


def _cost_guard_to_json(cfg: CostGuardConfig) -> dict[str, Any]:
    return {
        "max_cost_usd_per_run": cfg.max_cost_usd_per_run,
        "max_cost_usd_per_subject": cfg.max_cost_usd_per_subject,
        "monthly_budget_usd": cfg.monthly_budget_usd,
        "on_exceed": cfg.on_exceed,
    }


def _to_dto(row: EvalProfileRow) -> ProfileDTO:
    try:
        evaluators = [
            ProfileEvaluatorRef(
                evaluator_id=str(item.get("evaluator_id") or ""),
                weight=float(item.get("weight", 1.0) or 1.0),
                threshold=float(item.get("threshold", 0.6) or 0.6),
                params=dict(item.get("params") or {}),
            )
            for item in json.loads(row.evaluators_json or "[]")
        ]
    except Exception:
        evaluators = []
    try:
        judges = [
            ProfileJudgeRef(
                model_id=str(item.get("model_id") or ""),
                weight=float(item.get("weight", 1.0) or 1.0),
            )
            for item in json.loads(row.judge_models_json or "[]")
        ]
    except Exception:
        judges = []
    try:
        cost = json.loads(row.cost_guard_json or "{}")
    except Exception:
        cost = {}
    return ProfileDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        evaluators=evaluators,
        judge_models=judges,
        consensus=row.consensus,
        auto_run=row.auto_run,
        cost_guard=_normalise_cost_guard(cost),
        enabled=row.enabled,
        created_at=row.created_at,
        judge_rubric_text=getattr(row, "judge_rubric_text", "") or "",
        judge_rubric_mode=_normalise_judge_rubric_mode(
            getattr(row, "judge_rubric_mode", None)
        ),
        judge_system_prompt=getattr(row, "judge_system_prompt", "") or "",
        judge_user_message_template=getattr(row, "judge_user_message_template", "") or "",
        improvement_pack=getattr(row, "improvement_pack", "") or "easyobs_standard",
        judge_dimension_prompts=_parse_dimension_prompts_json(
            getattr(row, "judge_dimension_prompts_json", None)
        ),
        improvement_content_locale=_normalise_improvement_content_locale(
            getattr(row, "improvement_content_locale", None)
        ),
    )
