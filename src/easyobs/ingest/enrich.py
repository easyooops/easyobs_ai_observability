"""Span enrichment steps applied just before blob persistence.

The agent SDK only ships **observation** (model name, token counts,
query, response).  Derived/operator-side fields such as USD cost are
computed here so that prices can be updated — or pricing tables patched
— without touching any instrumented service.
"""

from __future__ import annotations

from typing import Any

from easyobs.services import pricing


def _find_attr(attrs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for a in attrs:
        if a.get("key") == key:
            return a
    return None


def _attr_str(attrs: list[dict[str, Any]], key: str) -> str | None:
    a = _find_attr(attrs, key)
    if not a:
        return None
    v = a.get("value") or {}
    if "stringValue" in v:
        return str(v["stringValue"])
    return None


def _attr_int(attrs: list[dict[str, Any]], key: str) -> int | None:
    a = _find_attr(attrs, key)
    if not a:
        return None
    v = a.get("value") or {}
    raw = v.get("intValue") if "intValue" in v else v.get("doubleValue")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def enrich_with_price(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill in ``o.price`` on LLM spans that reported model + tokens.

    Skipped when:

    - ``o.kind`` is not ``"llm"``.
    - The span already carries ``o.price`` (agent-side override wins).
    - Model or token counts are missing — we never fabricate costs.
    """
    for span in lines:
        attrs = span.get("attributes") or []
        if not isinstance(attrs, list):
            continue
        if _attr_str(attrs, "o.kind") != "llm":
            continue
        if _find_attr(attrs, "o.price") is not None:
            continue
        model = _attr_str(attrs, "o.model")
        price = pricing.estimate_cost(
            model,
            _attr_int(attrs, "o.tok.in"),
            _attr_int(attrs, "o.tok.out"),
        )
        if price is None:
            continue
        attrs.append({"key": "o.price", "value": {"doubleValue": float(price)}})
        span["attributes"] = attrs
    return lines
