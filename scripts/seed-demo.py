"""Seed EasyObs with a realistic demo trace set using OTLP/JSON.

Usage:
    python scripts/seed-demo.py           # 40 traces spread across 24h
    python scripts/seed-demo.py --count 100 --window-hours 6

Nothing fancy — plain stdlib so it also works without the agent SDK installed.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request

SERVICES = ("demo-agent", "rag-service", "ops-bot")
ROOT_NAMES = (
    "agent.rag.pipeline",
    "agent.llm.generate",
    "agent.rag.plan",
    "manual.verify",
    "agent.tool.search",
)
CHILD_NAMES = ("embed.query", "vector.lookup", "llm.call", "rerank", "post.process")


def _hex(n: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


def _build_trace(started_ns: int, duration_ms: int, svc: str, name: str, error: bool):
    trace_id = _hex(32)
    root_id = _hex(16)
    total_ns = duration_ms * 1_000_000
    spans = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": name,
            "kind": 1,
            "startTimeUnixNano": str(started_ns),
            "endTimeUnixNano": str(started_ns + total_ns),
            "status": {"code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_OK"},
            "attributes": [
                {"key": "o.m", "value": {"stringValue": f"demo input for {name}"}},
                {"key": "o.t", "value": {"stringValue": svc}},
                {"key": "env", "value": {"stringValue": "dev"}},
            ],
            "events": [{"name": "started", "timeUnixNano": str(started_ns)}],
        }
    ]
    child_count = random.randint(1, 3)
    cursor = started_ns + 2_000_000
    for i in range(child_count):
        piece_ms = max(5, duration_ms // (child_count + 1))
        cstart = cursor + i * piece_ms * 1_000_000
        cend = min(started_ns + total_ns - 1_000_000, cstart + piece_ms * 1_000_000)
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _hex(16),
                "parentSpanId": root_id,
                "name": random.choice(CHILD_NAMES),
                "kind": 1,
                "startTimeUnixNano": str(cstart),
                "endTimeUnixNano": str(cend),
                "status": {"code": "STATUS_CODE_OK"},
                "attributes": [
                    {"key": "o.t", "value": {"stringValue": svc}},
                ],
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": svc}}
                    ]
                },
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--window-hours", type=int, default=24)
    ap.add_argument("--base-url", default=os.environ.get("EASYOBS_BASE_URL", "http://127.0.0.1:8787"))
    ap.add_argument(
        "--token",
        default=os.environ.get("EASYOBS_INGEST_TOKEN", ""),
        help=(
            "Service ingest token (eobs_…). Mint one in the UI under "
            "Setup > Organizations > <org> > Services > <service>."
        ),
    )
    args = ap.parse_args()

    if not args.token:
        print(
            "[easyobs] missing ingest token. Pass --token <eobs_…> or set "
            "EASYOBS_INGEST_TOKEN. Mint one in the UI under "
            "Setup > Organizations > <org> > Services > <service>.",
            file=sys.stderr,
        )
        return 2

    now_ns = int(time.time() * 1e9)
    span_ns = args.window_hours * 3600 * 1_000_000_000

    sent = 0
    for _ in range(args.count):
        started_ns = now_ns - random.randint(0, span_ns)
        duration_ms = int(random.choice([40, 120, 250, 500, 900, 1400, 2100, 4500]))
        svc = random.choice(SERVICES)
        name = random.choice(ROOT_NAMES)
        error = random.random() < 0.15
        body = _build_trace(started_ns, duration_ms, svc, name, error)
        req = urllib.request.Request(
            f"{args.base_url}/otlp/v1/traces",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {args.token}",
                "Content-Type": "application/json",
            },
        )
        urllib.request.urlopen(req, timeout=4).read()
        sent += 1
    print(f"[easyobs] seeded {sent} traces into {args.base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
