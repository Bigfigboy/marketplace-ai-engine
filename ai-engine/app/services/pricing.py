"""Cost estimation from configurable per-model tariffs.

Prices are never hard-coded: they come from PRICE_INPUT_PER_1M_TOKENS and
PRICE_OUTPUT_PER_1M_TOKENS. When either is unset, the cost is reported as ``null``
rather than guessed.
"""

from __future__ import annotations

from app.core.config import Settings


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (~3 chars/token; Cyrillic tokenises less efficiently)."""
    return max(1, (len(text) + 2) // 3) if text else 0


def estimate_cost(settings: Settings, input_tokens: int, output_tokens: int) -> float | None:
    if settings.price_input_per_1m_tokens is None or settings.price_output_per_1m_tokens is None:
        return None
    cost = (input_tokens * settings.price_input_per_1m_tokens
            + output_tokens * settings.price_output_per_1m_tokens) / 1_000_000
    return round(cost, 8)
