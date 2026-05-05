"""Auto-rule trigger fired after every successful trace ingest.

The hook is wired into :class:`TraceIngestService.register_post_write_hook`
when the Quality module is enabled. It is intentionally minimal:

1. Look up the org of the service.
2. Find every enabled profile with ``auto_run=True`` for that org+project.
3. Execute the run with ``trigger_lane=rule_auto`` (judges always
   skipped — the auto path is rule-only by design).

Failures are logged and never propagate; the ingest path is therefore
unaffected even if the evaluation module is misconfigured.
"""

from __future__ import annotations

import asyncio
import logging

from easyobs.eval.services.profiles import ProfileService
from easyobs.eval.services.runs import RunService
from easyobs.eval.types import TriggerLane

_log = logging.getLogger("easyobs.eval.auto")


class AutoRuleTrigger:
    def __init__(
        self,
        *,
        profiles: ProfileService,
        runs: RunService,
        directory,
    ) -> None:
        self._profiles = profiles
        self._runs = runs
        self._directory = directory
        # Serialize auto-rule DB work: ingest fires one task per trace; SQLite
        # cannot absorb many concurrent writers.
        self._lock = asyncio.Lock()

    async def __call__(self, trace_id: str, service_id: str) -> None:
        try:
            service = await self._directory.get_service(service_id)
        except Exception:
            _log.exception("auto-rule: failed to resolve service")
            return
        if service is None:
            return
        org_id = service.org_id
        async with self._lock:
            try:
                profiles = await self._profiles.list_auto_run(
                    org_id=org_id, project_id=service_id
                )
            except Exception:
                _log.exception("auto-rule: failed to list profiles")
                return
            for profile in profiles:
                try:
                    await self._runs.execute(
                        org_id=org_id,
                        profile_id=profile.id,
                        profile=profile,
                        project_id=service_id,
                        trace_ids=[trace_id],
                        trigger_lane=TriggerLane.RULE_AUTO.value,
                        triggered_by=None,
                        notes="auto-rule (ingest)",
                        project_scope=[service_id],
                    )
                except Exception:
                    _log.exception(
                        "auto-rule run failed",
                        extra={"profile_id": profile.id, "trace_id": trace_id},
                    )
