from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_engine(database_url: str) -> None:
    global _engine, _session_factory
    # SQLite: wait up to 30s for writers (ingest + concurrent auto-rule tasks).
    # Without this, ``database is locked`` is common under parallel OTLP exports.
    kwargs: dict = {"echo": False}
    if "sqlite" in database_url:
        kwargs["connect_args"] = {"timeout": 30.0}
    _engine = create_async_engine(database_url, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db(metadata) -> None:
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        try:
            if conn.engine.dialect.name == "sqlite":
                # Better read/write overlap when post-ingest hooks write eval rows.
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA busy_timeout=30000"))
        except Exception:
            pass
        await _ensure_eval_judge_connection_column(conn)
        await _ensure_eval_result_session_id_column(conn)
        await _ensure_eval_profile_judge_rubric_columns(conn)
        await _ensure_eval_profile_pack_columns(conn)
        await _ensure_eval_profile_dimension_locale_columns(conn)
        await _ensure_eval_improvement_pack_columns(conn)
        await _ensure_eval_run_mode_columns(conn)
        await _ensure_eval_golden_set_redesign_columns(conn)
        await _ensure_eval_golden_item_review_columns(conn)
        await _ensure_eval_result_judge_error_column(conn)


async def _ensure_eval_judge_connection_column(conn) -> None:
    """SQLite ``create_all`` does not add new columns; ALTER once if missing."""
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    try:
        await conn.execute(
            text(
                "ALTER TABLE eval_judge_model ADD COLUMN connection_config_json TEXT DEFAULT '{}'"
            )
        )
    except Exception:
        pass


async def _ensure_eval_result_session_id_column(conn) -> None:
    """Add ``session_id`` when upgrading older SQLite DBs."""
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    try:
        await conn.execute(
            text("ALTER TABLE eval_result ADD COLUMN session_id VARCHAR(128)")
        )
    except Exception:
        pass


async def _ensure_eval_profile_judge_rubric_columns(conn) -> None:
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_profile ADD COLUMN judge_rubric_text TEXT DEFAULT ''",
        "ALTER TABLE eval_profile ADD COLUMN judge_rubric_mode VARCHAR(16) DEFAULT 'append'",
        "ALTER TABLE eval_profile ADD COLUMN judge_system_prompt TEXT DEFAULT ''",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_profile_dimension_locale_columns(conn) -> None:
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_profile ADD COLUMN judge_dimension_prompts_json TEXT DEFAULT '{}'",
        "ALTER TABLE eval_profile ADD COLUMN improvement_content_locale VARCHAR(8) DEFAULT 'en'",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_improvement_pack_columns(conn) -> None:
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_improvement ADD COLUMN improvement_pack VARCHAR(64)",
        "ALTER TABLE eval_improvement ADD COLUMN improvement_content_locale VARCHAR(8)",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_profile_pack_columns(conn) -> None:
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_profile ADD COLUMN judge_user_message_template TEXT DEFAULT ''",
        "ALTER TABLE eval_profile ADD COLUMN improvement_pack VARCHAR(64) DEFAULT 'easyobs_standard'",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_run_mode_columns(conn) -> None:
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_run ADD COLUMN run_mode VARCHAR(24) DEFAULT 'trace'",
        "ALTER TABLE eval_run ADD COLUMN golden_set_id VARCHAR(36)",
        "ALTER TABLE eval_run ADD COLUMN run_context_json TEXT DEFAULT '{}'",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_golden_set_redesign_columns(conn) -> None:
    """12.goldenset redesign — mode + agent invocation settings + synth
    job back-reference. Each ALTER is idempotent: wrapping with try/except
    means a re-deployed image after the column was added is a no-op."""
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_golden_set ADD COLUMN mode VARCHAR(16) DEFAULT 'regression'",
        "ALTER TABLE eval_golden_set ADD COLUMN expand_query_json TEXT DEFAULT '{}'",
        "ALTER TABLE eval_golden_set ADD COLUMN last_synth_job_id VARCHAR(36)",
        "ALTER TABLE eval_golden_set ADD COLUMN agent_endpoint_url TEXT DEFAULT ''",
        "ALTER TABLE eval_golden_set ADD COLUMN agent_request_template_json TEXT DEFAULT '{}'",
        "ALTER TABLE eval_golden_set ADD COLUMN agent_auth_ref VARCHAR(120) DEFAULT ''",
        "ALTER TABLE eval_golden_set ADD COLUMN agent_timeout_sec INTEGER DEFAULT 30",
        "ALTER TABLE eval_golden_set ADD COLUMN agent_max_concurrent INTEGER DEFAULT 5",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_golden_item_review_columns(conn) -> None:
    """12.goldenset §8.2 — review state + dispute reason + revision binding."""
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    for stmt in (
        "ALTER TABLE eval_golden_item ADD COLUMN revision_id VARCHAR(36)",
        "ALTER TABLE eval_golden_item ADD COLUMN label_kind VARCHAR(40)",
        "ALTER TABLE eval_golden_item ADD COLUMN review_state VARCHAR(16) DEFAULT 'unreviewed'",
        "ALTER TABLE eval_golden_item ADD COLUMN dispute_reason TEXT DEFAULT ''",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception:
            pass


async def _ensure_eval_result_judge_error_column(conn) -> None:
    """11/12 — Judge call failure detail. Stored as JSON so the per-model
    breakdown stays aligned with judge_per_model without doubling row count."""
    try:
        dialect = conn.engine.dialect.name
    except Exception:
        dialect = ""
    if dialect != "sqlite":
        return
    try:
        await conn.execute(
            text("ALTER TABLE eval_result ADD COLUMN judge_error_detail_json TEXT DEFAULT '{}'")
        )
    except Exception:
        pass


async def seed_default_judge_prompts() -> None:
    """Seed v1 (default) evaluation prompts for all dimensions on first run.

    Idempotent: skips dimensions that already have at least one row in the DB.
    This runs once during lifespan initialization so users see v1 defaults
    pre-populated when they first open the Evaluation Prompts tab.
    """
    if _session_factory is None:
        return
    try:
        from easyobs.db.models import EvalJudgePromptRow, OrganizationRow
        from easyobs.eval.judge.defaults import (
            DEFAULT_JUDGE_SYSTEM_PROMPT,
            DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
        )
        from easyobs.eval.judge.dimensions_meta import JUDGE_DIMENSION_IDS
        from sqlalchemy import select, func
        import uuid
        from datetime import datetime, timezone

        async with _session_factory() as db:
            orgs = (await db.execute(select(OrganizationRow))).scalars().all()
            if not orgs:
                return
            for org in orgs:
                existing_count = (
                    await db.execute(
                        select(func.count(EvalJudgePromptRow.id)).where(
                            EvalJudgePromptRow.org_id == org.id
                        )
                    )
                ).scalar() or 0
                if existing_count > 0:
                    continue
                now = datetime.now(timezone.utc)
                for dim_id in JUDGE_DIMENSION_IDS:
                    row = EvalJudgePromptRow(
                        id=str(uuid.uuid4()),
                        org_id=org.id,
                        dimension_id=dim_id,
                        version=1,
                        system_prompt=DEFAULT_JUDGE_SYSTEM_PROMPT,
                        user_message_template=DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
                        is_active=True,
                        description="v1 (default) — built-in system prompt",
                        created_at=now,
                        created_by=None,
                    )
                    db.add(row)
                await db.commit()
    except Exception:
        pass


def session_scope() -> async_sessionmaker[AsyncSession]:
    assert _session_factory is not None
    return _session_factory
