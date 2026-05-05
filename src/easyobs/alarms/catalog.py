"""Static catalog of supported delivery channels and observable signals.

Both lists are surfaced via ``GET /v1/alarms/catalog`` so the UI can render
the provider tiles (and their connection forms) directly from server-side
metadata. Keeping them as pure data avoids hard-coding the same shape on
both ends.

The icon glyphs are deliberately plain Unicode characters (not vendor
logos) so the bundle stays free of trademarked artwork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class AlarmChannelKind(str, Enum):
    SLACK = "slack"
    TEAMS = "teams"
    DISCORD = "discord"
    PAGERDUTY = "pagerduty"
    OPSGENIE = "opsgenie"
    WEBHOOK = "webhook"
    EMAIL = "email"


class AlarmSignalKind(str, Enum):
    TRACE_VOLUME = "trace_volume"
    ERROR_RATE = "error_rate"
    LATENCY_P95 = "latency_p95"
    LATENCY_P99 = "latency_p99"
    LLM_COST_USD = "llm_cost_usd"
    LLM_TOKENS_TOTAL = "llm_tokens_total"
    QUALITY_PASS_RATE = "quality_pass_rate"
    QUALITY_AVG_SCORE = "quality_avg_score"
    JUDGE_DISAGREEMENT = "judge_disagreement"
    IMPROVEMENT_OPEN_COUNT = "improvement_open_count"
    JUDGE_COST_USD_DAILY = "judge_cost_usd_daily"


# Surface where a rule can be pinned for at-a-glance visibility.
class AlarmSurface(str, Enum):
    OBSERVE = "observe_overview"
    QUALITY = "quality_overview"
    WORKSPACE = "workspace_overview"


# Comparator semantics. ``eq`` only fires when the value is exactly equal
# (within 1e-6) to the threshold and is mostly used for boolean-style
# signals (eg. improvement_open_count == 0).
ComparatorKind = Literal["gt", "gte", "lt", "lte", "eq"]


@dataclass(frozen=True, slots=True)
class ChannelFieldSpec:
    """Schema for a single config field on a channel kind.

    ``secret=True`` tells the UI to render a password input and to mask
    the value in subsequent reads.
    """

    key: str
    label: str
    type: Literal["string", "url", "secret", "number", "select", "multiline"]
    required: bool = False
    placeholder: str = ""
    help: str = ""
    options: list[str] = field(default_factory=list)
    secret: bool = False


@dataclass(frozen=True, slots=True)
class ChannelCatalogEntry:
    kind: str
    label: str
    blurb: str
    icon: str
    accent: str
    fields: list[ChannelFieldSpec]


@dataclass(frozen=True, slots=True)
class SignalCatalogEntry:
    kind: str
    label: str
    blurb: str
    surface: str  # 'observe' | 'quality'
    unit: str
    suggested_window_minutes: int
    suggested_min_samples: int
    suggested_severity: str
    suggested_comparator: ComparatorKind
    suggested_threshold: float


CHANNEL_CATALOG: list[ChannelCatalogEntry] = [
    ChannelCatalogEntry(
        kind=AlarmChannelKind.SLACK.value,
        label="Slack",
        blurb="Incoming Webhook URL — fastest team-friendly option.",
        icon="≋",
        accent="violet",
        fields=[
            ChannelFieldSpec(
                key="webhook_url",
                label="Webhook URL",
                type="url",
                required=True,
                placeholder="https://hooks.slack.com/services/…",
                secret=True,
            ),
            ChannelFieldSpec(
                key="default_channel",
                label="Default channel",
                type="string",
                placeholder="#alerts",
                help="Optional override; only used when the webhook supports it.",
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.TEAMS.value,
        label="Microsoft Teams",
        blurb="Incoming Webhook (Office 365 connector).",
        icon="▤",
        accent="blue",
        fields=[
            ChannelFieldSpec(
                key="webhook_url",
                label="Webhook URL",
                type="url",
                required=True,
                placeholder="https://outlook.office.com/webhook/…",
                secret=True,
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.DISCORD.value,
        label="Discord",
        blurb="Channel webhook URL.",
        icon="◬",
        accent="indigo",
        fields=[
            ChannelFieldSpec(
                key="webhook_url",
                label="Webhook URL",
                type="url",
                required=True,
                placeholder="https://discord.com/api/webhooks/…",
                secret=True,
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.PAGERDUTY.value,
        label="PagerDuty",
        blurb="Events API v2 — on-call standard.",
        icon="◉",
        accent="green",
        fields=[
            ChannelFieldSpec(
                key="routing_key",
                label="Integration Key",
                type="secret",
                required=True,
                placeholder="32-char Events V2 routing key",
                secret=True,
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.OPSGENIE.value,
        label="Opsgenie",
        blurb="Alerts API — on-call alternative.",
        icon="◭",
        accent="amber",
        fields=[
            ChannelFieldSpec(
                key="api_key",
                label="API Key",
                type="secret",
                required=True,
                secret=True,
            ),
            ChannelFieldSpec(
                key="region",
                label="Region",
                type="select",
                options=["us", "eu"],
                placeholder="us",
                help="us → api.opsgenie.com, eu → api.eu.opsgenie.com",
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.WEBHOOK.value,
        label="Generic Webhook",
        blurb="POST JSON to a custom endpoint with optional HMAC signing.",
        icon="↗",
        accent="slate",
        fields=[
            ChannelFieldSpec(
                key="url",
                label="Endpoint URL",
                type="url",
                required=True,
                placeholder="https://internal.example.com/alarms",
            ),
            ChannelFieldSpec(
                key="hmac_secret",
                label="HMAC secret (optional)",
                type="secret",
                secret=True,
                help="When set, EasyObs signs the body with HMAC-SHA256.",
            ),
            ChannelFieldSpec(
                key="extra_headers",
                label="Extra headers (key=value, one per line)",
                type="multiline",
            ),
        ],
    ),
    ChannelCatalogEntry(
        kind=AlarmChannelKind.EMAIL.value,
        label="Email (SMTP)",
        blurb="SMTP relay — closed-network friendly.",
        icon="✉",
        accent="gray",
        fields=[
            ChannelFieldSpec(
                key="smtp_host",
                label="SMTP host",
                type="string",
                required=True,
                placeholder="smtp.example.com",
            ),
            ChannelFieldSpec(
                key="smtp_port",
                label="SMTP port",
                type="number",
                placeholder="587",
            ),
            ChannelFieldSpec(
                key="smtp_username",
                label="Username",
                type="string",
            ),
            ChannelFieldSpec(
                key="smtp_password",
                label="Password",
                type="secret",
                secret=True,
            ),
            ChannelFieldSpec(
                key="from_address",
                label="From",
                type="string",
                required=True,
                placeholder="alarms@example.com",
            ),
            ChannelFieldSpec(
                key="to_addresses",
                label="To (comma separated)",
                type="string",
                required=True,
                placeholder="oncall@example.com, sre@example.com",
            ),
        ],
    ),
]


SIGNAL_CATALOG: list[SignalCatalogEntry] = [
    SignalCatalogEntry(
        kind=AlarmSignalKind.TRACE_VOLUME.value,
        label="Trace volume (count)",
        blurb="Number of traces ingested in the window.",
        surface="observe",
        unit="count",
        suggested_window_minutes=15,
        suggested_min_samples=1,
        suggested_severity="warning",
        suggested_comparator="lt",
        suggested_threshold=10.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.ERROR_RATE.value,
        label="Error rate (%)",
        blurb="Percentage of traces with status=ERROR.",
        surface="observe",
        unit="percent",
        suggested_window_minutes=15,
        suggested_min_samples=20,
        suggested_severity="critical",
        suggested_comparator="gt",
        suggested_threshold=5.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.LATENCY_P95.value,
        label="Latency P95 (ms)",
        blurb="95th percentile root-trace duration.",
        surface="observe",
        unit="ms",
        suggested_window_minutes=15,
        suggested_min_samples=20,
        suggested_severity="warning",
        suggested_comparator="gt",
        suggested_threshold=2000.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.LATENCY_P99.value,
        label="Latency P99 (ms)",
        blurb="99th percentile root-trace duration.",
        surface="observe",
        unit="ms",
        suggested_window_minutes=30,
        suggested_min_samples=20,
        suggested_severity="warning",
        suggested_comparator="gt",
        suggested_threshold=5000.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.LLM_COST_USD.value,
        label="LLM cost (USD, window)",
        blurb="Sum of price across LLM spans in the window.",
        surface="observe",
        unit="usd",
        suggested_window_minutes=60,
        suggested_min_samples=1,
        suggested_severity="warning",
        suggested_comparator="gt",
        suggested_threshold=10.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.LLM_TOKENS_TOTAL.value,
        label="LLM tokens total (window)",
        blurb="Total in+out tokens across LLM spans.",
        surface="observe",
        unit="tokens",
        suggested_window_minutes=60,
        suggested_min_samples=1,
        suggested_severity="info",
        suggested_comparator="gt",
        suggested_threshold=1_000_000.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.QUALITY_PASS_RATE.value,
        label="Quality pass rate",
        blurb="Pass-rate across recent eval results (0–1).",
        surface="quality",
        unit="ratio",
        suggested_window_minutes=120,
        suggested_min_samples=10,
        suggested_severity="critical",
        suggested_comparator="lt",
        suggested_threshold=0.8,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.QUALITY_AVG_SCORE.value,
        label="Quality avg score",
        blurb="Average score across recent eval results (0–1).",
        surface="quality",
        unit="ratio",
        suggested_window_minutes=120,
        suggested_min_samples=10,
        suggested_severity="warning",
        suggested_comparator="lt",
        suggested_threshold=0.7,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.JUDGE_DISAGREEMENT.value,
        label="Judge disagreement",
        blurb="Mean cross-model disagreement (0 = aligned, 1 = chaotic).",
        surface="quality",
        unit="ratio",
        suggested_window_minutes=120,
        suggested_min_samples=10,
        suggested_severity="warning",
        suggested_comparator="gt",
        suggested_threshold=0.4,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.IMPROVEMENT_OPEN_COUNT.value,
        label="Open improvements",
        blurb="Number of open improvement proposals.",
        surface="quality",
        unit="count",
        suggested_window_minutes=60,
        suggested_min_samples=1,
        suggested_severity="info",
        suggested_comparator="gt",
        suggested_threshold=20.0,
    ),
    SignalCatalogEntry(
        kind=AlarmSignalKind.JUDGE_COST_USD_DAILY.value,
        label="Judge cost / day (USD)",
        blurb="Daily judge spend (rolled up via eval_cost_daily).",
        surface="quality",
        unit="usd",
        suggested_window_minutes=24 * 60,
        suggested_min_samples=1,
        suggested_severity="critical",
        suggested_comparator="gt",
        suggested_threshold=20.0,
    ),
]


CHANNEL_KIND_VALUES = {c.kind for c in CHANNEL_CATALOG}
SIGNAL_KIND_VALUES = {s.kind for s in SIGNAL_CATALOG}
SIGNAL_SURFACES: dict[str, str] = {s.kind: s.surface for s in SIGNAL_CATALOG}
SURFACE_VALUES = {
    AlarmSurface.OBSERVE.value,
    AlarmSurface.QUALITY.value,
    AlarmSurface.WORKSPACE.value,
}
COMPARATOR_VALUES = {"gt", "gte", "lt", "lte", "eq"}
SEVERITY_VALUES = {"info", "warning", "critical"}
