"""Judge model registry. Org-scoped — every other service joins through it."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalJudgeModelRow
from easyobs.eval.judge.providers import JudgeModelSpec
from easyobs.eval.services.dtos import JudgeModelDTO


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JudgeModelService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(self, *, org_id: str, include_disabled: bool = False) -> list[JudgeModelDTO]:
        async with self._sf() as s:
            stmt = select(EvalJudgeModelRow).where(EvalJudgeModelRow.org_id == org_id)
            if not include_disabled:
                stmt = stmt.where(EvalJudgeModelRow.enabled.is_(True))
            rows = (await s.execute(stmt.order_by(EvalJudgeModelRow.created_at))).scalars().all()
            return [_to_dto(r) for r in rows]

    async def get(self, *, org_id: str, model_id: str) -> JudgeModelDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalJudgeModelRow, model_id)
            if row is None or row.org_id != org_id:
                return None
            return _to_dto(row)

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        provider: str,
        model: str,
        temperature: float,
        weight: float,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
        enabled: bool,
        actor: str | None,
        connection_config: dict | None = None,
    ) -> JudgeModelDTO:
        cfg_json = json.dumps(connection_config or {})
        async with self._sf() as s:
            row = EvalJudgeModelRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                name=name,
                provider=provider,
                model=model,
                temperature=temperature,
                weight=weight,
                cost_per_1k_input=cost_per_1k_input,
                cost_per_1k_output=cost_per_1k_output,
                connection_config_json=cfg_json,
                enabled=enabled,
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def update(
        self,
        *,
        org_id: str,
        model_id: str,
        **fields,
    ) -> JudgeModelDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalJudgeModelRow, model_id)
            if row is None or row.org_id != org_id:
                return None
            for k, v in fields.items():
                if v is None:
                    continue
                if hasattr(row, k):
                    setattr(row, k, v)
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def delete(self, *, org_id: str, model_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalJudgeModelRow, model_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def resolve_specs(
        self, *, org_id: str, refs: list[tuple[str, float]]
    ) -> list[JudgeModelSpec]:
        """Hydrate ``[(model_id, weight)]`` to provider specs.

        Disabled or org-foreign rows are silently dropped — callers should
        validate at write time, but the runner stays defensive."""

        if not refs:
            return []
        ids = [rid for rid, _ in refs]
        async with self._sf() as s:
            rows = (
                await s.execute(
                    select(EvalJudgeModelRow).where(
                        EvalJudgeModelRow.org_id == org_id,
                        EvalJudgeModelRow.id.in_(ids),
                        EvalJudgeModelRow.enabled.is_(True),
                    )
                )
            ).scalars().all()
        weight_map = {rid: w for rid, w in refs}
        out: list[JudgeModelSpec] = []
        for row in rows:
            try:
                conn = json.loads(row.connection_config_json or "{}")
                if not isinstance(conn, dict):
                    conn = {}
            except Exception:
                conn = {}
            out.append(
                JudgeModelSpec(
                    id=row.id,
                    provider=row.provider or "mock",
                    model=row.model or "",
                    name=row.name,
                    weight=float(weight_map.get(row.id, row.weight) or 1.0),
                    temperature=row.temperature,
                    cost_per_1k_input=row.cost_per_1k_input,
                    cost_per_1k_output=row.cost_per_1k_output,
                    connection=conn,
                )
            )
        return out


def _to_dto(row: EvalJudgeModelRow) -> JudgeModelDTO:
    try:
        conn = json.loads(row.connection_config_json or "{}")
        if not isinstance(conn, dict):
            conn = {}
    except Exception:
        conn = {}
    return JudgeModelDTO(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        provider=row.provider,
        model=row.model,
        temperature=row.temperature,
        weight=row.weight,
        cost_per_1k_input=row.cost_per_1k_input,
        cost_per_1k_output=row.cost_per_1k_output,
        enabled=row.enabled,
        created_at=row.created_at,
        connection_config=conn,
    )
