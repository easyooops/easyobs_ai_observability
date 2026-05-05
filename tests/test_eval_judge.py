"""Multi-judge consensus + cost-guard logic.

The mock provider is fully deterministic so we can pin scores for the
weighted/unanimous policies without flaking on real network calls.
"""

from __future__ import annotations

import pytest

from easyobs.eval.judge.consensus import aggregate_consensus
from easyobs.eval.judge.providers import (
    JudgeModelSpec,
    JudgeRequest,
    JudgeResponse,
    MockJudgeProvider,
)
from easyobs.eval.judge.runner import estimate_judge_cost, run_judges
from easyobs.eval.services.cost import CostGuard
from easyobs.eval.services.dtos import CostGuardConfig
from easyobs.eval.types import ConsensusPolicy, CostExceedAction, Verdict


def _spec(model_id: str, weight: float = 1.0, in_price: float = 0.5, out_price: float = 1.5):
    return JudgeModelSpec(
        id=model_id,
        provider="mock",
        model=model_id,
        name=model_id,
        weight=weight,
        cost_per_1k_input=in_price,
        cost_per_1k_output=out_price,
    )


def _resp(score: float, verdict: str = "warn"):
    return JudgeResponse(
        score=score,
        verdict=verdict,
        reason=f"score={score}",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
    )


def test_consensus_single_returns_first():
    pairs = [(_spec("a"), _resp(0.9, "pass"))]
    outcome = aggregate_consensus(pairs, ConsensusPolicy.SINGLE.value)
    assert outcome.verdict is Verdict.PASS
    assert outcome.score == pytest.approx(0.9)
    assert outcome.agreement_ratio == 1.0


def test_consensus_majority_picks_dominant_verdict():
    pairs = [
        (_spec("a"), _resp(0.95, "pass")),
        (_spec("b"), _resp(0.85, "pass")),
        (_spec("c"), _resp(0.3, "fail")),
    ]
    outcome = aggregate_consensus(pairs, ConsensusPolicy.MAJORITY.value)
    assert outcome.verdict is Verdict.PASS
    assert outcome.agreement_ratio == pytest.approx(2 / 3, abs=1e-3)


def test_consensus_majority_downgrades_on_tie():
    pairs = [
        (_spec("a"), _resp(0.9, "pass")),
        (_spec("b"), _resp(0.2, "fail")),
    ]
    outcome = aggregate_consensus(pairs, ConsensusPolicy.MAJORITY.value)
    assert outcome.verdict is Verdict.WARN


def test_consensus_unanimous_requires_all_agree():
    pairs = [
        (_spec("a"), _resp(0.9, "pass")),
        (_spec("b"), _resp(0.2, "fail")),
    ]
    outcome = aggregate_consensus(pairs, ConsensusPolicy.UNANIMOUS.value)
    assert outcome.verdict is Verdict.WARN
    pairs_all_pass = [
        (_spec("a"), _resp(0.9, "pass")),
        (_spec("b"), _resp(0.85, "pass")),
    ]
    outcome2 = aggregate_consensus(pairs_all_pass, ConsensusPolicy.UNANIMOUS.value)
    assert outcome2.verdict is Verdict.PASS


def test_consensus_weighted_uses_weights():
    pairs = [
        (_spec("a", weight=1.0), _resp(0.9, "pass")),
        (_spec("b", weight=3.0), _resp(0.2, "fail")),
    ]
    outcome = aggregate_consensus(pairs, ConsensusPolicy.WEIGHTED.value)
    expected = (0.9 * 1.0 + 0.2 * 3.0) / 4.0
    assert outcome.score == pytest.approx(expected, abs=1e-3)
    assert outcome.verdict in {Verdict.WARN, Verdict.FAIL}


@pytest.mark.asyncio
async def test_run_judges_uses_mock_provider_and_aggregates_cost():
    request = JudgeRequest(rubric_id="r1", prompt="evaluate", context={"response": "ok"})
    models = [_spec("a", in_price=1.0, out_price=2.0), _spec("b", in_price=0.5, out_price=1.5)]
    outcome = await run_judges(
        models=models, request=request, consensus_policy=ConsensusPolicy.MAJORITY.value
    )
    assert outcome.judge_calls == 2
    assert outcome.total_cost_usd > 0.0
    assert len(outcome.per_model) == 2


def test_estimate_judge_cost_is_positive():
    models = [_spec("a", in_price=1.0, out_price=2.0)]
    est = estimate_judge_cost(models)
    assert est > 0.0


def _guard_cfg(**kwargs):
    base = dict(
        max_cost_usd_per_run=1.0,
        max_cost_usd_per_subject=0.05,
        monthly_budget_usd=10.0,
        on_exceed=CostExceedAction.BLOCK.value,
    )
    base.update(kwargs)
    return CostGuardConfig(**base)


def test_cost_guard_allows_when_under_budget():
    decision = CostGuard.decide(
        _guard_cfg(),
        projected_usd=0.1,
        subject_count=4,
        monthly_spent_usd=1.0,
    )
    assert decision.allowed is True
    assert decision.downgrade_judges is False


def test_cost_guard_blocks_when_run_exceeds_budget():
    decision = CostGuard.decide(
        _guard_cfg(),
        projected_usd=5.0,
        subject_count=10,
        monthly_spent_usd=0.0,
    )
    assert decision.allowed is False
    assert decision.over_budget is True


def test_cost_guard_downgrades_when_configured():
    decision = CostGuard.decide(
        _guard_cfg(on_exceed=CostExceedAction.DOWNGRADE.value),
        projected_usd=5.0,
        subject_count=10,
        monthly_spent_usd=0.0,
    )
    assert decision.allowed is True
    assert decision.downgrade_judges is True


def test_cost_guard_notify_lets_run_through_with_warning():
    decision = CostGuard.decide(
        _guard_cfg(on_exceed=CostExceedAction.NOTIFY.value),
        projected_usd=5.0,
        subject_count=10,
        monthly_spent_usd=0.0,
    )
    assert decision.allowed is True
    assert decision.downgrade_judges is False
    assert "warning" in decision.note


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic():
    provider = MockJudgeProvider()
    spec = _spec("model-x")
    request = JudgeRequest(rubric_id="r1", prompt="hello", context={"response": "world"})
    a = await provider.evaluate(spec, request)
    b = await provider.evaluate(spec, request)
    assert a.score == b.score
    assert 0.0 <= a.score <= 1.0
