"""End-to-end service-layer tests for the Quality module.

Each test pulls the full ``eval_services`` fixture so we exercise the same
session factory, the same SQLAlchemy schema and the same wiring that the
HTTP layer uses. The trace loader is the deterministic fixture defined
in ``conftest._default_trace_loader``.
"""

from __future__ import annotations

import pytest

from easyobs.eval.services.cost import CostService
from easyobs.eval.services.dtos import CostGuardConfig
from easyobs.eval.services.improvements import derive_proposals
from easyobs.eval.services.profiles import ProfileService
from easyobs.eval.services.runs import RunService
from easyobs.eval.types import ConsensusPolicy, RunStatus, TriggerLane

ORG_A = "org-aaa"
ORG_B = "org-bbb"
PROJECT_A1 = "svc-a1"
PROJECT_A2 = "svc-a2"


@pytest.mark.asyncio
async def test_profile_create_get_list_delete(eval_services):
    profiles: ProfileService = eval_services["profiles"]
    created = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="p1",
        description="",
        evaluators=[{"evaluator_id": "rule.response.present", "weight": 1.0}],
        judge_models=[],
        consensus=ConsensusPolicy.SINGLE.value,
        auto_run=True,
        cost_guard=None,
        enabled=True,
        actor="u1",
    )
    assert created.id
    assert created.auto_run is True
    listed = await profiles.list(org_id=ORG_A, project_ids=[PROJECT_A1])
    assert any(p.id == created.id for p in listed)
    fetched = await profiles.get(
        org_id=ORG_A, profile_id=created.id, project_ids=[PROJECT_A1]
    )
    assert fetched is not None and fetched.name == "p1"
    deleted = await profiles.delete(org_id=ORG_A, profile_id=created.id)
    assert deleted is True
    assert await profiles.get(
        org_id=ORG_A, profile_id=created.id, project_ids=[PROJECT_A1]
    ) is None


@pytest.mark.asyncio
async def test_profile_isolated_per_org(eval_services):
    profiles: ProfileService = eval_services["profiles"]
    a = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=None,
        name="org-a-profile",
        description="",
        evaluators=[],
        judge_models=[],
        consensus="single",
        auto_run=False,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    listed_b = await profiles.list(org_id=ORG_B, project_ids=None)
    assert all(p.id != a.id for p in listed_b)
    cross = await profiles.get(org_id=ORG_B, profile_id=a.id, project_ids=None)
    assert cross is None


@pytest.mark.asyncio
async def test_profile_project_filter_drops_foreign_projects(eval_services):
    profiles: ProfileService = eval_services["profiles"]
    locked = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="locked",
        description="",
        evaluators=[],
        judge_models=[],
        consensus="single",
        auto_run=False,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    visible = await profiles.list(org_id=ORG_A, project_ids=[PROJECT_A2])
    assert all(p.id != locked.id for p in visible)


@pytest.mark.asyncio
async def test_profile_list_auto_run_includes_org_wide_and_matching_project(eval_services):
    profiles: ProfileService = eval_services["profiles"]
    org_wide = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=None,
        name="org-wide",
        description="",
        evaluators=[{"evaluator_id": "rule.response.present"}],
        judge_models=[],
        consensus="single",
        auto_run=True,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    project_one = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="proj1",
        description="",
        evaluators=[{"evaluator_id": "rule.status.ok"}],
        judge_models=[],
        consensus="single",
        auto_run=True,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    project_two = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A2,
        name="proj2",
        description="",
        evaluators=[{"evaluator_id": "rule.status.ok"}],
        judge_models=[],
        consensus="single",
        auto_run=True,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    found = await profiles.list_auto_run(org_id=ORG_A, project_id=PROJECT_A1)
    found_ids = {p.id for p in found}
    assert org_wide.id in found_ids
    assert project_one.id in found_ids
    assert project_two.id not in found_ids


@pytest.mark.asyncio
async def test_judge_model_crud_and_resolve_specs(eval_services):
    judges = eval_services["judges"]
    row = await judges.create(
        org_id=ORG_A,
        name="gpt-mini-judge",
        provider="mock",
        model="mock-x",
        temperature=0.0,
        weight=1.0,
        cost_per_1k_input=0.5,
        cost_per_1k_output=1.5,
        enabled=True,
        actor="u1",
    )
    listed = await judges.list(org_id=ORG_A)
    assert any(m.id == row.id for m in listed)
    cross = await judges.get(org_id=ORG_B, model_id=row.id)
    assert cross is None
    specs = await judges.resolve_specs(
        org_id=ORG_A, refs=[(row.id, 2.0)]
    )
    assert len(specs) == 1
    assert specs[0].weight == 2.0
    deleted = await judges.delete(org_id=ORG_A, model_id=row.id)
    assert deleted is True


