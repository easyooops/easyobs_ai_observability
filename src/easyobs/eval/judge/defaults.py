"""Default LLM-judge wording shared by providers and profile templates."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_JUDGE_SYSTEM_PROMPT = (
    'You are an evaluation judge. Read the rubric and the trace excerpt. '
    'Reply with strict JSON: {"score": <0.0-1.0>, "verdict": "pass|warn|fail", '
    '"reason": "..."}.'
)

# Placeholders: {rubric_id}, {rubric}, {context_json}
DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE = (
    "Evaluate using the rubric and trace context below. "
    'Respond with strict JSON only matching: '
    '{"score": <number 0-1>, "verdict": "pass"|"warn"|"fail", "reason": "<short>"}.\n\n'
    "rubric_id: {rubric_id}\n\n"
    "rubric:\n{rubric}\n\n"
    "context_json:\n{context_json}\n"
)


def context_json(context: dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False, default=str, sort_keys=True)


def build_default_user_message(
    *, rubric_id: str, rubric: str, context: dict[str, Any]
) -> str:
    return apply_user_template(
        DEFAULT_JUDGE_USER_MESSAGE_TEMPLATE,
        rubric_id=rubric_id,
        rubric=rubric,
        context_json=context_json(context),
    )


def apply_user_template(
    template: str,
    *,
    rubric_id: str,
    rubric: str,
    context_json: str,
) -> str:
    """Substitute placeholders without str.format (rubric text may contain ``{``)."""
    return (
        template.replace("{context_json}", context_json)
        .replace("{rubric_id}", rubric_id)
        .replace("{rubric}", rubric)
    )


def build_profile_user_message(
    *,
    rubric_id: str,
    rubric: str,
    context: dict[str, Any],
    template_override: str | None,
) -> str:
    raw = (template_override or "").strip()
    ctx = context_json(context)
    if not raw:
        return build_default_user_message(rubric_id=rubric_id, rubric=rubric, context=context)
    return apply_user_template(raw, rubric_id=rubric_id, rubric=rubric, context_json=ctx)
