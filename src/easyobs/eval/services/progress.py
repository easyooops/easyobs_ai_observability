"""In-process progress broker for long-running eval workers (12 §5).

The Golden Regression Runner and the Synthesizer both stream progress to
the UI over SSE so an operator can close the browser, come back later,
and still see what is going on. We keep the implementation deliberately
simple — a per-key ``asyncio.Queue`` that publishers append to and SSE
handlers drain — so it works in both the SQLite single-process setup
and the post-1.0 worker process model. When the commercial worker is
deployed, the broker is replaced with a Redis pub/sub backend; the
public ``ProgressBroker`` interface stays identical.

Each event is a plain ``dict[str, Any]`` so the SSE handler can JSON-
encode it without conversion. The ``status`` field is always present so
the UI can branch on terminal states (``done`` / ``failed`` /
``cancelled``) and stop reconnecting when the stream completes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, AsyncIterator

_log = logging.getLogger("easyobs.eval.progress")


class ProgressBroker:
    """Fan-out broker keyed by ``"<kind>:<id>"`` (eg. ``"golden_run:abc"``)."""

    def __init__(self, *, max_queue: int = 256) -> None:
        self._max = max_queue
        # Multiple concurrent SSE listeners are fine — each gets its own
        # queue. ``defaultdict`` keeps the registration race-free without
        # a lock because Python guarantees atomic dict insert under the GIL.
        self._listeners: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        # Replay buffer per key — last N events. SSE clients reconnecting
        # mid-run get the buffered tail before live events resume so the
        # UI can rehydrate state without re-reading the DB.
        self._tail: dict[str, list[dict[str, Any]]] = defaultdict(list)

    @staticmethod
    def key(kind: str, ident: str) -> str:
        return f"{kind}:{ident}"

    def publish(self, *, kind: str, ident: str, event: dict[str, Any]) -> None:
        k = self.key(kind, ident)
        # Never let a slow listener back-pressure the worker. We drop the
        # oldest event for the listener whose queue is full — the periodic
        # worker is also persisting status to DB, so missed SSE frames are
        # not data loss, just a rendering hiccup the UI can recover from.
        for q in list(self._listeners.get(k, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(event)
                except Exception:
                    _log.debug("dropping progress event for slow listener", extra={"key": k})
        # Keep last N events as the replay buffer.
        buf = self._tail[k]
        buf.append(event)
        if len(buf) > 32:
            del buf[: len(buf) - 32]

    async def stream(
        self, *, kind: str, ident: str, replay: bool = True
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator that yields events until a terminal status
        (``done``/``failed``/``cancelled``) is observed."""

        k = self.key(kind, ident)
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max)
        self._listeners[k].append(q)
        try:
            if replay:
                for ev in list(self._tail.get(k, ())):
                    yield ev
                    if _is_terminal(ev):
                        return
            while True:
                ev = await q.get()
                yield ev
                if _is_terminal(ev):
                    return
        finally:
            try:
                self._listeners[k].remove(q)
            except ValueError:
                pass
            if not self._listeners[k]:
                # Free the per-key list only when nothing is listening.
                self._listeners.pop(k, None)


def _is_terminal(event: dict[str, Any]) -> bool:
    return str(event.get("status", "")).lower() in {"done", "failed", "cancelled"}