@pytest.mark.asyncio
async def test_goldenset_manual_and_auto_discover_filter_by_org(eval_services):
    goldens = eval_services["goldens"]
    set_a = await goldens.create_set(
        org_id=ORG_A,
        project_id=PROJECT_A1,
        name="set-a",
        layer="L3",
        description="",
        actor=None,
    )
    item = await goldens.add_manual_item(
        org_id=ORG_A,
        set_id=set_a.id,
        payload={"query": "안녕", "response": "안녕하세요"},
        project_ids=[PROJECT_A1],
        actor=None,
    )
    assert item.status == "active"
    cross = await goldens.list_items(
        org_id=ORG_B, set_id=set_a.id, project_ids=None
    )
    assert cross == []


@pytest.mark.asyncio
async def test_goldenset_project_scope_blocks_foreign_project(eval_services):
    goldens = eval_services["goldens"]
    set_a = await goldens.create_set(
        org_id=ORG_A,
        project_id=PROJECT_A1,
        name="set-a",
        layer="L3",
        description="",
        actor=None,
    )
    with pytest.raises(PermissionError):
        await goldens.add_manual_item(
            org_id=ORG_A,
            set_id=set_a.id,
            payload={"query": "x", "response": "y"},
            project_ids=[PROJECT_A2],
            actor=None,
        )


@pytest.mark.asyncio
async def test_run_service_executes_rule_only_profile(eval_services):
    profiles: ProfileService = eval_services["profiles"]
    runs: RunService = eval_services["runs"]
    profile = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="rule-only",
        description="",
        evaluators=[
            {"evaluator_id": "rule.response.present", "weight": 1.0},
            {"evaluator_id": "rule.response.length", "weight": 1.0},
            {"evaluator_id": "rule.status.ok", "weight": 1.0},
        ],
        judge_models=[],
        consensus="single",
        auto_run=False,
        cost_guard=None,
        enabled=True,
        actor=None,
    )
    run = await runs.execute(
        org_id=ORG_A,
        profile_id=profile.id,
        profile=None,
        project_id=PROJECT_A1,
        trace_ids=["trace-1", "trace-2"],
        trigger_lane=TriggerLane.RULE_REPLAY.value,
        triggered_by="u1",
        project_scope=[PROJECT_A1],
    )
    assert run.status == RunStatus.COMPLETED.value
    assert run.subject_count == 2
    assert run.completed_count == 2
    assert run.cost_actual_usd == 0.0
    results = await runs.list_results(
        org_id=ORG_A, run_id=run.id, project_ids=[PROJECT_A1]
    )
    assert len(results) == 2
    assert all(r.judge_score is None for r in results)


@pytest.mark.asyncio
async def test_run_with_judges_records_cost_and_uses_consensus(eval_services):
    judges = eval_services["judges"]
    profiles: ProfileService = eval_services["profiles"]
    runs: RunService = eval_services["runs"]
    cost: CostService = eval_services["cost"]

    judge_a = await judges.create(
        org_id=ORG_A, name="alpha", provider="mock", model="mock-a",
        temperature=0.0, weight=1.0, cost_per_1k_input=0.5, cost_per_1k_output=1.5,
        enabled=True, actor=None,
    )
    judge_b = await judges.create(
        org_id=ORG_A, name="beta", provider="mock", model="mock-b",
        temperature=0.0, weight=1.0, cost_per_1k_input=0.5, cost_per_1k_output=1.5,
        enabled=True, actor=None,
    )
    profile = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="judge",
        description="",
        evaluators=[{"evaluator_id": "rule.response.present", "weight": 1.0}],
        judge_models=[
            {"model_id": judge_a.id, "weight": 1.0},
            {"model_id": judge_b.id, "weight": 1.0},
        ],
        consensus=ConsensusPolicy.MAJORITY.value,
        auto_run=False,
        cost_guard={
            "max_cost_usd_per_run": 5.0,
            "max_cost_usd_per_subject": 2.0,
            "monthly_budget_usd": 100.0,
            "on_exceed": "block",
        },
        enabled=True,
        actor=None,
    )
    run = await runs.execute(
        org_id=ORG_A,
        profile_id=profile.id,
        profile=None,
        project_id=PROJECT_A1,
        trace_ids=["trace-1"],
        trigger_lane=TriggerLane.JUDGE_MANUAL.value,
        triggered_by="u1",
        project_scope=[PROJECT_A1],
    )
    assert run.cost_actual_usd > 0.0
    monthly = await cost.monthly_spend(
        org_id=ORG_A, project_ids=[PROJECT_A1], profile_id=profile.id
    )
    assert monthly == pytest.approx(run.cost_actual_usd, abs=1e-6)
    results = await runs.list_results(
        org_id=ORG_A, run_id=run.id, project_ids=[PROJECT_A1]
    )
    assert results
    assert results[0].judge_score is not None
    assert any(f.kind == "judge" for f in results[0].findings)


