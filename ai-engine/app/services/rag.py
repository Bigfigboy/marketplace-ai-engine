"""Simple, dependency-free RAG: chunking + BM25 lexical retrieval.

Chunks are stored per company in ``document_chunks``; every query is filtered by
``company_id`` in SQL, so one company's documents can never be retrieved for another.

BM25 with light prefix stemming works offline, costs nothing, and handles Russian and
Kazakh morphology reasonably for FAQ-style knowledge bases. Embedding search can be added
behind the same ``retrieve`` function later (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import DocumentChunk, StoredFile

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    # ru
    "и", "в", "во", "не", "на", "что", "как", "с", "со", "по", "для", "это", "а", "но", "или",
    "у", "к", "ко", "из", "от", "до", "за", "о", "об", "же", "ли", "бы", "мы", "вы", "я", "он",
    "она", "они", "мне", "нам", "вам", "ваш", "наш", "есть", "быть", "так", "там", "тут",
    "какой", "какая", "какие", "каков", "можно", "при", "под", "над", "все", "всё",
    # kk
    "және", "мен", "бен", "пен", "бұл", "сол", "үшін", "да", "де", "та", "те", "ма", "ме", "ба",
    "бе", "па", "пе", "қалай", "қандай", "бар", "жоқ", "ол", "біз", "сіз",
    # en
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be",
    "it", "do", "does", "you", "your", "we", "our", "what", "how", "can", "i", "my", "with",
}
_STEM_LEN = 6


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _TOKEN_RE.findall(text.lower().replace("ё", "е")):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        # Prefix stemming: "доставка/доставки/доставку" -> "достав".
        tokens.append(raw[:_STEM_LEN] if len(raw) > _STEM_LEN and not raw.isdigit() else raw)
    return tokens


def chunk_text(text: str, *, size: int = 900, overlap: int = 150) -> list[str]:
    """Split text on paragraph boundaries into ~``size``-char chunks with overlap."""
    overlap = max(0, min(overlap, size // 3))  # guarantees forward progress below
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-•*]\s)", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > size:  # hard-split very long paragraphs on sentence/space boundaries
            cut = max(para.rfind(". ", 0, size), para.rfind(" ", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            piece, para = para[:cut].strip(), para[max(0, cut - overlap):].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            current = f"{tail}\n{para}".strip() if tail else para
        else:
            current = f"{current}\n\n{para}".strip() if current else para
    if current:
        chunks.append(current)
    return chunks


@dataclass
class RetrievedChunk:
    chunk_id: str
    file_id: str
    filename: str
    chunk_index: int
    text: str
    score: float
    flagged_injection: bool


def retrieve(db: Session, *, company_id: str, query: str, file_ids: list[str] | None = None,
             top_k: int = 5, k1: float = 1.5, b: float = 0.75) -> list[RetrievedChunk]:
    """BM25 over the company's knowledge-base chunks (optionally limited to ``file_ids``)."""
    stmt = (
        select(DocumentChunk, StoredFile.filename)
        .join(StoredFile, StoredFile.id == DocumentChunk.file_id)
        .where(DocumentChunk.company_id == company_id, StoredFile.company_id == company_id)
    )
    if file_ids:
        stmt = stmt.where(DocumentChunk.file_id.in_(file_ids))
    else:
        stmt = stmt.where(StoredFile.purpose == "knowledge_base")
    rows = db.execute(stmt).all()
    query_terms = set(tokenize(query))
    if not rows or not query_terms:
        return []

    docs = [(chunk, filename, tokenize(chunk.text)) for chunk, filename in rows]
    n = len(docs)
    avgdl = sum(len(toks) for _, _, toks in docs) / n or 1.0
    df: Counter[str] = Counter()
    for _, _, toks in docs:
        df.update(set(toks) & query_terms)

    scored: list[RetrievedChunk] = []
    for chunk, filename, toks in docs:
        tf = Counter(t for t in toks if t in query_terms)
        if not tf:
            continue
        score = 0.0
        for term, freq in tf.items():
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * len(toks) / avgdl))
        scored.append(RetrievedChunk(chunk_id=chunk.id, file_id=chunk.file_id, filename=filename,
                                     chunk_index=chunk.chunk_index, text=chunk.text,
                                     score=round(score, 4), flagged_injection=chunk.flagged_injection))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_k]
