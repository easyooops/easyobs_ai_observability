"""Suggested built-in rule evaluators per improvement pack.

Each pack biases *remediation categories* for improvement proposals; the
same pack id also maps to a **suggested** rule-evaluator set for new or
reset profiles. Operators may still edit the list after applying a preset.

Values are EasyObs-native (not copied from any external product)."""

from __future__ import annotations

from typing import Any

# (evaluator_id, weight, threshold, params)
_PackRow = tuple[str, float, float, dict[str, Any]]

_BASE: list[_PackRow] = [
    ("metric.d6_concise", 1.0, 0.6, {}),
    ("metric.f5_fail", 1.0, 0.7, {}),
    ("metric.f1_e2e_lat", 0.8, 0.6, {}),
    ("metric.f4_tokens", 0.8, 0.6, {}),
]

_PACK_PRESETS: dict[str, list[_PackRow]] = {
    "easyobs_standard": list(_BASE)
    + [
        ("metric.a5_lang", 0.6, 0.55, {}),
        ("metric.f3_cost", 0.8, 0.55, {}),
    ],
    "easyobs_security": [
        ("metric.d10_format", 0.8, 0.55, {}),
        ("metric.f5_fail", 1.0, 0.65, {}),
        ("metric.f3_cost", 0.9, 0.55, {}),
        ("metric.d6_concise", 0.9, 0.55, {}),
    ],
    "easyobs_rag": [
        ("metric.b1_recall", 1.2, 0.6, {}),
        ("metric.b2_precision", 1.0, 0.55, {}),
        ("metric.b3_mrr", 0.9, 0.55, {}),
        ("metric.f1_e2e_lat", 0.7, 0.5, {}),
        ("metric.f5_fail", 1.0, 0.65, {}),
    ],
    "easyobs_efficiency": [
        ("metric.f1_e2e_lat", 1.2, 0.6, {}),
        ("metric.f4_tokens", 1.2, 0.6, {}),
        ("metric.f3_cost", 1.2, 0.55, {}),
        ("metric.e7_path", 1.0, 0.55, {}),
        ("metric.f5_fail", 1.0, 0.65, {}),
    ],
}

# Minimal LLM-as-a-Judge recommendations (cost-aware defaults).
# Trend baseline: "RAG triad" style core (faithfulness + relevance + grounding/safety),
# keep count low and let deterministic rules carry broad coverage.
_PACK_JUDGE_METRICS: dict[str, list[str]] = {
    "easyobs_standard": [
        "metric.d3_faith",
        "metric.d1_relevance",
        "metric.d8_policy",
    ],
    "easyobs_security": [
        "metric.d8_policy",
        "metric.d3_faith",
        "metric.d12_refusal",
    ],
    "easyobs_rag": [
        "metric.d3_faith",
        "metric.d1_relevance",
        "metric.b8_coverage",
        "metric.c1_noise",
    ],
    "easyobs_efficiency": [
        "metric.d1_relevance",
    ],
}


def suggested_rule_evaluators_json(pack_id: str | None) -> list[dict[str, Any]]:
    """Return API-shaped rows for ``ProfileSavePayload.evaluators``."""
    key = (pack_id or "easyobs_standard").strip() or "easyobs_standard"
    rows = _PACK_PRESETS.get(key) or _PACK_PRESETS["easyobs_standard"]
    return [
        {
            "evaluatorId": eid,
            "weight": w,
            "threshold": th,
            "params": dict(params),
        }
        for eid, w, th, params in rows
    ]


def suggested_judge_metrics(pack_id: str | None) -> list[str]:
    key = (pack_id or "easyobs_standard").strip() or "easyobs_standard"
    rows = _PACK_JUDGE_METRICS.get(key) or _PACK_JUDGE_METRICS["easyobs_standard"]
    return [str(x) for x in rows if str(x).strip()]
