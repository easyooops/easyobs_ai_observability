"""Centralized logging configuration for the EasyObs API + ingest server.

The goal is twofold:

* **Local development**: every API/ingest event (startup banner, requests,
  unhandled exceptions, business-level info logs) shows up immediately in the
  same terminal that runs ``run-dev.ps1`` so the developer never has to tail a
  hidden log file.
* **Cloud deployment (ECS / EC2 / EKS)**: emit one JSON object per line on
  stdout so the platform's log driver (awslogs, fluentbit, OTel collector …)
  forwards them to CloudWatch Logs / Loki / Cloud Logging without any extra
  parsing.

The configuration is intentionally idempotent — calling :func:`configure_logging`
twice is safe (it tears down previously installed handlers first), which keeps
``uvicorn --reload`` happy.
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import os
import sys
from pathlib import Path
from typing import Any, Literal

LogFormat = Literal["console", "json"]

# Per-request context. The middleware (``easyobs.api.middleware``) sets these
# values; the formatter merges them into every record produced during the
# request so cross-module logs are correlatable.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "easyobs_request_id", default=None
)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "easyobs_user_id", default=None
)
org_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "easyobs_org_id", default=None
)


_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
    "taskName",
}


class _ContextFilter(logging.Filter):
    """Inject request_id / user_id / org_id from contextvars onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.org_id = org_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Minimal stdlib-only JSON formatter — no extra deps required."""

    def __init__(self, service: str = "easyobs-api") -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "msg": record.getMessage(),
        }
        for attr in ("request_id", "user_id", "org_id"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly single-line formatter for local development."""

    default_fmt = (
        "%(asctime)s %(levelname)-7s %(name)s%(ctx)s %(message)s"
    )

    def __init__(self) -> None:
        super().__init__(fmt=self.default_fmt, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        ctx_bits: list[str] = []
        for attr, prefix in (
            ("request_id", "rid="),
            ("user_id", "uid="),
            ("org_id", "org="),
        ):
            value = getattr(record, attr, None)
            if value:
                ctx_bits.append(f"{prefix}{value}")
        record.ctx = f" [{' '.join(ctx_bits)}]" if ctx_bits else ""
        base = super().format(record)
        return base


def configure_logging(
    level: str = "INFO",
    fmt: LogFormat = "console",
    log_file: str | os.PathLike[str] | None = None,
    *,
    service: str = "easyobs-api",
) -> None:
    """Install handlers/formatters on the root logger.

    Repeatable; subsequent calls replace the previous configuration.
    """
    level_value = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter(service=service)
    else:
        formatter = ConsoleFormatter()

    handlers: list[logging.Handler] = []
    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(_ContextFilter())
    handlers.append(stdout_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_ContextFilter())
        handlers.append(file_handler)

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level_value)

    # Make uvicorn's loggers flow through the same handlers/formatters so the
    # ERROR/INFO startup messages are not formatted differently from app logs.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level_value)

    # Silence uvicorn's built-in access logger — our RequestLoggingMiddleware
    # emits the canonical ``easyobs.access`` line (with method/path/status/
    # duration_ms/request_id) so anything from uvicorn would just be noise.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True

    # SQLAlchemy is noisy at INFO; pin to WARNING unless the operator
    # explicitly bumps the level.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING if level_value > logging.DEBUG else logging.INFO
    )
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    logging.getLogger("easyobs.boot").info(
        "logging configured", extra={"format": fmt, "level": level.upper()}
    )
