"""Read-only catalog over the built-in rule evaluators."""

from __future__ import annotations

from typing import Any

from easyobs.eval.catalog.catalog_loader import metric_row_by_id
from easyobs.eval.rules import BUILTIN_EVALUATORS, get_builtin


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if not str(k).startswith("_")}


class EvaluatorCatalogService:
    """No DB state — wraps the static catalog so the API layer reads it
    through a service the same way it reads everything else."""

    def list(self) -> list[dict[str, Any]]:
        meta = metric_row_by_id()
        out: list[dict[str, Any]] = []
        for e in BUILTIN_EVALUATORS:
            row = meta.get(e.id, {})
            item: dict[str, Any] = {
                "id": e.id,
                "name": e.name,
                "category": e.category,
                "description": e.description,
                "layer": e.layer,
                "defaultParams": _sanitize_params(dict(e.default_params)),
            }
            if row:
                item["metricCode"] = row.get("code")
                item["evaluationMode"] = row.get("mode")
                item["gt"] = row.get("gt")
                item["causeCode"] = row.get("causeCode")
                item["metricKind"] = row.get("kind")
                if row.get("ruleTarget"):
                    item["ruleTarget"] = row.get("ruleTarget")
                if row.get("judgeDimension"):
                    item["judgeDimension"] = row.get("judgeDimension")
            out.append(item)
        return out

    def get(self, evaluator_id: str) -> dict[str, Any] | None:
        spec = get_builtin(evaluator_id)
        if spec is None:
            return None
        row = metric_row_by_id().get(evaluator_id, {})
        item: dict[str, Any] = {
            "id": spec.id,
            "name": spec.name,
            "category": spec.category,
            "description": spec.description,
            "layer": spec.layer,
            "defaultParams": _sanitize_params(dict(spec.default_params)),
        }
        if row:
            item["metricCode"] = row.get("code")
            item["evaluationMode"] = row.get("mode")
            item["gt"] = row.get("gt")
            item["causeCode"] = row.get("causeCode")
            item["metricKind"] = row.get("kind")
        return item
