"""Service classes for the alarm domain.

Every service owns a session factory and exposes async methods that the
router and the background evaluator call. The shape mirrors the existing
``eval.services`` pattern so wiring stays homogeneous.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.alarms.catalog import (
    CHANNEL_KIND_VALUES,
    COMPARATOR_VALUES,
    SEVERITY_VALUES,
    SIGNAL_KIND_VALUES,
    SURFACE_VALUES,
)
from easyobs.alarms.dtos import (
    AlarmChannelDTO,
    AlarmEventDTO,
    AlarmPinDTO,
    AlarmRuleDTO,
)
from easyobs.db.models import (
    AlarmChannelRow,
    AlarmEventRow,
    AlarmPinRow,
    AlarmRuleChannelRow,
    AlarmRuleRow,
)

_UNIFIED_WORKSPACE_SURFACES = (
    "workspace_overview",
    "observe_overview",
    "quality_overview",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _loads(text: str | None, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class AlarmChannelService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(self, *, org_id: str) -> list[AlarmChannelDTO]:
        async with self._sf() as s:
            stmt = (
                select(AlarmChannelRow)
                .where(AlarmChannelRow.org_id == org_id)
                .order_by(AlarmChannelRow.created_at)
            )
            rows = (await s.execute(stmt)).scalars().all()
        return [_channel_to_dto(r) for r in rows]

    async def get(self, *, org_id: str, channel_id: str) -> AlarmChannelDTO | None:
        async with self._sf() as s:
            row = await s.get(AlarmChannelRow, channel_id)
        if row is None or row.org_id != org_id:
            return None
        return _channel_to_dto(row)

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        channel_kind: str,
        config: dict,
        enabled: bool = True,
        actor: str | None,
    ) -> AlarmChannelDTO:
        if channel_kind not in CHANNEL_KIND_VALUES:
            raise ValueError(f"unknown channel_kind: {channel_kind}")
        if not name.strip():
            raise ValueError("name required")
        async with self._sf() as s:
            row = AlarmChannelRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                name=name.strip()[:160],
                channel_kind=channel_kind,
                config_json=_dumps(config or {}),
                enabled=enabled,
                last_test_at=None,
                last_test_status="",
                last_test_error="",
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return _channel_to_dto(row)

    async def update(
        self,
        *,
        org_id: str,
        channel_id: str,
        name: str | None = None,
        config: dict | None = None,
        enabled: bool | None = None,
    ) -> AlarmChannelDTO | None:
        async with self._sf() as s:
            row = await s.get(AlarmChannelRow, channel_id)
            if row is None or row.org_id != org_id:
                return None
            if name is not None:
                row.name = name.strip()[:160]
            if config is not None:
                row.config_json = _dumps(config)
            if enabled is not None:
                row.enabled = enabled
            await s.commit()
            await s.refresh(row)
        return _channel_to_dto(row)

    async def delete(self, *, org_id: str, channel_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(AlarmChannelRow, channel_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
        return True

    async def record_test(
        self, *, channel_id: str, ok: bool, error: str = ""
    ) -> None:
        async with self._sf() as s:
            row = await s.get(AlarmChannelRow, channel_id)
            if row is None:
                return
            row.last_test_at = _now()
            row.last_test_status = "ok" if ok else "fail"
            row.last_test_error = "" if ok else (error or "")[:1000]
            await s.commit()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class AlarmRuleService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self,
        *,
        org_id: str,
        service_ids: Iterable[str] | None = None,
        only_enabled: bool = False,
    ) -> list[AlarmRuleDTO]:
        """Return rules visible to the caller.

        ``service_ids`` is the caller's accessible service list. ``None``
        means "no restriction" (SA / platform admin); otherwise the caller
        gets:
          - rules where service_id is NULL (org-wide), AND
          - rules where service_id is in the allowed list.
        """
        async with self._sf() as s:
            stmt = select(AlarmRuleRow).where(AlarmRuleRow.org_id == org_id)
            if only_enabled:
                stmt = stmt.where(AlarmRuleRow.enabled.is_(True))
            stmt = stmt.order_by(AlarmRuleRow.created_at)
            rows = (await s.execute(stmt)).scalars().all()
            ids = [r.id for r in rows]
            channel_map: dict[str, list[str]] = {rid: [] for rid in ids}
            if ids:
                join_stmt = select(AlarmRuleChannelRow).where(
                    AlarmRuleChannelRow.rule_id.in_(ids)
                )
                for j in (await s.execute(join_stmt)).scalars().all():
                    channel_map.setdefault(j.rule_id, []).append(j.channel_id)
        if service_ids is not None:
            allowed = set(service_ids)
            rows = [
                r for r in rows
                if r.service_id is None or r.service_id in allowed
            ]
        return [_rule_to_dto(r, channel_map.get(r.id, [])) for r in rows]

    async def get(
        self, *, org_id: str, rule_id: str
    ) -> AlarmRuleDTO | None:
        async with self._sf() as s:
            row = await s.get(AlarmRuleRow, rule_id)
            if row is None or row.org_id != org_id:
                return None
            join_stmt = select(AlarmRuleChannelRow).where(
                AlarmRuleChannelRow.rule_id == rule_id
            )
            channel_ids = [
                j.channel_id for j in (await s.execute(join_stmt)).scalars().all()
            ]
        return _rule_to_dto(row, channel_ids)

    async def create(
        self,
        *,
        org_id: str,
        service_id: str | None,
        name: str,
        description: str,
        signal_kind: str,
        signal_params: dict,
        comparator: str,
        threshold: float,
        window_minutes: int,
        min_samples: int,
        dedup_minutes: int,
        severity: str,
        channel_ids: list[str],
        enabled: bool,
        actor: str | None,
    ) -> AlarmRuleDTO:
        _validate_rule_input(
            signal_kind=signal_kind,
            comparator=comparator,
            severity=severity,
            window_minutes=window_minutes,
            min_samples=min_samples,
            dedup_minutes=dedup_minutes,
        )
        if not name.strip():
            raise ValueError("name required")
        async with self._sf() as s:
            row = AlarmRuleRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                service_id=service_id,
                name=name.strip()[:160],
                description=description or "",
                signal_kind=signal_kind,
                signal_params_json=_dumps(signal_params or {}),
                comparator=comparator,
                threshold=float(threshold),
                window_minutes=int(window_minutes),
                min_samples=int(min_samples),
                dedup_minutes=int(dedup_minutes),
                severity=severity,
                enabled=enabled,
                last_evaluated_at=None,
                last_observed_value=None,
                last_state="",
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.flush()
            for cid in dict.fromkeys(channel_ids or []):  # de-dup, preserve order
                s.add(AlarmRuleChannelRow(rule_id=row.id, channel_id=cid))
            await s.commit()
            await s.refresh(row)
        return _rule_to_dto(row, list(dict.fromkeys(channel_ids or [])))

    async def update(
        self,
        *,
        org_id: str,
        rule_id: str,
        **fields,
    ) -> AlarmRuleDTO | None:
        channel_ids = fields.pop("channel_ids", None)
        signal_params = fields.pop("signal_params", None)
        if any(v is not None for v in (
            fields.get("signal_kind"),
            fields.get("comparator"),
            fields.get("severity"),
            fields.get("window_minutes"),
            fields.get("min_samples"),
            fields.get("dedup_minutes"),
        )):
            _validate_rule_input(
                signal_kind=fields.get("signal_kind"),
                comparator=fields.get("comparator"),
                severity=fields.get("severity"),
                window_minutes=fields.get("window_minutes"),
                min_samples=fields.get("min_samples"),
                dedup_minutes=fields.get("dedup_minutes"),
                partial=True,
            )
        async with self._sf() as s:
            row = await s.get(AlarmRuleRow, rule_id)
            if row is None or row.org_id != org_id:
                return None
            for k, v in fields.items():
                if v is None:
                    continue
                if k == "name":
                    row.name = str(v).strip()[:160]
                elif hasattr(row, k):
                    setattr(row, k, v)
            if signal_params is not None:
                row.signal_params_json = _dumps(signal_params)
            if channel_ids is not None:
                # Replace the join rows wholesale.
                await s.execute(
                    delete(AlarmRuleChannelRow).where(
                        AlarmRuleChannelRow.rule_id == rule_id
                    )
                )
                for cid in dict.fromkeys(channel_ids):
                    s.add(AlarmRuleChannelRow(rule_id=rule_id, channel_id=cid))
            await s.commit()
            await s.refresh(row)
            join_stmt = select(AlarmRuleChannelRow).where(
                AlarmRuleChannelRow.rule_id == rule_id
            )
            chan_ids = [
                j.channel_id for j in (await s.execute(join_stmt)).scalars().all()
            ]
        return _rule_to_dto(row, chan_ids)

    async def delete(self, *, org_id: str, rule_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(AlarmRuleRow, rule_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
        return True

    async def list_all_enabled(self) -> list[AlarmRuleDTO]:
        """Used by the periodic evaluator — returns every enabled rule
        across every org (the evaluator is org-aware by reading row.org_id)."""
        async with self._sf() as s:
            stmt = select(AlarmRuleRow).where(AlarmRuleRow.enabled.is_(True))
            rows = (await s.execute(stmt)).scalars().all()
            ids = [r.id for r in rows]
            channel_map: dict[str, list[str]] = {rid: [] for rid in ids}
            if ids:
                join_stmt = select(AlarmRuleChannelRow).where(
                    AlarmRuleChannelRow.rule_id.in_(ids)
                )
                for j in (await s.execute(join_stmt)).scalars().all():
                    channel_map.setdefault(j.rule_id, []).append(j.channel_id)
        return [_rule_to_dto(r, channel_map.get(r.id, [])) for r in rows]

    async def update_evaluation_state(
        self,
        *,
        rule_id: str,
        last_state: str,
        last_observed_value: float | None,
    ) -> None:
        async with self._sf() as s:
            row = await s.get(AlarmRuleRow, rule_id)
            if row is None:
                return
            row.last_evaluated_at = _now()
            row.last_state = last_state
            row.last_observed_value = last_observed_value
            await s.commit()


def _validate_rule_input(
    *,
    signal_kind: str | None,
    comparator: str | None,
    severity: str | None,
    window_minutes: int | None,
    min_samples: int | None,
    dedup_minutes: int | None,
    partial: bool = False,
) -> None:
    if signal_kind is not None and signal_kind not in SIGNAL_KIND_VALUES:
        raise ValueError(f"unknown signal_kind: {signal_kind}")
    if comparator is not None and comparator not in COMPARATOR_VALUES:
        raise ValueError(f"unknown comparator: {comparator}")
    if severity is not None and severity not in SEVERITY_VALUES:
        raise ValueError(f"unknown severity: {severity}")
    if window_minutes is not None and not (1 <= int(window_minutes) <= 24 * 60 * 7):
        raise ValueError("window_minutes out of range")
    if min_samples is not None and int(min_samples) < 1:
        raise ValueError("min_samples must be >= 1")
    if dedup_minutes is not None and int(dedup_minutes) < 0:
        raise ValueError("dedup_minutes must be >= 0")
    if not partial and signal_kind is None:
        raise ValueError("signal_kind required")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class AlarmEventService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self,
        *,
        org_id: str,
        rule_id: str | None = None,
        service_ids: Iterable[str] | None = None,
        state: str | None = None,
        limit: int = 200,
    ) -> list[AlarmEventDTO]:
        async with self._sf() as s:
            stmt = (
                select(AlarmEventRow, AlarmRuleRow.name)
                .join(AlarmRuleRow, AlarmRuleRow.id == AlarmEventRow.rule_id)
                .where(AlarmEventRow.org_id == org_id)
            )
            if rule_id is not None:
                stmt = stmt.where(AlarmEventRow.rule_id == rule_id)
            if state is not None:
                stmt = stmt.where(AlarmEventRow.state == state)
            stmt = stmt.order_by(AlarmEventRow.started_at.desc()).limit(limit)
            rows = (await s.execute(stmt)).all()
        out: list[AlarmEventDTO] = []
        if service_ids is not None:
            allowed = set(service_ids)
        else:
            allowed = None
        for row, rule_name in rows:
            if allowed is not None and row.service_id is not None and row.service_id not in allowed:
                continue
            out.append(_event_to_dto(row, rule_name))
        return out

    async def find_active_firing(
        self, *, rule_id: str
    ) -> AlarmEventDTO | None:
        async with self._sf() as s:
            stmt = (
                select(AlarmEventRow, AlarmRuleRow.name)
                .join(AlarmRuleRow, AlarmRuleRow.id == AlarmEventRow.rule_id)
                .where(
                    AlarmEventRow.rule_id == rule_id,
                    AlarmEventRow.state == "firing",
                    AlarmEventRow.ended_at.is_(None),
                )
                .order_by(AlarmEventRow.started_at.desc())
                .limit(1)
            )
            row = (await s.execute(stmt)).first()
        if row is None:
            return None
        return _event_to_dto(row[0], row[1])

    async def open_firing(
        self,
        *,
        rule_id: str,
        org_id: str,
        service_id: str | None,
        severity: str,
        observed_value: float,
        threshold: float,
        context: dict,
    ) -> AlarmEventDTO:
        async with self._sf() as s:
            row = AlarmEventRow(
                id=uuid.uuid4().hex,
                rule_id=rule_id,
                org_id=org_id,
                service_id=service_id,
                state="firing",
                severity=severity,
                observed_value=observed_value,
                threshold=threshold,
                started_at=_now(),
                ended_at=None,
                context_json=_dumps(context),
                delivery_attempts=0,
                delivery_failures=0,
                last_delivery_error="",
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            stmt = select(AlarmRuleRow).where(AlarmRuleRow.id == rule_id)
            rule_row = (await s.execute(stmt)).scalar_one_or_none()
            rule_name = rule_row.name if rule_row else ""
        return _event_to_dto(row, rule_name)

    async def close_firing(
        self,
        *,
        event_id: str,
        observed_value: float,
        context: dict,
    ) -> None:
        async with self._sf() as s:
            row = await s.get(AlarmEventRow, event_id)
            if row is None or row.state != "firing":
                return
            row.ended_at = _now()
            row.state = "resolved"
            row.observed_value = observed_value
            existing = _loads(row.context_json, {})
            existing.update(context)
            row.context_json = _dumps(existing)
            await s.commit()

    async def open_resolved(
        self,
        *,
        rule_id: str,
        org_id: str,
        service_id: str | None,
        severity: str,
        observed_value: float,
        threshold: float,
        context: dict,
    ) -> AlarmEventDTO:
        """Append a standalone resolved row (used when the dispatcher needs
        to record the resolution as its own timeline entry)."""
        async with self._sf() as s:
            row = AlarmEventRow(
                id=uuid.uuid4().hex,
                rule_id=rule_id,
                org_id=org_id,
                service_id=service_id,
                state="resolved",
                severity=severity,
                observed_value=observed_value,
                threshold=threshold,
                started_at=_now(),
                ended_at=_now(),
                context_json=_dumps(context),
                delivery_attempts=0,
                delivery_failures=0,
                last_delivery_error="",
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            stmt = select(AlarmRuleRow).where(AlarmRuleRow.id == rule_id)
            rule_row = (await s.execute(stmt)).scalar_one_or_none()
            rule_name = rule_row.name if rule_row else ""
        return _event_to_dto(row, rule_name)

    async def record_delivery(
        self,
        *,
        event_id: str,
        ok: bool,
        error: str = "",
    ) -> None:
        async with self._sf() as s:
            row = await s.get(AlarmEventRow, event_id)
            if row is None:
                return
            row.delivery_attempts += 1
            if not ok:
                row.delivery_failures += 1
                row.last_delivery_error = (error or "")[:1000]
            await s.commit()


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


class AlarmPinService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self, *, org_id: str, surface: str | None = None
    ) -> list[AlarmPinDTO]:
        async with self._sf() as s:
            stmt = select(AlarmPinRow).where(AlarmPinRow.org_id == org_id)
            if surface is not None:
                stmt = stmt.where(AlarmPinRow.surface == surface)
            stmt = stmt.order_by(AlarmPinRow.surface, AlarmPinRow.order_index)
            rows = (await s.execute(stmt)).scalars().all()
        return [_pin_to_dto(r) for r in rows]

    async def replace_for_surface(
        self,
        *,
        org_id: str,
        surface: str,
        rule_ids: list[str],
        actor: str | None,
    ) -> list[AlarmPinDTO]:
        if surface not in SURFACE_VALUES:
            raise ValueError(f"unknown surface: {surface}")
        async with self._sf() as s:
            await s.execute(
                delete(AlarmPinRow).where(
                    AlarmPinRow.org_id == org_id,
                    AlarmPinRow.surface == surface,
                )
            )
            now = _now()
            seen: set[str] = set()
            for idx, rid in enumerate(rule_ids):
                if rid in seen:
                    continue
                seen.add(rid)
                s.add(
                    AlarmPinRow(
                        id=uuid.uuid4().hex,
                        org_id=org_id,
                        rule_id=rid,
                        surface=surface,
                        order_index=idx,
                        created_at=now,
                        created_by=actor,
                    )
                )
            await s.commit()
        return await self.list(org_id=org_id, surface=surface)

    async def replace_unified_workspace(
        self,
        *,
        org_id: str,
        rule_ids: list[str],
        actor: str | None,
    ) -> list[AlarmPinDTO]:
        """Pin set for the unified workspace overview.

        Clears legacy per-surface pins so Observe/Quality splits do not linger
        after operators consolidate alerts on one dashboard.
        """
        surface = "workspace_overview"
        if surface not in SURFACE_VALUES:
            raise ValueError(f"unknown surface: {surface}")
        async with self._sf() as s:
            await s.execute(
                delete(AlarmPinRow).where(
                    AlarmPinRow.org_id == org_id,
                    AlarmPinRow.surface.in_(_UNIFIED_WORKSPACE_SURFACES),
                )
            )
            now = _now()
            seen: set[str] = set()
            for idx, rid in enumerate(rule_ids):
                if rid in seen:
                    continue
                seen.add(rid)
                s.add(
                    AlarmPinRow(
                        id=uuid.uuid4().hex,
                        org_id=org_id,
                        rule_id=rid,
                        surface=surface,
                        order_index=idx,
                        created_at=now,
                        created_by=actor,
                    )
                )
            await s.commit()
        return await self.list(org_id=org_id, surface=surface)


# ---------------------------------------------------------------------------
# Row → DTO mappers
# ---------------------------------------------------------------------------


def _channel_to_dto(row: AlarmChannelRow) -> AlarmChannelDTO:
    return AlarmChannelDTO(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        channel_kind=row.channel_kind,
        config=_loads(row.config_json, {}),
        enabled=row.enabled,
        last_test_at=row.last_test_at,
        last_test_status=row.last_test_status,
        last_test_error=row.last_test_error,
        created_at=row.created_at,
    )


def _rule_to_dto(row: AlarmRuleRow, channel_ids: list[str]) -> AlarmRuleDTO:
    return AlarmRuleDTO(
        id=row.id,
        org_id=row.org_id,
        service_id=row.service_id,
        name=row.name,
        description=row.description,
        signal_kind=row.signal_kind,
        signal_params=_loads(row.signal_params_json, {}),
        comparator=row.comparator,
        threshold=row.threshold,
        window_minutes=row.window_minutes,
        min_samples=row.min_samples,
        dedup_minutes=row.dedup_minutes,
        severity=row.severity,
        enabled=row.enabled,
        last_evaluated_at=row.last_evaluated_at,
        last_observed_value=row.last_observed_value,
        last_state=row.last_state,
        channel_ids=list(channel_ids),
        created_at=row.created_at,
    )


def _event_to_dto(row: AlarmEventRow, rule_name: str) -> AlarmEventDTO:
    return AlarmEventDTO(
        id=row.id,
        rule_id=row.rule_id,
        rule_name=rule_name,
        org_id=row.org_id,
        service_id=row.service_id,
        state=row.state,
        severity=row.severity,
        observed_value=row.observed_value,
        threshold=row.threshold,
        started_at=row.started_at,
        ended_at=row.ended_at,
        context=_loads(row.context_json, {}),
        delivery_attempts=row.delivery_attempts,
        delivery_failures=row.delivery_failures,
        last_delivery_error=row.last_delivery_error,
    )


def _pin_to_dto(row: AlarmPinRow) -> AlarmPinDTO:
    return AlarmPinDTO(
        id=row.id,
        org_id=row.org_id,
        rule_id=row.rule_id,
        surface=row.surface,
        order_index=row.order_index,
    )
