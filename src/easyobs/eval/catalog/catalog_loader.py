"""Load the metric catalog (52 rows) shipped as JSON next to this module."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_metric_catalog() -> tuple[dict[str, Any], ...]:
    path = Path(__file__).with_name("eval_metric_catalog_v1.json")
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    rows = root.get("metrics")
    if not isinstance(rows, list):
        return ()
    return tuple(m for m in rows if isinstance(m, dict))


def metric_row_by_id() -> dict[str, dict[str, Any]]:
    return {str(m.get("id") or ""): dict(m) for m in load_metric_catalog() if m.get("id")}


def judge_metric_ids() -> frozenset[str]:
    return frozenset(str(m["id"]) for m in load_metric_catalog() if str(m.get("kind")) == "judge")


def human_metric_ids() -> frozenset[str]:
    return frozenset(str(m["id"]) for m in load_metric_catalog() if str(m.get("kind")) == "human")
