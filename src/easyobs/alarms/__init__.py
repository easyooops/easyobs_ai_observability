"""Threshold alerting (Alarms) module.

The alarm subsystem turns operational metrics (trace volume, error rate,
latency percentiles, LLM cost, …) and quality signals (pass rate, judge
disagreement, daily judge cost, …) into rules that fan out to delivery
channels (Slack, Microsoft Teams, Discord, PagerDuty, Opsgenie, generic
webhook, e-mail).

The module is opt-in via the ``EASYOBS_ALARM_ENABLED`` environment flag and
isolated from the operational ingest path: the evaluator runs as a
periodic asyncio task that reads from the catalog/eval tables and writes
``alarm_event`` rows. Delivery failures never block ingest.
"""

from easyobs.alarms.catalog import (
    CHANNEL_CATALOG,
    SIGNAL_CATALOG,
    AlarmChannelKind,
    AlarmSignalKind,
)
from easyobs.alarms.dispatcher import AlarmDispatcher, DeliveryOutcome
from easyobs.alarms.dtos import (
    AlarmChannelDTO,
    AlarmEventDTO,
    AlarmPinDTO,
    AlarmRuleDTO,
)
from easyobs.alarms.evaluator import AlarmEvaluator
from easyobs.alarms.services import (
    AlarmChannelService,
    AlarmEventService,
    AlarmPinService,
    AlarmRuleService,
)

__all__ = [
    "AlarmChannelDTO",
    "AlarmRuleDTO",
    "AlarmEventDTO",
    "AlarmPinDTO",
    "AlarmChannelService",
    "AlarmRuleService",
    "AlarmEventService",
    "AlarmPinService",
    "AlarmDispatcher",
    "AlarmEvaluator",
    "DeliveryOutcome",
    "CHANNEL_CATALOG",
    "SIGNAL_CATALOG",
    "AlarmChannelKind",
    "AlarmSignalKind",
]
