"""Channel dispatcher.

Each ``send`` call takes a single channel + event pair and converts the
EasyObs event into the channel-specific payload (Slack-blocks, Teams
MessageCard, PagerDuty Events API v2, …).

The dispatcher has no retry logic of its own — the periodic evaluator
will simply re-evaluate on the next tick and produce a follow-up firing
event if the system is still in violation. Failures are recorded on
``alarm_event`` so the UI can show "delivery_failures: 2 / 5".

Network access uses ``urllib`` from the standard library so the module
does not introduce a hard runtime dependency on ``httpx`` for ingress
boxes that already pin a different async client. ``loop.run_in_executor``
keeps the request off the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from easyobs.alarms.catalog import AlarmChannelKind
from easyobs.alarms.dtos import AlarmChannelDTO, AlarmEventDTO, AlarmRuleDTO

_log = logging.getLogger("easyobs.alarms.dispatch")

_SEVERITY_TO_PD = {
    "info": "info",
    "warning": "warning",
    "critical": "critical",
}

_SEVERITY_TO_OG_PRIORITY = {
    # Opsgenie priority: P1 (critical) … P5 (informational)
    "info": "P4",
    "warning": "P3",
    "critical": "P1",
}


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    ok: bool
    detail: str = ""


def _resolve_secret(value: str | None) -> str:
    """Allow operators to reference a secret by environment-variable name.

    The convention is the same as ``eval_judge_model.connection_config``:
    if a value starts with ``env:``, the rest is read from ``os.environ``.
    Plain values pass through.
    """
    if not value:
        return ""
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return str(value)


def _build_event_summary(rule: AlarmRuleDTO, event: AlarmEventDTO) -> str:
    state = event.state.upper()
    return (
        f"[{state}] {rule.name} — "
        f"{rule.signal_kind} {rule.comparator} {rule.threshold} "
        f"(observed {event.observed_value:g}, severity {event.severity})"
    )


def _build_event_payload(
    rule: AlarmRuleDTO, event: AlarmEventDTO
) -> dict[str, Any]:
    """The neutral JSON payload used by the generic webhook channel and
    embedded inside the more opinionated channel payloads."""
    return {
        "event_id": event.id,
        "rule_id": event.rule_id,
        "rule_name": rule.name,
        "org_id": event.org_id,
        "service_id": event.service_id,
        "state": event.state,
        "severity": event.severity,
        "signal_kind": rule.signal_kind,
        "signal_params": rule.signal_params,
        "comparator": rule.comparator,
        "threshold": event.threshold,
        "observed_value": event.observed_value,
        "started_at": event.started_at.isoformat(),
        "ended_at": event.ended_at.isoformat() if event.ended_at else None,
        "context": event.context,
        "source": "easyobs",
        "schema": "easyobs.alarms.event/v1",
    }


# ---------------------------------------------------------------------------
# Channel-specific payload builders
# ---------------------------------------------------------------------------


def _slack_payload(rule: AlarmRuleDTO, event: AlarmEventDTO) -> dict[str, Any]:
    color = "#dc2626" if event.severity == "critical" else (
        "#f59e0b" if event.severity == "warning" else "#0ea5e9"
    )
    if event.state == "resolved":
        color = "#16a34a"
    title = _build_event_summary(rule, event)
    fields = [
        {"title": "Signal", "value": rule.signal_kind, "short": True},
        {"title": "Severity", "value": event.severity, "short": True},
        {"title": "Threshold", "value": f"{rule.comparator} {event.threshold:g}", "short": True},
        {"title": "Observed", "value": f"{event.observed_value:g}", "short": True},
    ]
    if event.service_id:
        fields.append({"title": "Service", "value": event.service_id, "short": True})
    return {
        "text": title,
        "attachments": [
            {
                "color": color,
                "title": rule.name,
                "text": rule.description or "",
                "fields": fields,
                "footer": "EasyObs Alarms",
                "ts": int(event.started_at.timestamp()),
            }
        ],
    }


def _teams_payload(rule: AlarmRuleDTO, event: AlarmEventDTO) -> dict[str, Any]:
    color = "DC2626" if event.severity == "critical" else (
        "F59E0B" if event.severity == "warning" else "0EA5E9"
    )
    if event.state == "resolved":
        color = "16A34A"
    facts = [
        {"name": "Signal", "value": rule.signal_kind},
        {"name": "Severity", "value": event.severity},
        {"name": "Comparator", "value": f"{rule.comparator} {rule.threshold:g}"},
        {"name": "Observed", "value": f"{event.observed_value:g}"},
        {"name": "State", "value": event.state},
    ]
    if event.service_id:
        facts.append({"name": "Service", "value": event.service_id})
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": color,
        "summary": _build_event_summary(rule, event),
        "title": rule.name,
        "sections": [
            {
                "activityTitle": _build_event_summary(rule, event),
                "activitySubtitle": rule.description or "",
                "facts": facts,
                "markdown": True,
            }
        ],
    }


def _discord_payload(rule: AlarmRuleDTO, event: AlarmEventDTO) -> dict[str, Any]:
    color_int = 14431514 if event.severity == "critical" else (
        16096288 if event.severity == "warning" else 935396
    )
    if event.state == "resolved":
        color_int = 1535268
    return {
        "username": "EasyObs Alarms",
        "embeds": [
            {
                "title": _build_event_summary(rule, event),
                "description": rule.description or "",
                "color": color_int,
                "fields": [
                    {"name": "Signal", "value": rule.signal_kind, "inline": True},
                    {"name": "Severity", "value": event.severity, "inline": True},
                    {"name": "Threshold", "value": f"{rule.comparator} {rule.threshold:g}", "inline": True},
                    {"name": "Observed", "value": f"{event.observed_value:g}", "inline": True},
                    {"name": "Service", "value": event.service_id or "(org-wide)", "inline": True},
                    {"name": "State", "value": event.state, "inline": True},
                ],
                "timestamp": event.started_at.isoformat(),
            }
        ],
    }


def _pagerduty_payload(rule: AlarmRuleDTO, event: AlarmEventDTO, routing_key: str) -> dict[str, Any]:
    action = "trigger" if event.state == "firing" else "resolve"
    payload = {
        "routing_key": routing_key,
        "event_action": action,
        "dedup_key": f"easyobs:{rule.id}",
        "client": "EasyObs",
    }
    if action == "trigger":
        payload["payload"] = {
            "summary": _build_event_summary(rule, event)[:1024],
            "source": event.service_id or rule.org_id,
            "severity": _SEVERITY_TO_PD.get(event.severity, "warning"),
            "component": rule.signal_kind,
            "group": rule.org_id,
            "class": "easyobs.alarm",
            "custom_details": _build_event_payload(rule, event),
            "timestamp": event.started_at.isoformat(),
        }
    return payload


def _opsgenie_payload(
    rule: AlarmRuleDTO, event: AlarmEventDTO
) -> dict[str, Any]:
    """Trigger payload for Opsgenie ``POST /v2/alerts``.

    Resolves are sent through the alias to ``POST /v2/alerts/{alias}/close``
    by the dispatcher; the function only handles the trigger body.
    """
    return {
        "alias": f"easyobs-{rule.id}",
        "message": _build_event_summary(rule, event)[:130],
        "description": rule.description or "",
        "priority": _SEVERITY_TO_OG_PRIORITY.get(event.severity, "P3"),
        "source": "EasyObs",
        "tags": [rule.signal_kind, event.severity],
        "details": {
            "rule_name": rule.name,
            "service_id": event.service_id or "",
            "observed_value": f"{event.observed_value:g}",
            "threshold": f"{rule.comparator} {rule.threshold:g}",
            "state": event.state,
        },
    }


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _post_json_blocking(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, str]:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url=url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
            return (resp.status, text)
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return (exc.code, text or str(exc))
    except urllib.error.URLError as exc:
        return (0, str(exc.reason))
    except Exception as exc:  # noqa: BLE001
        return (0, str(exc))


def _send_email_blocking(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> tuple[bool, str]:
    if not recipients:
        return (False, "no recipients configured")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject[:200]
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as s:
                if username:
                    s.login(username, password)
                s.sendmail(sender, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                except Exception:  # noqa: BLE001
                    # Some closed-network relays accept plain SMTP; allow it.
                    pass
                if username:
                    s.login(username, password)
                s.sendmail(sender, recipients, msg.as_string())
        return (True, "")
    except Exception as exc:  # noqa: BLE001
        return (False, str(exc)[:500])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class AlarmDispatcher:
    def __init__(self) -> None:
        self._loop = None  # set on first send

    async def send(
        self,
        *,
        rule: AlarmRuleDTO,
        event: AlarmEventDTO,
        channel: AlarmChannelDTO,
    ) -> DeliveryOutcome:
        """Hand the event to a single channel. Returns delivery outcome."""
        if not channel.enabled:
            return DeliveryOutcome(ok=False, detail="channel disabled")
        try:
            if channel.channel_kind == AlarmChannelKind.SLACK.value:
                return await self._send_slack(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.TEAMS.value:
                return await self._send_teams(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.DISCORD.value:
                return await self._send_discord(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.PAGERDUTY.value:
                return await self._send_pagerduty(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.OPSGENIE.value:
                return await self._send_opsgenie(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.WEBHOOK.value:
                return await self._send_webhook(rule, event, channel)
            if channel.channel_kind == AlarmChannelKind.EMAIL.value:
                return await self._send_email(rule, event, channel)
            return DeliveryOutcome(ok=False, detail=f"unsupported kind {channel.channel_kind}")
        except Exception as exc:  # noqa: BLE001
            _log.exception("alarm dispatch error", extra={"channel_id": channel.id})
            return DeliveryOutcome(ok=False, detail=str(exc)[:500])

    async def _post(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: _post_json_blocking(url, body, headers=headers)
        )

    async def _send_slack(self, rule, event, channel) -> DeliveryOutcome:
        url = _resolve_secret(channel.config.get("webhook_url"))
        if not url:
            return DeliveryOutcome(ok=False, detail="missing webhook_url")
        status, body = await self._post(url, _slack_payload(rule, event))
        return _outcome_from_http(status, body)

    async def _send_teams(self, rule, event, channel) -> DeliveryOutcome:
        url = _resolve_secret(channel.config.get("webhook_url"))
        if not url:
            return DeliveryOutcome(ok=False, detail="missing webhook_url")
        status, body = await self._post(url, _teams_payload(rule, event))
        return _outcome_from_http(status, body)

    async def _send_discord(self, rule, event, channel) -> DeliveryOutcome:
        url = _resolve_secret(channel.config.get("webhook_url"))
        if not url:
            return DeliveryOutcome(ok=False, detail="missing webhook_url")
        status, body = await self._post(url, _discord_payload(rule, event))
        return _outcome_from_http(status, body)

    async def _send_pagerduty(self, rule, event, channel) -> DeliveryOutcome:
        routing_key = _resolve_secret(channel.config.get("routing_key"))
        if not routing_key:
            return DeliveryOutcome(ok=False, detail="missing routing_key")
        payload = _pagerduty_payload(rule, event, routing_key)
        status, body = await self._post(
            "https://events.pagerduty.com/v2/enqueue", payload
        )
        return _outcome_from_http(status, body)

    async def _send_opsgenie(self, rule, event, channel) -> DeliveryOutcome:
        api_key = _resolve_secret(channel.config.get("api_key"))
        if not api_key:
            return DeliveryOutcome(ok=False, detail="missing api_key")
        region = (channel.config.get("region") or "us").strip().lower()
        host = "api.eu.opsgenie.com" if region == "eu" else "api.opsgenie.com"
        if event.state == "firing":
            url = f"https://{host}/v2/alerts"
            body = _opsgenie_payload(rule, event)
        else:
            url = f"https://{host}/v2/alerts/easyobs-{rule.id}/close?identifierType=alias"
            body = {"source": "EasyObs", "note": "auto-resolved by EasyObs"}
        status, text = await self._post(
            url, body, headers={"Authorization": f"GenieKey {api_key}"}
        )
        return _outcome_from_http(status, text)

    async def _send_webhook(self, rule, event, channel) -> DeliveryOutcome:
        url = _resolve_secret(channel.config.get("url"))
        if not url:
            return DeliveryOutcome(ok=False, detail="missing url")
        body = _build_event_payload(rule, event)
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers: dict[str, str] = {}
        secret = _resolve_secret(channel.config.get("hmac_secret"))
        if secret:
            ts = str(int(time.time()))
            mac = hmac.HMAC(
                secret.encode("utf-8"),
                f"{ts}.".encode("utf-8") + body_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-EasyObs-Signature"] = f"t={ts},v1={mac}"
        for line in (channel.config.get("extra_headers") or "").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k:
                headers[k] = v
        status, text = await self._post(url, body, headers=headers)
        return _outcome_from_http(status, text)

    async def _send_email(self, rule, event, channel) -> DeliveryOutcome:
        cfg = channel.config or {}
        host = cfg.get("smtp_host") or ""
        if not host:
            return DeliveryOutcome(ok=False, detail="missing smtp_host")
        try:
            port = int(cfg.get("smtp_port") or 587)
        except (TypeError, ValueError):
            port = 587
        sender = cfg.get("from_address") or ""
        recipients = [
            x.strip() for x in (cfg.get("to_addresses") or "").split(",") if x.strip()
        ]
        if not sender or not recipients:
            return DeliveryOutcome(ok=False, detail="missing from/to addresses")
        username = cfg.get("smtp_username") or ""
        password = _resolve_secret(cfg.get("smtp_password"))
        subject = _build_event_summary(rule, event)
        body = json.dumps(
            _build_event_payload(rule, event), indent=2, ensure_ascii=False
        )
        loop = asyncio.get_running_loop()
        ok, error = await loop.run_in_executor(
            None,
            lambda: _send_email_blocking(
                host=host,
                port=port,
                username=username,
                password=password,
                sender=sender,
                recipients=recipients,
                subject=subject,
                body=body,
            ),
        )
        return DeliveryOutcome(ok=ok, detail=error if not ok else "")


def _outcome_from_http(status: int, body: str) -> DeliveryOutcome:
    if 200 <= status < 300:
        return DeliveryOutcome(ok=True)
    return DeliveryOutcome(
        ok=False, detail=f"HTTP {status}: {(body or '')[:300]}"
    )


# Helper used by the test endpoint to synthesize a fake event.
def synthetic_event(channel: AlarmChannelDTO) -> AlarmEventDTO:
    now = datetime.now(tz=timezone.utc)
    return AlarmEventDTO(
        id="test",
        rule_id="test",
        rule_name="EasyObs alarm test",
        org_id=channel.org_id,
        service_id=None,
        state="firing",
        severity="info",
        observed_value=42.0,
        threshold=10.0,
        started_at=now,
        ended_at=None,
        context={"note": "synthetic test event from EasyObs"},
        delivery_attempts=0,
        delivery_failures=0,
        last_delivery_error="",
    )


def synthetic_rule(channel: AlarmChannelDTO) -> AlarmRuleDTO:
    return AlarmRuleDTO(
        id="test",
        org_id=channel.org_id,
        service_id=None,
        name="EasyObs alarm test",
        description="Synthetic test event — no production rule was triggered.",
        signal_kind="trace_volume",
        signal_params={},
        comparator="gt",
        threshold=10.0,
        window_minutes=15,
        min_samples=1,
        dedup_minutes=15,
        severity="info",
        enabled=True,
        last_evaluated_at=None,
        last_observed_value=None,
        last_state="",
        channel_ids=[channel.id],
        created_at=None,
    )
