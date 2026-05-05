"""Golden-set authoring service.

Three creation paths (12 §1) feed the same ``eval_golden_item`` table:

1. **Manual** — operator authors a single item via the API. Items start
   in ``active`` because a human just typed them.
2. **Auto-discover** — sweep recent traces of a service and harvest the
   ones that pass a heuristic filter. Items land in ``candidate`` so a
   reviewer must promote them.
3. **Trace-GT** — operator labels a known trace as ground truth and the
   item is created in ``active`` (the trace was hand-picked).

The 12 redesign also adds:

- A ``mode`` discriminator (``regression`` / ``cohort`` / ``synthesized``)
  so the same table backs three distinct workflows.
- Agent invocation settings used by the Regression Run flow (12 §2.3).
- Immutable revisions auto-published the first time a regression set is
  attached to a Run (12 §3.2). New edits after publication go to a fresh
  revision so prior eval results stay reproducible.
- Review state (``unreviewed`` / ``reviewed`` / ``disputed``) so multi-
  rater workflows can route conflicts to a queue.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from easyobs.db.models import (
    EvalGoldenItemRow,
    EvalGoldenRevisionRow,
    EvalGoldenSetRow,
)
from easyobs.eval.services.dtos import (
    AgentInvokeSettings,
    GoldenItemDTO,
    GoldenRevisionDTO,
    GoldenSetDTO,
)
from easyobs.eval.types import (
    GoldenItemReviewState,
    GoldenLayer,
    GoldenSetMode,
    SourceKind,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_layer(value: str) -> str:
    if value not in {gl.value for gl in GoldenLayer}:
        raise ValueError(f"unsupported layer {value!r}")
    return value


def _normalise_source(value: str) -> str:
    if value not in {s.value for s in SourceKind}:
        return SourceKind.MANUAL.value
    return value


def _normalise_mode(value: str | None) -> str:
    if not value:
        return GoldenSetMode.REGRESSION.value
    if value not in {m.value for m in GoldenSetMode}:
        raise ValueError(f"unsupported golden set mode {value!r}")
    return value


def _normalise_review_state(value: str | None) -> str:
    if not value:
        return GoldenItemReviewState.UNREVIEWED.value
    if value not in {s.value for s in GoldenItemReviewState}:
        raise ValueError(f"invalid review_state {value!r}")
    return value


class GoldenSetService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        trace_query=None,
    ) -> None:
        """``trace_query`` is the existing :class:`TraceQueryService` and
        is required only for auto-discover / trace-GT flows. We accept it
        as a kw-arg so the unit tests can supply a stub."""
        self._sf = session_factory
        self._traces = trace_query

    # ------------------------------------------------------------------
    # Sets
    # ------------------------------------------------------------------

    async def list_sets(
        self,
        *,
        org_id: str,
        project_ids: list[str] | None,
        mode: str | None = None,
    ) -> list[GoldenSetDTO]:
        async with self._sf() as s:
            stmt = select(EvalGoldenSetRow).where(EvalGoldenSetRow.org_id == org_id)
            if mode:
                stmt = stmt.where(EvalGoldenSetRow.mode == _normalise_mode(mode))
            rows = (await s.execute(stmt.order_by(EvalGoldenSetRow.created_at))).scalars().all()
            if project_ids is not None:
                allowed = set(project_ids)
                rows = [r for r in rows if r.project_id is None or r.project_id in allowed]
            counts: dict[str, int] = {}
            if rows:
                counts_stmt = (
                    select(EvalGoldenItemRow.set_id, func.count())
                    .where(EvalGoldenItemRow.set_id.in_([r.id for r in rows]))
                    .group_by(EvalGoldenItemRow.set_id)
                )
                for set_id, c in (await s.execute(counts_stmt)).all():
                    counts[set_id] = int(c)
            return [_set_dto(r, counts.get(r.id, 0)) for r in rows]

    async def get_set(
        self, *, org_id: str, set_id: str, project_ids: list[str] | None
    ) -> GoldenSetDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalGoldenSetRow, set_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(EvalGoldenItemRow)
                    .where(EvalGoldenItemRow.set_id == set_id)
                )
            ).scalar() or 0
            return _set_dto(row, int(count))

    async def create_set(
        self,
        *,
        org_id: str,
        project_id: str | None,
        name: str,
        layer: str,
        description: str,
        actor: str | None,
        mode: str | None = None,
        expand_query: dict[str, Any] | None = None,
    ) -> GoldenSetDTO:
        normalised_layer = _normalise_layer(layer)
        normalised_mode = _normalise_mode(mode)
        async with self._sf() as s:
            row = EvalGoldenSetRow(
                id=uuid.uuid4().hex,
                org_id=org_id,
                project_id=project_id,
                name=name,
                layer=normalised_layer,
                description=description,
                mode=normalised_mode,
                expand_query_json=json.dumps(
                    expand_query or {}, ensure_ascii=False, default=str
                ),
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _set_dto(row, 0)

    async def delete_set(self, *, org_id: str, set_id: str) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalGoldenSetRow, set_id)
            if row is None or row.org_id != org_id:
                return False
            await s.delete(row)
            await s.commit()
            return True

    # ------------------------------------------------------------------
    # 12 §2.3 — Agent invocation settings (Regression Run)
    # ------------------------------------------------------------------

    async def update_agent_settings(
        self,
        *,
        org_id: str,
        set_id: str,
        settings: AgentInvokeSettings,
        project_ids: list[str] | None,
    ) -> GoldenSetDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalGoldenSetRow, set_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            row.agent_endpoint_url = settings.endpoint_url.strip()
            row.agent_request_template_json = json.dumps(
                settings.request_template or {}, ensure_ascii=False, default=str
            )
            row.agent_auth_ref = settings.auth_ref.strip()
            row.agent_timeout_sec = max(1, int(settings.timeout_sec))
            row.agent_max_concurrent = max(1, int(settings.max_concurrent))
            await s.commit()
            await s.refresh(row)
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(EvalGoldenItemRow)
                    .where(EvalGoldenItemRow.set_id == set_id)
                )
            ).scalar() or 0
            return _set_dto(row, int(count))

    # ------------------------------------------------------------------
    # 12 §3.2 — Immutable revisions
    # ------------------------------------------------------------------

    async def list_revisions(
        self, *, org_id: str, set_id: str, project_ids: list[str] | None
    ) -> list[GoldenRevisionDTO]:
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                return []
            if (
                project_ids is not None
                and base.project_id is not None
                and base.project_id not in project_ids
            ):
                return []
            stmt = (
                select(EvalGoldenRevisionRow)
                .where(EvalGoldenRevisionRow.set_id == set_id)
                .order_by(EvalGoldenRevisionRow.revision_no)
            )
            rows = (await s.execute(stmt)).scalars().all()
            return [_revision_dto(r) for r in rows]

    async def get_revision(
        self,
        *,
        org_id: str,
        set_id: str,
        revision_no: int,
        project_ids: list[str] | None,
    ) -> GoldenRevisionDTO | None:
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                return None
            if (
                project_ids is not None
                and base.project_id is not None
                and base.project_id not in project_ids
            ):
                return None
            stmt = select(EvalGoldenRevisionRow).where(
                EvalGoldenRevisionRow.set_id == set_id,
                EvalGoldenRevisionRow.revision_no == revision_no,
            )
            row = (await s.execute(stmt)).scalar_one_or_none()
            return _revision_dto(row) if row else None

    async def ensure_active_revision(
        self,
        *,
        org_id: str,
        set_id: str,
        actor: str | None,
    ) -> GoldenRevisionDTO:
        """Return a revision suitable for *new* work.

        The most recent revision is reused while it is still mutable; if
        the set has no revision yet (e.g. a brand-new set), or the latest
        revision is already immutable (because a Run consumed it), a
        fresh mutable revision is opened that copies in the live items
        of the set.

        This method is the canonical entry-point used by all create /
        update flows so the operator never has to think about revision
        bookkeeping by hand."""

        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                raise LookupError("golden set not found")
            stmt = (
                select(EvalGoldenRevisionRow)
                .where(EvalGoldenRevisionRow.set_id == set_id)
                .order_by(EvalGoldenRevisionRow.revision_no.desc())
                .limit(1)
            )
            latest = (await s.execute(stmt)).scalar_one_or_none()
            if latest is not None and not latest.immutable:
                return _revision_dto(latest)
            next_no = (latest.revision_no + 1) if latest else 1
            row = EvalGoldenRevisionRow(
                id=uuid.uuid4().hex,
                set_id=set_id,
                org_id=org_id,
                revision_no=next_no,
                immutable=False,
                item_count=0,
                trust_summary_json="{}",
                notes="",
                created_at=_now(),
                created_by=actor,
                locked_at=None,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _revision_dto(row)

    async def lock_revision_for_run(
        self, *, org_id: str, set_id: str, actor: str | None
    ) -> GoldenRevisionDTO:
        """Promote the active mutable revision to immutable so a Run
        can pin its inputs (12 §3.2). New writes to the set after this
        call open a brand-new revision."""

        rev = await self.ensure_active_revision(
            org_id=org_id, set_id=set_id, actor=actor
        )
        async with self._sf() as s:
            row = await s.get(EvalGoldenRevisionRow, rev.id)
            if row is None:
                raise LookupError("revision vanished mid-lock")
            if row.immutable:
                return _revision_dto(row)
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(EvalGoldenItemRow)
                    .where(EvalGoldenItemRow.set_id == set_id)
                )
            ).scalar() or 0
            row.item_count = int(count)
            row.immutable = True
            row.locked_at = _now()
            # Backfill the items so each carries this revision_id — this
            # is what makes the snapshot reproducible: future edits go to
            # a new revision with NULL revision_id on the new rows.
            stmt = select(EvalGoldenItemRow).where(
                EvalGoldenItemRow.set_id == set_id,
                EvalGoldenItemRow.revision_id.is_(None),
            )
            items = (await s.execute(stmt)).scalars().all()
            for it in items:
                it.revision_id = row.id
            await s.commit()
            await s.refresh(row)
            return _revision_dto(row)

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def list_items(
        self,
        *,
        org_id: str,
        set_id: str,
        project_ids: list[str] | None,
        status: str | None = None,
        review_state: str | None = None,
        revision_id: str | None = None,
        limit: int = 200,
    ) -> list[GoldenItemDTO]:
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                return []
            if (
                project_ids is not None
                and base.project_id is not None
                and base.project_id not in project_ids
            ):
                return []
            stmt = select(EvalGoldenItemRow).where(EvalGoldenItemRow.set_id == set_id)
            if status:
                stmt = stmt.where(EvalGoldenItemRow.status == status)
            if review_state:
                stmt = stmt.where(EvalGoldenItemRow.review_state == review_state)
            if revision_id:
                stmt = stmt.where(EvalGoldenItemRow.revision_id == revision_id)
            rows = (
                await s.execute(stmt.order_by(EvalGoldenItemRow.created_at.desc()).limit(limit))
            ).scalars().all()
            return [_item_dto(r) for r in rows]

    async def add_manual_item(
        self,
        *,
        org_id: str,
        set_id: str,
        payload: dict[str, Any],
        project_ids: list[str] | None,
        actor: str | None,
    ) -> GoldenItemDTO:
        return await self._insert_item(
            org_id=org_id,
            set_id=set_id,
            payload=payload,
            source_kind=SourceKind.MANUAL.value,
            status="active",
            source_trace_id=None,
            project_ids=project_ids,
            actor=actor,
        )

    async def bulk_add_from_upload(
        self,
        *,
        org_id: str,
        set_id: str,
        payloads: list[dict[str, Any]],
        project_ids: list[str] | None,
        actor: str | None,
    ) -> list[GoldenItemDTO]:
        """Insert N rows produced by the CSV/xlsx/JSONL upload parser
        in a single transaction. Items land as ``candidate`` so a human
        reviewer can promote them, mirroring the synthesizer behaviour
        (12 §5.2 — 자동 생성 항목은 검수 후 active 전환)."""

        if not payloads:
            return []
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                raise LookupError("golden set not found")
            if (
                project_ids is not None
                and base.project_id is not None
                and base.project_id not in project_ids
            ):
                raise PermissionError("project access denied")
            now = _now()
            rows: list[EvalGoldenItemRow] = []
            for payload in payloads:
                row = EvalGoldenItemRow(
                    id=uuid.uuid4().hex,
                    set_id=set_id,
                    org_id=org_id,
                    project_id=base.project_id,
                    layer=base.layer,
                    source_kind=SourceKind.IMPORT.value,
                    status="candidate",
                    payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                    source_trace_id=None,
                    review_state=GoldenItemReviewState.UNREVIEWED.value,
                    created_at=now,
                    created_by=actor,
                )
                s.add(row)
                rows.append(row)
            await s.commit()
            for row in rows:
                await s.refresh(row)
            return [_item_dto(r) for r in rows]

    async def add_from_trace(
        self,
        *,
        org_id: str,
        set_id: str,
        trace_id: str,
        labels: dict[str, Any],
        project_ids: list[str] | None,
        actor: str | None,
    ) -> GoldenItemDTO:
        """Trace-GT entry point. We hydrate the trace summary so the golden
        item carries enough context (query/response/expected docs) for the
        rule + judge layer to consume it later."""

        payload: dict[str, Any] = {**labels}
        if self._traces is not None:
            try:
                detail = await self._traces.trace_detail(
                    trace_id, allowed_service_ids=project_ids
                )
            except Exception:
                detail = None
            if detail:
                summary = detail.get("llmSummary") or {}
                payload.setdefault("query", summary.get("query"))
                payload.setdefault("response", summary.get("response"))
                payload.setdefault("session", summary.get("session"))
                payload.setdefault("user", summary.get("user"))
        return await self._insert_item(
            org_id=org_id,
            set_id=set_id,
            payload=payload,
            source_kind=SourceKind.TRACE_GT.value,
            status="active",
            source_trace_id=trace_id,
            project_ids=project_ids,
            actor=actor,
        )

    async def auto_discover(
        self,
        *,
        org_id: str,
        set_id: str,
        service_ids: list[str],
        sample_size: int,
        actor: str | None,
    ) -> list[GoldenItemDTO]:
        """Sweep recent traces and harvest the ones that look like good
        candidates (have both a query and a response). Items land in
        ``candidate`` so a reviewer must promote them before they are
        used by an evaluation run."""

        if self._traces is None:
            return []
        traces = await self._traces.list_traces(
            service_ids=service_ids,
            limit=max(sample_size * 2, 50),
            with_llm=True,
        )
        added: list[GoldenItemDTO] = []
        for trace in traces:
            if len(added) >= sample_size:
                break
            summary = trace.get("llmSummary") or {}
            query = summary.get("query") or trace.get("rootName")
            response = summary.get("response")
            if not query or not response:
                continue
            payload = {
                "query": query,
                "response": response,
                "session": summary.get("session"),
                "user": summary.get("user"),
            }
            try:
                item = await self._insert_item(
                    org_id=org_id,
                    set_id=set_id,
                    payload=payload,
                    source_kind=SourceKind.AUTO.value,
                    status="candidate",
                    source_trace_id=trace.get("traceId"),
                    project_ids=service_ids,
                    actor=actor,
                )
            except LookupError:
                break
            added.append(item)
        return added

    async def update_item_status(
        self,
        *,
        org_id: str,
        item_id: str,
        status: str,
        project_ids: list[str] | None,
    ) -> GoldenItemDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalGoldenItemRow, item_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            if status not in {"candidate", "active", "archived"}:
                raise ValueError("invalid status")
            row.status = status
            await s.commit()
            await s.refresh(row)
            return _item_dto(row)

    async def update_item_review(
        self,
        *,
        org_id: str,
        item_id: str,
        review_state: str | None = None,
        label_kind: str | None = None,
        dispute_reason: str | None = None,
        project_ids: list[str] | None,
    ) -> GoldenItemDTO | None:
        async with self._sf() as s:
            row = await s.get(EvalGoldenItemRow, item_id)
            if row is None or row.org_id != org_id:
                return None
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return None
            if review_state is not None:
                row.review_state = _normalise_review_state(review_state)
            if label_kind is not None:
                row.label_kind = label_kind.strip() or None
            if dispute_reason is not None:
                row.dispute_reason = dispute_reason
            await s.commit()
            await s.refresh(row)
            return _item_dto(row)

    async def delete_item(
        self,
        *,
        org_id: str,
        item_id: str,
        project_ids: list[str] | None,
    ) -> bool:
        async with self._sf() as s:
            row = await s.get(EvalGoldenItemRow, item_id)
            if row is None or row.org_id != org_id:
                return False
            if (
                project_ids is not None
                and row.project_id is not None
                and row.project_id not in project_ids
            ):
                return False
            await s.delete(row)
            await s.commit()
            return True

    # ------------------------------------------------------------------
    # Internal: shared insert
    # ------------------------------------------------------------------

    async def _insert_item(
        self,
        *,
        org_id: str,
        set_id: str,
        payload: dict[str, Any],
        source_kind: str,
        status: str,
        source_trace_id: str | None,
        project_ids: list[str] | None,
        actor: str | None,
    ) -> GoldenItemDTO:
        async with self._sf() as s:
            base = await s.get(EvalGoldenSetRow, set_id)
            if base is None or base.org_id != org_id:
                raise LookupError("golden set not found")
            if (
                project_ids is not None
                and base.project_id is not None
                and base.project_id not in project_ids
            ):
                raise PermissionError("project access denied")
            row = EvalGoldenItemRow(
                id=uuid.uuid4().hex,
                set_id=set_id,
                org_id=org_id,
                project_id=base.project_id,
                layer=base.layer,
                source_kind=_normalise_source(source_kind),
                status=status,
                payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                source_trace_id=source_trace_id,
                review_state=GoldenItemReviewState.UNREVIEWED.value,
                created_at=_now(),
                created_by=actor,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _item_dto(row)


def _set_dto(row: EvalGoldenSetRow, count: int) -> GoldenSetDTO:
    try:
        expand_query = json.loads(getattr(row, "expand_query_json", None) or "{}")
    except Exception:
        expand_query = {}
    if not isinstance(expand_query, dict):
        expand_query = {}
    try:
        request_template = json.loads(
            getattr(row, "agent_request_template_json", None) or "{}"
        )
    except Exception:
        request_template = {}
    if not isinstance(request_template, dict):
        request_template = {}
    invoke = AgentInvokeSettings(
        endpoint_url=getattr(row, "agent_endpoint_url", "") or "",
        request_template=request_template,
        auth_ref=getattr(row, "agent_auth_ref", "") or "",
        timeout_sec=int(getattr(row, "agent_timeout_sec", 30) or 30),
        max_concurrent=int(getattr(row, "agent_max_concurrent", 5) or 5),
    )
    return GoldenSetDTO(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        name=row.name,
        layer=row.layer,
        description=row.description,
        item_count=count,
        created_at=row.created_at,
        mode=getattr(row, "mode", None) or GoldenSetMode.REGRESSION.value,
        expand_query=expand_query,
        last_synth_job_id=getattr(row, "last_synth_job_id", None),
        agent_invoke=invoke,
    )


def _item_dto(row: EvalGoldenItemRow) -> GoldenItemDTO:
    try:
        payload = json.loads(row.payload_json or "{}")
    except Exception:
        payload = {}
    return GoldenItemDTO(
        id=row.id,
        set_id=row.set_id,
        org_id=row.org_id,
        project_id=row.project_id,
        layer=row.layer,
        source_kind=row.source_kind,
        status=row.status,
        payload=payload,
        source_trace_id=row.source_trace_id,
        created_at=row.created_at,
        revision_id=getattr(row, "revision_id", None),
        label_kind=getattr(row, "label_kind", None),
        review_state=getattr(row, "review_state", None)
        or GoldenItemReviewState.UNREVIEWED.value,
        dispute_reason=getattr(row, "dispute_reason", "") or "",
    )


def _revision_dto(row: EvalGoldenRevisionRow) -> GoldenRevisionDTO:
    try:
        trust = json.loads(row.trust_summary_json or "{}")
    except Exception:
        trust = {}
    if not isinstance(trust, dict):
        trust = {}
    return GoldenRevisionDTO(
        id=row.id,
        set_id=row.set_id,
        org_id=row.org_id,
        revision_no=int(row.revision_no),
        immutable=bool(row.immutable),
        item_count=int(row.item_count or 0),
        notes=row.notes or "",
        trust_summary=trust,
        created_at=row.created_at,
        locked_at=row.locked_at,
    )
