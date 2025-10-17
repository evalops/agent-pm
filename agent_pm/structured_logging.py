"""Structured JSON logging with correlation IDs."""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from typing import Any

from agent_pm.utils.datetime import utc_now_isoformat

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get current correlation ID or generate new one."""
    cid = correlation_id.get()
    if not cid:
        cid = str(uuid.uuid4())
        correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    """Set correlation ID for current context."""
    correlation_id.set(cid)


class StructuredFormatter(logging.Formatter):
    """JSON formatter with correlation IDs."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": utc_now_isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
            ):
                log_data[key] = value

        return json.dumps(log_data)


def configure_structured_logging(level: str = "INFO"):
    """Configure structured JSON logging for all loggers."""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