@pytest.mark.asyncio
async def test_run_blocks_when_cost_guard_exceeds(eval_services):
    judges = eval_services["judges"]
    profiles: ProfileService = eval_services["profiles"]
    runs: RunService = eval_services["runs"]
    expensive = await judges.create(
        org_id=ORG_A, name="expensive", provider="mock", model="m",
        temperature=0.0, weight=1.0,
        cost_per_1k_input=10000.0, cost_per_1k_output=10000.0,
        enabled=True, actor=None,
    )
    profile = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="costly",
        description="",
        evaluators=[],
        judge_models=[{"model_id": expensive.id, "weight": 1.0}],
        consensus="single",
        auto_run=False,
        cost_guard={
            "max_cost_usd_per_run": 0.001,
            "max_cost_usd_per_subject": 0.001,
            "monthly_budget_usd": 0.01,
            "on_exceed": "block",
        },
        enabled=True,
        actor=None,
    )
    with pytest.raises(PermissionError):
        await runs.execute(
            org_id=ORG_A,
            profile_id=profile.id,
            profile=None,
            project_id=PROJECT_A1,
            trace_ids=["trace-1"],
            trigger_lane=TriggerLane.JUDGE_MANUAL.value,
            triggered_by="u1",
            project_scope=[PROJECT_A1],
        )


@pytest.mark.asyncio
async def test_run_downgrades_when_guard_says_so(eval_services):
    judges = eval_services["judges"]
    profiles: ProfileService = eval_services["profiles"]
    runs: RunService = eval_services["runs"]
    expensive = await judges.create(
        org_id=ORG_A, name="expensive", provider="mock", model="m",
        temperature=0.0, weight=1.0,
        cost_per_1k_input=10000.0, cost_per_1k_output=10000.0,
        enabled=True, actor=None,
    )
    profile = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="costly-downgrade",
        description="",
        evaluators=[{"evaluator_id": "rule.response.present"}],
        judge_models=[{"model_id": expensive.id, "weight": 1.0}],
        consensus="single",
        auto_run=False,
        cost_guard={
            "max_cost_usd_per_run": 0.001,
            "max_cost_usd_per_subject": 0.001,
            "monthly_budget_usd": 0.01,
            "on_exceed": "downgrade",
        },
        enabled=True,
        actor=None,
    )
    run = await runs.execute(
        org_id=ORG_A,
        profile_id=profile.id,
        profile=None,
        project_id=PROJECT_A1,
        trace_ids=["trace-1"],
        trigger_lane=TriggerLane.JUDGE_MANUAL.value,
        triggered_by="u1",
        project_scope=[PROJECT_A1],
    )
    assert run.cost_actual_usd == 0.0
    assert "downgrade" in run.notes


@pytest.mark.asyncio
async def test_estimate_returns_per_subject_projection(eval_services):
    judges = eval_services["judges"]
    profiles: ProfileService = eval_services["profiles"]
    runs: RunService = eval_services["runs"]
    judge = await judges.create(
        org_id=ORG_A, name="alpha", provider="mock", model="mock-a",
        temperature=0.0, weight=1.0, cost_per_1k_input=1.0, cost_per_1k_output=2.0,
        enabled=True, actor=None,
    )
    profile = await profiles.upsert(
        org_id=ORG_A,
        profile_id=None,
        project_id=PROJECT_A1,
        name="x",
        description="",
        evaluators=[],
        judge_models=[{"model_id": judge.id, "weight": 1.0}],
        consensus="single",
        auto_run=False,
        cost_guard={
            "max_cost_usd_per_run": 100.0,
            "max_cost_usd_per_subject": 1.0,
            "monthly_budget_usd": 1000.0,
            "on_exceed": "block",
        },
        enabled=True,
        actor=None,
    )
    est = await runs.estimate(
        org_id=ORG_A, profile_id=profile.id, subject_count=10,
        project_ids=[PROJECT_A1],
    )
    assert est.judge_calls == 10
    assert est.cost_estimate_usd > 0
    assert est.cost_guard_allowed is True


