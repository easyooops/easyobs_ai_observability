"""Plain-old data DTOs for the alarms domain.

The router maps these to Pydantic models for HTTP I/O and the service
layer uses them to keep ORM rows out of the rest of the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AlarmChannelDTO:
    id: str
    org_id: str
    name: str
    channel_kind: str
    config: dict[str, Any]
    enabled: bool
    last_test_at: datetime | None
    last_test_status: str
    last_test_error: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AlarmRuleDTO:
    id: str
    org_id: str
    service_id: str | None
    name: str
    description: str
    signal_kind: str
    signal_params: dict[str, Any]
    comparator: str
    threshold: float
    window_minutes: int
    min_samples: int
    dedup_minutes: int
    severity: str
    enabled: bool
    last_evaluated_at: datetime | None
    last_observed_value: float | None
    last_state: str
    channel_ids: list[str] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlarmEventDTO:
    id: str
    rule_id: str
    rule_name: str
    org_id: str
    service_id: str | None
    state: str
    severity: str
    observed_value: float
    threshold: float
    started_at: datetime
    ended_at: datetime | None
    context: dict[str, Any]
    delivery_attempts: int
    delivery_failures: int
    last_delivery_error: str


@dataclass(frozen=True, slots=True)
class AlarmPinDTO:
    id: str
    org_id: str
    rule_id: str
    surface: str
    order_index: int
