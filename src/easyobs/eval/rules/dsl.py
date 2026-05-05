"""Tiny, **safe** expression DSL for rule-based evaluators.

We deliberately do *not* expose Python's ``eval`` even with a restricted
namespace — the AST allow-list approach is the only safe one when an
operator can paste profile YAML through the UI. The grammar is a strict
subset of Python expressions:

  literal | identifier | unary | binary | call | subscript | attribute

Identifiers resolve through a :class:`RuleContext` that exposes the trace
roll-up (``llm`` summary, span counts, cost, etc.) plus a small library of
helper callables (``len``, ``regex_match``, ``json_path``, ``contains``).
Anything not on the allow-list raises :class:`DSLError`.

The evaluator results are normalised so the calling layer can treat them
uniformly: every rule returns ``(score in [0, 1], passed: bool, reason: str)``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

_MAX_NODES = 256
_MAX_DEPTH = 16


class DSLError(ValueError):
    """Raised for any unsafe construct or runtime failure inside the DSL."""


# ---------------------------------------------------------------------------
# Helper callables exposed to expressions
# ---------------------------------------------------------------------------


def _regex_match(pattern: str, value: Any) -> bool:
    if value is None:
        return False
    try:
        return re.search(pattern, str(value)) is not None
    except re.error as exc:  # invalid regex from an operator — surface clearly
        raise DSLError(f"invalid regex {pattern!r}: {exc}") from exc


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None or needle is None:
        return False
    try:
        return str(needle) in str(haystack)
    except Exception:
        return False


def _json_path(value: Any, path: str) -> Any:
    """Tiny dotted-path lookup — safer than relying on jsonpath libs."""
    cur = value
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def _safe_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return len(value)
    return len(str(value))


def _word_count(value: Any) -> int:
    if value is None:
        return 0
    return len(re.findall(r"\S+", str(value)))


_HELPERS: dict[str, Callable[..., Any]] = {
    "len": _safe_len,
    "wc": _word_count,
    "regex_match": _regex_match,
    "contains": _contains,
    "json_path": _json_path,
    "lower": lambda v: (str(v) if v is not None else "").lower(),
    "upper": lambda v: (str(v) if v is not None else "").upper(),
    "min": min,
    "max": max,
    "abs": abs,
    "round": lambda v, n=2: round(float(v), int(n)),
    "bool": bool,
    "str": lambda v: "" if v is None else str(v),
    "int": lambda v: int(v) if v is not None else 0,
    "float": lambda v: float(v) if v is not None else 0.0,
    "is_empty": lambda v: v is None or (hasattr(v, "__len__") and _safe_len(v) == 0),
    "any": any,
    "all": all,
}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class RuleContext:
    """Read-only state visible to every DSL expression.

    The same context object is passed to every rule in a profile, so we keep
    it tiny: just the trace summary plus opaque ``extra`` for evaluator-
    specific overrides (e.g. golden item attached for retrieval rules)."""

    trace: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    spans: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_namespace(self) -> dict[str, Any]:
        return {
            "trace": self.trace,
            "summary": self.summary,
            "spans": self.spans,
            "extra": self.extra,
            "llm": self.summary,
            **self.extra,
        }


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------


_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Attribute,
    ast.Subscript,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.IfExp,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.Pow,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Slice,
)


def _walk_check(tree: ast.AST) -> None:
    """First-pass safety walk: limit node count and reject anything that
    is not strictly on the allow-list."""

    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_NODES:
        raise DSLError("expression too large")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise DSLError(f"unsupported expression: {type(node).__name__}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise DSLError("private attribute access is not allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise DSLError("only top-level helper calls are allowed")
            if node.func.id not in _HELPERS:
                raise DSLError(f"unknown helper {node.func.id!r}")


def _depth(node: ast.AST, current: int = 0) -> int:
    if current > _MAX_DEPTH:
        raise DSLError("expression nested too deep")
    children = list(ast.iter_child_nodes(node))
    if not children:
        return current
    return max(_depth(child, current + 1) for child in children)


def evaluate_dsl(expression: str, ctx: RuleContext) -> Any:
    """Compile and execute ``expression`` against ``ctx``.

    Returns whatever value the expression produces; callers wrap that into
    the rule's score / verdict shape.
    """

    if not expression or not expression.strip():
        raise DSLError("empty expression")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise DSLError(f"syntax error: {exc.msg}") from exc
    _walk_check(tree)
    _depth(tree)
    namespace = ctx.to_namespace()
    namespace_with_helpers = {**_HELPERS, **namespace}
    code = compile(tree, "<dsl>", "eval")
    try:
        # Globals are restricted (no __builtins__ exposure); locals carry
        # the namespace + helper bindings together so identifier resolution
        # is uniform.
        return eval(  # noqa: S307 — explicitly sandboxed AST
            code, {"__builtins__": {}}, namespace_with_helpers
        )
    except DSLError:
        raise
    except Exception as exc:
        raise DSLError(f"runtime error: {exc}") from exc


def coerce_score(value: Any) -> float:
    """Normalise rule output to a 0..1 score."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        if v < 0.0:
            return 0.0
        if v > 1.0 and v <= 100.0:  # tolerate percentages
            return min(v / 100.0, 1.0)
        if v > 1.0:
            return 1.0
        return v
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        items = list(value)
        if not items:
            return 0.0
        return sum(coerce_score(x) for x in items) / len(items)
    return 0.0
