"""Cost projection, daily roll-up and the per-profile cost guard.

The guard is intentionally pre-flight: ``allow_run`` runs *before* any
provider is called, taking the run's projected total and comparing it
against three budgets (per run, per subject, per month). Going over a
budget routes through ``on_exceed`` ∈ {``block``, ``downgrade``,
``notify``} which the run service interprets:

- ``block``     — refuse the run (HTTP 402).
- ``downgrade`` — drop judges, keep deterministic rules only.
- ``notify``    — let it through but mark the run with a warning note.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalCostDailyRow
from easyobs.eval.services.dtos import CostGuardConfig
from easyobs.eval.types import CostExceedAction

_log = logging.getLogger("easyobs.eval.cost")


@dataclass(frozen=True, slots=True)
class CostGuardDecision:
    allowed: bool
    downgrade_judges: bool
    note: str
    over_budget: bool
    monthly_spent_usd: float
    projection_usd: float


class CostGuard:
    """Pure-function-ish: takes the projection + monthly spend and returns
    a decision. Stateless so it's trivial to unit-test."""

    @staticmethod
    def decide(
        cfg: CostGuardConfig,
        *,
        projected_usd: float,
        subject_count: int,
        monthly_spent_usd: float,
    ) -> CostGuardDecision:
        per_subject = projected_usd / max(subject_count, 1)
        over_run = projected_usd > cfg.max_cost_usd_per_run
        over_subject = per_subject > cfg.max_cost_usd_per_subject
        over_month = (monthly_spent_usd + projected_usd) > cfg.monthly_budget_usd
        triggered = over_run or over_subject or over_month
        if not triggered:
            return CostGuardDecision(
                allowed=True,
                downgrade_judges=False,
                note="",
                over_budget=False,
                monthly_spent_usd=monthly_spent_usd,
                projection_usd=projected_usd,
            )
        reason_bits = []
        if over_run:
            reason_bits.append(f"run ${projected_usd:.4f} > ${cfg.max_cost_usd_per_run:.4f}")
        if over_subject:
            reason_bits.append(
                f"per-subject ${per_subject:.4f} > ${cfg.max_cost_usd_per_subject:.4f}"
            )
        if over_month:
            reason_bits.append(
                f"month ${monthly_spent_usd + projected_usd:.4f} > ${cfg.monthly_budget_usd:.4f}"
            )
        note = "; ".join(reason_bits)
        action = cfg.on_exceed
        if action == CostExceedAction.BLOCK.value:
            return CostGuardDecision(
                allowed=False,
                downgrade_judges=False,
                note=note,
                over_budget=True,
                monthly_spent_usd=monthly_spent_usd,
                projection_usd=projected_usd,
            )
        if action == CostExceedAction.DOWNGRADE.value:
            return CostGuardDecision(
                allowed=True,
                downgrade_judges=True,
                note=f"downgraded: {note}",
                over_budget=True,
                monthly_spent_usd=monthly_spent_usd,
                projection_usd=0.0,
            )
        return CostGuardDecision(
            allowed=True,
            downgrade_judges=False,
            note=f"warning: {note}",
            over_budget=True,
            monthly_spent_usd=monthly_spent_usd,
            projection_usd=projected_usd,
        )


