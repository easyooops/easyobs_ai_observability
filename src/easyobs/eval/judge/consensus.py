"""Aggregate per-model judge responses into a single verdict.

Strategies:

- ``single``     — exactly one model registered, take its score verbatim.
- ``majority``   — verdict wins if > 50% agree; otherwise downgrade to
  ``warn``. Score is the mean across models.
- ``unanimous``  — verdict wins only when every model agrees, else
  ``warn``. Score is the **minimum** across models so the strict policy
  is reflected in the number too.
- ``weighted``   — weighted average of scores; verdict from the weighted
  threshold (≥0.7 pass, ≥0.4 warn, else fail). Weights default to 1.0.

The function also computes ``disagreement`` as the population standard
deviation of scores so the UI can plot how often judges disagree per run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from easyobs.eval.judge.providers import JudgeModelSpec, JudgeResponse
from easyobs.eval.types import ConsensusPolicy, Verdict


@dataclass(frozen=True, slots=True)
class ConsensusOutcome:
    score: float
    verdict: Verdict
    disagreement: float
    agreement_ratio: float
    reason: str


def _verdict_from_score(score: float) -> Verdict:
    if score >= 0.7:
        return Verdict.PASS
    if score >= 0.4:
        return Verdict.WARN
    return Verdict.FAIL


def _stddev(values: Iterable[float]) -> float:
    arr = list(values)
    if len(arr) < 2:
        return 0.0
    mean = sum(arr) / len(arr)
    var = sum((v - mean) ** 2 for v in arr) / len(arr)
    return math.sqrt(var)


def aggregate_consensus(
    pairs: list[tuple[JudgeModelSpec, JudgeResponse]],
    policy: str | ConsensusPolicy,
) -> ConsensusOutcome:
    if not pairs:
        return ConsensusOutcome(
            score=0.0,
            verdict=Verdict.UNSET,
            disagreement=0.0,
            agreement_ratio=0.0,
            reason="no judge responses",
        )

    policy_str = str(policy)
    scores = [resp.score for _, resp in pairs]
    verdicts = [_normalise_verdict(resp.verdict) for _, resp in pairs]
    weights = [max(0.0, model.weight) for model, _ in pairs]
    if all(w == 0.0 for w in weights):
        weights = [1.0 for _ in pairs]
    disagreement = round(_stddev(scores), 4)

    counts: dict[Verdict, int] = {}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    top_verdict, top_count = max(counts.items(), key=lambda kv: kv[1])
    agreement_ratio = round(top_count / len(verdicts), 4)

    if policy_str == ConsensusPolicy.SINGLE.value or len(pairs) == 1:
        score = scores[0]
        return ConsensusOutcome(
            score=round(score, 4),
            verdict=verdicts[0],
            disagreement=disagreement,
            agreement_ratio=1.0,
            reason=pairs[0][1].reason,
        )

    if policy_str == ConsensusPolicy.UNANIMOUS.value:
        if len(set(verdicts)) == 1:
            return ConsensusOutcome(
                score=round(min(scores), 4),
                verdict=verdicts[0],
                disagreement=disagreement,
                agreement_ratio=1.0,
                reason=f"unanimous {verdicts[0].value}",
            )
        return ConsensusOutcome(
            score=round(min(scores), 4),
            verdict=Verdict.WARN,
            disagreement=disagreement,
            agreement_ratio=agreement_ratio,
            reason=f"judges disagree ({_verdict_breakdown(counts)})",
        )

    if policy_str == ConsensusPolicy.WEIGHTED.value:
        total_weight = sum(weights)
        weighted_score = sum(s * w for s, w in zip(scores, weights)) / total_weight
        verdict = _verdict_from_score(weighted_score)
        return ConsensusOutcome(
            score=round(weighted_score, 4),
            verdict=verdict,
            disagreement=disagreement,
            agreement_ratio=agreement_ratio,
            reason=f"weighted score {weighted_score:.2f} ({_verdict_breakdown(counts)})",
        )

    if policy_str == ConsensusPolicy.MAJORITY.value:
        if top_count / len(verdicts) > 0.5:
            score = sum(scores) / len(scores)
            return ConsensusOutcome(
                score=round(score, 4),
                verdict=top_verdict,
                disagreement=disagreement,
                agreement_ratio=agreement_ratio,
                reason=f"majority {top_verdict.value} ({top_count}/{len(verdicts)})",
            )
        score = sum(scores) / len(scores)
        return ConsensusOutcome(
            score=round(score, 4),
            verdict=Verdict.WARN,
            disagreement=disagreement,
            agreement_ratio=agreement_ratio,
            reason=f"no majority ({_verdict_breakdown(counts)})",
        )

    score = sum(scores) / len(scores)
    return ConsensusOutcome(
        score=round(score, 4),
        verdict=_verdict_from_score(score),
        disagreement=disagreement,
        agreement_ratio=agreement_ratio,
        reason=f"unknown policy {policy_str!r}, fell back to mean",
    )


def _normalise_verdict(value: str) -> Verdict:
    v = (value or "").lower().strip()
    if v in {Verdict.PASS.value, Verdict.WARN.value, Verdict.FAIL.value, Verdict.UNSET.value}:
        return Verdict(v)
    if v in {"good", "ok"}:
        return Verdict.PASS
    if v in {"bad"}:
        return Verdict.FAIL
    # ``error`` is a real verdict (12 §4) — keep it as-is so the runner can
    # detect a fully-failed multi-judge call instead of misclassifying it
    # as a soft FAIL.
    if v == Verdict.ERROR.value:
        return Verdict.ERROR
    return Verdict.WARN


def _verdict_breakdown(counts: dict[Verdict, int]) -> str:
    return ", ".join(f"{k.value}={v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
