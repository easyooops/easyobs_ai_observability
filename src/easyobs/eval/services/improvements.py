"""Improvement Pack generator.

Given a low-scoring evaluation result, the service produces a set of
``proposals`` ranked by category. The generator is purely heuristic: it
inspects the failed findings and the trace summary and emits 1..N
proposals from a layered taxonomy. Operators can extend the taxonomy with
their own categories — those propagate through ``custom_categories``.

When the operator's profile has registered judge models, the runner can
*also* enrich the proposals through the same multi-judge consensus path,
but for the MVP we keep it deterministic so the suggestions are
predictable in tests.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import EvalImprovementRow
from easyobs.eval.services.dtos import ImprovementDTO
from easyobs.eval.pack_rule_presets import (
    suggested_judge_metrics,
    suggested_rule_evaluators_json,
)
from easyobs.eval.services.improvement_catalog import (
    CATEGORY_DETAILS,
    cause_for_evaluator,
    details_for_cause,
    effort_for,
    group_for,
    label_both as detail_label_both,
    primary_detail_for_evaluator,
    summary_both as detail_summary_both,
)
from easyobs.eval.services.improvement_i18n import (
    CATEGORY_TAXONOMY_I18N,
    actions_both,
    actions_for_category,
    category_meta,
    category_meta_both,
    fallback_rationale,
    normalize_locale,
    pack_label_both,
)

def _now() -> datetime:
    return datetime.now(timezone.utc)


# Legacy EN-only view (tests / callers that expect flat strings).
CATEGORY_TAXONOMY: dict[str, dict[str, Any]] = {
    k: {
        "label": v["label"]["en"],
        "layer": v["layer"],
        "summary": v["summary"]["en"],
    }
    for k, v in CATEGORY_TAXONOMY_I18N.items()
}


DEFAULT_IMPROVEMENT_PACK = "easyobs_standard"


def _metric_evaluator_fallback_category(evaluator_id: str) -> str | None:
    """Map ``metric.*`` ids to remediation taxonomy when no legacy rule key."""

    if not evaluator_id.startswith("metric."):
        return None
    if evaluator_id.startswith("metric.b"):
        return "retrieval_quality"
    if evaluator_id.startswith("metric.c"):
        return "context_grounding"
    if evaluator_id.startswith("metric.d"):
        return "answer_format"
    if evaluator_id.startswith("metric.e"):
        return "tool_orchestration"
    if evaluator_id.startswith("metric.f"):
        return "performance_budget"
    if evaluator_id.startswith("metric.a"):
        return "prompt_clarity"
    return "prompt_clarity"


_IMPROVEMENT_PACK_ORDER = (
    "easyobs_standard",
    "easyobs_security",
    "easyobs_rag",
    "easyobs_efficiency",
)

# Optional packs bias which taxonomy rows appear in generated proposals.
IMPROVEMENT_PACKS: dict[str, dict[str, Any]] = {
    "easyobs_standard": {
        "categories": None,
    },
    "easyobs_security": {
        "categories": frozenset(
            {
                "safety_guardrails",
                "prompt_clarity",
                "answer_format",
                "context_grounding",
            }
        ),
    },
    "easyobs_rag": {
        "categories": frozenset(
            {
                "retrieval_quality",
                "context_grounding",
                "prompt_clarity",
            }
        ),
    },
    "easyobs_efficiency": {
        "categories": frozenset(
            {
                "performance_budget",
                "model_choice",
                "tool_orchestration",
            }
        ),
    },
}


def list_improvement_pack_meta() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pack_id in _IMPROVEMENT_PACK_ORDER:
        meta = IMPROVEMENT_PACKS.get(pack_id)
        if meta is None:
            continue
        li = pack_label_both(pack_id)
        out.append(
            {
                "id": pack_id,
                "label": li["en"],
                "labelI18n": {"en": li["en"], "ko": li["ko"]},
                "suggestedRuleEvaluators": suggested_rule_evaluators_json(pack_id),
                "suggestedJudgeMetrics": suggested_judge_metrics(pack_id),
            }
        )
    return out


def _pack_category_filter(pack_id: str | None) -> frozenset[str] | None:
    key = (pack_id or "").strip() or DEFAULT_IMPROVEMENT_PACK
    meta = IMPROVEMENT_PACKS.get(key)
    if meta is None:
        return None
    cats = meta.get("categories")
    return cats


def _filter_proposals_by_pack(
    proposals: list[dict[str, Any]],
    pack_id: str | None,
) -> list[dict[str, Any]]:
    allowed = _pack_category_filter(pack_id)
    if allowed is None:
        return proposals
    return [p for p in proposals if str(p.get("category") or "") in allowed]


# Rule evaluators → remediation taxonomy. ``judge.consensus`` maps to a generic
# bucket so LLM-judge failures still surface improvement rows when pack allows it.
_FINDING_TO_CATEGORY: dict[str, str] = {
    "rule.response.length": "answer_format",
    "rule.response.json": "answer_format",
    "rule.response.language": "prompt_clarity",
    "rule.response.present": "prompt_clarity",
    "rule.safety.no_pii": "safety_guardrails",
    "rule.safety.no_secret": "safety_guardrails",
    "rule.safety.no_profanity": "safety_guardrails",
    "rule.retrieval.recall_at_k": "retrieval_quality",
    "rule.retrieval.precision_at_k": "retrieval_quality",
    "rule.retrieval.mrr": "retrieval_quality",
    "rule.perf.latency": "performance_budget",
    "rule.perf.token_budget": "performance_budget",
    "rule.perf.cost_budget": "performance_budget",
    "rule.status.ok": "tool_orchestration",
    "rule.agent.no_tool_loop": "tool_orchestration",
    "rule.custom.dsl": "prompt_clarity",
    "judge.consensus": "prompt_clarity",
    # Proposed AI security rules (see design doc 10). Reserved here so the
    # legacy ``category`` key correctly rolls these findings into
    # ``safety_guardrails`` when the evaluators ship.
    "rule.safety.injection_pattern": "safety_guardrails",
    "rule.safety.jailbreak_canary": "safety_guardrails",
    "rule.safety.exfil_url": "safety_guardrails",
    "rule.tool.secret_egress": "safety_guardrails",
    "rule.safety.self_redact": "safety_guardrails",
}


def _resolve_detail_category(
    *,
    cause_code: str,
    evaluator_id: str,
    legacy_category: str,
) -> tuple[str | None, list[str]]:
    """Resolve ``(primary_detail, secondary_details)`` for a finding.

    Resolution order — first non-empty wins:

    1. ``cause_code`` lookup (from the metric catalog).
    2. ``evaluator_id`` → canonical cause bridge → catalog lookup
       (so rule findings without an explicit ``cause_code`` still get
       the same secondary candidate list).
    3. ``evaluator_id`` direct lookup (rule.* / metric.*) — primary only.
    4. Pick any detail in the legacy category as a last resort.
    """

    primary, secondary = details_for_cause(cause_code)
    if primary is not None:
        return primary, list(secondary)
    bridged_cause = cause_for_evaluator(evaluator_id)
    if bridged_cause:
        primary, secondary = details_for_cause(bridged_cause)
        if primary is not None:
            return primary, list(secondary)
    primary = primary_detail_for_evaluator(evaluator_id)
    if primary is not None:
        return primary, []
    for detail, meta in CATEGORY_DETAILS.items():
        if str(meta.get("legacy") or "") == legacy_category:
            return detail, []
    return None, []


def _detail_payload(detail: str, secondaries: list[str]) -> dict[str, Any]:
    """Render the detail-related proposal fields used by API & UI."""

    label = detail_label_both(detail)
    summary = detail_summary_both(detail)
    sec_payload: list[dict[str, Any]] = []
    for sd in secondaries:
        if sd not in CATEGORY_DETAILS:
            continue
        sl = detail_label_both(sd)
        sec_payload.append(
            {
                "category": sd,
                "group": group_for(sd),
                "labelI18n": sl,
                "effort": effort_for(sd),
            }
        )
    return {
        "categoryDetail": detail,
        "categoryGroup": group_for(detail),
        "categoryDetailLabelI18n": label,
        "categoryDetailSummaryI18n": summary,
        "secondaryCandidates": sec_payload,
    }


def _empty_detail_payload() -> dict[str, Any]:
    return {
        "categoryDetail": None,
        "categoryGroup": None,
        "categoryDetailLabelI18n": {"en": "", "ko": ""},
        "categoryDetailSummaryI18n": {"en": "", "ko": ""},
        "secondaryCandidates": [],
    }


def derive_proposals(
    *,
    findings: list[dict[str, Any]],
    custom_categories: dict[str, dict[str, Any]] | None = None,
    pack_id: str | None = None,
    locale: str | None = None,
) -> list[dict[str, Any]]:
    """Heuristic proposal builder. Returns a stable, deduplicated list of
    proposals ranked by failure severity (lower score → higher priority).

    Each proposal carries both legacy fields (``category``, ``categoryLabel``)
    used by existing pack filters / dashboards, **and** the new detail-layer
    fields driven by ``improvement_catalog`` (``categoryDetail``,
    ``categoryGroup``, ``effort``, ``secondaryCandidates`` …) so the UI can
    render the 52-metric × N mapping and effort badges per design §08.
    """

    loc = normalize_locale(locale)
    catalog_i18n: dict[str, Any] = {**CATEGORY_TAXONOMY_I18N}
    if custom_categories:
        for cat, raw in custom_categories.items():
            if not isinstance(raw, dict):
                continue
            if "label" in raw and isinstance(raw["label"], dict):
                catalog_i18n[cat] = {
                    "layer": raw.get("layer", "L3"),
                    "label": raw["label"],
                    "summary": raw.get("summary", {"en": "", "ko": ""}),
                }
                continue
            catalog_i18n[cat] = {
                "layer": raw.get("layer", "L3"),
                "label": {
                    "en": str(raw.get("label", cat)),
                    "ko": str(raw.get("label_ko", raw.get("label", cat))),
                },
                "summary": {
                    "en": str(raw.get("summary", "")),
                    "ko": str(raw.get("summary_ko", raw.get("summary", ""))),
                },
            }
    seen: set[str] = set()
    proposals: list[dict[str, Any]] = []
    for finding in sorted(findings, key=lambda f: float(f.get("score") or 0.0)):
        verdict = (finding.get("verdict") or "").lower()
        if verdict not in {"warn", "fail"}:
            continue
        evaluator_id = str(finding.get("evaluator_id") or finding.get("evaluatorId") or "")
        cause_code = str(
            finding.get("cause_code")
            or finding.get("causeCode")
            or finding.get("cause")
            or ""
        )
        category = _FINDING_TO_CATEGORY.get(evaluator_id)
        if category is None:
            category = _metric_evaluator_fallback_category(evaluator_id)
        if category is None:
            category = "prompt_clarity"
        m = catalog_i18n.get(category) or CATEGORY_TAXONOMY_I18N["prompt_clarity"]
        meta = {
            "layer": m["layer"],
            "label": m["label"].get(loc) or m["label"].get("en") or category,
            "summary": m["summary"].get(loc) or m["summary"].get("en") or "",
        }
        bi = {
            "labelEn": str(m["label"].get("en", "")),
            "labelKo": str(m["label"].get("ko", "")),
            "summaryEn": str(m["summary"].get("en", "")),
            "summaryKo": str(m["summary"].get("ko", "")),
        }
        key = f"{category}:{evaluator_id}"
        if key in seen:
            continue
        seen.add(key)
        reason = str(finding.get("reason") or "")
        title = str(meta.get("summary") or "Review recommended")
        act_bi = actions_both(category)
        primary_detail, secondary_details = _resolve_detail_category(
            cause_code=cause_code,
            evaluator_id=evaluator_id,
            legacy_category=category,
        )
        if primary_detail is not None:
            detail_payload = _detail_payload(primary_detail, secondary_details)
            effort = effort_for(primary_detail)
        else:
            detail_payload = _empty_detail_payload()
            # Fallback to severity-derived effort to retain the previous
            # behaviour for callers that have no detail mapping yet.
            effort = "high" if verdict == "fail" else "medium"
        proposal = {
            "category": category,
            "categoryLabel": meta.get("label", category),
            "categoryLabelI18n": {"en": bi["labelEn"], "ko": bi["labelKo"]},
            "layer": meta.get("layer", "L3"),
            "evaluatorId": evaluator_id,
            "causeCode": cause_code or None,
            "severity": "high" if verdict == "fail" else "medium",
            "score": finding.get("score"),
            "title": title,
            "titleI18n": {"en": bi["summaryEn"], "ko": bi["summaryKo"]},
            "rationale": reason or title,
            "expectedLift": 0.22 if verdict == "fail" else 0.12,
            "effort": effort,
            "effortReason": "",
            "evidence": reason,
            "actions": actions_for_category(category, loc),
            "actionsI18n": {"en": act_bi["en"], "ko": act_bi["ko"]},
        }
        proposal.update(detail_payload)
        proposals.append(proposal)
    return _filter_proposals_by_pack(proposals, pack_id)


def fallback_proposals_for_non_pass(
    *,
    final_score: float,
    final_verdict: str,
    pack_id: str | None = None,
    locale: str | None = None,
) -> list[dict[str, Any]]:
    """When aggregate verdict is not PASS but no warn/fail findings fed the taxonomy."""
    allowed = _pack_category_filter(pack_id)
    loc = normalize_locale(locale)

    def _one(cat: str) -> dict[str, Any]:
        meta = category_meta(cat, loc)
        bi = category_meta_both(cat)
        act_bi = actions_both(cat)
        title = str(meta["summary"])
        # Pick a sensible default detail for the legacy category so the UI
        # can still render an effort badge even when there is no finding.
        default_detail: str | None = None
        for detail, dmeta in CATEGORY_DETAILS.items():
            if str(dmeta.get("legacy") or "") == cat:
                default_detail = detail
                break
        if default_detail is not None:
            detail_payload = _detail_payload(default_detail, [])
            effort = effort_for(default_detail)
        else:
            detail_payload = _empty_detail_payload()
            effort = "medium"
        proposal = {
            "category": cat,
            "categoryLabel": meta["label"],
            "categoryLabelI18n": {"en": bi["labelEn"], "ko": bi["labelKo"]},
            "layer": meta.get("layer", "L3"),
            "evaluatorId": "",
            "causeCode": None,
            "severity": "medium",
            "score": final_score,
            "title": title,
            "titleI18n": {"en": bi["summaryEn"], "ko": bi["summaryKo"]},
            "rationale": fallback_rationale(final_score, final_verdict, loc),
            "expectedLift": 0.15,
            "effort": effort,
            "effortReason": "",
            "evidence": "",
            "actions": actions_for_category(cat, loc),
            "actionsI18n": {"en": act_bi["en"], "ko": act_bi["ko"]},
        }
        proposal.update(detail_payload)
        return proposal

    primary = _one("model_choice")
    if allowed is None:
        return [primary]
    if "model_choice" in allowed:
        return [primary]
    cat = sorted(allowed)[0]
    return [_one(cat)]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ImprovementService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def list(
        self,
        *,
        org_id: str,
        project_ids: list[str] | None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ImprovementDTO]:
        async with self._sf() as s:
            stmt = select(EvalImprovementRow).where(EvalImprovementRow.org_id == org_id)
            if status:
                stmt = stmt.where(EvalImprovementRow.status == status)
            rows = (
                await s.execute(stmt.order_by(EvalImprovementRow.created_at.desc()).limit(limit))
            ).scalars().all()
            if project_ids is not None:
                allowed = set(project_ids)
                rows = [r for r in rows if r.project_id is None or r.project_id in allowed]
            return [_to_dto(r) for r in rows]

    async def create(
        self,
        *,
        org_id: str,
        project_id: str | None,
        result_id: str,
        trace_id: str,
        summary: str,
        proposals: list[dict[str, Any]],
        judge_models: list[str],
        consensus_policy: str,
        agreement_ratio: float,
        judge_cost_usd: float,
        actor: str | None,
        improvement_pack: str | None = None,
        improvement_content_locale: str | None = None,
    ) -> ImprovementDTO:
        async with self._sf() as s:
            row = EvalImprovementRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                project_id=project_id,
                result_id=result_id,
                trace_id=trace_id,
                summary=summary,
                proposals_json=json.dumps(proposals, ensure_ascii=False, default=str),
                judge_models_json=json.dumps(judge_models),
                consensus_policy=consensus_policy,
                agreement_ratio=agreement_ratio,
                judge_cost_usd=judge_cost_usd,
                status="open",
                improvement_pack=improvement_pack,
                improvement_content_locale=improvement_content_locale,
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)

    async def update_status(
        self,
        *,
        org_id: str,
        improvement_id: str,
        status: str,
        project_ids: list[str] | None,
    ) -> ImprovementDTO | None:
        if status not in {
            "open",
            "accepted",
            "rejected",
            "applied",
            "dismissed",
            "defer",
        }:
            raise ValueError("invalid status")
        async with self._sf() as s:
            row = await s.get(EvalImprovementRow, improvement_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            row.status = status
            await s.commit()
            await s.refresh(row)
            return _to_dto(row)


def _to_dto(row: EvalImprovementRow) -> ImprovementDTO:
    try:
        proposals = json.loads(row.proposals_json or "[]")
    except Exception:
        proposals = []
    try:
        models = json.loads(row.judge_models_json or "[]")
    except Exception:
        models = []
    return ImprovementDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        result_id=row.result_id,
        trace_id=row.trace_id,
        summary=row.summary,
        proposals=proposals,
        judge_models=models,
        consensus_policy=row.consensus_policy,
        agreement_ratio=row.agreement_ratio,
        judge_cost_usd=row.judge_cost_usd,
        status=row.status,
        created_at=row.created_at,
        improvement_pack=getattr(row, "improvement_pack", None),
        improvement_content_locale=getattr(row, "improvement_content_locale", None),
    )
