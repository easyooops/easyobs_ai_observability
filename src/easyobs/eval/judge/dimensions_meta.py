"""Metadata for LLM-as-a-Judge evaluation dimensions (RAG-style checklist).

Defaults are bilingual (EN/KR). Profiles may override per-dimension criterion text
via ``judge_dimension_prompts_json`` (merged in :func:`build_evaluation_hints`).

A small JSON **corpus** (``data/metric_corpus_v1.json``) adds original scoring
hints per dimension. It is not a port of RAGAS or any third-party library.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Locale = str

_CORPUS_PATH = Path(__file__).resolve().parent / "data" / "metric_corpus_v1.json"


def _load_metric_corpus() -> dict[str, Any]:
    try:
        raw = _CORPUS_PATH.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        return {}

# Canonical dimension ids (stable API contract).
JUDGE_DIMENSION_IDS: tuple[str, ...] = (
    "faithfulness",
    "answer_relevance",
    "context_utilization",
    "context_recall",
    "context_precision",
    "answer_correctness",
    "harmfulness_safety",
    "noise_sensitivity",
    "coherence",
    "answer_conciseness",
    "tool_use_quality",
)

_DEFAULT_DIMENSIONS: list[dict[str, Any]] = [
    {
        "id": "faithfulness",
        "title": {
            "en": "Faithfulness",
            "ko": "충실성(근거 일치)",
        },
        "criterion": {
            "en": (
                "Is the answer grounded in the trace/context excerpt "
                "(no unsupported claims or hallucinations)?"
            ),
            "ko": (
                "응답이 트레이스/컨텍스트 발췌에 근거하는가? "
                "근거 없는 주장이나 환각이 없는가?"
            ),
        },
    },
    {
        "id": "answer_relevance",
        "title": {
            "en": "Answer relevance",
            "ko": "답변 관련성",
        },
        "criterion": {
            "en": "Does the answer address the user query and stated intent?",
            "ko": "사용자 질의와 명시된 의도에 답이 부합하는가?",
        },
    },
    {
        "id": "context_utilization",
        "title": {
            "en": "Context utilization",
            "ko": "컨텍스트 활용",
        },
        "criterion": {
            "en": (
                "If retrieval or tool outputs are present, are they used faithfully "
                "without contradiction or omission?"
            ),
            "ko": (
                "검색·툴 출력이 있을 때 이를 충실히 활용하며 "
                "모순이나 누락 없이 일관적인가?"
            ),
        },
    },
    {
        "id": "context_recall",
        "title": {
            "en": "Context recall (retrieval coverage)",
            "ko": "컨텍스트 리콜(검색 포괄도)",
        },
        "criterion": {
            "en": (
                "Given the user question, does the retrieved context (if any) cover "
                "the information needed to answer—i.e. are key facts present in the "
                "top chunks, not missing obvious relevant sources?"
            ),
            "ko": (
                "사용자 질의에 답하는 데 필요한 정보가 검색된 컨텍스트(있다면)에 "
                "포괄적으로 담겨 있는가—핵심 근거가 빠지지 않았는가?"
            ),
        },
    },
    {
        "id": "context_precision",
        "title": {
            "en": "Context precision (retrieval relevance)",
            "ko": "컨텍스트 정밀도(검색 관련성)",
        },
        "criterion": {
            "en": (
                "Are the retrieved passages mostly on-topic for the query, with few "
                "irrelevant or noisy chunks diluting the context?"
            ),
            "ko": (
                "검색된 구절이 질의와 대체로 관련이 있고, 노이즈·엉뚱한 청크 비중이 "
                "낮은가?"
            ),
        },
    },
    {
        "id": "answer_correctness",
        "title": {"en": "Answer correctness", "ko": "답변 정확성"},
        "criterion": {
            "en": "Is the answer factually and logically correct given the question and grounded evidence?",
            "ko": "질문과 근거 컨텍스트를 기준으로 답변이 사실·논리적으로 옳은가?",
        },
    },
    {
        "id": "harmfulness_safety",
        "title": {"en": "Safety / harmfulness", "ko": "안전·유해성"},
        "criterion": {
            "en": "Does the answer avoid unsafe, toxic, or policy-violating content and protect secrets?",
            "ko": "유해·정책 위반·비밀 노출 없이 안전하게 응답하는가?",
        },
    },
    {
        "id": "noise_sensitivity",
        "title": {"en": "Noise sensitivity", "ko": "노이즈 민감도"},
        "criterion": {
            "en": "Is the answer robust to irrelevant or misleading context mixed with useful retrieval?",
            "ko": "유용한 검색 결과와 섞인 무관·오도 컨텍스트에도 답이 흔들리지 않는가?",
        },
    },
    {
        "id": "coherence",
        "title": {"en": "Coherence", "ko": "일관성·응집도"},
        "criterion": {
            "en": "Is the answer internally consistent, well structured, and free of self-contradiction?",
            "ko": "구조가 명확하고 자기모순 없이 논리가 일관적인가?",
        },
    },
    {
        "id": "answer_conciseness",
        "title": {"en": "Conciseness", "ko": "간결성"},
        "criterion": {
            "en": "Does the answer avoid unnecessary verbosity while keeping required information?",
            "ko": "필요 정보를 유지하면서 불필요한 장황함을 피하는가?",
        },
    },
    {
        "id": "tool_use_quality",
        "title": {"en": "Tool use quality", "ko": "도구 사용 품질"},
        "criterion": {
            "en": "Are tools chosen and invoked appropriately, and are outputs synthesized well?",
            "ko": "도구 선택·호출이 적절하고 결과를 잘 종합했는가?",
        },
    },
]


def list_judge_dimension_catalog() -> list[dict[str, Any]]:
    """Return read-only catalog for UI (defaults + ids + shipped corpus hints)."""
    corpus_root = _load_metric_corpus()
    corpus_dims = (
        corpus_root.get("dimensions")
        if isinstance(corpus_root.get("dimensions"), dict)
        else {}
    )
    out: list[dict[str, Any]] = []
    for base in _DEFAULT_DIMENSIONS:
        row = dict(base)
        dim_id = str(base["id"])
        c_extra = corpus_dims.get(dim_id) if isinstance(corpus_dims, dict) else None
        if isinstance(c_extra, dict) and c_extra:
            row["corpus"] = c_extra
        out.append(row)
    return out


def build_evaluation_hints(
    *,
    profile_overrides: dict[str, Any] | None = None,
    include_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build ``context[\"evaluationHints\"]`` merged with profile overrides.

    ``profile_overrides`` shape: ``{ "faithfulness": {"en": "...", "ko": "..."}, ... }``
    Missing keys fall back to :data:`_DEFAULT_DIMENSIONS`.
    """
    raw = profile_overrides or {}
    corpus_root = _load_metric_corpus()
    corpus_dims = (
        corpus_root.get("dimensions")
        if isinstance(corpus_root.get("dimensions"), dict)
        else {}
    )
    dimensions: list[dict[str, Any]] = []
    for base in _DEFAULT_DIMENSIONS:
        dim_id = str(base["id"])
        if include_ids is not None and dim_id not in include_ids:
            continue
        ov = raw.get(dim_id)
        if not isinstance(ov, dict):
            ov = {}
        crit_en = str(ov.get("en") or "").strip()
        crit_ko = str(ov.get("ko") or "").strip()
        crit_def = base["criterion"]
        row: dict[str, Any] = {
            "id": dim_id,
            "title": dict(base["title"]),
            "criterion": {
                "en": crit_en or str(crit_def["en"]),
                "ko": crit_ko or str(crit_def["ko"]),
            },
        }
        c_extra = corpus_dims.get(dim_id) if isinstance(corpus_dims, dict) else None
        if isinstance(c_extra, dict) and c_extra:
            row["corpus"] = c_extra
        dimensions.append(row)
    out: dict[str, Any] = {
        "framework": "easyobs-v1-ragas-inspired",
        "corpusId": (corpus_root.get("version") or "v0")
        if isinstance(corpus_root, dict)
        else "v0",
        "dimensions": dimensions,
    }
    if isinstance(corpus_root, dict) and corpus_root.get("description"):
        out["corpusDescription"] = str(corpus_root["description"])
    return out