@pytest.mark.asyncio
async def test_estimate_raises_for_unknown_profile(eval_services):
    runs: RunService = eval_services["runs"]
    with pytest.raises(LookupError):
        await runs.estimate(
            org_id=ORG_A, profile_id="missing", subject_count=1,
            project_ids=None,
        )


def test_derive_proposals_dedups_and_orders_by_severity():
    findings = [
        {"evaluator_id": "rule.safety.no_pii", "verdict": "fail", "score": 0.0,
         "reason": "email leaked"},
        {"evaluator_id": "rule.safety.no_pii", "verdict": "fail", "score": 0.1,
         "reason": "email leaked"},
        {"evaluator_id": "rule.response.length", "verdict": "warn", "score": 0.5,
         "reason": "too short"},
    ]
    proposals = derive_proposals(findings=findings)
    assert len(proposals) == 2
    assert proposals[0]["severity"] == "high"
    assert proposals[1]["severity"] == "medium"


def test_derive_proposals_pack_filters_categories():
    findings = [
        {
            "evaluator_id": "rule.safety.no_pii",
            "verdict": "fail",
            "score": 0.0,
            "reason": "leak",
        },
        {
            "evaluator_id": "rule.retrieval.recall_at_k",
            "verdict": "warn",
            "score": 0.5,
            "reason": "low recall",
        },
    ]
    full = derive_proposals(findings=findings, pack_id="easyobs_standard")
    assert len(full) == 2
    rag = derive_proposals(findings=findings, pack_id="easyobs_rag")
    assert len(rag) == 1
    assert rag[0]["category"] == "retrieval_quality"


@pytest.mark.asyncio
async def test_cost_overview_filters_by_project_scope(eval_services):
    cost: CostService = eval_services["cost"]
    await cost.record(
        org_id=ORG_A, project_id=PROJECT_A1, profile_id=None,
        judge_calls=2, judge_input_tokens=100, judge_output_tokens=50,
        judge_cost_usd=0.5, rule_evals=10,
    )
    await cost.record(
        org_id=ORG_A, project_id=PROJECT_A2, profile_id=None,
        judge_calls=1, judge_input_tokens=50, judge_output_tokens=25,
        judge_cost_usd=0.25, rule_evals=5,
    )
    overview_full = await cost.overview(org_id=ORG_A, project_ids=None)
    assert overview_full["monthCostUsd"] == pytest.approx(0.75, abs=1e-6)
    overview_scoped = await cost.overview(org_id=ORG_A, project_ids=[PROJECT_A1])
    assert overview_scoped["monthCostUsd"] == pytest.approx(0.5, abs=1e-6)


@pytest.mark.asyncio
async def test_schedule_crud(eval_services):
    schedules = eval_services["schedules"]
    profiles = eval_services["profiles"]
    profile = await profiles.upsert(
        org_id=ORG_A, profile_id=None, project_id=PROJECT_A1,
        name="sch", description="",
        evaluators=[], judge_models=[], consensus="single",
        auto_run=False, cost_guard=None, enabled=True, actor=None,
    )
    row = await schedules.create(
        org_id=ORG_A, project_id=PROJECT_A1, profile_id=profile.id,
        name="daily", interval_hours=24, sample_size=20, enabled=True, actor=None,
    )
    assert row.id
    listed = await schedules.list(org_id=ORG_A, project_ids=[PROJECT_A1])
    assert any(s.id == row.id for s in listed)
    updated = await schedules.update(org_id=ORG_A, schedule_id=row.id, sample_size=50)
    assert updated.sample_size == 50
    deleted = await schedules.delete(org_id=ORG_A, schedule_id=row.id)
    assert deleted is True


@pytest.mark.asyncio
async def test_evaluator_catalog_lists_built_ins(eval_services):
    catalog = eval_services["catalog"]
    items = catalog.list()
    assert len(items) >= 16
    ids = {item["id"] for item in items}
    assert "rule.response.present" in ids
    assert "rule.custom.dsl" in ids
    one = catalog.get("rule.safety.no_pii")
    assert one is not None
