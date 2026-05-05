"""Detailed Improvement-Pack category catalog (46 builtin × 11 groups).

Why this module exists
----------------------
The legacy 8-key taxonomy in ``improvement_i18n.CATEGORY_TAXONOMY_I18N``
(``prompt_clarity`` / ``retrieval_quality`` / …) was too coarse to
represent the **52 evaluation metrics** defined in the design document
(see ``docs/comparison/02.design/06.evaluation-metrics-and-pipelines.md``
§9). Operators kept asking *"the metric tells me Recall@K dropped, but
which knob exactly should I turn?"* and the legacy bucket only said
``retrieval_quality``.

This module adds a **detail layer** on top of the legacy taxonomy:

* **46 fine-grained categories** in 11 groups, each with a stable
  ``effort`` (low / medium / high) so the UI can color-code work
  size at a glance (see §08 §3 of the design doc — distinct from
  trace verdict palette to avoid collision).
* A **cause_code → detail** mapping (1 primary + 0..5 secondary
  candidates). Average ≈3 candidates per metric → roughly **155
  metric ↔ remediation pairs**, which fulfils the "52 × N" view
  operators asked for without forcing 52 distinct categories
  (overlap is allowed, e.g. ``retrieval.tune`` covers B1, B4, B8…).
* Helper functions to translate detail ↔ legacy ↔ group, and to
  pick a primary detail when only an evaluator id is available.

Backward compatibility
----------------------
This module is **additive**. It does not modify the legacy 8-key
contract that ``derive_proposals`` and existing tests rely on.
``improvements.py`` enriches each proposal with the detail
metadata while keeping the legacy ``category`` key untouched.

Naming & legal note
-------------------
Group / category identifiers (``prompt.rewrite``, ``retrieval.tune``,
``query.intent_taxonomy`` …) are this product's own naming scheme.
They were intentionally chosen to **not** mirror Langfuse's
``Score Config``, OpenLIT's ``Evaluation Type`` or Phoenix's
``Evaluator`` identifiers (see ``docs/comparison/02.design/05`` §0).
Public metric names (Recall@K, Faithfulness …) are industry
terminology and are referenced here only as cross-links, not copied
from any third-party source code.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Effort enum
# ---------------------------------------------------------------------------

#: Allowed effort ranks.  Keep ordered ascending so the UI can sort and
#: render distribution bars deterministically.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")

#: Default human-readable hints for each effort tier (used as tooltips).
#: The UI joins these with the per-category ``summary`` for a complete
#: "what this is + how heavy it is" sentence.
EFFORT_HINTS: dict[str, dict[str, str]] = {
    "low": {
        "en": "Apply within minutes/hours — single profile or prompt edit.",
        "ko": "분~시간 내 적용 가능. 단일 Profile/Prompt 변경.",
    },
    "medium": {
        "en": "1–5 day sprint — rule, golden set, or parameter combo update.",
        "ko": "1~5일 작업. 룰·골든셋·파라미터 조합 변경.",
    },
    "high": {
        "en": "Weeks/months — embedding swap, index rebuild, new tool, infra change.",
        "ko": "주~월 단위 변경. 임베딩/인덱스/툴 재구축 또는 인프라 변경.",
    },
}


def normalize_effort(raw: str | None) -> str:
    v = (raw or "").strip().lower()
    if v in EFFORT_LEVELS:
        return v
    return "medium"


# ---------------------------------------------------------------------------
# 46 detailed categories × 11 groups
# ---------------------------------------------------------------------------

#: Fixed display order of the 12 groups (used for accordions, filters).
#:
#: ``supply`` was added for the **AI Security hardening track** (see
#: ``docs/comparison/02.design/10.ai-security-hardening.md``). It captures
#: third-party/vendor controls that surfaced as urgent after the April 2026
#: Anthropic Claude Mythos preview supply-chain incident: a third-party
#: vendor's identity was abused to gain unauthorized access to the Mythos
#: model, and Anthropic separately leaked Claude source via a webpack
#: source map shipped to production. Operators of an LLM observability
#: platform face the same shape of risk (ingest tokens, judge providers,
#: web bundle artifacts) so the catalog needs first-class proposals for it.
GROUP_ORDER: tuple[str, ...] = (
    "prompt",
    "query",
    "retrieval",
    "context",
    "tool",
    "agent",
    "format",
    "safety",
    "model",
    "dataset",
    "infra",
    "supply",
)

GROUP_LABELS: dict[str, dict[str, str]] = {
    "prompt": {"en": "Prompt", "ko": "프롬프트"},
    "query": {"en": "Query routing", "ko": "질의 라우팅"},
    "retrieval": {"en": "Retrieval", "ko": "검색"},
    "context": {"en": "Context", "ko": "컨텍스트"},
    "tool": {"en": "Tool", "ko": "툴"},
    "agent": {"en": "Agent / planning", "ko": "에이전트·계획"},
    "format": {"en": "Output format", "ko": "출력 형식"},
    "safety": {"en": "Safety", "ko": "안전"},
    "model": {"en": "Model", "ko": "모델"},
    "dataset": {"en": "Golden set", "ko": "골든 세트"},
    "infra": {"en": "Infra", "ko": "인프라"},
    "supply": {"en": "Supply chain", "ko": "공급망"},
}


#: 46-row catalog. Each row is the source of truth for **what to label,
#: how heavy the work is, and which legacy bucket it rolls up to**.
#:
#: ``legacy`` is one of the 8-key taxonomy values from
#: ``improvement_i18n.CATEGORY_TAXONOMY_I18N``. It is used for:
#:
#: * Pack filter (``IMPROVEMENT_PACKS[*]['categories']``) backward compat.
#: * Aggregations done by external dashboards that already key by the
#:   8-bucket taxonomy.
#:
#: The default ``effort`` may be **upgraded** by a Judge that has trace
#: evidence (e.g. retrieval.tune defaults to low, but if the Judge says
#: "the index itself is mis-built" then this single proposal moves to
#: ``high`` and ``effort_reason`` carries the explanation).
CATEGORY_DETAILS: dict[str, dict[str, Any]] = {
    # -------- prompt (9) --------
    "prompt.rewrite": {
        "group": "prompt",
        "label": {"en": "Prompt rewrite", "ko": "프롬프트 재작성"},
        "summary": {
            "en": "Edit the prompt body to remove ambiguity and add explicit constraints.",
            "ko": "프롬프트 본문을 수정해 모호함을 줄이고 제약을 명시하세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    "prompt.system_split": {
        "group": "prompt",
        "label": {"en": "System / user split", "ko": "system/user 역할 분리"},
        "summary": {
            "en": "Move durable rules (tool policy, persona) into the system prompt.",
            "ko": "툴 정책·페르소나처럼 안정적인 규칙은 system 프롬프트로 옮기세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    "prompt.few_shot": {
        "group": "prompt",
        "label": {"en": "Few-shot examples", "ko": "Few-shot 예시 보강"},
        "summary": {
            "en": "Add or replace 1–3 few-shot demonstrations covering the failure pattern.",
            "ko": "실패 패턴을 다루는 few-shot 예시 1~3개를 추가하거나 교체하세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    "prompt.ko_localize": {
        "group": "prompt",
        "label": {"en": "Korean / honorifics", "ko": "한국어·존댓말 강화"},
        "summary": {
            "en": "Force response language and honorific tone explicitly.",
            "ko": "응답 언어와 존댓말 톤을 명시적으로 강제하세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    "prompt.grounding": {
        "group": "prompt",
        "label": {"en": "Cite-then-answer (grounding)", "ko": "근거 우선 응답"},
        "summary": {
            "en": "Forbid statements without [doc:id] citations from retrieved context.",
            "ko": "검색 컨텍스트의 [doc:id] 인용 없는 문장을 금지하세요.",
        },
        "effort": "low",
        "legacy": "context_grounding",
    },
    "prompt.checklist": {
        "group": "prompt",
        "label": {"en": "Answer checklist", "ko": "응답 체크리스트"},
        "summary": {
            "en": "Force the model to enumerate must_include facets before finalizing.",
            "ko": "응답을 마감하기 전에 must_include 항목을 모델이 점검하게 하세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    "prompt.length_constraint": {
        "group": "prompt",
        "label": {"en": "Length / repetition cap", "ko": "응답 길이·반복 제어"},
        "summary": {
            "en": "Bound the response length and forbid repeated phrases.",
            "ko": "응답 길이를 제한하고 반복 표현을 금지하세요.",
        },
        "effort": "low",
        "legacy": "answer_format",
    },
    "prompt.refusal_policy": {
        "group": "prompt",
        "label": {"en": "Refusal copy", "ko": "거절 응답 표준화"},
        "summary": {
            "en": "Standardize refusal / limited-answer wording with a disclaimer template.",
            "ko": "거절·제한적 응답 문구를 면책 템플릿으로 표준화하세요.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "prompt.tone_guide": {
        "group": "prompt",
        "label": {"en": "Tone & persona guide", "ko": "톤·페르소나 가이드"},
        "summary": {
            "en": "Codify the brand voice — verb mood, register, persona constants.",
            "ko": "브랜드 보이스(어조·존중·페르소나 상수)를 명문화하세요.",
        },
        "effort": "low",
        "legacy": "prompt_clarity",
    },
    # -------- query (3) --------
    "query.intent_taxonomy": {
        "group": "query",
        "label": {"en": "Intent taxonomy", "ko": "Intent 분류 체계"},
        "summary": {
            "en": "Redefine the user-intent taxonomy so the router can disambiguate.",
            "ko": "사용자 의도 분류 체계를 재정의해 라우터가 명확히 분기하게 하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    "query.clarify_turn": {
        "group": "query",
        "label": {"en": "Clarification turn", "ko": "되묻기 턴 추가"},
        "summary": {
            "en": "Insert a clarification turn when required slots are missing or ambiguous.",
            "ko": "필수 슬롯이 비거나 모호할 때 한 차례 되묻기 턴을 추가하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    "query.router": {
        "group": "query",
        "label": {"en": "Query router", "ko": "질의 라우터"},
        "summary": {
            "en": "Route by language, complexity, or domain to the matching pipeline.",
            "ko": "언어·난이도·도메인별로 알맞은 파이프라인으로 라우팅하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    # -------- retrieval (8) --------
    "retrieval.tune": {
        "group": "retrieval",
        "label": {"en": "Retrieval tuning", "ko": "검색 파라미터 튜닝"},
        "summary": {
            "en": "Tune top_k, hybrid weights, and basic filters before deeper changes.",
            "ko": "top_k·하이브리드 가중치·기본 필터를 우선 조정하세요.",
        },
        "effort": "low",
        "legacy": "retrieval_quality",
    },
    "retrieval.reranker": {
        "group": "retrieval",
        "label": {"en": "Reranker", "ko": "Reranker 도입/교체"},
        "summary": {
            "en": "Enable or swap a reranker (e.g. cross-encoder, BGE-reranker).",
            "ko": "Reranker(예: cross-encoder, BGE-reranker)를 켜거나 교체하세요.",
        },
        "effort": "medium",
        "legacy": "retrieval_quality",
    },
    "retrieval.chunking": {
        "group": "retrieval",
        "label": {"en": "Chunk strategy", "ko": "청크 전략"},
        "summary": {
            "en": "Change chunk size or overlap — re-indexing is required.",
            "ko": "청크 크기·오버랩을 변경하세요. 재인덱싱이 동반됩니다.",
        },
        "effort": "high",
        "legacy": "retrieval_quality",
    },
    "retrieval.embedding": {
        "group": "retrieval",
        "label": {"en": "Embedding model swap", "ko": "임베딩 모델 교체"},
        "summary": {
            "en": "Swap the embedding model — full re-indexing and benchmark needed.",
            "ko": "임베딩 모델을 교체하세요. 전면 재인덱싱·벤치마크가 필요합니다.",
        },
        "effort": "high",
        "legacy": "retrieval_quality",
    },
    "retrieval.query_expand": {
        "group": "retrieval",
        "label": {"en": "Query expansion", "ko": "질의 확장"},
        "summary": {
            "en": "Expand the query (HyDE / multi-query) before retrieval.",
            "ko": "검색 전 질의를 확장(HyDE·멀티쿼리)하세요.",
        },
        "effort": "medium",
        "legacy": "retrieval_quality",
    },
    "retrieval.metadata_filter": {
        "group": "retrieval",
        "label": {"en": "Metadata filter", "ko": "메타데이터 필터"},
        "summary": {
            "en": "Fix metadata filter logic (tenant, time range, doc type).",
            "ko": "메타데이터 필터(tenant·기간·문서 유형) 로직을 정정하세요.",
        },
        "effort": "medium",
        "legacy": "retrieval_quality",
    },
    "retrieval.dedup": {
        "group": "retrieval",
        "label": {"en": "Top-K dedup / MMR", "ko": "Top-K 중복 제거"},
        "summary": {
            "en": "Apply MMR or cosine-cut to drop near-duplicate chunks.",
            "ko": "MMR·코사인 컷오프로 유사 중복 청크를 제거하세요.",
        },
        "effort": "low",
        "legacy": "retrieval_quality",
    },
    "retrieval.compress": {
        "group": "retrieval",
        "label": {"en": "Context compression", "ko": "컨텍스트 압축"},
        "summary": {
            "en": "Summarize the retrieved set before sending to the LLM.",
            "ko": "검색 결과를 요약·압축한 뒤 LLM에 전달하세요.",
        },
        "effort": "medium",
        "legacy": "performance_budget",
    },
    # -------- context (2) --------
    "context.attribution": {
        "group": "context",
        "label": {"en": "Source attribution", "ko": "출처 메타 강화"},
        "summary": {
            "en": "Always attach source metadata to every chunk shipped to the model.",
            "ko": "모델로 가는 모든 청크에 출처 메타데이터를 강제로 동봉하세요.",
        },
        "effort": "low",
        "legacy": "context_grounding",
    },
    "context.ordering": {
        "group": "context",
        "label": {"en": "Context ordering", "ko": "청크 순서 정렬"},
        "summary": {
            "en": "Re-order context by score, recency, and source authority.",
            "ko": "컨텍스트를 점수·최신성·출처 신뢰도로 재정렬하세요.",
        },
        "effort": "low",
        "legacy": "context_grounding",
    },
    # -------- tool (5) --------
    "tool.spec": {
        "group": "tool",
        "label": {"en": "Tool schema", "ko": "툴 스키마 강화"},
        "summary": {
            "en": "Tighten tool schemas — required fields, enums, descriptions.",
            "ko": "툴 스키마(필수 필드·enum·설명)를 더 엄격하게 잡으세요.",
        },
        "effort": "low",
        "legacy": "tool_orchestration",
    },
    "tool.add": {
        "group": "tool",
        "label": {"en": "Add missing tool", "ko": "누락 툴 추가"},
        "summary": {
            "en": "Implement a missing tool the agent kept improvising for.",
            "ko": "에이전트가 즉흥 처리했던 누락 툴을 새로 구현하세요.",
        },
        "effort": "high",
        "legacy": "tool_orchestration",
    },
    "tool.remove": {
        "group": "tool",
        "label": {"en": "Remove dead tool", "ko": "미사용 툴 제거"},
        "summary": {
            "en": "Remove tools that have not been called in 30+ days.",
            "ko": "30일 이상 호출 0건인 툴을 정리하세요.",
        },
        "effort": "low",
        "legacy": "tool_orchestration",
    },
    "tool.policy": {
        "group": "tool",
        "label": {"en": "Tool call policy", "ko": "툴 호출 정책"},
        "summary": {
            "en": "Cap retries, dedup identical args, set clear exit criteria.",
            "ko": "재시도 상한·동일 인자 dedup·종료 조건을 정책으로 두세요.",
        },
        "effort": "low",
        "legacy": "tool_orchestration",
    },
    "tool.cache": {
        "group": "tool",
        "label": {"en": "Tool result cache", "ko": "툴 결과 캐시"},
        "summary": {
            "en": "Cache tool results keyed by a hash of arguments with a TTL.",
            "ko": "인자 해시를 키로 TTL 기반 툴 결과 캐시를 적용하세요.",
        },
        "effort": "medium",
        "legacy": "tool_orchestration",
    },
    # -------- agent (3) --------
    "agent.planner_template": {
        "group": "agent",
        "label": {"en": "Planner template", "ko": "Planner 템플릿"},
        "summary": {
            "en": "Adopt a Plan→Act→Reflect template for multi-step tasks.",
            "ko": "멀티스텝 작업에 Plan→Act→Reflect 템플릿을 도입하세요.",
        },
        "effort": "medium",
        "legacy": "tool_orchestration",
    },
    "agent.path_limit": {
        "group": "agent",
        "label": {"en": "Reasoning path cap", "ko": "추론 경로 상한"},
        "summary": {
            "en": "Cap reasoning steps and add a fallback when the cap is hit.",
            "ko": "추론 단계 상한과 한계 도달 시 fallback 정책을 두세요.",
        },
        "effort": "low",
        "legacy": "tool_orchestration",
    },
    "agent.synthesis_template": {
        "group": "agent",
        "label": {"en": "Synthesis template", "ko": "결과 종합 템플릿"},
        "summary": {
            "en": "Standardize multi-tool result merging into a tabular synthesis.",
            "ko": "다중 도구 결과 종합을 표 형식 + 결론 1문 템플릿으로 표준화하세요.",
        },
        "effort": "medium",
        "legacy": "tool_orchestration",
    },
    # -------- format (3) --------
    "format.guard": {
        "group": "format",
        "label": {"en": "Format guard", "ko": "응답 형식 강제"},
        "summary": {
            "en": "Force JSON via response_format and validate; one retry max.",
            "ko": "response_format으로 JSON을 강제하고 검증, 최대 1회 재시도하세요.",
        },
        "effort": "low",
        "legacy": "answer_format",
    },
    "format.citation": {
        "group": "format",
        "label": {"en": "Citation format", "ko": "인용 형식 표준화"},
        "summary": {
            "en": "Pin a single citation pattern such as [doc:id] across responses.",
            "ko": "[doc:id] 같은 단일 인용 패턴으로 표준화하세요.",
        },
        "effort": "low",
        "legacy": "answer_format",
    },
    "format.schema": {
        "group": "format",
        "label": {"en": "JSON Schema rev", "ko": "JSON Schema 갱신"},
        "summary": {
            "en": "Publish a new JSON Schema revision (handle deprecation carefully).",
            "ko": "JSON Schema 새 리비전을 발행하세요(디프리케이션 주의).",
        },
        "effort": "medium",
        "legacy": "answer_format",
    },
    # -------- safety (3) --------
    "safety.policy": {
        "group": "safety",
        "label": {"en": "Safety policy", "ko": "안전 정책"},
        "summary": {
            "en": "Add or tighten content / denylist policies before responding.",
            "ko": "응답 직전 컨텐츠·denylist 정책을 추가하거나 강화하세요.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "safety.refusal": {
        "group": "safety",
        "label": {"en": "Refusal template", "ko": "거절 템플릿"},
        "summary": {
            "en": "Standardize refusal copy with a legal disclaimer where needed.",
            "ko": "필요 시 법적 면책을 포함한 거절 템플릿으로 표준화하세요.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "safety.pii_mask": {
        "group": "safety",
        "label": {"en": "PII masking", "ko": "PII 마스킹"},
        "summary": {
            "en": "Add a PII masking middleware just before the response leaves.",
            "ko": "응답 직전에 PII 마스킹 미들웨어를 적용하세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    # The following 8 rows were added for the AI security hardening track
    # in response to the April 2026 Mythos preview / Anthropic supply-chain
    # incidents (see design doc 10). They round the safety group out from
    # 3 to 11 categories so the catalog can answer modern AI threat causes
    # (prompt injection / jailbreak drift / exfil / supply chain).
    "safety.injection_guard": {
        "group": "safety",
        "label": {"en": "Prompt injection guard", "ko": "프롬프트 인젝션 가드"},
        "summary": {
            "en": "Detect 'ignore previous instructions' style payloads in the user input and tool outputs before they reach the model.",
            "ko": "사용자 입력·툴 결과에서 '이전 지시 무시' 류 인젝션 페이로드를 사전 차단하세요.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "safety.jailcanary": {
        "group": "safety",
        "label": {"en": "Jailbreak canary", "ko": "Jailbreak 카나리"},
        "summary": {
            "en": "Embed a hidden canary string in the system prompt; alert when the model leaks or paraphrases it (jailbreak signal).",
            "ko": "system 프롬프트에 카나리 토큰을 심고 응답에 노출되거나 풀어쓰면 jailbreak 신호로 알림.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "safety.exfil_filter": {
        "group": "safety",
        "label": {"en": "Exfil URL / blob filter", "ko": "외부 URL·blob 유출 필터"},
        "summary": {
            "en": "Block responses that include suspicious external URLs, base64 blobs, or DNS-tunnel-shaped strings.",
            "ko": "응답에 의심 URL·base64 blob·DNS 터널 형태 문자열이 들어가면 차단·경고.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "safety.secret_egress": {
        "group": "safety",
        "label": {"en": "Tool-arg secret egress", "ko": "툴 인자 시크릿 이그레스"},
        "summary": {
            "en": "Scan tool call arguments for secrets/PII before they leave the agent boundary, in addition to response scanning.",
            "ko": "응답뿐 아니라 툴 호출 인자에서도 시크릿·PII 패턴을 검사해 외부로 새는 것을 막으세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "safety.judge_sanitize": {
        "group": "safety",
        "label": {"en": "Sanitize trace before Judge", "ko": "Judge 전송 전 트레이스 살균"},
        "summary": {
            "en": "Mask PII / customer fields and strip tool credentials before sending the trace context to an external Judge provider.",
            "ko": "외부 Judge로 트레이스를 보내기 전에 PII·고객 필드 마스킹과 툴 자격증명 제거를 적용하세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "safety.audit_log": {
        "group": "safety",
        "label": {"en": "Admin audit log", "ko": "관리자 감사 로그"},
        "summary": {
            "en": "Persist who-did-what-when for profile/judge/token mutations so a third-party identity abuse can be reconstructed forensically.",
            "ko": "프로파일·Judge·토큰 변경의 행위자/시점/내용을 감사 로그로 저장해 서드파티 신원 악용 시 추적 가능하게 하세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "safety.ingest_redact": {
        "group": "safety",
        "label": {"en": "Ingest-time redaction", "ko": "수집 시 자동 마스킹"},
        "summary": {
            "en": "Apply field-level redaction at the ingest pipeline so PII never lands in blob storage in the first place.",
            "ko": "수집 파이프라인에서 필드 단위 redaction을 적용해 PII가 blob에 처음부터 저장되지 않도록 하세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "safety.secret_rotate": {
        "group": "safety",
        "label": {"en": "Secret rotation", "ko": "시크릿 정기 회전"},
        "summary": {
            "en": "Schedule periodic rotation for ingest tokens and judge provider API keys; auto-revoke unused keys.",
            "ko": "인제스트 토큰·Judge API 키를 주기적으로 회전·미사용 키 자동 폐기.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    # -------- supply (4) --------
    "supply.vendor_review": {
        "group": "supply",
        "label": {"en": "Third-party vendor review", "ko": "서드파티 벤더 리뷰"},
        "summary": {
            "en": "Run a quarterly access & posture review on every third-party vendor that holds an org/service token (Mythos lesson).",
            "ko": "org·service 토큰을 보유한 서드파티 벤더에 대해 분기별 접근·자세 점검을 수행하세요(미토스 교훈).",
        },
        "effort": "high",
        "legacy": "safety_guardrails",
    },
    "supply.sbom": {
        "group": "supply",
        "label": {"en": "SBOM publishing", "ko": "SBOM 발행"},
        "summary": {
            "en": "Publish and monitor a Software Bill of Materials for the platform so transitive vulns can be triaged at AI-attack speed.",
            "ko": "플랫폼 SBOM을 발행·모니터링해 의존성 취약점을 AI 공격 속도에 맞춰 분류·조치하세요.",
        },
        "effort": "medium",
        "legacy": "safety_guardrails",
    },
    "supply.sourcemap_strip": {
        "group": "supply",
        "label": {"en": "Strip source maps", "ko": "소스맵 제거"},
        "summary": {
            "en": "Remove webpack/Vite source maps from production bundles to prevent reverse-engineering (cf. the 512K-line Claude leak).",
            "ko": "프로덕션 번들에서 webpack/Vite 소스맵을 제거해 역공학을 차단하세요(클로드 51만 줄 유출 사례).",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    "supply.cache_acl": {
        "group": "supply",
        "label": {"en": "Public cache / blob ACL audit", "ko": "공개 캐시·blob ACL 점검"},
        "summary": {
            "en": "Audit any externally-reachable cache/blob endpoint to ensure model names, internal docs, or trace shards are never world-readable.",
            "ko": "외부 접근 가능한 캐시·blob 엔드포인트의 ACL을 점검해 모델·내부문서·트레이스 shard가 공개로 노출되지 않게 하세요.",
        },
        "effort": "low",
        "legacy": "safety_guardrails",
    },
    # -------- model (3) --------
    "model.swap": {
        "group": "model",
        "label": {"en": "Model swap", "ko": "모델 교체"},
        "summary": {
            "en": "A/B another model — capacity, latency, or cost tier.",
            "ko": "다른 모델(용량·지연·비용 등급)을 A/B 검증하세요.",
        },
        "effort": "medium",
        "legacy": "model_choice",
    },
    "model.params": {
        "group": "model",
        "label": {"en": "Sampling params", "ko": "샘플링 파라미터"},
        "summary": {
            "en": "Lower temperature/top_p, cap max_tokens for predictable outputs.",
            "ko": "temperature·top_p를 낮추고 max_tokens 상한을 두세요.",
        },
        "effort": "low",
        "legacy": "model_choice",
    },
    "model.judge_swap": {
        "group": "model",
        "label": {"en": "Judge model swap", "ko": "Judge 모델 교체"},
        "summary": {
            "en": "Swap the judge model itself, or move to multi-judge consensus.",
            "ko": "Judge 모델 자체를 교체하거나 다중 합의로 전환하세요.",
        },
        "effort": "medium",
        "legacy": "model_choice",
    },
    # -------- dataset (3) --------
    "dataset.expand": {
        "group": "dataset",
        "label": {"en": "Expand golden set", "ko": "골든셋 확장"},
        "summary": {
            "en": "Mine similar failures and add them as golden-set candidates.",
            "ko": "유사 실패 사례를 자동 발굴해 골든셋 candidate로 추가하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    "dataset.relabel": {
        "group": "dataset",
        "label": {"en": "Relabel golden", "ko": "골든 라벨 재검수"},
        "summary": {
            "en": "Relabel ambiguous golden items (the GT itself was unclear).",
            "ko": "기대 답이 모호한 골든 항목을 재라벨링하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    "dataset.curate": {
        "group": "dataset",
        "label": {"en": "Curate L1/L2/L3", "ko": "L1/L2/L3 보강"},
        "summary": {
            "en": "Fill missing layers per item — query intent, retrieval IDs, expected answer.",
            "ko": "항목별로 누락 레이어(질의 의도·정답 ID·기대 답변)를 보강하세요.",
        },
        "effort": "medium",
        "legacy": "prompt_clarity",
    },
    # -------- infra (4) --------
    "infra.cache": {
        "group": "infra",
        "label": {"en": "Response / embedding cache", "ko": "응답·임베딩 캐시"},
        "summary": {
            "en": "Cache repeated queries / embeddings to cut cost and latency.",
            "ko": "반복 질의·임베딩을 캐시해 비용·지연을 줄이세요.",
        },
        "effort": "medium",
        "legacy": "performance_budget",
    },
    "infra.timeout": {
        "group": "infra",
        "label": {"en": "Timeout tuning", "ko": "타임아웃 조정"},
        "summary": {
            "en": "Right-size step timeouts so slow tools fail fast.",
            "ko": "단계별 타임아웃을 적절히 조정해 느린 툴은 빠르게 실패하게 하세요.",
        },
        "effort": "low",
        "legacy": "performance_budget",
    },
    "infra.parallel": {
        "group": "infra",
        "label": {"en": "Parallelize steps", "ko": "단계 병렬화"},
        "summary": {
            "en": "Run independent retrieval / tool calls in parallel.",
            "ko": "독립적인 검색·툴 호출을 병렬화하세요.",
        },
        "effort": "medium",
        "legacy": "performance_budget",
    },
    "infra.retry": {
        "group": "infra",
        "label": {"en": "Retry / backoff", "ko": "재시도·백오프"},
        "summary": {
            "en": "Add retry with exponential backoff on transient failures.",
            "ko": "일시적 실패에 지수 backoff 기반 재시도를 추가하세요.",
        },
        "effort": "low",
        "legacy": "performance_budget",
    },
}


# ---------------------------------------------------------------------------
# Cause code → primary + secondary detail categories
# ---------------------------------------------------------------------------

#: Mirrors design doc §06 §9 — 52 metric × N candidate mapping.
#:
#: For each ``cause_code`` (defined in the metric catalog), the runner can
#: surface ``primary`` immediately and offer ``secondary`` candidates as
#: "also worth trying" rows in the UI.
CAUSE_TO_DETAILS: dict[str, dict[str, Any]] = {
    # --- A. Query input understanding ---
    "query.intent_mismatch": {
        "primary": "query.intent_taxonomy",
        "secondary": ["prompt.few_shot", "query.router", "dataset.expand"],
    },
    "query.rewrite_drift": {
        "primary": "prompt.rewrite",
        "secondary": ["prompt.few_shot", "model.params"],
    },
    "query.ambiguous": {
        "primary": "query.clarify_turn",
        "secondary": ["prompt.refusal_policy", "query.intent_taxonomy"],
    },
    "query.slot_missing": {
        "primary": "tool.spec",
        "secondary": ["query.clarify_turn", "format.schema"],
    },
    "query.lang_mismatch": {
        "primary": "query.router",
        "secondary": ["prompt.ko_localize", "safety.policy"],
    },
    "query.complexity_high": {
        "primary": "query.router",
        "secondary": ["agent.planner_template", "model.swap"],
    },
    # --- B. Retrieval ---
    "retrieval.recall_low": {
        "primary": "retrieval.tune",
        "secondary": ["retrieval.query_expand", "retrieval.embedding", "dataset.curate"],
    },
    "retrieval.noise_high": {
        "primary": "retrieval.reranker",
        "secondary": ["retrieval.metadata_filter", "retrieval.tune"],
    },
    "retrieval.first_hit_late": {
        "primary": "retrieval.reranker",
        "secondary": ["retrieval.tune", "context.ordering"],
    },
    "retrieval.miss": {
        "primary": "retrieval.tune",
        "secondary": ["retrieval.query_expand", "dataset.expand"],
    },
    "retrieval.rank_quality": {
        "primary": "retrieval.reranker",
        "secondary": ["retrieval.embedding", "context.ordering"],
    },
    "retrieval.precision_low": {
        "primary": "retrieval.reranker",
        "secondary": ["retrieval.tune", "retrieval.metadata_filter"],
    },
    "retrieval.chunk_irrelevant": {
        "primary": "retrieval.embedding",
        "secondary": ["retrieval.chunking", "retrieval.reranker"],
    },
    "retrieval.coverage_low": {
        "primary": "retrieval.tune",
        "secondary": ["retrieval.query_expand", "dataset.curate"],
    },
    "retrieval.filter_wrong": {
        "primary": "retrieval.metadata_filter",
        "secondary": ["format.schema"],
    },
    "retrieval.rerank_no_gain": {
        "primary": "retrieval.reranker",
        "secondary": ["retrieval.embedding"],
    },
    "retrieval.dup_high": {
        "primary": "retrieval.dedup",
        "secondary": ["retrieval.tune"],
    },
    "retrieval.latency_over": {
        "primary": "infra.cache",
        "secondary": ["infra.parallel", "retrieval.tune", "infra.timeout"],
    },
    # --- C. Context / chunk quality ---
    "context.noise": {
        "primary": "retrieval.metadata_filter",
        "secondary": ["retrieval.reranker"],
    },
    "context.dup": {
        "primary": "retrieval.dedup",
        "secondary": ["retrieval.tune"],
    },
    "context.token_waste": {
        "primary": "retrieval.compress",
        "secondary": ["prompt.length_constraint", "retrieval.tune"],
    },
    "context.insufficient": {
        "primary": "retrieval.tune",
        "secondary": ["retrieval.query_expand", "dataset.curate"],
    },
    "context.order_bad": {
        "primary": "context.ordering",
        "secondary": ["retrieval.reranker"],
    },
    "context.attribution_missing": {
        "primary": "context.attribution",
        "secondary": ["prompt.grounding"],
    },
    "context.sim_low": {
        "primary": "retrieval.embedding",
        "secondary": ["retrieval.chunking"],
    },
    # --- D. Generation ---
    "gen.relevance_low": {
        "primary": "prompt.rewrite",
        "secondary": ["retrieval.tune", "model.params"],
    },
    "gen.incorrect": {
        "primary": "dataset.expand",
        "secondary": ["prompt.few_shot", "model.swap", "dataset.relabel"],
    },
    "gen.unfaithful": {
        "primary": "prompt.grounding",
        "secondary": ["format.citation", "safety.refusal"],
    },
    "gen.hallucination": {
        "primary": "prompt.grounding",
        "secondary": ["prompt.refusal_policy", "safety.policy"],
    },
    "gen.incomplete": {
        "primary": "prompt.checklist",
        "secondary": ["prompt.few_shot", "dataset.curate"],
    },
    "gen.verbose": {
        "primary": "prompt.length_constraint",
        "secondary": ["model.params"],
    },
    "gen.tone_off": {
        "primary": "prompt.tone_guide",
        "secondary": ["prompt.few_shot"],
    },
    "gen.policy_violation": {
        "primary": "safety.policy",
        "secondary": ["prompt.refusal_policy", "prompt.rewrite"],
    },
    "gen.citation_wrong": {
        "primary": "format.citation",
        "secondary": ["prompt.grounding"],
    },
    "gen.format_invalid": {
        "primary": "format.guard",
        "secondary": ["format.schema", "prompt.rewrite"],
    },
    "gen.grammar": {
        "primary": "prompt.ko_localize",
        "secondary": ["model.swap"],
    },
    "gen.refusal_bad": {
        "primary": "prompt.refusal_policy",
        "secondary": ["safety.refusal", "dataset.curate"],
    },
    # --- E. Tool / agent ---
    "tool.wrong": {
        "primary": "tool.spec",
        "secondary": ["prompt.few_shot", "agent.planner_template", "tool.remove"],
    },
    "tool.arg_invalid": {
        "primary": "tool.spec",
        "secondary": ["format.schema", "prompt.checklist"],
    },
    "tool.fail": {
        "primary": "tool.policy",
        "secondary": ["infra.retry", "infra.timeout"],
    },
    "tool.retry_high": {
        "primary": "tool.policy",
        "secondary": ["infra.retry"],
    },
    "tool.over_call": {
        "primary": "tool.policy",
        "secondary": ["tool.cache"],
    },
    "agent.plan_wrong": {
        "primary": "agent.planner_template",
        "secondary": ["prompt.few_shot", "tool.spec"],
    },
    "agent.path_long": {
        "primary": "agent.path_limit",
        "secondary": ["agent.planner_template"],
    },
    "agent.synthesis_low": {
        "primary": "agent.synthesis_template",
        "secondary": ["prompt.checklist"],
    },
    # --- F. Operational ---
    "ops.latency_over": {
        "primary": "infra.cache",
        "secondary": ["infra.parallel", "model.swap"],
    },
    "ops.gen_slow": {
        "primary": "model.swap",
        "secondary": ["model.params", "prompt.length_constraint"],
    },
    "ops.cost_over": {
        "primary": "model.swap",
        "secondary": ["infra.cache", "retrieval.compress", "prompt.length_constraint"],
    },
    "ops.token_over": {
        "primary": "prompt.length_constraint",
        "secondary": ["retrieval.compress", "retrieval.tune"],
    },
    "ops.failure": {
        "primary": "infra.retry",
        "secondary": ["infra.timeout", "tool.policy"],
    },
    # --- G. Human feedback ---
    "human.dislike": {
        "primary": "dataset.expand",
        "secondary": ["prompt.rewrite", "query.intent_taxonomy"],
    },
    "human.complaint": {
        "primary": "dataset.expand",
        "secondary": ["prompt.tone_guide", "prompt.rewrite"],
    },
    "human.incorrect": {
        "primary": "dataset.relabel",
        "secondary": ["dataset.curate", "prompt.few_shot"],
    },
    "human.taxonomy": {
        "primary": "dataset.curate",
        "secondary": [],
    },
    "human.csat_low": {
        "primary": "prompt.rewrite",
        "secondary": ["prompt.tone_guide", "dataset.expand"],
    },
    # --- H. AI security (Mythos hardening track, design 10) ----------------
    # Each H-cause maps to one primary "what to add" and a secondary list
    # that reads as "while you're at it, also …". The categories here
    # intentionally lean on safety.* and supply.* so the operator does not
    # need to read the threat brief to know what to do — the proposal text
    # itself describes the control.
    "safety.injection_attempt": {
        "primary": "safety.injection_guard",
        "secondary": ["prompt.system_split", "prompt.refusal_policy", "safety.policy"],
    },
    "safety.jailbreak_drift": {
        "primary": "safety.jailcanary",
        "secondary": ["prompt.refusal_policy", "model.judge_swap", "safety.audit_log"],
    },
    "safety.exfil_url": {
        "primary": "safety.exfil_filter",
        "secondary": ["safety.policy", "format.guard"],
    },
    "safety.secret_egress": {
        "primary": "safety.secret_egress",
        "secondary": ["safety.pii_mask", "tool.spec", "safety.ingest_redact"],
    },
    "safety.self_redact": {
        "primary": "safety.audit_log",
        "secondary": ["safety.jailcanary", "model.judge_swap"],
    },
    "supply.third_party_breach": {
        "primary": "supply.vendor_review",
        "secondary": ["safety.secret_rotate", "safety.audit_log", "supply.cache_acl"],
    },
    "supply.sourcemap_leak": {
        "primary": "supply.sourcemap_strip",
        "secondary": ["supply.sbom", "supply.cache_acl"],
    },
    "supply.public_cache": {
        "primary": "supply.cache_acl",
        "secondary": ["supply.vendor_review", "safety.audit_log"],
    },
}


# ---------------------------------------------------------------------------
# Fallback maps when only an evaluator id is known
# ---------------------------------------------------------------------------

#: Direct evaluator_id → primary detail category. Used when a finding
#: does not carry a ``cause_code`` and only the evaluator id is known.
#: This mirrors ``improvements._FINDING_TO_CATEGORY`` but emits the
#: detailed category instead of the legacy 8-key.
FINDING_TO_DETAIL: dict[str, str] = {
    "rule.response.length": "prompt.length_constraint",
    "rule.response.json": "format.guard",
    "rule.response.language": "query.router",
    "rule.response.present": "prompt.rewrite",
    "rule.response.citation_accuracy": "format.citation",
    "rule.safety.no_pii": "safety.pii_mask",
    "rule.safety.no_secret": "safety.policy",
    "rule.safety.no_profanity": "safety.policy",
    "rule.retrieval.recall_at_k": "retrieval.tune",
    "rule.retrieval.precision_at_k": "retrieval.reranker",
    "rule.retrieval.mrr": "retrieval.reranker",
    "rule.retrieval.hit_rate_at_k": "retrieval.tune",
    "rule.retrieval.ndcg_at_k_binary": "retrieval.reranker",
    "rule.retrieval.map_binary": "retrieval.reranker",
    "rule.retrieval.reranker_gain": "retrieval.reranker",
    "rule.retrieval.dup_ratio": "retrieval.dedup",
    "rule.retrieval.step_latency": "infra.cache",
    "rule.context.dup_chunks": "retrieval.dedup",
    "rule.context.token_waste": "retrieval.compress",
    "rule.context.chunk_semantic_similarity": "retrieval.embedding",
    "rule.tool.selection_accuracy": "tool.spec",
    "rule.tool.argument_validity": "tool.spec",
    "rule.tool.success_rate": "tool.policy",
    "rule.tool.retry_count": "tool.policy",
    "rule.query.intent_match": "query.intent_taxonomy",
    "rule.query.complexity": "query.router",
    "rule.perf.latency": "infra.cache",
    "rule.perf.token_budget": "prompt.length_constraint",
    "rule.perf.cost_budget": "model.swap",
    "rule.perf.model_infer_latency": "model.swap",
    "rule.status.ok": "tool.policy",
    "rule.agent.no_tool_loop": "agent.path_limit",
    "rule.custom.dsl": "prompt.rewrite",
    "judge.consensus": "prompt.rewrite",
    # --- proposed AI security rules (design 10) ---
    "rule.safety.injection_pattern": "safety.injection_guard",
    "rule.safety.jailbreak_canary": "safety.jailcanary",
    "rule.safety.exfil_url": "safety.exfil_filter",
    "rule.tool.secret_egress": "safety.secret_egress",
    "rule.safety.self_redact": "safety.audit_log",
}


#: Bridge from a known evaluator id to its canonical cause code in
#: ``CAUSE_TO_DETAILS``. This lets ``_resolve_detail_category`` surface
#: the same primary + secondary candidates as a metric run that produced
#: an explicit ``cause_code`` — i.e. the operator gets the same
#: "Also try …" list whether the failure came from a rule or a metric.
EVALUATOR_TO_CAUSE: dict[str, str] = {
    # --- response / format rules ---
    "rule.response.length": "gen.verbose",
    "rule.response.json": "gen.format_invalid",
    "rule.response.language": "query.lang_mismatch",
    "rule.response.present": "gen.incomplete",
    "rule.response.citation_accuracy": "gen.citation_wrong",
    # --- safety rules ---
    "rule.safety.no_pii": "gen.policy_violation",
    "rule.safety.no_secret": "gen.policy_violation",
    "rule.safety.no_profanity": "gen.policy_violation",
    # --- retrieval rules ---
    "rule.retrieval.recall_at_k": "retrieval.recall_low",
    "rule.retrieval.precision_at_k": "retrieval.precision_low",
    "rule.retrieval.mrr": "retrieval.first_hit_late",
    "rule.retrieval.hit_rate_at_k": "retrieval.miss",
    "rule.retrieval.ndcg_at_k_binary": "retrieval.rank_quality",
    "rule.retrieval.map_binary": "retrieval.rank_quality",
    "rule.retrieval.reranker_gain": "retrieval.rerank_no_gain",
    "rule.retrieval.dup_ratio": "retrieval.dup_high",
    "rule.retrieval.step_latency": "retrieval.latency_over",
    # --- context rules ---
    "rule.context.dup_chunks": "context.dup",
    "rule.context.token_waste": "context.token_waste",
    "rule.context.chunk_semantic_similarity": "context.sim_low",
    # --- tool / agent rules ---
    "rule.tool.selection_accuracy": "tool.wrong",
    "rule.tool.argument_validity": "tool.arg_invalid",
    "rule.tool.success_rate": "tool.fail",
    "rule.tool.retry_count": "tool.retry_high",
    "rule.agent.no_tool_loop": "agent.path_long",
    "rule.status.ok": "ops.failure",
    # --- query rules ---
    "rule.query.intent_match": "query.intent_mismatch",
    "rule.query.complexity": "query.complexity_high",
    # --- perf / cost rules ---
    "rule.perf.latency": "ops.latency_over",
    "rule.perf.token_budget": "ops.token_over",
    "rule.perf.cost_budget": "ops.cost_over",
    "rule.perf.model_infer_latency": "ops.gen_slow",
    # --- holistic / DSL ---
    "rule.custom.dsl": "gen.relevance_low",
    "judge.consensus": "gen.relevance_low",
    # --- AI security (proposed evaluators, design 10) ---
    # These ids are not yet wired in ``builtin.py`` but are reserved here
    # so adding the rules later is purely additive. The Improvement Pack
    # will then surface the right safety.* primary + secondaries the
    # moment the rule fires its first finding.
    "rule.safety.injection_pattern": "safety.injection_attempt",
    "rule.safety.jailbreak_canary": "safety.jailbreak_drift",
    "rule.safety.exfil_url": "safety.exfil_url",
    "rule.tool.secret_egress": "safety.secret_egress",
    "rule.safety.self_redact": "safety.self_redact",
}


#: When the evaluator id is ``metric.<group_letter><idx>_*``, infer the
#: primary detail by the catalog group letter.
_GROUP_LETTER_PRIMARY: dict[str, str] = {
    "a": "query.router",
    "b": "retrieval.tune",
    "c": "retrieval.dedup",
    "d": "prompt.rewrite",
    "e": "tool.spec",
    "f": "infra.cache",
    "g": "dataset.expand",
}


def cause_for_evaluator(evaluator_id: str) -> str | None:
    """Return the canonical cause code associated with an evaluator id.

    Used by ``derive_proposals`` so that legacy callers — which often
    pass only ``evaluator_id`` without a separate ``cause_code`` — still
    surface the catalog's full ``primary + secondary`` candidate list.
    """

    if not evaluator_id:
        return None
    return EVALUATOR_TO_CAUSE.get(evaluator_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def detail_meta(detail: str) -> dict[str, Any] | None:
    return CATEGORY_DETAILS.get(detail)


def effort_for(detail: str) -> str:
    meta = CATEGORY_DETAILS.get(detail)
    if meta is None:
        return "medium"
    return normalize_effort(str(meta.get("effort") or "medium"))


def legacy_for(detail: str) -> str:
    meta = CATEGORY_DETAILS.get(detail)
    if meta is None:
        return "prompt_clarity"
    return str(meta.get("legacy") or "prompt_clarity")


def group_for(detail: str) -> str:
    meta = CATEGORY_DETAILS.get(detail)
    if meta is None:
        return "prompt"
    return str(meta.get("group") or "prompt")


def label_for(detail: str, locale: str = "en") -> str:
    loc = "ko" if str(locale or "").lower().startswith("ko") else "en"
    meta = CATEGORY_DETAILS.get(detail)
    if meta is None:
        return detail
    label = meta.get("label") or {}
    return str(label.get(loc) or label.get("en") or detail)


def label_both(detail: str) -> dict[str, str]:
    meta = CATEGORY_DETAILS.get(detail) or {}
    label = meta.get("label") or {}
    return {
        "en": str(label.get("en") or detail),
        "ko": str(label.get("ko") or label.get("en") or detail),
    }


def summary_for(detail: str, locale: str = "en") -> str:
    loc = "ko" if str(locale or "").lower().startswith("ko") else "en"
    meta = CATEGORY_DETAILS.get(detail)
    if meta is None:
        return ""
    summary = meta.get("summary") or {}
    return str(summary.get(loc) or summary.get("en") or "")


def summary_both(detail: str) -> dict[str, str]:
    meta = CATEGORY_DETAILS.get(detail) or {}
    summary = meta.get("summary") or {}
    return {
        "en": str(summary.get("en") or ""),
        "ko": str(summary.get("ko") or summary.get("en") or ""),
    }


def details_for_cause(cause_code: str) -> tuple[str | None, list[str]]:
    """Return ``(primary, secondary)`` for a cause code, or ``(None, [])``."""

    row = CAUSE_TO_DETAILS.get(cause_code or "")
    if row is None:
        return None, []
    primary = row.get("primary")
    secondary = list(row.get("secondary") or [])
    return (primary if isinstance(primary, str) else None), secondary


def primary_detail_for_evaluator(evaluator_id: str) -> str | None:
    """Best-effort detail picker when only the evaluator id is known."""

    if not evaluator_id:
        return None
    if evaluator_id in FINDING_TO_DETAIL:
        return FINDING_TO_DETAIL[evaluator_id]
    if evaluator_id.startswith("metric."):
        # ``metric.b1_recall`` → group letter ``b``
        rest = evaluator_id.removeprefix("metric.")
        letter = (rest[:1] or "").lower()
        if letter in _GROUP_LETTER_PRIMARY:
            return _GROUP_LETTER_PRIMARY[letter]
    return None


def list_categories(group: str | None = None) -> list[dict[str, Any]]:
    """Return a serializable view of the catalog (for ``GET`` endpoints)."""

    out: list[dict[str, Any]] = []
    for detail, meta in CATEGORY_DETAILS.items():
        g = str(meta.get("group") or "")
        if group and g != group:
            continue
        out.append(
            {
                "category": detail,
                "group": g,
                "label": dict(meta.get("label") or {}),
                "summary": dict(meta.get("summary") or {}),
                "effort": normalize_effort(str(meta.get("effort") or "medium")),
                "legacy": meta.get("legacy"),
            }
        )
    out.sort(
        key=lambda r: (GROUP_ORDER.index(r["group"]) if r["group"] in GROUP_ORDER else 99, r["category"])
    )
    return out
