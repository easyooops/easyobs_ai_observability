"""Alarms router — channels, rules, events, pins, and the catalog endpoint.

Permission map (mirrors the rest of EasyObs):

- Reads (list / get / events / pins / overview): ``require_org_member`` →
  SA, platform-admin, platform-member, or any approved member of ``org_id``.
  DV callers are additionally filtered down to their assigned services so a
  rule scoped to a service the DV cannot read is invisible.
- Mutations (create / update / delete / pin / test): ``require_org_admin`` →
  SA, platform-admin, or PO of ``org_id``.

Catalog (``GET …/alarms/catalog``) is org-member readable so the rule editor
can populate channel-kind / signal-kind tiles without a second round trip.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from easyobs.alarms import (
    AlarmChannelDTO,
    AlarmChannelService,
    AlarmEventDTO,
    AlarmEventService,
    AlarmPinDTO,
    AlarmPinService,
    AlarmRuleDTO,
    AlarmRuleService,
    CHANNEL_CATALOG,
    SIGNAL_CATALOG,
)
from easyobs.alarms.dispatcher import AlarmDispatcher, synthetic_event, synthetic_rule
from easyobs.api.security import (
    CallerContext,
    CurrentUser,
    require_org_admin,
    require_org_member,
)
from easyobs.services.directory import DirectoryService

router = APIRouter(prefix="/v1/organizations", tags=["alarms"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _alarm_services(request: Request) -> dict[str, Any]:
    services = getattr(request.app.state, "alarm_services", None)
    if services is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="alarm module disabled",
        )
    return services


def _channel_svc(request: Request) -> AlarmChannelService:
    return _alarm_services(request)["channels"]


def _rule_svc(request: Request) -> AlarmRuleService:
    return _alarm_services(request)["rules"]


def _event_svc(request: Request) -> AlarmEventService:
    return _alarm_services(request)["events"]


def _pin_svc(request: Request) -> AlarmPinService:
    return _alarm_services(request)["pins"]


AlarmPinSurface = Literal[
    "observe_overview",
    "quality_overview",
    "workspace_overview",
]


async def _merged_workspace_pins(
    request: Request, org_id: str
) -> list[AlarmPinDTO]:
    """Union of pins across the unified and legacy surfaces (deduped)."""
    pin_svc = _pin_svc(request)
    merged: list[AlarmPinDTO] = []
    seen: set[str] = set()
    for surface in ("workspace_overview", "observe_overview", "quality_overview"):
        rows = await pin_svc.list(org_id=org_id, surface=surface)
        for p in rows:
            if p.rule_id in seen:
                continue
            seen.add(p.rule_id)
            merged.append(p)
    return merged


def _dispatcher(request: Request) -> AlarmDispatcher:
    return _alarm_services(request)["dispatcher"]


def _directory(request: Request) -> DirectoryService:
    return request.app.state.directory


async def _accessible_service_ids(
    request: Request, caller: CallerContext, org_id: str
) -> list[str] | None:
    """Resolve the caller's effective service-id scope inside ``org_id``.

    Returns ``None`` for SA / platform-admin / platform-member (no filter)
    and the explicit allowlist for org-scoped roles. The DTOs we return
    re-use this to hide service-scoped rules / events the caller cannot
    read.
    """
    return await _directory(request).accessible_service_ids(
        user_id=caller.user_id,
        is_super_admin=caller.is_super_admin,
        org_id=org_id,
        is_platform_admin=caller.is_platform_admin,
        is_platform_member=caller.is_platform_member,
    )


# ---------------------------------------------------------------------------
# Pydantic IO
# ---------------------------------------------------------------------------


class ChannelFieldOut(BaseModel):
    key: str
    label: str
    type: str
    required: bool
    placeholder: str
    help: str
    options: list[str]
    secret: bool


class ChannelCatalogOut(BaseModel):
    kind: str
    label: str
    blurb: str
    icon: str
    accent: str
    fields: list[ChannelFieldOut]


class SignalCatalogOut(BaseModel):
    kind: str
    label: str
    blurb: str
    surface: str
    unit: str
    suggestedWindowMinutes: int
    suggestedMinSamples: int
    suggestedSeverity: str
    suggestedComparator: str
    suggestedThreshold: float


class CatalogOut(BaseModel):
    channels: list[ChannelCatalogOut]
    signals: list[SignalCatalogOut]


class ChannelOut(BaseModel):
    id: str
    orgId: str
    name: str
    channelKind: str
    config: dict[str, Any]
    enabled: bool
    lastTestAt: str | None
    lastTestStatus: str
    lastTestError: str
    createdAt: str


class CreateChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    channelKind: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class UpdateChannelIn(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class TestChannelOut(BaseModel):
    ok: bool
    detail: str = ""


class RuleOut(BaseModel):
    id: str
    orgId: str
    serviceId: str | None
    name: str
    description: str
    signalKind: str
    signalParams: dict[str, Any]
    comparator: str
    threshold: float
    windowMinutes: int
    minSamples: int
    dedupMinutes: int
    severity: str
    enabled: bool
    channelIds: list[str]
    lastEvaluatedAt: str | None
    lastObservedValue: float | None
    lastState: str
    createdAt: str | None


class CreateRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    serviceId: str | None = None
    description: str = ""
    signalKind: str
    signalParams: dict[str, Any] = Field(default_factory=dict)
    comparator: Literal["gt", "gte", "lt", "lte", "eq"]
    threshold: float
    windowMinutes: int = Field(default=15, ge=1, le=24 * 60 * 7)
    minSamples: int = Field(default=1, ge=1)
    dedupMinutes: int = Field(default=15, ge=0)
    severity: Literal["info", "warning", "critical"] = "warning"
    enabled: bool = True
    channelIds: list[str] = Field(default_factory=list)


class UpdateRuleIn(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    serviceId: str | None = None
    description: str | None = None
    signalKind: str | None = None
    signalParams: dict[str, Any] | None = None
    comparator: Literal["gt", "gte", "lt", "lte", "eq"] | None = None
    threshold: float | None = None
    windowMinutes: int | None = Field(default=None, ge=1, le=24 * 60 * 7)
    minSamples: int | None = Field(default=None, ge=1)
    dedupMinutes: int | None = Field(default=None, ge=0)
    severity: Literal["info", "warning", "critical"] | None = None
    enabled: bool | None = None
    channelIds: list[str] | None = None


class EventOut(BaseModel):
    id: str
    ruleId: str
    ruleName: str
    orgId: str
    serviceId: str | None
    state: str
    severity: str
    observedValue: float
    threshold: float
    startedAt: str
    endedAt: str | None
    context: dict[str, Any]
    deliveryAttempts: int
    deliveryFailures: int
    lastDeliveryError: str


class PinOut(BaseModel):
    id: str
    orgId: str
    ruleId: str
    surface: str
    orderIndex: int


class ReplacePinsIn(BaseModel):
    ruleIds: list[str]


class OverviewRuleOut(BaseModel):
    rule: RuleOut
    pin: PinOut


class OverviewOut(BaseModel):
    surface: str
    items: list[OverviewRuleOut]


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


def _channel_to_out(c: AlarmChannelDTO) -> ChannelOut:
    return ChannelOut(
        id=c.id,
        orgId=c.org_id,
        name=c.name,
        channelKind=c.channel_kind,
        config=_redact_secrets(c.channel_kind, c.config),
        enabled=c.enabled,
        lastTestAt=c.last_test_at.isoformat() if c.last_test_at else None,
        lastTestStatus=c.last_test_status,
        lastTestError=c.last_test_error,
        createdAt=c.created_at.isoformat(),
    )


def _redact_secrets(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Replace secret-marked values with a fixed sentinel so the UI can
    surface "configured" without exposing the value. The caller still
    sends back the sentinel on PATCH and the service preserves the stored
    value when it sees it.
    """
    spec = next((c for c in CHANNEL_CATALOG if c.kind == kind), None)
    if spec is None:
        return dict(config)
    out: dict[str, Any] = {}
    for k, v in (config or {}).items():
        field = next((f for f in spec.fields if f.key == k), None)
        if field is not None and field.secret and v:
            out[k] = "•••configured•••"
        else:
            out[k] = v
    return out


