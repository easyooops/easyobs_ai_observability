from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EASYOBS_", env_file=str(_ENV_FILE), extra="ignore")

    data_dir: Path = Field(default=Path("./data"))
    database_url: str = Field(
        default="",
        description=(
            "SQLAlchemy async URL for the catalog metadata DB. Defaults to "
            "SQLite under ``data_dir``; for production swap in a Postgres URL "
            "(e.g. ``postgresql+asyncpg://user:pw@host:5432/easyobs``). Leave "
            "blank to derive ``<data_dir>/catalog.sqlite3``."
        ),
    )

    @model_validator(mode="after")
    def _default_db_url(self) -> "Settings":
        if not self.database_url:
            db_path = (self.data_dir / "catalog.sqlite3").as_posix()
            self.database_url = f"sqlite+aiosqlite:///{db_path}"
        return self
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    cors_origins: str = (
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:3001,http://localhost:3001,"
        "http://127.0.0.1:3002,http://localhost:3002"
    )
    otlp_http_path: str = "/otlp/v1/traces"
    pricing_source: Literal["auto", "tokencost", "litellm", "builtin"] = Field(
        default="auto",
        description=(
            "LLM price data source used for server-side o.price enrichment. "
            "'auto' prefers tokencost, then litellm, then the built-in table."
        ),
    )

    # --- auth -------------------------------------------------------------
    jwt_secret: str = Field(
        default="",
        description=(
            "HS256 signing key for session JWTs. When empty, the server "
            "generates one on first boot and persists it under "
            "``data/jwt.secret`` (chmod 600 recommended)."
        ),
    )
    jwt_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)

    # --- logging ----------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Root log level for the easyobs.* and uvicorn loggers.",
    )
    log_format: Literal["console", "json"] = Field(
        default="console",
        description=(
            "'console' = human readable single-line records (good for local dev "
            "and `docker logs`). 'json' = one JSON object per line — picked up "
            "as-is by CloudWatch Logs (awslogs/fluentbit), Loki, GCP Cloud "
            "Logging, etc."
        ),
    )
    log_file: Path | None = Field(
        default=None,
        description=(
            "Optional path that mirrors all log records to a file in addition "
            "to stdout. Leave unset on container/serverless deployments so the "
            "container runtime owns log capture."
        ),
    )
    log_request_body: bool = Field(
        default=False,
        description=(
            "When true, the request-logging middleware also records JSON "
            "request payloads (capped to 4 KB). Off by default to avoid "
            "leaking PII / secrets."
        ),
    )

    # --- mock / demo data -------------------------------------------------
    seed_mock_data: bool = Field(
        default=False,
        description=(
            "When true, on first boot the API populates the default "
            "'administrator' organization with a 'demo' service and "
            "``seed_mock_traces`` synthetic OTLP traces so an MVP server can "
            "be explored end-to-end without any manual setup. Skipped silently "
            "once any trace exists in the catalog (i.e. the seeder never "
            "overwrites real data)."
        ),
    )
    seed_mock_traces: int = Field(
        default=1000,
        ge=0,
        le=10_000,
        description=(
            "How many synthetic traces the demo seeder generates. Spread "
            "evenly across the last ``seed_mock_window_hours`` so dashboards "
            "have enough points to render."
        ),
    )
    seed_mock_window_hours: int = Field(
        default=720,
        ge=1,
        le=24 * 30,
        description=(
            "Time window the synthetic traces are spread over (~30d max). "
            "Wider windows populate long-range workspace filters."
        ),
    )
    seed_mock_live: bool = Field(
        default=True,
        description=(
            "When true, an asyncio task drips one synthetic trace into "
            "the demo service every ``seed_mock_live_interval_sec`` so "
            "the 'Last 1h / 6h' workspace windows stay populated even "
            "after the server has been up for hours. Requires "
            "``seed_mock_data=true`` (the demo service must already "
            "exist); a no-op otherwise."
        ),
    )
    seed_mock_live_interval_sec: float = Field(
        default=20.0,
        ge=1.0,
        le=3600.0,
        description=(
            "Average cadence for the live demo traffic generator. The "
            "actual delay is jittered ±30% so the resulting chart does "
            "not look like a perfect comb."
        ),
    )
    seed_mock_live_burst_window_sec: float = Field(
        default=10.0,
        ge=0.1,
        le=300.0,
        description=(
            "Each live-traffic trace's start time is jittered backwards "
            "by up to this many seconds from `now()`. Keeps successive "
            "traces from landing on the same nanosecond and gives "
            "Sessions / Spans tabs a tiny bit of natural skew."
        ),
    )

    # --- evaluation (Quality) module --------------------------------------
    eval_enabled: bool = Field(
        default=True,
        description=(
            "Enables the Quality (evaluation) module. When false the API "
            "and UI silently hide every /v1/evaluations endpoint and the "
            "Quality navigation group, so existing operational deployments "
            "that haven't opted in stay unchanged."
        ),
    )
    eval_auto_rule_on_ingest: bool = Field(
        default=True,
        description=(
            "When the Quality module is enabled, also run profile-defined "
            "auto-rule evaluations as a fire-and-forget task right after "
            "ingest. Failures never affect ingest — they are logged and "
            "surfaced in the Quality > Runs view only."
        ),
    )

    # 12.goldenset §2 — Regression Run lifecycle defaults.
    eval_regression_collect_timeout_sec: int = Field(
        default=60,
        ge=5,
        le=900,
        description=(
            "Phase-2 (collecting) window for the Golden Regression Run: "
            "after every agent invocation has either responded or errored, "
            "wait up to this many seconds for the OTLP traces to arrive. "
            "Items whose trace never lands transition to ``timeout`` and "
            "are recorded as ``verdict=error`` so they do not skew the "
            "run's pass-rate."
        ),
    )
    eval_regression_default_max_concurrent: int = Field(
        default=5,
        ge=1,
        le=64,
        description=(
            "Fallback concurrency cap for Golden Regression invocations. "
            "Per-set ``agent_max_concurrent`` overrides this value; the "
            "global setting protects the operator's agent service when a "
            "set is created with the default."
        ),
    )
    eval_regression_poll_interval_sec: float = Field(
        default=2.0,
        ge=0.5,
        le=30.0,
        description=(
            "Cadence the Regression Run worker uses to poll the trace_map "
            "for collection progress. Lower values give a snappier UI at "
            "the cost of one extra DB sweep per tick."
        ),
    )

    # 12.goldenset §9.3 — Golden Set upload guards (CSV/JSONL/xlsx).
    eval_upload_max_mb: int = Field(
        default=25,
        ge=1,
        le=200,
        description=(
            "Hard limit on Golden Set upload payload size. Files above "
            "this threshold are rejected at the router so a malicious "
            "operator cannot exhaust server memory with a single POST."
        ),
    )
    eval_upload_max_rows: int = Field(
        default=50_000,
        ge=1,
        le=500_000,
        description=(
            "Maximum number of rows accepted from a single upload. The "
            "validator stops parsing past this limit and surfaces a "
            "clear error to the operator."
        ),
    )
    eval_upload_max_cols: int = Field(
        default=32,
        ge=1,
        le=256,
        description=(
            "Maximum number of columns accepted per row. Anything wider "
            "is almost certainly a wrongly-shaped sheet."
        ),
    )

    # --- alarm (threshold alerting) module --------------------------------
    alarm_enabled: bool = Field(
        default=True,
        description=(
            "Enables the threshold-alerting (Alarms) module. When false "
            "the API hides every /v1/organizations/.../alarms endpoint and "
            "the periodic evaluator is not started, so deployments that "
            "manage alerting elsewhere stay untouched."
        ),
    )
    alarm_eval_interval_seconds: int = Field(
        default=60,
        ge=15,
        le=3600,
        description=(
            "Cadence for the in-process alarm evaluator. Lower values give "
            "faster firing latency at the cost of one extra DB sweep per "
            "tick. The pluggable ``obsctl-worker`` (commercial) replaces "
            "this loop for higher throughput."
        ),
    )

    @property
    def blob_root(self) -> Path:
        return self.data_dir / "blob"

    @property
    def jwt_secret_path(self) -> Path:
        return self.data_dir / "jwt.secret"


def get_settings() -> Settings:
    return Settings()
