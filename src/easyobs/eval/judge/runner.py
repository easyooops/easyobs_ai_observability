"""Multi-judge orchestrator.

Given a set of registered judge models and a profile-supplied rubric, the
runner fans out one provider call per model in parallel, then folds the
responses through the consensus aggregator. Cost guards are enforced
*before* the network round-trips so a misconfigured profile cannot quietly
burn an API budget.

12 §4 — Judge call failure handling:
    Providers raise :class:`JudgeProviderError` to signal an exclusion-
    worthy failure. The runner attempts a small bounded retry, then
    drops the failing model out of the consensus pool. If at least one
    model still has a clean response, consensus runs over the survivors
    (matching the 12 §4 *partial-failure* policy). If **every** model
    fails the runner returns an outcome carrying ``Verdict.ERROR`` so
    the result row can be excluded from pass-rate / avg-score
    aggregations on the persistence side.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from easyobs.eval.judge.consensus import ConsensusOutcome, aggregate_consensus
from easyobs.eval.judge.providers import (
    JudgeModelSpec,
    JudgeProviderError,
    JudgeRequest,
    JudgeResponse,
    get_provider,
)
from easyobs.eval.types import Verdict

_log = logging.getLogger("easyobs.eval.judge")

# Bounded retry — provider error types like ``timeout`` and ``rate_limit``
# are worth a second look; a third attempt rarely changes the outcome and
# would noticeably slow large runs. Mirrors the 12 §4 default.
_DEFAULT_RETRIES = 2
_RETRY_BACKOFF_SEC = 1.0


@dataclass(frozen=True, slots=True)
class JudgeOutcome:
    consensus: ConsensusOutcome
    per_model: list[dict[str, Any]]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    judge_calls: int
    # 12 §4 — populated when at least one model failed. Empty dict when all
    # models returned cleanly. Shape:
    # {"errorType": "<dominant>",
    #  "perModel": [{"modelId": ..., "errorType": ..., "message": ...,
    #                 "retryCount": 1}],
    #  "partialFailure": true|false,
    #  "totalFailure": true|false}
    judge_error_detail: dict[str, Any]


def estimate_judge_cost(
    models: list[JudgeModelSpec],
    avg_prompt_chars: int = 1500,
    avg_response_tokens: int = 200,
) -> float:
    """Rough projection used by the cost guard *before* any HTTP call."""
    total = 0.0
    for m in models:
        in_tokens = max(64, avg_prompt_chars // 4)
        out_tokens = avg_response_tokens
        total += (
            in_tokens / 1000.0 * m.cost_per_1k_input
            + out_tokens / 1000.0 * m.cost_per_1k_output
        )
    return round(total, 6)


async def _call_one(
    model: JudgeModelSpec, request: JudgeRequest
) -> tuple[JudgeModelSpec, JudgeResponse | None, dict[str, Any] | None]:
    """Returns ``(model, response, None)`` on success and
    ``(model, None, error_detail)`` on definitive failure. The runner
    relies on this two-tuple shape so downstream consensus / accounting
    can treat success and exclusion uniformly."""

    provider = get_provider(model.provider) or get_provider("mock")
    assert provider is not None  # mock is always registered
    last_err: JudgeProviderError | None = None
    for attempt in range(_DEFAULT_RETRIES + 1):
        try:
            resp = await provider.evaluate(model, request)
            return model, resp, None
        except JudgeProviderError as exc:
            last_err = exc
            _log.info(
                "judge provider raised exclusion-worthy error",
                extra={
                    "model": model.id,
                    "errorType": exc.error_type,
                    "attempt": attempt + 1,
                },
            )
            # Backoff only between retries, never after the last one.
            if attempt < _DEFAULT_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
        except Exception as exc:
            # Truly unexpected exceptions still get a single retry; if the
            # second attempt also blows up we treat the model as excluded
            # rather than letting the whole run die.
            _log.exception(
                "judge provider raised unexpected exception",
                extra={"model": model.id, "attempt": attempt + 1},
            )
            last_err = JudgeProviderError(
                "unknown",
                model_id=model.id,
                detail=f"{exc.__class__.__name__}: {exc}"[:200],
            )
            if attempt < _DEFAULT_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
    detail = {
        "modelId": model.id,
        "errorType": (last_err.error_type if last_err else "unknown"),
        "message": (last_err.detail if last_err else ""),
        "retryCount": _DEFAULT_RETRIES,
    }
    return model, None, detail


async def run_judges(
    *,
    models: list[JudgeModelSpec],
    request: JudgeRequest,
    consensus_policy: str,
) -> JudgeOutcome:
    if not models:
        return JudgeOutcome(
            consensus=ConsensusOutcome(
                score=0.0,
                verdict=Verdict.UNSET,
                disagreement=0.0,
                agreement_ratio=0.0,
                reason="no judge models",
            ),
            per_model=[],
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
            judge_calls=0,
            judge_error_detail={},
        )

    triples = await asyncio.gather(*(_call_one(m, request) for m in models))

    survivors: list[tuple[JudgeModelSpec, JudgeResponse]] = []
    failures: list[dict[str, Any]] = []
    per_model: list[dict[str, Any]] = []
    in_tokens = 0
    out_tokens = 0
    cost = 0.0
    for model, resp, err in triples:
        if resp is None:
            failures.append(err or {"modelId": model.id, "errorType": "unknown"})
            per_model.append(
                {
                    "modelId": model.id,
                    "modelName": model.name,
                    "provider": model.provider,
                    "score": None,
                    "verdict": Verdict.ERROR.value,
                    "reason": (err or {}).get("message") or "judge call failed",
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "costUsd": 0.0,
                    "errorType": (err or {}).get("errorType") or "unknown",
                }
            )
            continue
        survivors.append((model, resp))
        in_tokens += resp.input_tokens
        out_tokens += resp.output_tokens
        cost += resp.cost_usd
        per_model.append(
            {
                "modelId": model.id,
                "modelName": model.name,
                "provider": model.provider,
                "score": resp.score,
                "verdict": resp.verdict,
                "reason": resp.reason,
                "inputTokens": resp.input_tokens,
                "outputTokens": resp.output_tokens,
                "costUsd": resp.cost_usd,
            }
        )

    total_failure = bool(models) and not survivors
    if total_failure:
        # All models failed → mark the row so it is excluded from
        # aggregations (12 §4). The consensus carries Verdict.ERROR and
        # a reason summary so the UI can render it without re-parsing
        # the per-model breakdown.
        dominant = _dominant_error_type(failures)
        return JudgeOutcome(
            consensus=ConsensusOutcome(
                score=0.0,
                verdict=Verdict.ERROR,
                disagreement=0.0,
                agreement_ratio=0.0,
                reason=f"all {len(models)} judges failed ({dominant})",
            ),
            per_model=per_model,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
            judge_calls=len(models),
            judge_error_detail={
                "errorType": dominant,
                "totalFailure": True,
                "partialFailure": False,
                "perModel": failures,
            },
        )

    consensus = aggregate_consensus(survivors, consensus_policy)
    judge_error_detail: dict[str, Any] = {}
    if failures:
        judge_error_detail = {
            "errorType": _dominant_error_type(failures),
            "totalFailure": False,
            "partialFailure": True,
            "perModel": failures,
        }
    return JudgeOutcome(
        consensus=consensus,
        per_model=per_model,
        total_input_tokens=in_tokens,
        total_output_tokens=out_tokens,
        total_cost_usd=round(cost, 6),
        judge_calls=len(survivors),  # only count successful provider calls
        judge_error_detail=judge_error_detail,
    )


def _dominant_error_type(failures: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for f in failures:
        et = str(f.get("errorType") or "unknown")
        counts[et] = counts.get(et, 0) + 1
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]
