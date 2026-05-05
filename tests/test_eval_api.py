"""HTTP smoke + access-control tests for the Quality module router.

We boot the real FastAPI app via :class:`asgi_lifespan.LifespanManager` so
the lifespan wiring (eval services, auto-rule hook registration) actually
runs. ``httpx.AsyncClient`` is bound to the ASGI transport so we never
open a real TCP socket.

Every test isolates state by pointing ``EASYOBS_DATA_DIR`` at a fresh
``tmp_path`` and clearing ``easyobs.db.session`` module-level globals
between tests.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager


@pytest_asyncio.fixture()
async def app_client(tmp_path) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    """Spin up an isolated FastAPI app rooted at ``tmp_path``.

    We have to reset the ``easyobs.db.session`` and ``easyobs.settings``
    modules between tests because both stash module-level globals (the
    engine + cached settings). ``importlib.reload`` is the cleanest way
    to do that without leaking state across runs."""

    os.environ["EASYOBS_DATA_DIR"] = str(tmp_path)
    os.environ["EASYOBS_DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{(tmp_path / 'cat.sqlite3').as_posix()}"
    )
    os.environ["EASYOBS_EVAL_ENABLED"] = "true"
    os.environ["EASYOBS_EVAL_AUTO_RULE_ON_INGEST"] = "false"
    os.environ["EASYOBS_LOG_LEVEL"] = "WARNING"
    os.environ["EASYOBS_SEED_MOCK_DATA"] = "false"

    for mod in list(sys.modules):
        if mod.startswith("easyobs."):
            del sys.modules[mod]

    from easyobs.http_app import create_app
    app = create_app()

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client, app


async def _bootstrap_directory(app):
    """Create one SA + one tenant org + a service inside that org. Returns
    a dictionary the tests can pull IDs / tokens out of."""

    directory = app.state.directory
    jwt_codec = app.state.jwt
    sa = await directory.sign_up(
        email="sa@example.com",
        password="password123",
        display_name="SA",
        org_id=None,
        requested_role=None,
    )
    org = await directory.create_organization("acme")
    service = await directory.create_service(
        org_id=org.id,
        name="payments",
        description="payments service",
        actor_user_id=sa.user.id,
    )
    sa_token = jwt_codec.issue(
        user_id=sa.user.id, is_super_admin=True, current_org=org.id,
    )
    return {
        "sa_user_id": sa.user.id,
        "org_id": org.id,
        "service_id": service.id,
        "token": sa_token,
    }


async def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_rejected(app_client):
    client, _ = app_client
    resp = await client.get("/v1/evaluations/profiles")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_evaluator_catalog_lists_all_built_ins(app_client):
    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    resp = await client.get(
        "/v1/evaluations/evaluators",
        headers=await _auth_headers(bootstrap["token"]),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert any(item["id"] == "rule.response.present" for item in data["items"])
    assert len(data["items"]) >= 52


@pytest.mark.asyncio
async def test_full_profile_lifecycle_through_http(app_client):
    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    headers = await _auth_headers(bootstrap["token"])

    judge_resp = await client.post(
        "/v1/evaluations/judge-models",
        json={
            "name": "alpha-judge",
            "provider": "mock",
            "model": "mock-1",
            "weight": 1.0,
            "cost_per_1k_input": 0.5,
            "cost_per_1k_output": 1.5,
        },
        headers=headers,
    )
    assert judge_resp.status_code == 201, judge_resp.text
    judge_id = judge_resp.json()["id"]

    profile_resp = await client.post(
        "/v1/evaluations/profiles",
        json={
            "project_id": bootstrap["service_id"],
            "name": "default-quality",
            "description": "demo",
            "evaluators": [
                {"evaluator_id": "rule.response.present"},
                {"evaluator_id": "rule.response.length"},
                {"evaluator_id": "rule.status.ok"},
            ],
            "judge_models": [{"model_id": judge_id, "weight": 1.0}],
            "consensus": "single",
            "auto_run": False,
            "cost_guard": {
                "max_cost_usd_per_run": 5.0,
                "max_cost_usd_per_subject": 2.0,
                "monthly_budget_usd": 50.0,
                "on_exceed": "block",
            },
            "judge_rubric_text": "Prefer concise answers.",
            "judge_rubric_mode": "append",
            "judge_system_prompt": "",
        },
        headers=headers,
    )
    assert profile_resp.status_code == 201, profile_resp.text
    profile = profile_resp.json()
    assert profile["projectId"] == bootstrap["service_id"]
    assert profile["judgeModels"][0]["modelId"] == judge_id
    assert profile.get("judgeRubricText") == "Prefer concise answers."
    assert profile.get("judgeRubricMode") == "append"

    list_resp = await client.get("/v1/evaluations/profiles", headers=headers)
    assert list_resp.status_code == 200
    assert any(p["id"] == profile["id"] for p in list_resp.json()["items"])

    estimate_resp = await client.post(
        "/v1/evaluations/runs:estimate",
        json={
            "profile_id": profile["id"],
            "subject_count": 5,
            "project_id": bootstrap["service_id"],
        },
        headers=headers,
    )
    assert estimate_resp.status_code == 200
    estimate = estimate_resp.json()
    assert estimate["judgeCalls"] == 5
    assert estimate["costEstimateUsd"] > 0
    assert estimate["costGuard"]["allowed"] is True


@pytest.mark.asyncio
async def test_golden_run_requires_set_id(app_client):
    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    headers = await _auth_headers(bootstrap["token"])

    profile_resp = await client.post(
        "/v1/evaluations/profiles",
        json={
            "project_id": bootstrap["service_id"],
            "name": "g-pro",
            "evaluators": [{"evaluator_id": "rule.response.present"}],
            "judge_models": [],
            "consensus": "single",
        },
        headers=headers,
    )
    profile_id = profile_resp.json()["id"]
    bad = await client.post(
        "/v1/evaluations/runs",
        json={
            "profile_id": profile_id,
            "project_id": bootstrap["service_id"],
            "trace_ids": ["t1"],
            "trigger_lane": "judge_manual",
            "run_mode": "golden_gt",
        },
        headers=headers,
    )
    assert bad.status_code == 400, bad.text


@pytest.mark.asyncio
async def test_run_creation_blocked_when_trace_missing(app_client):
    """The mock trace loader returns ``None`` for unknown ids — runs still
    succeed but every subject is recorded as failed."""

    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    headers = await _auth_headers(bootstrap["token"])

    profile_resp = await client.post(
        "/v1/evaluations/profiles",
        json={
            "project_id": bootstrap["service_id"],
            "name": "rule-only",
            "evaluators": [{"evaluator_id": "rule.response.present"}],
            "judge_models": [],
            "consensus": "single",
        },
        headers=headers,
    )
    profile_id = profile_resp.json()["id"]
    run_resp = await client.post(
        "/v1/evaluations/runs",
        json={
            "profile_id": profile_id,
            "project_id": bootstrap["service_id"],
            "trace_ids": ["unknown-trace"],
            "trigger_lane": "judge_manual",
        },
        headers=headers,
    )
    assert run_resp.status_code == 201, run_resp.text
    run = run_resp.json()
    assert run["subjectCount"] == 1
    assert run["failedCount"] == 1


@pytest.mark.asyncio
async def test_golden_set_create_and_list(app_client):
    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    headers = await _auth_headers(bootstrap["token"])

    set_resp = await client.post(
        "/v1/evaluations/golden-sets",
        json={
            "project_id": bootstrap["service_id"],
            "name": "L3 demo",
            "layer": "L3",
            "description": "demo set",
        },
        headers=headers,
    )
    assert set_resp.status_code == 201, set_resp.text
    set_id = set_resp.json()["id"]

    item_resp = await client.post(
        f"/v1/evaluations/golden-sets/{set_id}/items",
        json={"payload": {"query": "hi", "response": "hello"}},
        headers=headers,
    )
    assert item_resp.status_code == 201, item_resp.text
    assert item_resp.json()["status"] == "active"

    list_items = await client.get(
        f"/v1/evaluations/golden-sets/{set_id}/items", headers=headers
    )
    assert list_items.status_code == 200
    assert len(list_items.json()["items"]) == 1


@pytest.mark.asyncio
async def test_quality_overview_kpis_render(app_client):
    client, app = app_client
    bootstrap = await _bootstrap_directory(app)
    headers = await _auth_headers(bootstrap["token"])
    overview = await client.get("/v1/evaluations/overview", headers=headers)
    assert overview.status_code == 200
    body = overview.json()
    assert "kpi" in body and "cost" in body and "recentRuns" in body
    assert body["kpi"]["profileCount"] == 0


@pytest.mark.asyncio
async def test_module_disabled_returns_404_for_routes(tmp_path):
    """When EASYOBS_EVAL_ENABLED=false the router must not be mounted."""

    os.environ["EASYOBS_DATA_DIR"] = str(tmp_path)
    os.environ["EASYOBS_DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{(tmp_path / 'off.sqlite3').as_posix()}"
    )
    os.environ["EASYOBS_EVAL_ENABLED"] = "false"
    os.environ["EASYOBS_EVAL_AUTO_RULE_ON_INGEST"] = "false"
    os.environ["EASYOBS_LOG_LEVEL"] = "WARNING"

    for mod in list(sys.modules):
        if mod.startswith("easyobs."):
            del sys.modules[mod]
    from easyobs.http_app import create_app
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.get("/v1/evaluations/evaluators")
            assert resp.status_code == 404
