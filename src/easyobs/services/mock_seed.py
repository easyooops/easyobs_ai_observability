"""First-boot mock data seeder.

Enabled via ``EASYOBS_SEED_MOCK_DATA=true`` in the environment, this module
populates the default ``administrator`` organization with a ``demo`` service
and a configurable number of synthetic OTLP traces so an MVP server can be
demoed end-to-end without any manual setup (sign-up → mint token → run
seed script).

Design notes:

- The seeder is **idempotent and safe**. It never runs if any trace already
  exists in the catalog, so it cannot overwrite real data. Re-enabling the
  flag after the catalog has been wiped (``easyobs reset-data``) re-seeds.
- Synthetic traces are routed through ``TraceIngestService.ingest`` exactly
  like real OTLP/HTTP traffic, so the same flatten / enrich / blob-write
  pipeline is exercised. No bespoke storage path.
- The owning ``demo`` service belongs to the default org. ``created_by`` is
  set to a sentinel ``"system"`` because the bootstrapped super admin may
  not exist yet at this point in the lifespan.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from easyobs.db.models import ServiceRow, TraceIndexRow
from easyobs.eval.services.judge_models import JudgeModelService
from easyobs.eval.services.profiles import ProfileService
from easyobs.services.directory import DirectoryService
from easyobs.services.trace_ingest import TraceIngestService

_log = logging.getLogger("easyobs.seed")

# ASCII name so Windows consoles without UTF-8 show the label correctly in logs.
_DEMO_RULE_PROFILE_NAME = "Demo - Rule baseline"

DEMO_SERVICE_NAME = "demo"
DEMO_SERVICE_SLUG = "demo"
DEMO_SERVICE_DESCRIPTION = (
    "Auto-generated demo service. Created on first boot when "
    "EASYOBS_SEED_MOCK_DATA=true. Safe to delete once you have your own "
    "services; the seeder will not regenerate it as long as any traces "
    "exist anywhere in the catalog."
)

# Realistic-ish vocabulary for synthetic traces. Borrowed from the offline
# seed-demo.py so the resulting dashboards mirror what a small RAG / agent
# workload would look like.
_ROOT_NAMES = (
    "agent.rag.pipeline",
    "agent.llm.generate",
    "agent.rag.plan",
    "manual.verify",
    "agent.tool.search",
)
_LLM_MODELS = (
    ("gpt-4o-mini", "openai"),
    ("gpt-4o", "openai"),
    ("claude-3-5-sonnet", "anthropic"),
    ("llama-3.1-70b", "meta"),
)
_TOOLS = ("vector.search", "sql.query", "http.fetch", "format.markdown")
_QUERIES = (
    "summarise yesterday's incidents",
    "draft a status update for stakeholders",
    "what changed in the deployment this morning?",
    "find similar past tickets to INC-4821",
    "explain this stack trace",
    "rewrite this prompt to reduce tokens",
    "compare p95 latency across regions",
    "which dependency upgrade triggered the rollback?",
    "draft an incident timeline for leadership",
)
_QUERIES_KO = (
    "어제 장애 요약해 줘",
    "이번 배포에서 바뀐 설정이 뭐야?",
    "스택 트레이스 원인 설명해 줘",
    "유사 티켓 INC-4821 찾아줘",
    "토큰 줄이면서 품질 유지하려면?",
    "리더용 상태 업데이트 초안 작성",
)
_RESPONSES = (
    "here is a concise summary…",
    "draft attached — three bullet points covering scope, impact, ETA.",
    "no schema migrations; only the worker queue depth changed.",
    "found 3 close matches; INC-4392 looks most relevant.",
    "the NPE happens because the auth header is dropped before retry.",
    "trimmed system prompt by 40% with no quality drop on test set.",
    "p95 spiked in eu-west after the cache rollout; us-east stayed flat.",
    "the ORM bump changed lazy-loading; that matches the rollback commit.",
    "timeline: 09:12 detector, 09:18 mitigation, 09:45 verified.",
)
_RESPONSES_KO = (
    "간단 요약입니다. 영향 범위와 후속 조치를 정리했습니다.",
    "스키마 변경은 없고 워커 큐 깊이만 변했습니다.",
    "유사 사례 3건 중 INC-4392가 가장 근접합니다.",
    "NPE는 재시도 전에 인증 헤더가 빠져서 발생했습니다.",
    "테스트 세트 기준 프롬프트를 40% 줄였고 품질은 유지됐습니다.",
)


def _hex(n: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


def _attr_str(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _attr_int(key: str, value: int) -> dict[str, Any]:
    return {"key": key, "value": {"intValue": value}}


def _build_trace(
    now_ns: int,
    window_ns: int,
    *,
    session_id: str,
    turn_index: int,
) -> dict[str, Any]:
    """Return one OTLP/JSON ExportTraceServiceRequest body.

    The shape is intentionally identical to what
    ``easyobs_agent.init(...).traced(...)`` produces in the wild so the
    flatten + enrich + analytics layers all light up exactly the same way.
    ``session_id`` is shared across multiple traces so Sessions / analytics
    see realistic multi-turn groups; ``turn_index`` rotates Q/A pairs.
    """
    # Triangular distribution biased toward "now" (mode=0) so the most
    # recent buckets always have visible activity even when the user only
    # looks at a 1h / 6h window. With a 240h window this puts ~25% of
    # traces in the last 24h, ~6% in the last 6h, ~1% in the last 1h --
    # enough to never display an empty chart on first boot.
    started_ns = now_ns - int(random.triangular(0, window_ns, 0))
    duration_ms = int(random.choice([40, 120, 250, 500, 900, 1400, 2100, 4500]))
    total_ns = duration_ms * 1_000_000
    error = random.random() < 0.12

    trace_id = _hex(32)
    root_id = _hex(16)
    name = random.choice(_ROOT_NAMES)
    session = session_id
    user = random.choice(("alice", "bob", "carol", "dave", "eve"))
    request = f"req-{_hex(8)}"
    qi = (turn_index + random.randint(0, 2)) % len(_QUERIES)
    ri = (qi + random.randint(0, 2)) % len(_RESPONSES)
    query = _QUERIES[qi]
    response = _RESPONSES[ri]
    model, vendor = random.choice(_LLM_MODELS)
    tokens_in = random.randint(120, 1800)
    tokens_out = random.randint(60, 900)

    flavor = random.choices(
        ["plain", "short", "ko_ok", "ko_en", "email", "swear", "heavy_tok"],
        weights=[0.48, 0.09, 0.1, 0.11, 0.07, 0.07, 0.08],
        k=1,
    )[0]
    if flavor == "short":
        response = "ok."
    elif flavor == "ko_ok":
        qk = random.randint(0, len(_QUERIES_KO) - 1)
        query = _QUERIES_KO[qk]
        response = _RESPONSES_KO[qk % len(_RESPONSES_KO)]
    elif flavor == "ko_en":
        query = random.choice(_QUERIES_KO)
        response = random.choice(_RESPONSES)
    elif flavor == "email":
        response = f"{random.choice(_RESPONSES)} Escalation: oncall@example.com"
    elif flavor == "swear":
        response = f"{random.choice(_RESPONSES)} — this rollout is damn fragile."
    elif flavor == "heavy_tok":
        tokens_in = random.randint(3200, 5200)
        tokens_out = random.randint(1200, 2400)

    spans: list[dict[str, Any]] = [
        {
            "traceId": trace_id,
            "spanId": root_id,
            "name": name,
            "kind": 1,
            "startTimeUnixNano": str(started_ns),
            "endTimeUnixNano": str(started_ns + total_ns),
            "status": {"code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_OK"},
            "attributes": [
                _attr_str("o.kind", "agent"),
                _attr_str("o.q", query),
                _attr_str("o.r", response),
                _attr_str("o.sess", session),
                _attr_str("o.user", user),
                _attr_str("o.req", request),
            ],
            "events": [{"name": "started", "timeUnixNano": str(started_ns)}],
        }
    ]

    cursor = started_ns + 2_000_000

    # retrieve span (RAG-style)
    rs_ms = max(8, duration_ms // 4)
    rs_end = cursor + rs_ms * 1_000_000
    spans.append(
        {
            "traceId": trace_id,
            "spanId": _hex(16),
            "parentSpanId": root_id,
            "name": "retrieve",
            "kind": 1,
            "startTimeUnixNano": str(cursor),
            "endTimeUnixNano": str(rs_end),
            "status": {"code": "STATUS_CODE_OK"},
            "attributes": [
                _attr_str("o.kind", "retrieve"),
                _attr_str("o.tool", "vector.search"),
                _attr_int("o.docs.n", random.randint(3, 12)),
                _attr_str("o.sess", session),
            ],
        }
    )
    cursor = rs_end + 1_000_000

    # llm span (the bit that drives token / price KPIs)
    llm_ms = max(20, duration_ms // 2)
    llm_end = min(started_ns + total_ns - 1_000_000, cursor + llm_ms * 1_000_000)
    spans.append(
        {
            "traceId": trace_id,
            "spanId": _hex(16),
            "parentSpanId": root_id,
            "name": "llm.call",
            "kind": 1,
            "startTimeUnixNano": str(cursor),
            "endTimeUnixNano": str(llm_end),
            "status": {
                "code": "STATUS_CODE_ERROR" if error else "STATUS_CODE_OK"
            },
            "attributes": [
                _attr_str("o.kind", "llm"),
                _attr_str("o.model", model),
                _attr_str("o.vendor", vendor),
                _attr_str("o.q", query),
                _attr_str("o.r", response),
                _attr_int("o.tok.in", tokens_in),
                _attr_int("o.tok.out", tokens_out),
                _attr_int("o.tok.sum", tokens_in + tokens_out),
                _attr_str("o.sess", session),
            ],
        }
    )
    cursor = llm_end + 500_000

    # optional tool / postprocess span
    if random.random() < 0.7 and cursor < started_ns + total_ns - 2_000_000:
        tool_end = min(
            started_ns + total_ns - 1_000_000,
            cursor + max(5, duration_ms // 6) * 1_000_000,
        )
        spans.append(
            {
                "traceId": trace_id,
                "spanId": _hex(16),
                "parentSpanId": root_id,
                "name": "post.process",
                "kind": 1,
                "startTimeUnixNano": str(cursor),
                "endTimeUnixNano": str(tool_end),
                "status": {"code": "STATUS_CODE_OK"},
                "attributes": [
                    _attr_str("o.kind", "tool"),
                    _attr_str("o.tool", random.choice(_TOOLS)),
                    _attr_str("o.sess", session),
                ],
            }
        )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr_str("service.name", DEMO_SERVICE_NAME),
                        _attr_str("env", "demo"),
                    ]
                },
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }


async def _existing_demo_service(directory: DirectoryService, org_id: str) -> str | None:
    """Return the demo service id if it already exists, else ``None``.

    Direct DB read so we don't trip the create-service unique-slug error
    when the seeder is re-run (e.g. catalog wiped but org/service kept).
    """
    async with directory._sf() as s:  # noqa: SLF001 — internal seam by design
        row = (
            await s.execute(
                select(ServiceRow).where(
                    ServiceRow.org_id == org_id,
                    ServiceRow.slug == DEMO_SERVICE_SLUG,
                )
            )
        ).scalar_one_or_none()
        return row.id if row else None


async def _trace_count(directory: DirectoryService) -> int:
    async with directory._sf() as s:  # noqa: SLF001 — internal seam by design
        return int(
            (await s.execute(select(func.count()).select_from(TraceIndexRow))).scalar_one()
        )


async def _ensure_demo_rule_baseline_profile(
    profiles: ProfileService,
    org_id: str,
    demo_service_id: str,
) -> None:
    """Create a rule-only, auto-run profile for the demo service before traces ingest.

    When ``EASYOBS_EVAL_AUTO_RULE_ON_INGEST`` is on, each seeded trace then
    receives a ``rule_auto`` evaluation so Quality > Runs has data on first boot.
    """
    existing = await profiles.list(
        org_id=org_id,
        project_ids=[demo_service_id],
        include_disabled=True,
    )
    if any(p.name == _DEMO_RULE_PROFILE_NAME for p in existing):
        return
    await profiles.upsert(
        org_id=org_id,
        profile_id=None,
        project_id=demo_service_id,
        name=_DEMO_RULE_PROFILE_NAME,
        description=(
            "Rule-only baseline for mock/demo traces (seeded). Judges disabled; "
            "pairs with ingest auto-rule when EASYOBS_EVAL_AUTO_RULE_ON_INGEST=true."
        ),
        evaluators=[
            {"evaluator_id": "rule.response.present", "weight": 1.0, "threshold": 0.6},
            {"evaluator_id": "rule.response.length", "weight": 1.0, "threshold": 0.6},
            {"evaluator_id": "rule.response.language", "weight": 0.85, "threshold": 0.6},
            {
                "evaluator_id": "rule.perf.token_budget",
                "weight": 1.0,
                "threshold": 0.6,
                "params": {"budget_tokens": 1400},
            },
            {
                "evaluator_id": "rule.perf.latency",
                "weight": 0.75,
                "threshold": 0.6,
                "params": {"budget_ms": 1500},
            },
            {"evaluator_id": "rule.safety.no_pii", "weight": 1.0, "threshold": 0.6},
            {"evaluator_id": "rule.safety.no_profanity", "weight": 0.85, "threshold": 0.6},
            {"evaluator_id": "rule.status.ok", "weight": 1.0, "threshold": 0.6},
        ],
        judge_models=[],
        consensus="single",
        auto_run=True,
        cost_guard=None,
        enabled=True,
        actor="system",
    )
    _log.info(
        "seed.eval profile %r for demo service %s",
        _DEMO_RULE_PROFILE_NAME,
        demo_service_id[:8],
    )


async def maybe_seed_mock_data(
    *,
    directory: DirectoryService,
    trace_ingest: TraceIngestService,
    count: int,
    window_hours: int,
    profile_service: ProfileService | None = None,
    judge_model_service: JudgeModelService | None = None,
) -> None:
    """Seed ``count`` synthetic traces if (and only if) the catalog is empty.

    Safe to call on every boot — it short-circuits once any trace exists.
    """
    if count <= 0:
        return
    existing = await _trace_count(directory)
    if existing > 0:
        _log.info(
            "seed.skip catalog already has %d traces — leaving as-is", existing
        )
        return

    default_org = await directory.ensure_default_org()
    service_id = await _existing_demo_service(directory, default_org.id)
    if service_id is None:
        try:
            svc = await directory.create_service(
                org_id=default_org.id,
                name=DEMO_SERVICE_NAME,
                description=DEMO_SERVICE_DESCRIPTION,
                actor_user_id="system",
            )
        except ValueError as e:
            _log.warning("seed.skip cannot create demo service: %s", e)
            return
        service_id = svc.id

    if profile_service is not None:
        try:
            await _ensure_demo_rule_baseline_profile(
                profile_service, default_org.id, service_id
            )
        except Exception:  # pragma: no cover — never fail boot
            _log.exception("seed.eval profile bootstrap failed; continuing without it")

    _ = judge_model_service  # reserved for future seeding hooks

    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    window_ns = window_hours * 3600 * 1_000_000_000

    # Multi-turn sessions: several traces share one ``o.sess`` (Sessions page).
    num_sessions = max(8, min(40, count // 4 or 1))
    session_ids = [f"demo-sess-{_hex(10)}" for _ in range(num_sessions)]
    traces_per_session = max(2, (count + num_sessions - 1) // num_sessions)

    succeeded = 0
    sess_i = 0
    turn = 0
    for _ in range(count):
        sid = session_ids[sess_i % len(session_ids)]
        body = _build_trace(now_ns, window_ns, session_id=sid, turn_index=turn)
        try:
            await trace_ingest.ingest(
                payload=body,
                content_type="application/json",
                service_id=service_id,
            )
            succeeded += 1
        except Exception:  # pragma: no cover — defensive, never fail boot
            _log.exception("seed.trace failed; continuing")
        turn += 1
        if turn >= traces_per_session:
            turn = 0
            sess_i += 1

    _log.info(
        "seed.done generated %d/%d demo traces in service_id=%s (org=%s)",
        succeeded,
        count,
        service_id,
        default_org.name,
    )
