"""Self-authored prompt templates for the LLM-driven Synthesizer.

Lifted out of :mod:`easyobs.eval.services.synthesizer` so prompt changes
can be reviewed without touching the worker logic.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# RAG-aware mode (12 §10.2)
# ---------------------------------------------------------------------------

# System prompt is shared between RAG-aware and trace-driven modes — it
# defines the strict-JSON contract every candidate must follow.
SYSTEM_PROMPT = (
    "You generate evaluation candidates for a RAG / agent quality "
    "benchmark. Output strict JSON with the schema below. Never invent "
    "facts outside the supplied source. Output one candidate per call."
)


def build_system_prompt(custom_prompt: str | None = None) -> str:
    """Combine the base system prompt with an optional user-supplied
    domain-specific instruction. When ``custom_prompt`` is given it is
    appended after a separator so the LLM receives domain guidance
    alongside the structural contract."""
    if not custom_prompt or not custom_prompt.strip():
        return SYSTEM_PROMPT
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"--- Domain guidance (provided by the operator) ---\n"
        f"{custom_prompt.strip()}"
    )

# Doc-grounded user message: render with ``.format(doc_id=..., doc_text=...)``.
# ``doc_text`` is truncated by the caller to keep prompt cost bounded.
RAG_USER_TEMPLATE = (
    'Source document id: "{doc_id}"\n'
    "Source text:\n<<<\n{doc_text}\n>>>\n\n"
    "Generate one candidate:\n"
    '{{"queryText": "<natural-language question this doc answers>", '
    '"intent": "<short slug>", '
    '"expectedAnswer": "<answer grounded only in the source>", '
    '"mustInclude": ["<critical phrase>", ...], '
    '"citationsExpected": ["{doc_id}"], '
    '"difficulty": "easy|medium|hard"}}'
)


# ---------------------------------------------------------------------------
# Trace-driven mode (12 §10.3)
# ---------------------------------------------------------------------------

# Keep the same system prompt so the JSON shape is consistent across
# modes — only the user payload differs.
TRACE_USER_TEMPLATE = (
    'Trace id: "{trace_id}"\n'
    "Observed query:\n<<<\n{query_text}\n>>>\n\n"
    'Observed response:\n<<<\n{response_text}\n>>>\n\n'
    "Generate one candidate (preserve user intent; do not invent facts):\n"
    '{{"queryText": "<paraphrased natural-language question>", '
    '"intent": "<short slug>", '
    '"expectedAnswer": "<answer that captures the response\'s ground truth>", '
    '"mustInclude": ["<critical phrase>", ...], '
    '"citationsExpected": [], '
    '"difficulty": "easy|medium|hard"}}'
)