class CostService:
    """Persists the daily roll-up and answers ``how much did <project>
    spend this month?`` queries used by the guard and the cost dashboard.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def record(
        self,
        *,
        org_id: str,
        project_id: str | None,
        profile_id: str | None,
        judge_calls: int,
        judge_input_tokens: int,
        judge_output_tokens: int,
        judge_cost_usd: float,
        rule_evals: int,
        when: datetime | None = None,
    ) -> None:
        if (
            judge_calls == 0
            and judge_cost_usd == 0
            and rule_evals == 0
            and judge_input_tokens == 0
            and judge_output_tokens == 0
        ):
            return
        day = (when or datetime.now(tz=timezone.utc)).date().isoformat()
        # Concurrent auto-rule runs can hit this from many ingests at once; a naive
        # SELECT-then-INSERT races and trips uq_eval_cost_daily. Use upsert.
        for attempt in range(8):
            try:
                async with self._sf() as s:
                    dialect = s.bind.sync_engine.dialect.name
                    insert_fn = sqlite_insert if dialect == "sqlite" else pg_insert
                    ins = insert_fn(EvalCostDailyRow).values(
                        org_id=org_id,
                        project_id=project_id,
                        profile_id=profile_id,
                        day=day,
                        judge_calls=judge_calls,
                        judge_input_tokens=judge_input_tokens,
                        judge_output_tokens=judge_output_tokens,
                        judge_cost_usd=judge_cost_usd,
                        rule_evals=rule_evals,
                    )
                    upsert = ins.on_conflict_do_update(
                        index_elements=[
                            EvalCostDailyRow.org_id,
                            EvalCostDailyRow.project_id,
                            EvalCostDailyRow.profile_id,
                            EvalCostDailyRow.day,
                        ],
                        set_={
                            "judge_calls": EvalCostDailyRow.judge_calls
                            + ins.excluded.judge_calls,
                            "judge_input_tokens": EvalCostDailyRow.judge_input_tokens
                            + ins.excluded.judge_input_tokens,
                            "judge_output_tokens": EvalCostDailyRow.judge_output_tokens
                            + ins.excluded.judge_output_tokens,
                            "judge_cost_usd": EvalCostDailyRow.judge_cost_usd
                            + ins.excluded.judge_cost_usd,
                            "rule_evals": EvalCostDailyRow.rule_evals
                            + ins.excluded.rule_evals,
                        },
                    )
                    await s.execute(upsert)
                    await s.commit()
                return
            except OperationalError as exc:
                msg = str(exc).lower()
                if attempt < 7 and ("locked" in msg or "busy" in msg):
                    await asyncio.sleep(0.05 * (2**attempt))
                    continue
                raise

    async def monthly_spend(
        self,
        *,
        org_id: str,
        project_ids: Iterable[str] | None,
        profile_id: str | None = None,
    ) -> float:
        today = datetime.now(tz=timezone.utc).date()
        first = today.replace(day=1)
        async with self._sf() as s:
            stmt = select(EvalCostDailyRow).where(
                EvalCostDailyRow.org_id == org_id,
                EvalCostDailyRow.day >= first.isoformat(),
            )
            if profile_id is not None:
                stmt = stmt.where(EvalCostDailyRow.profile_id == profile_id)
            rows = (await s.execute(stmt)).scalars().all()
            allowed = set(project_ids) if project_ids is not None else None
            total = 0.0
            for row in rows:
                if allowed is not None and row.project_id is not None and row.project_id not in allowed:
                    continue
                total += float(row.judge_cost_usd)
            return round(total, 6)

    async def daily(
        self,
        *,
        org_id: str,
        project_ids: Iterable[str] | None,
        days: int = 30,
    ) -> list[dict]:
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).date().isoformat()
        async with self._sf() as s:
            stmt = select(EvalCostDailyRow).where(
                EvalCostDailyRow.org_id == org_id,
                EvalCostDailyRow.day >= cutoff,
            ).order_by(EvalCostDailyRow.day)
            rows = (await s.execute(stmt)).scalars().all()
            allowed = set(project_ids) if project_ids is not None else None
            out: list[dict] = []
            for row in rows:
                if allowed is not None and row.project_id is not None and row.project_id not in allowed:
                    continue
                out.append(
                    {
                        "day": row.day,
                        "projectId": row.project_id,
                        "profileId": row.profile_id,
                        "judgeCalls": row.judge_calls,
                        "judgeInputTokens": row.judge_input_tokens,
                        "judgeOutputTokens": row.judge_output_tokens,
                        "judgeCostUsd": round(row.judge_cost_usd, 6),
                        "ruleEvals": row.rule_evals,
                    }
                )
            return out

    async def overview(
        self,
        *,
        org_id: str,
        project_ids: Iterable[str] | None,
    ) -> dict:
        daily = await self.daily(org_id=org_id, project_ids=project_ids, days=30)
        total = round(sum(r["judgeCostUsd"] for r in daily), 6)
        calls = sum(r["judgeCalls"] for r in daily)
        rule = sum(r["ruleEvals"] for r in daily)
        in_tokens = sum(r["judgeInputTokens"] for r in daily)
        out_tokens = sum(r["judgeOutputTokens"] for r in daily)
        return {
            "monthCostUsd": total,
            "judgeCalls": calls,
            "ruleEvals": rule,
            "judgeInputTokens": in_tokens,
            "judgeOutputTokens": out_tokens,
            "daily": daily,
        }
