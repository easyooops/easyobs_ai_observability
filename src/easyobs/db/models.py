from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from easyobs.db.types import UtcDateTime


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Identity & multi-tenancy
# ---------------------------------------------------------------------------


class UserRow(Base):
    """Application user. The first user ever created is flagged as super
    admin and bypasses every RBAC check; subsequent users gain access only
    via approved memberships."""

    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)


class OrganizationRow(Base):
    """Tenant boundary. The auto-bootstrapped ``administrator`` org is the
    only one created on first run; SAs may create additional orgs later."""

    __tablename__ = "organization"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)


class MembershipRow(Base):
    """Join row binding a user to an organization with a requested/approved
    role. SA bypasses memberships entirely (no rows required)."""

    __tablename__ = "membership"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True
    )
    # 'PO' (project owner) or 'DV' (developer/user). SA never has a row here.
    role: Mapped[str] = mapped_column(String(2))
    # 'pending' | 'approved' | 'rejected'
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime())
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ServiceRow(Base):
    """A service is the unit that owns ingest tokens and trace data inside
    an organization. Ingest tokens are bound to a single service."""

    __tablename__ = "service"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_service_org_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ServiceAssignmentRow(Base):
    """Per-service grant for ``DV`` members. PO/SA are implicitly granted
    everything in scope."""

    __tablename__ = "service_assignment"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("service.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(UtcDateTime())
    assigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


# ---------------------------------------------------------------------------
# Trace catalog & ingest tokens (now scoped to a service)
# ---------------------------------------------------------------------------


class TraceIndexRow(Base):
    __tablename__ = "trace_index"

    trace_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("service.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    root_name: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="UNSET")
    service_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_relpath: Mapped[str] = mapped_column(Text)


class IngestTokenRow(Base):
    """Bearer tokens accepted on the OTLP ingest endpoint, bound to a service.

    The plaintext secret is only shown once at creation time; storage is a
    SHA-256 hash plus a short preview fragment for display (``eobs_xxxx••••xxxx``).
    """

    __tablename__ = "ingest_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("service.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(128), default="")
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    preview: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


# ---------------------------------------------------------------------------
# Persistent admin / runtime overrides
# ---------------------------------------------------------------------------


class AppSettingRow(Base):
    """Singleton-style key/value store for runtime-tunable platform settings.

    ``value`` is opaque JSON (text-encoded for SQLite portability) so we can
    add settings groups (storage, retention, …) without schema migrations.
    Currently used by the Settings > Storage UI to persist blob/catalog
    overrides on top of the env-defined defaults.
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


# ---------------------------------------------------------------------------
# Quality / Evaluation tables
#
# Every ``eval_*`` table is opt-in and kept strictly disjoint from the
# operational tracing path. The catalog uses these only when the Quality
# feature is enabled — ingest/query paths above do not touch them. All rows
# are scoped by ``(org_id, project_id)`` where ``project_id`` is the EasyObs
# ``service.id`` so the same access matrix that gates traces also gates
# evaluations.
# ---------------------------------------------------------------------------


class EvalJudgeModelRow(Base):
    """Registered LLM model usable as a judge.

    The same model can be referenced by multiple evaluation profiles. Pricing
    is stored next to the model so cost projections are deterministic, and
    the secret never lives in this table — only an opaque provider handle
    (``provider_id``) that the runtime resolves to a real API key.
    """

    __tablename__ = "eval_judge_model"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_eval_judge_model_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="mock")
    model: Mapped[str] = mapped_column(String(120), default="")
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    cost_per_1k_input: Mapped[float] = mapped_column(Float, default=0.0)
    cost_per_1k_output: Mapped[float] = mapped_column(Float, default=0.0)
    # Non-secret connection hints (env var names, endpoints, deployment ids).
    # API keys must not be stored here — operators inject secrets via env/secret store.
    connection_config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalProfileRow(Base):
    """A profile bundles selected evaluators, judge model set, consensus
    policy and cost guard. Org-scoped; optional ``project_id`` restricts the
    profile to a single service. ``evaluators_json`` and ``judge_models_json``
    keep the schema flexible without a separate join table for the MVP."""

    __tablename__ = "eval_profile"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_eval_profile_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("service.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    # JSON list[{evaluator_id, weight, threshold, params}]
    evaluators_json: Mapped[str] = mapped_column(Text, default="[]")
    # JSON list[{model_id, weight}]
    judge_models_json: Mapped[str] = mapped_column(Text, default="[]")
    # 'single' | 'majority' | 'unanimous' | 'weighted'
    consensus: Mapped[str] = mapped_column(String(16), default="single")
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False)
    # JSON {max_cost_usd_per_run, max_cost_usd_per_subject, on_exceed}
    cost_guard_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # LLM-judge rubric overrides (optional; see JudgeRubricMode).
    judge_rubric_text: Mapped[str] = mapped_column(Text, default="")
    judge_rubric_mode: Mapped[str] = mapped_column(String(16), default="append")
    judge_system_prompt: Mapped[str] = mapped_column(Text, default="")
    judge_user_message_template: Mapped[str] = mapped_column(Text, default="")
    improvement_pack: Mapped[str] = mapped_column(String(64), default="easyobs_standard")
    # JSON: per judge dimension criterion overrides
    # {"faithfulness": {"en": "...", "ko": "..."}, ...}
    judge_dimension_prompts_json: Mapped[str] = mapped_column(Text, default="{}")
    # Language for generated improvement proposal text: "en" | "ko"
    improvement_content_locale: Mapped[str] = mapped_column(String(8), default="en")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalJudgePromptRow(Base):
    """Versioned prompt templates for each LLM-as-a-Judge evaluation dimension.

    Each dimension (e.g. faithfulness, answer_relevance, …) has its own prompt
    chain: system message + user message template. Edits create a new version
    (v1 → v2 → v3 …). Only the latest active version is used at evaluation
    time unless a profile pins a specific version.
    """

    __tablename__ = "eval_judge_prompt"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "dimension_id", "version", name="uq_eval_judge_prompt_dim_ver"
        ),
        Index("ix_eval_judge_prompt_dim", "org_id", "dimension_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    dimension_id: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_message_template: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalGoldenSetRow(Base):
    """Container for golden items, scoped to an org and (optionally) one
    project. Layer is one of L1 (query intent), L2 (retrieval), L3
    (response). Items inside follow the layer's schema.

    The 12.goldenset redesign adds a ``mode`` discriminator so the same
    table backs three meanings: ``regression`` (GT-based, immutable revs),
    ``cohort`` (trace-only group, no GT), and ``synthesized`` (LLM-built
    candidates pending review). Agent invocation settings power the
    Regression Run flow (real API call → OTLP trace → evaluate)."""

    __tablename__ = "eval_golden_set"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_eval_golden_set_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("service.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    layer: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text, default="")
    # 12.goldenset §1: 'regression' (default; GT-based) | 'cohort' (trace
    # group, no GT) | 'synthesized' (LLM auto-generated, candidate first).
    mode: Mapped[str] = mapped_column(String(16), default="regression", index=True)
    # ``cohort`` mode: stored search expression that expands to trace_ids
    # at evaluation time. JSON: {time_window?, filter?, session_id?, ...}.
    expand_query_json: Mapped[str] = mapped_column(Text, default="{}")
    # Last Synthesizer job for this set (mode=synthesized).
    last_synth_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Agent service connection used by Regression Run (12 §2.3).
    agent_endpoint_url: Mapped[str] = mapped_column(Text, default="")
    # JSON template — string interpolated with {{query_text}}, {{run_id}},
    # {{item_id}} so the agent receives a stable shape.
    agent_request_template_json: Mapped[str] = mapped_column(Text, default="{}")
    # Reference to a Vault/env-stored bearer/api key (no plaintext here).
    agent_auth_ref: Mapped[str] = mapped_column(String(120), default="")
    agent_timeout_sec: Mapped[int] = mapped_column(Integer, default=30)
    agent_max_concurrent: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalGoldenItemRow(Base):
    """A single labelled example. ``source_kind`` records the entry point
    (manual / auto / trace_gt / import) and ``status`` carries the curation
    state (candidate / active / archived). ``payload_json`` follows the
    layer schema; we keep it as opaque JSON to dodge schema churn.

    12.goldenset §8.2 adds review-state metadata so multi-rater
    workflows can mark disputed items + auto/synthesized candidates can
    be tracked through curation."""

    __tablename__ = "eval_golden_item"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("eval_golden_set.id", ondelete="CASCADE"),
        index=True,
    )
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    layer: Mapped[str] = mapped_column(String(8))
    source_kind: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    source_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Optional revision binding — populated when a regression set publishes
    # the item under an immutable revision (12 §3.2).
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 12 §8.2: human label verdict (separate from runtime ``verdict``) —
    # a free-form short label so different orgs can keep their own enum.
    label_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 12 §8.2: 'unreviewed' | 'reviewed' | 'disputed'.
    review_state: Mapped[str] = mapped_column(String(16), default="unreviewed", index=True)
    dispute_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalGoldenRevisionRow(Base):
    """Immutable snapshot of a regression-mode set's items. Revisions are
    auto-created when a set is first attached to a Run (12 §3.2): the live
    items are duplicated under the new revision id, the revision is
    flipped to ``immutable=True``, and any subsequent edits start a fresh
    revision so prior eval results stay reproducible.

    Cohort and Synthesized sets do not auto-revise — they stay mutable
    until the operator explicitly publishes."""

    __tablename__ = "eval_golden_revision"
    __table_args__ = (
        UniqueConstraint("set_id", "revision_no", name="uq_eval_golden_revision_set_no"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("eval_golden_set.id", ondelete="CASCADE"),
        index=True,
    )
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    immutable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    # Cached trust metrics for this revision (12 §11.1). JSON object
    # populated by the daily worker. Shape:
    # {"cohen_kappa": 0.71, "fleiss_kappa": null,
    #  "krippendorff_alpha_nominal": 0.69,
    #  "krippendorff_alpha_ordinal": null,
    #  "multi_judge_avg_agreement": 0.91,
    #  "human_judge_kappa": 0.62}
    trust_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class EvalSynthJobRow(Base):
    """LLM-driven Golden Set generator job (12 §10).

    Two modes:
    - ``rag_aware``  — sample documents from a configured source policy
      and ask the judge LLM to author Q+A candidates grounded in each.
    - ``trace_driven`` — cluster operational traces, surface uncovered /
      high-failure / new-intent patterns, and convert them into
      candidates the operator must review.

    Progress is streamed over SSE; the worker keeps running across
    browser closes so operators can come back to a finished job."""

    __tablename__ = "eval_synth_job"
    __table_args__ = (
        Index("ix_synth_job_status", "org_id", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("eval_golden_set.id", ondelete="CASCADE"),
        index=True,
    )
    revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 'rag_aware' | 'trace_driven'
    mode: Mapped[str] = mapped_column(String(16))
    # 'collection' | 'tag' | 'trace_freq' | 'random' | 'explicit'
    source_policy: Mapped[str] = mapped_column(String(20))
    # JSON: collection_id / tag / explicit doc ids / window etc.
    source_spec_json: Mapped[str] = mapped_column(Text, default="{}")
    judge_model_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    generated_count: Mapped[int] = mapped_column(Integer, default=0)
    # 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # 0..100 — for SSE progress without a divisor on the client.
    progress: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_actual_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error_log_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class EvalGoldenRunTraceMapRow(Base):
    """Maps an agent-API invocation made by a Regression Run to the OTLP
    trace it produced (12 §2.2).

    The runner stamps ``golden_run_id`` and ``golden_item_id`` into the
    request metadata; an ingest hook then fills ``trace_id`` once the
    matching trace lands. Until the trace arrives or the collection
    timeout fires, the row sits in ``pending`` / ``invoked`` / ``timeout``
    states so the UI can render per-item progress."""

    __tablename__ = "eval_golden_run_trace_map"
    __table_args__ = (
        Index("ix_golden_run_trace_map_run", "run_id", "invoke_status"),
        Index("ix_golden_run_trace_map_trace", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_run.id", ondelete="CASCADE"), index=True
    )
    golden_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_golden_item.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 'pending' | 'invoked' | 'collected' | 'timeout' | 'error'
    invoke_status: Mapped[str] = mapped_column(String(16), default="pending")
    invoke_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    invoke_finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # Compact agent response — full payload (if any) lives in S3.
    agent_response_json: Mapped[str] = mapped_column(Text, default="{}")
    # JSON detail when invoke_status in ('timeout','error').
    error_detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class EvalGoldenTrustDailyRow(Base):
    """Daily roll-up of inter-rater reliability per revision (12 §11).

    The four metrics live on one row so the UI can render trend charts
    without joining four tables. The worker recomputes each open
    (org, revision) every night."""

    __tablename__ = "eval_golden_trust_daily"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "revision_id", "day", name="uq_eval_golden_trust_daily"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    set_id: Mapped[str] = mapped_column(String(36), index=True)
    revision_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    cohen_kappa: Mapped[float | None] = mapped_column(Float, nullable=True)
    fleiss_kappa: Mapped[float | None] = mapped_column(Float, nullable=True)
    krippendorff_alpha_nominal: Mapped[float | None] = mapped_column(Float, nullable=True)
    krippendorff_alpha_ordinal: Mapped[float | None] = mapped_column(Float, nullable=True)
    multi_judge_avg_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    human_judge_kappa: Mapped[float | None] = mapped_column(Float, nullable=True)
    rater_count: Mapped[int] = mapped_column(Integer, default=0)
    judge_model_count: Mapped[int] = mapped_column(Integer, default=0)
    disputed_item_count: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(UtcDateTime())


class EvalScheduleRow(Base):
    """Cron-style trigger for a Judge profile. Stored as an interval (hours)
    plus an optional cron string for future use; the runtime currently uses
    ``interval_hours`` only."""

    __tablename__ = "eval_schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("service.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_profile.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), default="")
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    cron: Mapped[str] = mapped_column(String(64), default="")
    sample_size: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EvalRunRow(Base):
    """A single execution of a profile against a target subject set.
    ``trigger_lane`` distinguishes auto-rule (ingest) from manual / scheduled
    judge runs and from replays. Cost columns hold both the projection
    (``cost_estimate_usd``) and the actual rolled-up cost
    (``cost_actual_usd``) — these stay in sync with the ``eval_cost_daily``
    aggregate but allow per-run views without a second query."""

    __tablename__ = "eval_run"
    __table_args__ = (
        Index("ix_eval_run_org_started", "org_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("eval_profile.id", ondelete="SET NULL"),
        nullable=True,
    )
    schedule_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("eval_schedule.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_lane: Mapped[str] = mapped_column(String(20), default="judge_manual")
    triggered_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    subject_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_actual_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_score: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    # EvalRunMode — trace | human_label | golden_gt | golden_judge
    run_mode: Mapped[str] = mapped_column(String(24), default="trace")
    golden_set_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("eval_golden_set.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Extra payload: humanLabels[], agentInvoke hints, etc.
    run_context_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class EvalResultRow(Base):
    """One trace evaluated by one run. Findings (per evaluator) are stored
    as compact JSON to keep this table append-only and cheap to scan."""

    __tablename__ = "eval_result"
    __table_args__ = (
        Index("ix_eval_result_run_score", "run_id", "score"),
        Index("ix_eval_result_org_proj_trace", "org_id", "project_id", "trace_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_run.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(16), default="unset")
    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_disagreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    judge_per_model_json: Mapped[str] = mapped_column(Text, default="[]")
    trigger_lane: Mapped[str] = mapped_column(String(20), default="judge_manual")
    # 12 §4: when judge calls fail (timeout / 5xx / parse error / etc.)
    # the runtime records the verdict='error' here so aggregates can
    # cleanly exclude the row and the UI can render an error breakdown.
    # Shape: {"errorType": "timeout|rate_limit|parse_error|server_error|...",
    #          "perModel": [{"modelId": "...", "errorType": "...",
    #                        "message": "...", "retryCount": 3}]}
    judge_error_detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)


class EvalCostDailyRow(Base):
    """Daily roll-up of judge spend per ``(org, project, profile)`` triple.
    Lets the UI render the cost trend widget without scanning every result.
    """

    __tablename__ = "eval_cost_daily"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "project_id",
            "profile_id",
            "day",
            name="uq_eval_cost_daily",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    judge_calls: Mapped[int] = mapped_column(Integer, default=0)
    judge_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    rule_evals: Mapped[int] = mapped_column(Integer, default=0)


class EvalImprovementRow(Base):
    """An auto-generated improvement pack for a low-scoring result. The
    pack body itself stays as JSON because the proposal taxonomy is open
    (custom categories per org)."""

    __tablename__ = "eval_improvement"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    result_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_result.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    proposals_json: Mapped[str] = mapped_column(Text, default="[]")
    judge_models_json: Mapped[str] = mapped_column(Text, default="[]")
    consensus_policy: Mapped[str] = mapped_column(String(16), default="single")
    agreement_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    judge_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    improvement_pack: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    improvement_content_locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class HumanLabelAnnotationRow(Base):
    """Org-scoped human reference labels for traces.

    Used by manual ``human_label`` evaluation runs: the runner merges these
    rows into the judge ``context``. This is an EasyObs-native workflow.
    """

    __tablename__ = "human_label_annotation"
    __table_args__ = (UniqueConstraint("org_id", "trace_id", name="uq_human_label_org_trace"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    expected_response: Mapped[str] = mapped_column(Text, default="")
    human_verdict: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


# ---------------------------------------------------------------------------
# Threshold alerting (Alarms)
#
# The alarm tables are intentionally separate from the operational ``trace_*``
# and the ``eval_*`` evaluation tables — alarms read from both worlds but write
# to a dedicated namespace so the access matrix and the lifecycle stay clean.
# Naming is EasyObs-native (``alarm_*``) and does not match the identifiers used
# by the OSS we benchmarked.
# ---------------------------------------------------------------------------


class AlarmChannelRow(Base):
    """A delivery channel (Slack / Teams / Discord / PagerDuty / Opsgenie /
    Webhook / Email) the dispatcher can hand a firing event to.

    Secrets (incoming-webhook URLs, integration keys, …) are stored as
    structured JSON in ``config_json``. For the MVP operators may either
    paste the secret directly *or* reference an environment variable
    (``"<field>_env": "ENV_VAR_NAME"``). The dispatcher resolves env-name
    references at send time so a production deployment can keep secrets in
    KMS / secret manager.
    """

    __tablename__ = "alarm_channel"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_alarm_channel_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    # 'slack' | 'teams' | 'discord' | 'pagerduty' | 'opsgenie' | 'webhook' | 'email'
    channel_kind: Mapped[str] = mapped_column(String(32), index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # 'ok' | 'fail' | '' (never tested)
    last_test_status: Mapped[str] = mapped_column(String(16), default="")
    last_test_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AlarmRuleRow(Base):
    """A threshold rule. ``signal_kind`` selects the metric, ``signal_params_json``
    carries the optional scoping parameters (eg. ``profile_id`` for
    quality_pass_rate, ``model`` for llm_cost), and the ``comparator``
    + ``threshold`` + ``window_minutes`` triple drives the evaluator.

    ``service_id`` NULL means org-wide (sums every accessible service);
    a non-NULL value scopes the metric to a single service so the rule
    obeys the same access matrix as the rest of the platform.
    """

    __tablename__ = "alarm_rule"
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_alarm_rule_org_name"),
        Index(
            "ix_alarm_rule_org_kind",
            "org_id",
            "service_id",
            "signal_kind",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    service_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("service.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    # 'trace_volume' | 'error_rate' | 'latency_p95' | 'latency_p99' |
    # 'llm_cost_usd' | 'llm_tokens_total' | 'quality_pass_rate' |
    # 'quality_avg_score' | 'judge_disagreement' | 'improvement_open_count' |
    # 'judge_cost_usd_daily'
    signal_kind: Mapped[str] = mapped_column(String(40), index=True)
    signal_params_json: Mapped[str] = mapped_column(Text, default="{}")
    # 'gt' | 'gte' | 'lt' | 'lte' | 'eq'
    comparator: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    window_minutes: Mapped[int] = mapped_column(Integer, default=15)
    min_samples: Mapped[int] = mapped_column(Integer, default=1)
    dedup_minutes: Mapped[int] = mapped_column(Integer, default=15)
    # 'info' | 'warning' | 'critical'
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 'ok' | 'firing' | 'insufficient_data' | 'disabled' | ''
    last_state: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AlarmRuleChannelRow(Base):
    """N:N join — a rule can fan-out to multiple channels and one channel
    can be reused by many rules."""

    __tablename__ = "alarm_rule_channel"

    rule_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("alarm_rule.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("alarm_channel.id", ondelete="CASCADE"),
        primary_key=True,
    )


class AlarmEventRow(Base):
    """One firing/resolved transition for a rule.

    The dispatcher updates ``delivery_attempts`` / ``delivery_failures`` and
    leaves the row append-only otherwise — a resolve creates a separate row
    with ``state='resolved'`` so the timeline view can show the full history.
    """

    __tablename__ = "alarm_event"
    __table_args__ = (
        Index("ix_alarm_event_rule_started", "rule_id", "started_at"),
        Index("ix_alarm_event_org_started", "org_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alarm_rule.id", ondelete="CASCADE"), index=True
    )
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    service_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 'firing' | 'resolved'
    state: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    observed_value: Mapped[float] = mapped_column(Float, default=0.0)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivery_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_delivery_error: Mapped[str] = mapped_column(Text, default="")


class AlarmPinRow(Base):
    """Surfaces an alarm rule on Observe Overview or Quality Overview.

    PO/SA pin a rule to a "surface" so DV can see the live state without
    leaving the page they already use the most.
    """

    __tablename__ = "alarm_pin"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "surface", "rule_id", name="uq_alarm_pin_surface_rule"
        ),
        Index("ix_alarm_pin_surface_order", "org_id", "surface", "order_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organization.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alarm_rule.id", ondelete="CASCADE"), index=True
    )
    # 'observe_overview' | 'quality_overview' | 'workspace_overview'
    surface: Mapped[str] = mapped_column(String(32))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
