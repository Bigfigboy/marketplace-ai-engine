"""Structured JSON logging with redaction.

Policy: log identifiers, statuses, counters and timings only. Never log prompts,
model responses, document text, resumes or credentials.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.redaction import redact_mapping, redact_text

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact_text(record.getMessage()),
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extra:
            payload.update(redact_mapping(extra))
        if record.exc_info and record.levelno >= logging.ERROR:
            # Exception type only: messages may contain user data.
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(getattr(h, "_ai_engine", False) for h in root.handlers):
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._ai_engine = True  # type: ignore[attr-defined]
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Third-party HTTP clients may log full URLs/bodies at DEBUG.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
