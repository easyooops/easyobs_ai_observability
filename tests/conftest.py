"""Shared fixtures for the evaluation test suite.

Each test gets its own SQLite file (no cross-contamination) and an in-process
``async_sessionmaker`` that wires every eval service. We deliberately *do not*
spin up the FastAPI app here — the service-layer tests run faster and cover
the meat of the logic. A separate module (``test_eval_api_smoke``) exercises
the HTTP surface end-to-end through ``httpx.AsyncClient``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from easyobs.db import models as db_models
from easyobs.eval.services.cost import CostService
from easyobs.eval.services.evaluators import EvaluatorCatalogService
from easyobs.eval.services.goldensets import GoldenSetService
from easyobs.eval.services.improvements import ImprovementService
from easyobs.eval.services.judge_models import JudgeModelService
from easyobs.eval.services.profiles import ProfileService
from easyobs.eval.services.runs import RunService
from easyobs.eval.services.schedules import ScheduleService


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create a fresh SQLite DB per-test and return a session factory bound
    to it. We use the same metadata the production app uses so we exercise
    the *real* ``eval_*`` schema."""

    db_path = tmp_path / "eval.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(db_models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def eval_services(session_factory):
    """Wire the full eval service stack. Tests that need fewer services
    can still pull individual fixtures from the dict."""

    profiles = ProfileService(session_factory)
    judges = JudgeModelService(session_factory)
    cost = CostService(session_factory)
    improvements = ImprovementService(session_factory)
    runs = RunService(
        session_factory,
        profiles=profiles,
        judge_models=judges,
        cost=cost,
        improvements=improvements,
        load_trace=_default_trace_loader,
    )
    goldens = GoldenSetService(session_factory)
    schedules = ScheduleService(session_factory)
    catalog = EvaluatorCatalogService()
    return {
        "session_factory": session_factory,
        "profiles": profiles,
        "judges": judges,
        "cost": cost,
        "improvements": improvements,
        "runs": runs,
        "goldens": goldens,
        "schedules": schedules,
        "catalog": catalog,
    }


async def _default_trace_loader(trace_id: str):
    """A built-in deterministic trace fixture so the run service has data
    to evaluate without needing the full TraceQuery stack."""

    return {
        "traceId": trace_id,
        "status": "OK",
        "startedAt": "2026-04-25T00:00:00+00:00",
        "endedAt": "2026-04-25T00:00:01+00:00",
        "llmSummary": {
            "query": "한국어 질문입니다",
            "response": (
                "안녕하세요! 요청하신 답변은 다음과 같습니다. 모델은 컨텍스트를 "
                "참고하여 정확한 답변을 생성했습니다."
            ),
            "tokensTotal": 1200,
            "price": 0.002,
            "toolCalls": 2,
        },
        "spans": [],
    }
