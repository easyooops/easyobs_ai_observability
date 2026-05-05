"""Server-side LLM price lookup with pluggable external sources.

Pricing is **the collector's responsibility**, not the agent's.  Keeping it
here means operators can swap the data source (or patch prices) in a
single place without redeploying every instrumented service.

Source priority (first hit wins):

1. :func:`register` — explicit operator overrides (contract rates,
   private models).  Highest priority.
2. Auto-detected external packages:

   - `tokencost <https://github.com/AgentOps-AI/tokencost>`_ — compact,
     pure-Python, mirrors ``litellm``'s public price JSON.
   - `litellm <https://github.com/BerriAI/litellm>`_ — broadest model
     coverage in the ecosystem.

3. A minimal bundled table (offline fallback so local dev and air-gapped
   installs keep working).

Source selection is driven by the ``EASYOBS_PRICING_SOURCE`` env var
(``auto`` | ``tokencost`` | ``litellm`` | ``builtin``) — see
:func:`set_source`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

__all__ = [
    "ModelPrice",
    "active_source",
    "estimate_cost",
    "get_price",
    "register",
    "set_source",
]

log = logging.getLogger("easyobs.pricing")

Source = Literal["auto", "tokencost", "litellm", "builtin"]


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token USD price pair."""

    input_per_million: float
    output_per_million: float

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (
            tokens_in * self.input_per_million / 1_000_000.0
            + tokens_out * self.output_per_million / 1_000_000.0
        )


# ---------------------------------------------------------------------------
# Built-in fallback table (kept small; external sources are the source of
# truth for day-to-day OpenAI/Anthropic/Google/Mistral/etc.).
# ---------------------------------------------------------------------------
_BUILTIN: dict[str, ModelPrice] = {
    "gpt-4o": ModelPrice(5.00, 15.00),
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "gpt-4-turbo": ModelPrice(10.00, 30.00),
    "gpt-4": ModelPrice(30.00, 60.00),
    "gpt-3.5-turbo": ModelPrice(0.50, 1.50),
    "o1": ModelPrice(15.00, 60.00),
    "o1-mini": ModelPrice(3.00, 12.00),
    "o3-mini": ModelPrice(1.10, 4.40),
    "claude-3-opus": ModelPrice(15.00, 75.00),
    "claude-3-sonnet": ModelPrice(3.00, 15.00),
    "claude-3-haiku": ModelPrice(0.25, 1.25),
    "claude-3-5-sonnet": ModelPrice(3.00, 15.00),
    "claude-3-5-haiku": ModelPrice(1.00, 5.00),
    "gemini-1.5-pro": ModelPrice(1.25, 5.00),
    "gemini-1.5-flash": ModelPrice(0.075, 0.30),
    "gemini-2.0-flash": ModelPrice(0.10, 0.40),
    "mistral-large": ModelPrice(2.00, 6.00),
    "mistral-small": ModelPrice(0.20, 0.60),
    "qwen2.5-14b": ModelPrice(0.05, 0.20),
    "qwen2.5-7b": ModelPrice(0.0, 0.0),
    "llama-3-8b": ModelPrice(0.0, 0.0),
    "llama-3-70b": ModelPrice(0.0, 0.0),
    "local-llm-7b": ModelPrice(0.0, 0.0),
    "local": ModelPrice(0.0, 0.0),
}

_USER: dict[str, ModelPrice] = {}

_source: Source = "auto"
_UNSET: object = object()
_cached_external: Callable[[str, int, int], float | None] | None | object = _UNSET
_cached_name: str = "builtin"


def _normalise(model: str) -> str:
    name = model.strip().lower()
    if "/" in name:
        name = name.split("/", 1)[1]
    return name


def _match_table(name: str, table: dict[str, ModelPrice]) -> ModelPrice | None:
    if name in table:
        return table[name]
    best: tuple[int, ModelPrice] | None = None
    for key, price in table.items():
        if name.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), price)
    return best[1] if best else None