def _merge_secret_config(
    kind: str,
    incoming: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """When the UI submits the redacted sentinel for a secret field, keep
    the previously stored value so the caller does not have to re-type it.
    """
    spec = next((c for c in CHANNEL_CATALOG if c.kind == kind), None)
    if spec is None:
        return dict(incoming)
    merged = dict(incoming)
    for f in spec.fields:
        if not f.secret:
            continue
        new_val = merged.get(f.key)
        if new_val == "•••configured•••" or new_val == "":
            if f.key in existing:
                merged[f.key] = existing[f.key]
    return merged


def _rule_to_out(r: AlarmRuleDTO) -> RuleOut:
    return RuleOut(
        id=r.id,
        orgId=r.org_id,
        serviceId=r.service_id,
        name=r.name,
        description=r.description,
        signalKind=r.signal_kind,
        signalParams=r.signal_params,
        comparator=r.comparator,
        threshold=r.threshold,
        windowMinutes=r.window_minutes,
        minSamples=r.min_samples,
        dedupMinutes=r.dedup_minutes,
        severity=r.severity,
        enabled=r.enabled,
        channelIds=r.channel_ids,
        lastEvaluatedAt=r.last_evaluated_at.isoformat() if r.last_evaluated_at else None,
        lastObservedValue=r.last_observed_value,
        lastState=r.last_state,
        createdAt=r.created_at.isoformat() if r.created_at else None,
    )


def _event_to_out(e: AlarmEventDTO) -> EventOut:
    return EventOut(
        id=e.id,
        ruleId=e.rule_id,
        ruleName=e.rule_name,
        orgId=e.org_id,
        serviceId=e.service_id,
        state=e.state,
        severity=e.severity,
        observedValue=e.observed_value,
        threshold=e.threshold,
        startedAt=e.started_at.isoformat(),
        endedAt=e.ended_at.isoformat() if e.ended_at else None,
        context=e.context,
        deliveryAttempts=e.delivery_attempts,
        deliveryFailures=e.delivery_failures,
        lastDeliveryError=e.last_delivery_error,
    )


def _pin_to_out(p: AlarmPinDTO) -> PinOut:
    return PinOut(
        id=p.id,
        orgId=p.org_id,
        ruleId=p.rule_id,
        surface=p.surface,
        orderIndex=p.order_index,
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/alarms/catalog",
    response_model=CatalogOut,
    dependencies=[Depends(require_org_member())],
)
async def get_catalog(org_id: str) -> CatalogOut:
    return CatalogOut(
        channels=[
            ChannelCatalogOut(
                kind=c.kind,
                label=c.label,
                blurb=c.blurb,
                icon=c.icon,
                accent=c.accent,
                fields=[
                    ChannelFieldOut(
                        key=f.key,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        placeholder=f.placeholder,
                        help=f.help,
                        options=list(f.options),
                        secret=f.secret,
                    )
                    for f in c.fields
                ],
            )
            for c in CHANNEL_CATALOG
        ],
        signals=[
            SignalCatalogOut(
                kind=s.kind,
                label=s.label,
                blurb=s.blurb,
                surface=s.surface,
                unit=s.unit,
                suggestedWindowMinutes=s.suggested_window_minutes,
                suggestedMinSamples=s.suggested_min_samples,
                suggestedSeverity=s.suggested_severity,
                suggestedComparator=s.suggested_comparator,
                suggestedThreshold=s.suggested_threshold,
            )
            for s in SIGNAL_CATALOG
        ],
    )


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/alarms/channels",
    response_model=list[ChannelOut],
    dependencies=[Depends(require_org_member())],
)
async def list_channels(org_id: str, request: Request) -> list[ChannelOut]:
    rows = await _channel_svc(request).list(org_id=org_id)
    return [_channel_to_out(r) for r in rows]


