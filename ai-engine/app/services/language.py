"""Lightweight, offline language detection for ru / kk / en.

Kazakh uses Cyrillic plus nine extra letters (ә ғ қ ң ө ұ ү һ і). Their presence, or
frequent Kazakh function words, distinguishes Kazakh from Russian. Latin-dominant text
is treated as English.
"""

from __future__ import annotations

import re

_KK_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
_KK_WORDS = {
    "және", "бұл", "үшін", "мен", "сен", "біз", "сіз", "қалай", "қандай", "керек", "жоқ",
    "бар", "емес", "туралы", "бойынша", "арқылы", "маған", "бізге", "сізге", "жасап",
    "беріңіз", "ма", "ме", "па", "пе", "ба", "бе", "дейін", "кейін", "немесе",
}
_RU_WORDS = {
    "и", "в", "не", "на", "что", "как", "для", "это", "по", "с", "у", "мне", "нам",
    "пожалуйста", "или", "если", "есть", "какой", "какие", "сколько",
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str, default: str = "ru") -> str:
    if not text or not text.strip():
        return default
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return default
    cyrillic = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    latin = sum(1 for ch in letters if ch.isascii())
    kk_letters = sum(1 for ch in letters if ch in _KK_LETTERS)

    words = [w.lower() for w in _WORD_RE.findall(text)]
    kk_hits = sum(1 for w in words if w in _KK_WORDS)
    ru_hits = sum(1 for w in words if w in _RU_WORDS)

    if cyrillic >= latin:
        # Two or more Kazakh-specific letters, or Kazakh words outweighing Russian ones.
        if kk_letters >= 2 or (kk_letters >= 1 and kk_hits >= ru_hits) or kk_hits > ru_hits:
            return "kk"
        return "ru"
    return "en"