def _tokencost_source() -> Callable[[str, int, int], float | None] | None:
    try:
        from tokencost import calculate_cost_by_tokens  # type: ignore
    except ImportError:
        return None

    def _call(model: str, t_in: int, t_out: int) -> float | None:
        try:
            c_in = calculate_cost_by_tokens(int(t_in), model, "input")
            c_out = calculate_cost_by_tokens(int(t_out), model, "output")
        except Exception:  # noqa: BLE001 — tokencost raises KeyError for unknown models
            return None
        try:
            return round(float(c_in) + float(c_out), 6)
        except (TypeError, ValueError):
            return None

    return _call


def _litellm_source() -> Callable[[str, int, int], float | None] | None:
    try:
        from litellm import cost_per_token  # type: ignore
    except ImportError:
        return None

    def _call(model: str, t_in: int, t_out: int) -> float | None:
        try:
            c_in, c_out = cost_per_token(
                model=model,
                prompt_tokens=int(t_in),
                completion_tokens=int(t_out),
            )
        except Exception:  # noqa: BLE001 — litellm raises for unknown models
            return None
        try:
            return round(float(c_in) + float(c_out), 6)
        except (TypeError, ValueError):
            return None

    return _call


def _resolve_external() -> Callable[[str, int, int], float | None] | None:
    global _cached_external, _cached_name
    if _cached_external is not _UNSET:
        return _cached_external  # type: ignore[return-value]

    chosen: Callable[[str, int, int], float | None] | None = None
    chosen_name = "builtin"
    if _source == "auto":
        chosen = _tokencost_source()
        if chosen is not None:
            chosen_name = "tokencost"
        else:
            chosen = _litellm_source()
            if chosen is not None:
                chosen_name = "litellm"
    elif _source == "tokencost":
        chosen = _tokencost_source()
        if chosen is None:
            log.warning(
                "EASYOBS_PRICING_SOURCE=tokencost but the `tokencost` "
                "package is not installed; falling back to the built-in table."
            )
        else:
            chosen_name = "tokencost"
    elif _source == "litellm":
        chosen = _litellm_source()
        if chosen is None:
            log.warning(
                "EASYOBS_PRICING_SOURCE=litellm but `litellm` is not "
                "installed; falling back to the built-in table."
            )
        else:
            chosen_name = "litellm"

    _cached_external = chosen
    _cached_name = chosen_name
    return chosen


def set_source(source: Source) -> None:
    """Choose the active pricing source."""
    global _source, _cached_external, _cached_name
    if source not in ("auto", "tokencost", "litellm", "builtin"):
        raise ValueError(f"unknown pricing source: {source!r}")
    _source = source
    _cached_external = _UNSET
    _cached_name = "builtin"


def active_source() -> str:
    """Return the name of the source actually in use now."""
    if _source == "builtin":
        return "builtin"
    _resolve_external()
    return _cached_name


def register(
    model: str,
    *,
    input_per_million: float,
    output_per_million: float,
) -> None:
    """Register or override a model's price (highest priority)."""
    _USER[_normalise(model)] = ModelPrice(
        input_per_million=float(input_per_million),
        output_per_million=float(output_per_million),
    )


def get_price(model: str | None) -> ModelPrice | None:
    """Return the stored :class:`ModelPrice` from overrides or built-ins."""
    if not model:
        return None
    name = _normalise(model)
    return _match_table(name, _USER) or _match_table(name, _BUILTIN)


def estimate_cost(
    model: str | None,
    tokens_in: int | float | None,
    tokens_out: int | float | None,
) -> float | None:
    """Return the USD cost or ``None`` when inputs are missing/unknown."""
    if not model or tokens_in is None or tokens_out is None:
        return None
    try:
        t_in = int(tokens_in)
        t_out = int(tokens_out)
    except (TypeError, ValueError):
        return None
    name = _normalise(model)

    hit = _match_table(name, _USER)
    if hit is not None:
        return round(hit.cost(t_in, t_out), 6)

    if _source != "builtin":
        external = _resolve_external()
        if external is not None:
            result = external(model, t_in, t_out)
            if result is not None:
                return round(float(result), 6)
            if _source == "auto":
                alt = (
                    _litellm_source() if _cached_name == "tokencost" else _tokencost_source()
                )
                if alt is not None:
                    result = alt(model, t_in, t_out)
                    if result is not None:
                        return round(float(result), 6)

    hit = _match_table(name, _BUILTIN)
    if hit is not None:
        return round(hit.cost(t_in, t_out), 6)
    return None