@router.post(
    "/{org_id}/alarms/channels",
    response_model=ChannelOut,
    status_code=201,
)
async def create_channel(
    org_id: str,
    body: CreateChannelIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> ChannelOut:
    try:
        row = await _channel_svc(request).create(
            org_id=org_id,
            name=body.name,
            channel_kind=body.channelKind,
            config=body.config or {},
            enabled=body.enabled,
            actor=caller.user_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _channel_to_out(row)


@router.get(
    "/{org_id}/alarms/channels/{channel_id}",
    response_model=ChannelOut,
    dependencies=[Depends(require_org_member())],
)
async def get_channel(
    org_id: str, channel_id: str, request: Request
) -> ChannelOut:
    row = await _channel_svc(request).get(org_id=org_id, channel_id=channel_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="channel not found")
    return _channel_to_out(row)


@router.patch(
    "/{org_id}/alarms/channels/{channel_id}",
    response_model=ChannelOut,
)
async def update_channel(
    org_id: str,
    channel_id: str,
    body: UpdateChannelIn,
    request: Request,
    _admin: CallerContext = Depends(require_org_admin()),
) -> ChannelOut:
    svc = _channel_svc(request)
    existing = await svc.get(org_id=org_id, channel_id=channel_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="channel not found")
    config = body.config
    if config is not None:
        config = _merge_secret_config(existing.channel_kind, config, existing.config)
    try:
        row = await svc.update(
            org_id=org_id,
            channel_id=channel_id,
            name=body.name,
            config=config,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="channel not found")
    return _channel_to_out(row)


@router.delete(
    "/{org_id}/alarms/channels/{channel_id}",
    status_code=204,
    dependencies=[Depends(require_org_admin())],
)
async def delete_channel(
    org_id: str, channel_id: str, request: Request
) -> None:
    deleted = await _channel_svc(request).delete(
        org_id=org_id, channel_id=channel_id
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="channel not found")


@router.post(
    "/{org_id}/alarms/channels/{channel_id}/test",
    response_model=TestChannelOut,
)
async def test_channel(
    org_id: str,
    channel_id: str,
    request: Request,
    _admin: CallerContext = Depends(require_org_admin()),
) -> TestChannelOut:
    svc = _channel_svc(request)
    row = await svc.get(org_id=org_id, channel_id=channel_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="channel not found")
    # Use the *real* (un-redacted) row from the service — get() returns raw config.
    rule = synthetic_rule(row)
    event = synthetic_event(row)
    outcome = await _dispatcher(request).send(rule=rule, event=event, channel=row)
    await svc.record_test(
        channel_id=channel_id, ok=outcome.ok, error=outcome.detail
    )
    return TestChannelOut(ok=outcome.ok, detail=outcome.detail)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/alarms/rules",
    response_model=list[RuleOut],
    dependencies=[Depends(require_org_member())],
)
async def list_rules(
    org_id: str, request: Request, caller: CurrentUser
) -> list[RuleOut]:
    accessible = await _accessible_service_ids(request, caller, org_id)
    rows = await _rule_svc(request).list(
        org_id=org_id, service_ids=accessible
    )
    return [_rule_to_out(r) for r in rows]


@router.post(
    "/{org_id}/alarms/rules",
    response_model=RuleOut,
    status_code=201,
)
async def create_rule(
    org_id: str,
    body: CreateRuleIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> RuleOut:
    if body.serviceId is not None:
        # Make sure the target service belongs to this org so PO of org A
        # cannot pin a rule on a service of org B.
        svc = await _directory(request).get_service(body.serviceId)
        if svc is None or svc.org_id != org_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="service not in this org"
            )
    try:
        row = await _rule_svc(request).create(
            org_id=org_id,
            service_id=body.serviceId,
            name=body.name,
            description=body.description,
            signal_kind=body.signalKind,
            signal_params=body.signalParams or {},
            comparator=body.comparator,
            threshold=body.threshold,
            window_minutes=body.windowMinutes,
            min_samples=body.minSamples,
            dedup_minutes=body.dedupMinutes,
            severity=body.severity,
            channel_ids=body.channelIds,
            enabled=body.enabled,
            actor=caller.user_id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return _rule_to_out(row)


@router.get(
    "/{org_id}/alarms/rules/{rule_id}",
    response_model=RuleOut,
    dependencies=[Depends(require_org_member())],
)
async def get_rule(
    org_id: str, rule_id: str, request: Request, caller: CurrentUser
) -> RuleOut:
    accessible = await _accessible_service_ids(request, caller, org_id)
    row = await _rule_svc(request).get(org_id=org_id, rule_id=rule_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="rule not found")
    if (
        accessible is not None
        and row.service_id is not None
        and row.service_id not in accessible
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="service access denied")
    return _rule_to_out(row)


@router.patch(
    "/{org_id}/alarms/rules/{rule_id}",
    response_model=RuleOut,
)
async def update_rule(
    org_id: str,
    rule_id: str,
    body: UpdateRuleIn,
    request: Request,
    _admin: CallerContext = Depends(require_org_admin()),
) -> RuleOut:
    if body.serviceId is not None:
        svc = await _directory(request).get_service(body.serviceId)
        if svc is None or svc.org_id != org_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="service not in this org"
            )
    payload: dict[str, Any] = {}
    if body.name is not None:
        payload["name"] = body.name
    if body.description is not None:
        payload["description"] = body.description
    if body.signalKind is not None:
        payload["signal_kind"] = body.signalKind
    if body.signalParams is not None:
        payload["signal_params"] = body.signalParams
    if body.comparator is not None:
        payload["comparator"] = body.comparator
    if body.threshold is not None:
        payload["threshold"] = float(body.threshold)
    if body.windowMinutes is not None:
        payload["window_minutes"] = int(body.windowMinutes)
    if body.minSamples is not None:
        payload["min_samples"] = int(body.minSamples)
    if body.dedupMinutes is not None:
        payload["dedup_minutes"] = int(body.dedupMinutes)
    if body.severity is not None:
        payload["severity"] = body.severity
    if body.enabled is not None:
        payload["enabled"] = body.enabled
    if body.serviceId is not None:
        payload["service_id"] = body.serviceId
    if body.channelIds is not None:
        payload["channel_ids"] = body.channelIds
    try:
        row = await _rule_svc(request).update(
            org_id=org_id, rule_id=rule_id, **payload
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="rule not found")
    return _rule_to_out(row)


@router.delete(
    "/{org_id}/alarms/rules/{rule_id}",
    status_code=204,
    dependencies=[Depends(require_org_admin())],
)
async def delete_rule(
    org_id: str, rule_id: str, request: Request
) -> None:
    deleted = await _rule_svc(request).delete(org_id=org_id, rule_id=rule_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="rule not found")


@router.post(
    "/{org_id}/alarms/rules/{rule_id}/evaluate",
    response_model=RuleOut,
)
async def evaluate_rule(
    org_id: str,
    rule_id: str,
    request: Request,
    _admin: CallerContext = Depends(require_org_admin()),
) -> RuleOut:
    services = _alarm_services(request)
    rule = await services["rules"].get(org_id=org_id, rule_id=rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="rule not found")
    evaluator = services.get("evaluator")
    if evaluator is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="evaluator not available",
        )
    channels = await services["channels"].list(org_id=org_id)
    await evaluator._evaluate_rule(rule, {c.id: c for c in channels})
    refreshed = await services["rules"].get(org_id=org_id, rule_id=rule_id)
    return _rule_to_out(refreshed or rule)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/alarms/events",
    response_model=list[EventOut],
    dependencies=[Depends(require_org_member())],
)
async def list_events(
    org_id: str,
    request: Request,
    caller: CurrentUser,
    rule_id: str | None = None,
    state: Literal["firing", "resolved"] | None = None,
    limit: int = 200,
) -> list[EventOut]:
    accessible = await _accessible_service_ids(request, caller, org_id)
    rows = await _event_svc(request).list(
        org_id=org_id,
        rule_id=rule_id,
        service_ids=accessible,
        state=state,
        limit=max(1, min(limit, 1000)),
    )
    return [_event_to_out(r) for r in rows]


