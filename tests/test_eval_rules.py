"""Built-in rule evaluators and the safe DSL.

These tests don't touch the database — they exercise the deterministic
scoring helpers directly so a regression on a single rule is easy to
isolate.
"""

from __future__ import annotations

import pytest

from easyobs.eval.rules import RuleContext, evaluate_dsl
from easyobs.eval.rules.builtin import BUILTIN_EVALUATORS, run_evaluator
from easyobs.eval.rules.dsl import DSLError, coerce_score
from easyobs.eval.types import Verdict


def _ctx(*, summary=None, trace=None, extra=None):
    return RuleContext(
        trace=trace or {},
        summary=summary or {},
        spans=[],
        extra=extra or {},
    )


def test_catalog_has_unique_ids_and_layers():
    seen = set()
    for evaluator in BUILTIN_EVALUATORS:
        assert evaluator.id not in seen, f"duplicate id {evaluator.id}"
        seen.add(evaluator.id)
        assert evaluator.layer in {"L1", "L2", "L3"}
        assert evaluator.runner is not None


def test_response_present_pass_and_fail():
    pass_outcome = run_evaluator(
        "rule.response.present", _ctx(summary={"response": "hello"})
    )
    fail_outcome = run_evaluator(
        "rule.response.present", _ctx(summary={"response": ""})
    )
    assert pass_outcome.verdict is Verdict.PASS
    assert fail_outcome.verdict is Verdict.FAIL


def test_response_length_warns_for_short():
    outcome = run_evaluator(
        "rule.response.length",
        _ctx(summary={"response": "short"}),
        params={"min_chars": 20},
    )
    assert outcome.verdict is Verdict.WARN
    assert outcome.score < 1.0


def test_response_length_passes_for_normal_range():
    outcome = run_evaluator(
        "rule.response.length",
        _ctx(summary={"response": "x" * 100}),
        params={"min_chars": 20, "max_chars": 4000},
    )
    assert outcome.verdict is Verdict.PASS


def test_pii_detection_flags_email_and_rrn():
    text = "연락처: john.doe@example.com / 주민번호 900101-1234567"
    outcome = run_evaluator(
        "rule.safety.no_pii", _ctx(summary={"response": text})
    )
    assert outcome.verdict is Verdict.FAIL
    assert outcome.details["email"] == 1
    assert outcome.details["kr_rrn"] == 1


def test_secret_detection_flags_openai_keys():
    outcome = run_evaluator(
        "rule.safety.no_secret",
        _ctx(summary={"response": "key=sk-ABCDEFGHIJKLMNOPQRSTUV12345"}),
    )
    assert outcome.verdict is Verdict.FAIL
    assert "openai" in outcome.details["types"]


def test_retrieval_recall_unset_without_golden():
    outcome = run_evaluator(
        "rule.retrieval.recall_at_k",
        _ctx(summary={"docsRaw": []}),
    )
    assert outcome.verdict is Verdict.UNSET


def test_retrieval_recall_with_golden_match():
    docs = [{"id": "doc-1"}, {"id": "doc-2"}, {"id": "doc-3"}]
    outcome = run_evaluator(
        "rule.retrieval.recall_at_k",
        _ctx(
            summary={"docsRaw": docs},
            extra={"golden": {"expected_doc_ids": ["doc-1", "doc-2"]}},
        ),
        params={"k": 5, "threshold": 0.5},
    )
    assert outcome.verdict is Verdict.PASS
    assert outcome.score == pytest.approx(1.0)


def test_retrieval_mrr_first_hit_at_three():
    docs = [{"id": "x"}, {"id": "y"}, {"id": "doc-1"}]
    outcome = run_evaluator(
        "rule.retrieval.mrr",
        _ctx(
            summary={"docsRaw": docs},
            extra={"golden": {"expected_doc_ids": ["doc-1"]}},
        ),
    )
    assert outcome.score == pytest.approx(1 / 3, abs=1e-3)


def test_latency_within_budget():
    outcome = run_evaluator(
        "rule.perf.latency",
        _ctx(
            trace={
                "startedAt": "2026-04-25T00:00:00+00:00",
                "endedAt": "2026-04-25T00:00:01+00:00",
            },
        ),
        params={"budget_ms": 5000},
    )
    assert outcome.verdict is Verdict.PASS


def test_dsl_pass_for_simple_expression():
    outcome = run_evaluator(
        "rule.custom.dsl",
        _ctx(summary={"tokensTotal": 200}),
        params={"expression": "summary['tokensTotal'] < 1000", "threshold": 0.5},
    )
    assert outcome.verdict is Verdict.PASS


def test_dsl_rejects_unsafe_calls():
    with pytest.raises(DSLError):
        evaluate_dsl("__import__('os').system('echo')", RuleContext())


def test_dsl_helper_regex_match():
    ctx = RuleContext(summary={"response": "ORDER-12345"})
    result = evaluate_dsl("regex_match('ORDER-\\\\d+', summary['response'])", ctx)
    assert result is True


def test_dsl_coerce_score_handles_percent():
    assert coerce_score(57) == pytest.approx(0.57)
    assert coerce_score(True) == 1.0
    assert coerce_score([True, False, True]) == pytest.approx(2 / 3)
