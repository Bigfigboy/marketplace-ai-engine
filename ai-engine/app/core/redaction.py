"""PII and secret redaction.

Adapted from Resume-AI-Parser (MIT, (c) 2026 Nabi Bukhsh Baloch,
https://github.com/NabiBukhsh-AI/Resume-AI-Parser) ``observability/redaction.py``,
extended with Kazakhstan identifiers (IIN/BIN) and OpenAI-style API keys.

Used in two places:
* the log formatter (defence in depth — document text is never logged on purpose);
* the HR agent, which masks candidate contact details and protected attributes before
  any text leaves the server.
"""

from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Loose international phone shape: optional +, then 7-20 digits with separators.
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,20}\d(?!\w)")
# Kazakhstan IIN / BIN: exactly 12 digits.
IIN_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
API_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|aie_[A-Za-z0-9_-]{16,})\b")
URL_CREDENTIALS_RE = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")

SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "authorization", "openai_api_key", "password", "secret",
    "token", "x-api-key", "jwt_secret", "key",
})
BULK_TEXT_KEYS = frozenset({
    "text", "content", "resume_text", "document_text", "prompt", "body", "message",
    "response", "history",
})
MASK = "[redacted]"


def redact_text(value: str) -> str:
    masked = API_KEY_RE.sub("[api_key]", value)
    masked = EMAIL_RE.sub("[email]", masked)
    masked = IIN_RE.sub("[iin]", masked)
    masked = PHONE_RE.sub("[phone]", masked)
    return URL_CREDENTIALS_RE.sub(MASK, masked)


def truncate(value: str, limit: int = 120) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [+{len(value) - limit} chars]"


def redact_mapping(data: dict[str, Any], *, text_limit: int = 64) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if lowered in SENSITIVE_KEYS:
            out[key] = MASK
        elif lowered in BULK_TEXT_KEYS:
            out[key] = f"[{len(value)} chars]" if isinstance(value, str) else MASK
        elif isinstance(value, str):
            out[key] = truncate(redact_text(value), 512)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, text_limit=text_limit)
        else:
            out[key] = value
    return out
