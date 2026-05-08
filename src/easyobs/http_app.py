from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from easyobs.adapters.blob_local import LocalFilesystemBlobStore
from easyobs.adapters.blob_parquet import LocalParquetBlobStore
from easyobs.adapters.catalog_sqlite import SqliteTraceCatalog
from easyobs.alarms import (
    AlarmChannelService,
    AlarmDispatcher,
    AlarmEvaluator,
    AlarmEventService,
    AlarmPinService,
    AlarmRuleService,
)
from easyobs.api.middleware import RequestLoggingMiddleware
from easyobs.api.routers import (
    alarms as alarms_router,
    analytics,
    auth,
    dashboard,
    evaluations,
    health,
    organizations,
    settings as settings_router,
    tokens,
    traces,
)
from easyobs.api.routers.otlp import build_otlp_router
from easyobs.db import models as db_models
from easyobs.db.session import configure_engine, init_db, session_scope
from easyobs.eval.auto_rule import AutoRuleTrigger
from easyobs.eval.services import (
    EvaluatorCatalogService,
    GoldenRegressionService,
    GoldenSetService,
    HumanLabelService,
    ImprovementService,
    JudgeModelService,
    ProfileService,
    ProgressBroker,
    RunService,
    ScheduleService,
    SynthesizerService,
    TrustService,
)
from easyobs.eval.services.cost import CostService
from easyobs.logging_setup import configure_logging
from easyobs.services import pricing as pricing_service
from easyobs.services.analytics import AnalyticsService
from easyobs.services.app_settings import AppSettingsService, StorageConfig
from easyobs.services.auth import JwtCodec, load_or_create_secret
from easyobs.services.directory import DirectoryService
from easyobs.services.llm_attrs import read_attrs
from easyobs.services.mock_seed import maybe_seed_mock_data
from easyobs.services.tokens import TokenService
from easyobs.services.trace_ingest import TraceIngestService
from easyobs.services.trace_query import TraceQueryService
from easyobs.settings import get_settings


