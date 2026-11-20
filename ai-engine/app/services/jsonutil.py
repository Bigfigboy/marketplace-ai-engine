"""Tolerant JSON-object parsing for model output."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidate = _FENCE_RE.sub("", text.strip())
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def as_str(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def as_str_list(value: Any, limit: int = 50, item_limit: int = 1000) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [as_str(v, item_limit) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return out[:limit]
