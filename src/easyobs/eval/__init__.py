"""EasyObs Quality (evaluation) module.

Everything under ``easyobs.eval`` is opt-in and isolated from the live
ingest / query path. Importing this package has no side effects; the
http_app wires the routers and background workers explicitly when the
``EASYOBS_EVAL_ENABLED`` flag is on.

Sub-packages:

- ``types`` — strict enums shared by services / API / tests.
- ``rules`` — Rule-based evaluator catalog and a tiny safe expression DSL.
- ``judge`` — Multi-judge orchestrator + provider adapters (mock + OpenAI).
- ``services`` — Async business services consumed by the API router.
- ``goldensets`` — Golden set authoring (manual / auto-discover / GT label).
"""

from easyobs.eval.types import (
    EvaluatorKind,
    GoldenLayer,
    SourceKind,
    TriggerLane,
    Verdict,
)

__all__ = [
    "EvaluatorKind",
    "GoldenLayer",
    "SourceKind",
    "TriggerLane",
    "Verdict",
]
