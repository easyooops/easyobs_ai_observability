"""Third-party framework bridges (LangChain, …).

These modules are imported lazily so the core EasyObs agent SDK works
without heavy optional dependencies installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["EasyObsCallbackHandler"]


def __getattr__(name: str) -> Any:  # pragma: no cover - tiny shim
    if name == "EasyObsCallbackHandler":
        from easyobs_agent.callbacks.langchain import EasyObsCallbackHandler

        return EasyObsCallbackHandler
    raise AttributeError(f"module 'easyobs_agent.callbacks' has no attribute {name!r}")


if TYPE_CHECKING:  # keep IDE hints without triggering langchain import
    from easyobs_agent.callbacks.langchain import (  # noqa: F401
        EasyObsCallbackHandler,
    )
