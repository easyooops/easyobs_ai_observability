"""Plain-old data DTOs the API router serialises directly.

Keeping them as ``@dataclass(frozen=True, slots=True)`` lets us avoid
pydantic in the service layer (the router still uses pydantic for input
validation) and makes the surface easy to assert on in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class JudgeModelDTO:
    id: str
    org_id: str
    name: str
    provider: str
    model: str
    temperature: float
    weight: float
    cost_per_1k_input: float
    cost_per_1k_output: float
    enabled: bool
    created_at: datetime
    connection_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileEvaluatorRef:
    evaluator_id: str
    weight: float = 1.0
    threshold: float = 0.6
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileJudgeRef:
    model_id: str
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class CostGuardConfig:
    max_cost_usd_per_run: float = 5.0
    max_cost_usd_per_subject: float = 0.05
    monthly_budget_usd: float = 100.0
    on_exceed: str = "block"


@dataclass(frozen=True, slots=True)
class ProfileDTO:
    id: str
    org_id: str
    project_id: str | None
    name: str
    description: str
    evaluators: list[ProfileEvaluatorRef]
    judge_models: list[ProfileJudgeRef]
    consensus: str
    auto_run: bool
    cost_guard: CostGuardConfig
    enabled: bool
    created_at: datetime
    judge_rubric_text: str = ""
    judge_rubric_mode: str = "append"
    judge_system_prompt: str = ""
    judge_user_message_template: str = ""
    improvement_pack: str = "easyobs_standard"
    # Per-dimension criterion overrides for context["evaluationHints"] (EN/KR).
    judge_dimension_prompts: dict[str, dict[str, str]] = field(default_factory=dict)
    improvement_content_locale: str = "en"


@dataclass(frozen=True, slots=True)
class AgentInvokeSettings:
    """12 §2.3 — connection used by Regression Run to call the agent
    service. ``auth_ref`` is a Vault/env reference; the bytes never live
    in this DTO."""

    endpoint_url: str = ""
    request_template: dict[str, Any] = field(default_factory=dict)
    auth_ref: str = ""
    timeout_sec: int = 30
    max_concurrent: int = 5


@dataclass(frozen=True, slots=True)
class GoldenSetDTO:
    id: str
    org_id: str
    project_id: str | None
    name: str
    layer: str
    description: str
    item_count: int
    created_at: datetime
    # 12 §0: 'regression' | 'cohort' | 'synthesized'
    mode: str = "regression"
    expand_query: dict[str, Any] = field(default_factory=dict)
    last_synth_job_id: str | None = None
    agent_invoke: AgentInvokeSettings = field(default_factory=AgentInvokeSettings)


@dataclass(frozen=True, slots=True)
class GoldenItemDTO:
    id: str
    set_id: str
    org_id: str
    project_id: str | None
    layer: str
    source_kind: str
    status: str
    payload: dict[str, Any]
    source_trace_id: str | None
    created_at: datetime
    revision_id: str | None = None
    label_kind: str | None = None
    review_state: str = "unreviewed"
    dispute_reason: str = ""


@dataclass(frozen=True, slots=True)
class GoldenRevisionDTO:
    id: str
    set_id: str
    org_id: str
    revision_no: int
    immutable: bool
    item_count: int
    notes: str
    trust_summary: dict[str, Any]
    created_at: datetime
    locked_at: datetime | None


@dataclass(frozen=True, slots=True)
class SynthJobDTO:
    id: str
    org_id: str
    project_id: str | None
    set_id: str
    revision_id: str | None
    mode: str
    source_policy: str
    source_spec: dict[str, Any]
    judge_model_id: str | None
    target_count: int
    generated_count: int
    status: str
    progress: int
    cost_estimate_usd: float
    cost_actual_usd: float
    error_log: list[dict[str, Any]]
    started_at: datetime | None
    finished_at: datetime | None
    triggered_by: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class GoldenRunInvokeDTO:
    """One row of ``eval_golden_run_trace_map`` lifted into a DTO so the
    Regression Run progress UI can render per-item status without
    leaking the SQL row shape."""

    id: str
    run_id: str
    golden_item_id: str
    trace_id: str | None
    invoke_status: str
    invoke_started_at: datetime | None
    invoke_finished_at: datetime | None
    agent_response: dict[str, Any]
    error_detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GoldenTrustDailyDTO:
    org_id: str
    set_id: str
    revision_id: str
    day: str
    cohen_kappa: float | None
    fleiss_kappa: float | None
    krippendorff_alpha_nominal: float | None
    krippendorff_alpha_ordinal: float | None
    multi_judge_avg_agreement: float | None
    human_judge_kappa: float | None
    rater_count: int
    judge_model_count: int
    disputed_item_count: int
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class ScheduleDTO:
    id: str
    org_id: str
    project_id: str
    profile_id: str
    name: str
    interval_hours: int
    cron: str
    sample_size: int
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunDTO:
    id: str
    org_id: str
    project_id: str | None
    profile_id: str | None
    schedule_id: str | None
    trigger_lane: str
    triggered_by: str | None
    status: str
    subject_count: int
    completed_count: int
    failed_count: int
    cost_estimate_usd: float
    cost_actual_usd: float
    pass_rate: float
    avg_score: float
    notes: str
    started_at: datetime
    finished_at: datetime | None
    run_mode: str = "trace"
    golden_set_id: str | None = None
    run_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FindingDTO:
    evaluator_id: str
    kind: str
    score: float
    verdict: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResultDTO:
    id: str
    run_id: str
    org_id: str
    project_id: str | None
    trace_id: str
    session_id: str | None
    score: float
    verdict: str
    rule_score: float
    judge_score: float | None
    judge_disagreement: float | None
    judge_input_tokens: int
    judge_output_tokens: int
    judge_cost_usd: float
    findings: list[FindingDTO]
    judge_per_model: list[dict[str, Any]]
    trigger_lane: str
    created_at: datetime
    # 12 §4: when verdict == 'error', this carries the type+message+per-model
    # breakdown so the UI can render the failure mix without scraping the
    # findings_json. Empty dict on success.
    judge_error_detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImprovementDTO:
    id: str
    org_id: str
    project_id: str | None
    result_id: str
    trace_id: str
    summary: str
    proposals: list[dict[str, Any]]
    judge_models: list[str]
    consensus_policy: str
    agreement_ratio: float
    judge_cost_usd: float
    status: str
    created_at: datetime
    improvement_pack: str | None = None
    improvement_content_locale: str | None = None
