"""Closed enums shared by the evaluation domain.

Defining them as ``StrEnum`` keeps the DB columns plain text (for SQLite
portability) while still giving the Python layer real validation.
"""

from __future__ import annotations

from enum import StrEnum


class EvaluatorKind(StrEnum):
    """Three families of evaluators correspond to the three columns in the
    Result Detail UI: deterministic rules (cheap, fast), LLM judges
    (expensive, opinionated), and human reviewers (slow, ground truth)."""

    RULE = "rule"
    JUDGE = "judge"
    HUMAN = "human"


class TriggerLane(StrEnum):
    """How the run started. The lane drives both UI badges and accounting:
    ``rule_auto`` runs are free; the three judge lanes contribute to the
    daily cost roll-up. ``golden_regression`` is the 12 §2 flow that
    invokes the agent service API, waits for the OTLP trace, then
    evaluates against L1/L2/L3 GT."""

    RULE_AUTO = "rule_auto"
    JUDGE_MANUAL = "judge_manual"
    JUDGE_SCHEDULE = "judge_schedule"
    JUDGE_REPLAY = "judge_replay"
    RULE_REPLAY = "rule_replay"
    GOLDEN_REGRESSION = "golden_regression"


class ConsensusPolicy(StrEnum):
    SINGLE = "single"
    MAJORITY = "majority"
    UNANIMOUS = "unanimous"
    WEIGHTED = "weighted"


class Verdict(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNSET = "unset"
    # 12 §4: Judge call failed (timeout / 5xx / parse error / etc.) —
    # results carrying this verdict MUST be excluded from pass-rate /
    # avg-score aggregations so the failure does not skew evaluation.
    ERROR = "error"


class JudgeErrorType(StrEnum):
    """Why a judge invocation was excluded from the result aggregate.
    Persisted under ``eval_result.judge_error_detail_json.errorType`` and
    bucketed by the Run summary so operators see the exact failure mix."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    PARSE_ERROR = "parse_error"
    AUTH_ERROR = "auth_error"
    UNKNOWN = "unknown"


class GoldenLayer(StrEnum):
    """Three layers reflecting where in the agent loop the example lives.
    L1 captures intent, L2 captures retrieval ground truth, L3 captures
    the final response."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class SourceKind(StrEnum):
    """Where a golden item came from. Used by the UI to badge the row and
    by the curation service to decide whether the item starts in
    ``candidate`` or ``active`` status. ``IMPORT`` was added by the 12
    redesign to track CSV/xlsx/JSONL upload provenance."""

    MANUAL = "manual"
    AUTO = "auto"
    TRACE_GT = "trace_gt"
    IMPORT = "import"


class GoldenSetMode(StrEnum):
    """12 §0: same table, three meanings.

    - ``regression`` — GT-anchored, immutable revision, used for repeatable
      regression / SLA / certification suites.
    - ``cohort``     — GT-less group of traces; ``expand_query`` resolves
      to a fresh trace list at evaluation time.
    - ``synthesized``— LLM-built candidates that need human review before
      they can be promoted to active items.
    """

    REGRESSION = "regression"
    COHORT = "cohort"
    SYNTHESIZED = "synthesized"


class GoldenItemReviewState(StrEnum):
    """12 §8.2 — independent of ``status`` (curation) and ``label_kind``
    (verdict). A ``disputed`` item has at least two raters with
    incompatible labels and gets routed to the review queue."""

    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    DISPUTED = "disputed"


class RunStatus(StrEnum):
    """11 §3.4 / 12 §2.5: a Golden Regression Run walks
    queued → invoking → collecting → evaluating → done so the UI can show
    granular progress; trace-only runs still use queued → running → done."""

    QUEUED = "queued"
    RUNNING = "running"
    INVOKING = "invoking"
    COLLECTING = "collecting"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GoldenRunInvokeStatus(StrEnum):
    """Lifecycle of a single Golden Regression invocation row in
    ``eval_golden_run_trace_map``."""

    PENDING = "pending"
    INVOKED = "invoked"
    COLLECTED = "collected"
    TIMEOUT = "timeout"
    ERROR = "error"


class SynthJobMode(StrEnum):
    """12 §10: which generator strategy ran. RAG-aware samples documents
    from a configured source policy; trace-driven mines operational
    traces for uncovered / failing patterns."""

    RAG_AWARE = "rag_aware"
    TRACE_DRIVEN = "trace_driven"


class SynthJobSourcePolicy(StrEnum):
    """12 §10.2 — how the RAG-aware generator chooses documents."""

    COLLECTION = "collection"
    TAG = "tag"
    TRACE_FREQ = "trace_freq"
    RANDOM = "random"
    EXPLICIT = "explicit"


class SynthJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CostExceedAction(StrEnum):
    BLOCK = "block"
    DOWNGRADE = "downgrade"
    NOTIFY = "notify"


class EvalRunMode(StrEnum):
    """How a manual evaluation run interprets subjects (trace-only, human
    labels, or golden-set-assisted). Agent HTTP replay is out-of-band:
    operators call the service, ingest traces, then pass ``trace_ids``."""

    TRACE = "trace"
    HUMAN_LABEL = "human_label"
    GOLDEN_GT = "golden_gt"
    GOLDEN_JUDGE = "golden_judge"


class JudgeRubricMode(StrEnum):
    """Whether ``judge_rubric_text`` replaces the auto-generated rubric or
    is appended after the default profile/evaluator summary lines."""

    APPEND = "append"
    REPLACE = "replace"
