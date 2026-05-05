"""Background demo-traffic generator.

The seeder (`services.mock_seed`) only runs once on first boot and lays
down a static fan of synthetic traces. As soon as wall-clock time moves
past those timestamps the "Last 1h / 6h" workspace windows go empty,
which is the most common WTF moment for new users on a long-running dev
server. To keep the freshest buckets populated, this module spawns a
single asyncio task that ingests **one synthetic trace at a time on a
randomised cadence** while the API process is alive.

Design notes:

- Reuses the same trace builder as the offline seeder so the analytics
  pipeline sees one shape for both initial fan-out and live drip.
- Owns no DB session of its own -- it routes through the live
  ``TraceIngestService``, exactly the same path real OTLP traffic would
  take. The only difference is the source.
- Activity targets the **demo service** that the seeder already created.
  If the demo service does not exist (e.g. ``EASYOBS_SEED_MOCK_DATA`` was
  off) the loop self-disables on the first iteration and exits cleanly.
- Cancellable: ``asyncio.CancelledError`` from the lifespan teardown is
  re-raised so the task disappears on shutdown without leaking.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

from easyobs.services.directory import DirectoryService
from easyobs.services.mock_seed import (
    _build_trace,
    _existing_demo_service,
    _hex,
)
from easyobs.services.trace_ingest import TraceIngestService

_log = logging.getLogger("easyobs.live")


async def run_mock_live_traffic(
    *,
    directory: DirectoryService,
    trace_ingest: TraceIngestService,
    interval_sec: float,
    burst_window_sec: float,
) -> None:
    """Drip one synthetic trace into the demo service every ~interval_sec.

    The next trace's start time is anchored to ``datetime.now(UTC)`` and
    jittered backwards by up to ``burst_window_sec`` so successive traces
    don't land on the exact same nanosecond (which would distort
    Sessions / Spans aggregations). Cadence itself is jittered ±30% so
    the resulting time series doesn't look unnaturally clean.
    """
    default_org = await directory.ensure_default_org()
    service_id = await _existing_demo_service(directory, default_org.id)
    if not service_id:
        _log.info(
            "live.skip demo service not found (run with EASYOBS_SEED_MOCK_DATA=true "
            "first); live traffic disabled"
        )
        return

    _log.info(
        "live.start interval=%.1fs burst_window=%.1fs service_id=%s",
        interval_sec,
        burst_window_sec,
        service_id[:8],
    )

    session_pool = [f"live-sess-{_hex(8)}" for _ in range(8)]
    turn = 0
    sent = 0
    burst_ns = max(1, int(burst_window_sec * 1_000_000_000))

    try:
        while True:
            try:
                now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
                sid = random.choice(session_pool)
                body = _build_trace(
                    now_ns,
                    burst_ns,
                    session_id=sid,
                    turn_index=turn,
                )
                await trace_ingest.ingest(
                    payload=body,
                    content_type="application/json",
                    service_id=service_id,
                )
                turn += 1
                sent += 1
                if sent % 50 == 0:
                    _log.info("live.tick total=%d", sent)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("live.trace failed; continuing")

            # Jitter the cadence ±30% so the live-traffic chart doesn't
            # show a perfectly periodic comb pattern.
            delay = max(
                1.0,
                random.uniform(interval_sec * 0.7, interval_sec * 1.3),
            )
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        _log.info("live.stop total=%d", sent)
        raise