def _resolve_storage(
    settings, log: logging.Logger
) -> tuple[str, Path, AppSettingsService, StorageConfig]:
    """Read the UI-saved storage override (file-backed) and decide the
    effective catalog URL + blob root for this boot.

    The override lives in ``<data_dir>/app_settings.json`` so it survives
    catalog backend swaps (you don't lose the override row when you switch
    SQLite → Postgres).
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app_settings = AppSettingsService(settings.data_dir)
    saved = app_settings.get_storage_sync()

    db_url = settings.database_url
    if saved.catalog.provider == "postgres" and saved.catalog.pg_host:
        db_url = saved.catalog.to_async_url()
        log.info("applying UI-saved postgres catalog override", extra={"database_url": db_url})
    elif saved.catalog.provider == "sqlite" and saved.catalog.sqlite_path:
        candidate = saved.catalog.to_async_url()
        if candidate != db_url:
            db_url = candidate
            log.info("applying UI-saved sqlite catalog override", extra={"database_url": db_url})

    blob_root = settings.blob_root
    if saved.blob.provider == "local" and saved.blob.path:
        blob_root = Path(saved.blob.path)

    return db_url, blob_root, app_settings, saved


def _create_blob_store(settings, saved: StorageConfig, blob_root: Path, log: logging.Logger):
    """Create the appropriate blob store based on provider and storage format."""
    provider = saved.blob.provider
    use_parquet = settings.storage_format == "parquet"

    if provider == "s3":
        from easyobs.adapters.blob_s3 import S3ParquetBlobStore

        log.info("blob store: S3 Parquet", extra={"bucket": saved.blob.bucket})
        return S3ParquetBlobStore(cfg=saved.blob)

    elif provider == "azure":
        from easyobs.adapters.blob_azure import AzureParquetBlobStore

        log.info("blob store: Azure Parquet", extra={"container": saved.blob.azure_container})
        return AzureParquetBlobStore(cfg=saved.blob)

    elif provider == "gcs":
        from easyobs.adapters.blob_gcs import GCSParquetBlobStore

        log.info("blob store: GCS Parquet", extra={"bucket": saved.blob.bucket})
        return GCSParquetBlobStore(cfg=saved.blob)

    else:
        # Local filesystem
        if use_parquet:
            log.info("blob store: Local Parquet", extra={"root": str(blob_root)})
            return LocalParquetBlobStore(blob_root)
        else:
            log.info("blob store: Local NDJSON (legacy)", extra={"root": str(blob_root)})
            return LocalFilesystemBlobStore(blob_root)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("easyobs.boot")
    log.info(
        "starting api",
        extra={
            "data_dir": str(settings.data_dir),
            "database_url": settings.database_url,
            "log_format": settings.log_format,
        },
    )
    pricing_service.set_source(settings.pricing_source)

    effective_db_url, blob_root, app_settings, _saved = _resolve_storage(settings, log)
    settings.database_url = effective_db_url
    configure_engine(effective_db_url)
    await init_db(db_models.Base.metadata)

    blob_root.mkdir(parents=True, exist_ok=True)
    blob = _create_blob_store(settings, _saved, blob_root, log)
    catalog = SqliteTraceCatalog(session_scope())
    token_svc = TokenService(session_scope())
    directory = DirectoryService(session_scope())
    app.state.app_settings = app_settings
    # Make sure the default ``administrator`` org exists so the SA bootstrap
    # path during sign-up has something to attach to.
    await directory.ensure_default_org()

    secret = load_or_create_secret(settings.jwt_secret, settings.jwt_secret_path)
    jwt_codec = JwtCodec(secret=secret, ttl_hours=settings.jwt_ttl_hours)

    trace_ingest = TraceIngestService(blob=blob, catalog=catalog)
    trace_query = TraceQueryService(blob=blob, catalog=catalog)
    app.state.settings = settings
    app.state.blob = blob
    app.state.tokens = token_svc
    app.state.directory = directory
    app.state.jwt = jwt_codec
    app.state.trace_ingest = trace_ingest
    app.state.trace_query = trace_query
    app.state.analytics = AnalyticsService(blob=blob, catalog=catalog)

    # ------------------------------------------------------------------
    # DuckDB Query Engine — opt-in via EASYOBS_QUERY_ENGINE=duckdb
    # When active, replaces the legacy Python-loop analytics and trace
    # query services with DuckDB-powered equivalents for sub-second
    # aggregation over Parquet data at any scale.
    # ------------------------------------------------------------------
    query_engine = None
    if settings.query_engine == "duckdb" and settings.storage_format == "parquet":
        try:
            from easyobs.services.analytics_duckdb import DuckDBAnalyticsService
            from easyobs.services.query_engine import QueryEngine
            from easyobs.services.trace_query_duckdb import DuckDBTraceQueryService

            scan_uri = blob.scan_uri() if hasattr(blob, "scan_uri") else ""
            if scan_uri:
                query_engine = QueryEngine(blob_cfg=_saved.blob, scan_base_uri=scan_uri)
                app.state.trace_query = DuckDBTraceQueryService(
                    engine=query_engine, blob=blob, catalog=catalog
                )
                app.state.analytics = DuckDBAnalyticsService(
                    engine=query_engine, blob=blob, catalog=catalog
                )
                log.info(
                    "DuckDB query engine active",
                    extra={"scan_uri": scan_uri, "storage_format": "parquet"},
                )
            else:
                log.warning("DuckDB requested but scan_uri is empty; falling back to legacy")
        except ImportError as e:
            log.warning(
                "DuckDB query engine requested but dependencies missing; "
                "falling back to legacy analytics. Install with: "
                "pip install easyobs[analytics]",
                extra={"error": str(e)},
            )
    else:
        log.info(
            "using legacy query engine",
            extra={
                "query_engine": settings.query_engine,
                "storage_format": settings.storage_format,
            },
        )
    app.state.query_engine = query_engine

    # ------------------------------------------------------------------
    # Evaluation (Quality) module wiring — strictly opt-in and fully
    # isolated from the live ingest path. We expose every service through
    # ``app.state.eval_services`` so the router can pull them through a
    # single dependency lookup.
    # ------------------------------------------------------------------
    if settings.eval_enabled:
        sf = session_scope()

        from easyobs.db.session import seed_default_judge_prompts

        await seed_default_judge_prompts()

        async def _trace_loader(trace_id: str):
            return await trace_query.trace_detail(
                trace_id, allowed_service_ids=None
            )

        profiles = ProfileService(sf)
        judge_models = JudgeModelService(sf)
        cost = CostService(sf)
        improvements = ImprovementService(sf)
        goldensets = GoldenSetService(sf, trace_query=trace_query)
        human_labels = HumanLabelService(sf)
        runs = RunService(
            sf,
            profiles=profiles,
            judge_models=judge_models,
            cost=cost,
            improvements=improvements,
            load_trace=_trace_loader,
            goldensets=goldensets,
            human_labels=human_labels,
        )
        schedules = ScheduleService(sf)
        evaluators = EvaluatorCatalogService()
        # 11/12 redesign — broker for SSE-streamed long-running workers
        # (Golden Regression Runs and Synthesizer jobs).
        progress = ProgressBroker()
        golden_regression = GoldenRegressionService(
            sf,
            runs=runs,
            goldensets=goldensets,
            progress=progress,
            collect_timeout_sec=settings.eval_regression_collect_timeout_sec,
            poll_interval_sec=settings.eval_regression_poll_interval_sec,
        )
        synthesizer = SynthesizerService(
            sf,
            judge_models=judge_models,
            progress=progress,
            trace_query=trace_query,
        )
        trust = TrustService(sf)
        app.state.eval_services = {
            "profiles": profiles,
            "judge_models": judge_models,
            "cost": cost,
            "improvements": improvements,
            "runs": runs,
            "goldensets": goldensets,
            "schedules": schedules,
            "evaluators": evaluators,
            "human_labels": human_labels,
            "progress": progress,
            "golden_regression": golden_regression,
            "synthesizer": synthesizer,
            "trust": trust,
        }
        # Wire the trace correlator so attribute-stamped OTLP traces feed
        # back into the active Regression Run trace_map automatically.
        async def _golden_correlation_hook(trace_id: str, service_id: str) -> None:
            try:
                detail = await trace_query.trace_detail(
                    trace_id, allowed_service_ids=None
                )
            except Exception:
                return
            if not detail:
                return
            attrs: dict[str, object] = {}
            for span in detail.get("spans") or []:
                # spans may be in two shapes:
                #   (a) raw OTLP — ``attributes`` is a list of
                #       ``{"key": ..., "value": {"stringValue": ...}}`` records
                #   (b) flattened — ``attributes`` is already a dict
                # ``read_attrs`` handles (a). For (b) we copy as-is. Anything
                # else is silently skipped — correlation is best-effort.
                raw = span.get("attributes")
                if isinstance(raw, list):
                    for k, v in read_attrs(span).items():
                        attrs[str(k)] = v
                elif isinstance(raw, dict):
                    for k, v in raw.items():
                        attrs[str(k)] = v
            if attrs:
                await golden_regression.correlate_trace_attribute(trace_id, attrs)

        trace_ingest.register_post_write_hook(_golden_correlation_hook)
        if settings.eval_auto_rule_on_ingest:
            trigger = AutoRuleTrigger(
                profiles=profiles, runs=runs, directory=directory
            )
            trace_ingest.register_post_write_hook(trigger)
            log.info("auto-rule trigger registered")
        log.info("evaluation module enabled")
    else:
        app.state.eval_services = None
        log.info("evaluation module disabled (EASYOBS_EVAL_ENABLED=false)")

    # ------------------------------------------------------------------
    # Alarms (threshold alerting) module — wired separately from Quality
    # because it can run even when the eval module is disabled (operational
    # signals only). The evaluator and dispatcher are kept in
    # ``app.state.alarm_services`` so the router and shutdown hook can
    # reach them through a single lookup.
    # ------------------------------------------------------------------
    alarm_evaluator: AlarmEvaluator | None = None
    if settings.alarm_enabled:
        sf = session_scope()
        alarm_channels = AlarmChannelService(sf)
        alarm_rules = AlarmRuleService(sf)
        alarm_events = AlarmEventService(sf)
        alarm_pins = AlarmPinService(sf)
        alarm_dispatcher = AlarmDispatcher()
        alarm_evaluator = AlarmEvaluator(
            session_factory=sf,
            rules=alarm_rules,
            channels=alarm_channels,
            events=alarm_events,
            dispatcher=alarm_dispatcher,
            analytics=app.state.analytics,
            interval_seconds=settings.alarm_eval_interval_seconds,
        )
        app.state.alarm_services = {
            "channels": alarm_channels,
            "rules": alarm_rules,
            "events": alarm_events,
            "pins": alarm_pins,
            "dispatcher": alarm_dispatcher,
            "evaluator": alarm_evaluator,
        }
        alarm_evaluator.start()
        log.info(
            "alarm module enabled",
            extra={"interval_sec": settings.alarm_eval_interval_seconds},
        )
    else:
        app.state.alarm_services = None
        log.info("alarm module disabled (EASYOBS_ALARM_ENABLED=false)")

    # Optional first-boot mock data. Off by default; only runs when the
    # catalog is empty so we never overwrite real ingest.
    if settings.seed_mock_data:
        seed_profiles = (
            app.state.eval_services["profiles"]
            if settings.eval_enabled and app.state.eval_services
            else None
        )
        seed_judges = (
            app.state.eval_services["judge_models"]
            if settings.eval_enabled and app.state.eval_services
            else None
        )
        await maybe_seed_mock_data(
            directory=directory,
            trace_ingest=trace_ingest,
            count=settings.seed_mock_traces,
            window_hours=settings.seed_mock_window_hours,
            profile_service=seed_profiles,
            judge_model_service=seed_judges,
        )

    # Live demo-traffic generator. Keeps "Last 1h / 6h" populated while
    # the server is up; the seeder alone only fills history once.
    live_traffic_task: asyncio.Task | None = None
    if settings.seed_mock_data and settings.seed_mock_live:
        from easyobs.services.mock_traffic import run_mock_live_traffic

        live_traffic_task = asyncio.create_task(
            run_mock_live_traffic(
                directory=directory,
                trace_ingest=trace_ingest,
                interval_sec=settings.seed_mock_live_interval_sec,
                burst_window_sec=settings.seed_mock_live_burst_window_sec,
            ),
            name="easyobs-mock-live",
        )

    log.info("api ready")
    yield
    log.info("api shutting down")
    if query_engine is not None:
        try:
            query_engine.close()
        except Exception:  # noqa: BLE001
            log.exception("query engine shutdown failed")
    if alarm_evaluator is not None:
        try:
            await alarm_evaluator.stop()
        except Exception:  # noqa: BLE001
            log.exception("alarm evaluator shutdown failed")
    if live_traffic_task is not None and not live_traffic_task.done():
        live_traffic_task.cancel()
        try:
            await live_traffic_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def create_app() -> FastAPI:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(_env_path, override=False)

    settings = get_settings()
    # Configure logging FIRST so any import-time / startup messages from the
    # routers below land on the configured handlers (stdout + optional file).
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_file=settings.log_file,
        service="easyobs-api",
    )

    app = FastAPI(
        title="EasyObs API",
        version="0.2.0",
        openapi_version="3.1.0",
        lifespan=lifespan,
    )
    # Order matters: request-logging is the OUTERMOST layer, so it sees the
    # final status code (after CORS and any other middleware).
    app.add_middleware(RequestLoggingMiddleware)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        # Also trust any local dev origin (Next.js falls back to 3001/3002… when
        # 3000 is held by a zombie process). Narrowed to localhost on purpose.
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(organizations.router)
    app.include_router(traces.router)
    app.include_router(dashboard.router)
    app.include_router(analytics.router)
    app.include_router(tokens.router)
    app.include_router(settings_router.router)
    if settings.eval_enabled:
        app.include_router(evaluations.router)
    if settings.alarm_enabled:
        app.include_router(alarms_router.router)
    app.include_router(build_otlp_router(settings))
    return app
