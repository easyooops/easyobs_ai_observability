"""Bilingual metadata for improvement proposals and packs (EN/KR).

Future locales (ja, zh, …) can extend the same structure.
"""

from __future__ import annotations

from typing import Any

Loc = str


def normalize_locale(raw: str | None) -> str:
    v = (raw or "en").strip().lower()
    if v.startswith("ko"):
        return "ko"
    return "en"


CATEGORY_TAXONOMY_I18N: dict[str, dict[str, Any]] = {
    "prompt_clarity": {
        "layer": "L1",
        "label": {"en": "Prompt clarity", "ko": "프롬프트 명확도"},
        "summary": {
            "en": "State intent and constraints more explicitly so the model cannot shortcut them.",
            "ko": "의도와 제약을 더 명시해 모델이 우회하지 못하게 하세요.",
        },
    },
    "context_grounding": {
        "layer": "L2",
        "label": {"en": "Context grounding", "ko": "컨텍스트 근거"},
        "summary": {
            "en": "Tighten the system prompt so retrieval results are cited and grounded in the answer.",
            "ko": "검색 결과를 답변에 인용·근거화하도록 시스템 프롬프트를 보강하세요.",
        },
    },
    "retrieval_quality": {
        "layer": "L2",
        "label": {"en": "Retrieval quality", "ko": "검색(Retrieval) 품질"},
        "summary": {
            "en": "Retrieval looks weak — revisit chunking, embeddings, top-k, and MMR.",
            "ko": "검색 품질이 낮습니다. 청크, 임베딩, top-k, MMR을 점검하세요.",
        },
    },
    "tool_orchestration": {
        "layer": "L3",
        "label": {"en": "Tool orchestration", "ko": "툴 오케스트레이션"},
        "summary": {
            "en": "Cap tool calls and ordering in policy; add guards against infinite tool loops.",
            "ko": "툴 호출·순서를 정책으로 제한하고 무한 루프 방지를 두세요.",
        },
    },
    "answer_format": {
        "layer": "L3",
        "label": {"en": "Answer format", "ko": "응답 형식"},
        "summary": {
            "en": "Enforce JSON / Markdown schemas, validate output, and retry once on failure.",
            "ko": "JSON/마크다운 스키마를 강제하고 검증 후 최대 1회 재시도하세요.",
        },
    },
    "safety_guardrails": {
        "layer": "L3",
        "label": {"en": "Safety guardrails", "ko": "안전 가드레일"},
        "summary": {
            "en": "Apply PII / secret / profanity gates immediately before returning to the user.",
            "ko": "PII·시크릿·비속어 가드를 사용자 응답 직전에 적용하세요.",
        },
    },
    "performance_budget": {
        "layer": "L3",
        "label": {"en": "Performance budget", "ko": "성능·비용 예산"},
        "summary": {
            "en": "Latency, tokens, or cost exceed budget — try smaller models, shorter prompts, caching.",
            "ko": "지연·토큰·비용 예산 초과 — 작은 모델, 짧은 프롬프트, 캐싱을 검토하세요.",
        },
    },
    "model_choice": {
        "layer": "L3",
        "label": {"en": "Model choice", "ko": "모델 선택"},
        "summary": {
            "en": "Model capacity may be mismatched to task difficulty — A/B another model or temperature.",
            "ko": "작업 난이도와 모델 용량이 맞지 않을 수 있습니다. 다른 모델/온도를 A/B 하세요.",
        },
    },
}