# ---------------------------------------------------------------------------
# Pins / Overview
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/alarms/pins/{surface}",
    response_model=list[PinOut],
    dependencies=[Depends(require_org_member())],
)
async def list_pins(
    org_id: str,
    surface: AlarmPinSurface,
    request: Request,
) -> list[PinOut]:
    if surface == "workspace_overview":
        rows = await _merged_workspace_pins(request, org_id)
    else:
        rows = await _pin_svc(request).list(org_id=org_id, surface=surface)
    return [_pin_to_out(p) for p in rows]


@router.put(
    "/{org_id}/alarms/pins/{surface}",
    response_model=list[PinOut],
)
async def replace_pins(
    org_id: str,
    surface: AlarmPinSurface,
    body: ReplacePinsIn,
    request: Request,
    caller: CallerContext = Depends(require_org_admin()),
) -> list[PinOut]:
    rules = await _rule_svc(request).list(org_id=org_id)
    valid_ids = {r.id for r in rules}
    invalid = [rid for rid in body.ruleIds if rid not in valid_ids]
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unknown rule ids: {invalid}",
        )
    try:
        if surface == "workspace_overview":
            pins = await _pin_svc(request).replace_unified_workspace(
                org_id=org_id,
                rule_ids=body.ruleIds,
                actor=caller.user_id,
            )
        else:
            pins = await _pin_svc(request).replace_for_surface(
                org_id=org_id,
                surface=surface,
                rule_ids=body.ruleIds,
                actor=caller.user_id,
            )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return [_pin_to_out(p) for p in pins]


@router.get(
    "/{org_id}/alarms/overview/{surface}",
    response_model=OverviewOut,
    dependencies=[Depends(require_org_member())],
)
async def overview(
    org_id: str,
    surface: AlarmPinSurface,
    request: Request,
    caller: CurrentUser,
) -> OverviewOut:
    """Pinned alarms with their rule snapshot — unified Overview widget."""
    accessible = await _accessible_service_ids(request, caller, org_id)
    if surface == "workspace_overview":
        pins = await _merged_workspace_pins(request, org_id)
    else:
        pins = await _pin_svc(request).list(org_id=org_id, surface=surface)
    if not pins:
        return OverviewOut(surface=surface, items=[])
    rules = await _rule_svc(request).list(
        org_id=org_id, service_ids=accessible
    )
    by_id = {r.id: r for r in rules}
    items: list[OverviewRuleOut] = []
    for p in pins:
        rule = by_id.get(p.rule_id)
        if rule is None:
            continue
        items.append(
            OverviewRuleOut(rule=_rule_to_out(rule), pin=_pin_to_out(p))
        )
    return OverviewOut(surface=surface, items=items)