ACTIONS_I18N: dict[str, dict[str, list[str]]] = {
    "prompt_clarity": {
        "en": [
            "Bulleted output format/constraints in the system prompt",
            "Inject 1–2 few-shot examples",
        ],
        "ko": [
            "시스템 프롬프트에 출력 형식·제약을 bullet으로 명시",
            "few-shot 예시 1~2개 주입",
        ],
    },
    "context_grounding": {
        "en": [
            "Require [doc:id]-style citations in answers",
            "Add a refusal policy when evidence is insufficient",
        ],
        "ko": [
            "답변에 [doc:id] 형태 인용 강제",
            "근거 부족 시 거절 정책 추가",
        ],
    },
    "retrieval_quality": {
        "en": [
            "Re-index after changing chunk size (e.g. 256→512)",
            "Upgrade embeddings or add a re-ranker",
            "Tune top-k and MMR diversity",
        ],
        "ko": [
            "청크 크기 변경 후 재인덱싱 (예: 256→512)",
            "임베딩 업그레이드 또는 re-ranker 추가",
            "top-k·MMR 다양성 파라미터 조정",
        ],
    },
    "tool_orchestration": {
        "en": [
            "Hard cap on tool calls with clear exit criteria",
            "Cache tool results and dedupe identical calls",
        ],
        "ko": [
            "툴 호출 상한과 명확한 종료 조건",
            "툴 결과 캐시 및 동일 호출 dedup",
        ],
    },
    "answer_format": {
        "en": [
            "Force JSON via function calling / response_format",
            "Validate against schema; at most one retry",
        ],
        "ko": [
            "function calling / response_format으로 JSON 강제",
            "스키마 검증 후 최대 1회 재시도",
        ],
    },
    "safety_guardrails": {
        "en": [
            "PII masking middleware before the response leaves",
            "Register secret patterns for pre-response blocking",
        ],
        "ko": [
            "응답 직전 PII 마스킹 미들웨어",
            "Secret 패턴 사전 차단 정책 등록",
        ],
    },
    "performance_budget": {
        "en": [
            "Try a smaller model or shorter prompts",
            "Introduce shared context caching",
        ],
        "ko": [
            "더 작은 모델 또는 짧은 프롬프트 검토",
            "공통 컨텍스트 캐싱 도입",
        ],
    },
    "model_choice": {
        "en": [
            "A/B an alternative model",
            "Lower temperature into a 0–0.3 band",
        ],
        "ko": [
            "다른 모델 A/B",
            "temperature 0~0.3 범위로 보수화",
        ],
    },
}


PACK_LABEL_I18N: dict[str, dict[str, str]] = {
    "easyobs_standard": {
        "en": "EasyObs (default)",
        "ko": "EasyObs (기본)",
    },
    "easyobs_security": {
        "en": "Security, safety & format",
        "ko": "보안·안전·형식",
    },
    "easyobs_rag": {
        "en": "RAG: grounding & retrieval",
        "ko": "RAG: 근거·검색",
    },
    "easyobs_efficiency": {
        "en": "Latency, cost & tools",
        "ko": "지연·비용·툴",
    },
}


def category_meta(category: str, locale: str) -> dict[str, Any]:
    loc = normalize_locale(locale)
    alt = "en" if loc == "ko" else "ko"
    m = CATEGORY_TAXONOMY_I18N.get(category) or CATEGORY_TAXONOMY_I18N["model_choice"]
    return {
        "layer": m["layer"],
        "label": m["label"].get(loc) or m["label"][alt],
        "summary": m["summary"].get(loc) or m["summary"][alt],
    }


def category_meta_both(category: str) -> dict[str, str]:
    m = CATEGORY_TAXONOMY_I18N.get(category) or CATEGORY_TAXONOMY_I18N["model_choice"]
    return {
        "labelEn": m["label"]["en"],
        "labelKo": m["label"]["ko"],
        "summaryEn": m["summary"]["en"],
        "summaryKo": m["summary"]["ko"],
    }


def actions_for_category(category: str, locale: str) -> list[str]:
    loc = normalize_locale(locale)
    alt = "en" if loc == "ko" else "ko"
    a = ACTIONS_I18N.get(category, {}).get(loc) or ACTIONS_I18N.get(category, {}).get(alt)
    if a:
        return list(a)
    return (
        ACTIONS_I18N.get("model_choice", {}).get(loc)
        or ["Further analysis recommended"]
    )


def actions_both(category: str) -> dict[str, list[str]]:
    a = ACTIONS_I18N.get(category) or ACTIONS_I18N["model_choice"]
    return {"en": list(a["en"]), "ko": list(a["ko"])}


def pack_label(pack_id: str, locale: str) -> str:
    loc = normalize_locale(locale)
    alt = "en" if loc == "ko" else "ko"
    p = PACK_LABEL_I18N.get(pack_id, {})
    return str(p.get(loc) or p.get(alt) or pack_id)


def pack_label_both(pack_id: str) -> dict[str, str]:
    p = PACK_LABEL_I18N.get(pack_id, {})
    return {
        "en": str(p.get("en") or pack_id),
        "ko": str(p.get("ko") or p.get("en") or pack_id),
    }


def fallback_rationale(final_score: float, final_verdict: str, locale: str) -> str:
    loc = normalize_locale(locale)
    if loc == "ko":
        return (
            f"종합 점수 {final_score:.2f}, 판정 {final_verdict!r} 로 프로필 합격 구간(설계 기본 ≥0.7) "
            "미만입니다. 평가기·임계값·모델 선택을 조정하세요."
        )
    return (
        f"Blended score {final_score:.2f} with verdict {final_verdict!r} is below the "
        "profile pass band (design default ≥0.7). Tune evaluators, thresholds, or model choice."
    )
