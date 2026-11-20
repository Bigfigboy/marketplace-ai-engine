"""AI Customer Support Agent (slug: support).

RAG over the calling company's own knowledge base. Answers are grounded in retrieved
fragments, every answer cites sources, and anything not covered is returned with
``status = escalation_required`` and a prepared (not sent) manager handoff.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.core.i18n import t
from app.database.models import StoredFile
from app.prompts.support import build_system_prompt
from app.schemas.common import Source
from app.security.prompt_guard import sanitize_document_text, wrap_untrusted
from app.services.jsonutil import as_str, as_str_list, parse_json_object
from app.services.rag import RetrievedChunk, retrieve, tokenize


class SupportOptions(BaseModel):
    mode: Literal["auto", "question", "complaint", "faq"] = Field(
        default="auto", description="Hint about the request type; 'auto' lets the agent classify it")
    top_k: int | None = Field(default=None, ge=1, le=10, description="Number of fragments to retrieve")


class Handoff(BaseModel):
    summary: str
    category: str
    priority: Literal["low", "normal", "high"]
    missing_information: list[str]
    suggested_next_steps: list[str]
    delivered: bool = False
    delivery_note: str


class SupportOutput(BaseModel):
    intent: Literal["question", "complaint", "faq", "other"]
    answer_status: Literal["answered", "escalation_required"]
    handoff: Handoff | None
    retrieved_fragments: int
    neutralized_injection_fragments: int


_COMPLAINT_RE = re.compile(
    r"жалоб|недовол|ужасн|отвратит|возмущ|плохо обслуж|обман|верните деньги|не работает|сломал|"
    r"complain|terrible|awful|disappointed|refund me|scam|"
    r"шағым|наразы|нашар|алдады", re.IGNORECASE)
_FORWARD_CLAIM_RE = re.compile(
    r"[^.!?\n]*(переда(л|ла|ли|но|на|ны)\b[^.!?\n]*менеджер|менеджер[^.!?\n]*(свяжется|перезвонит)|"
    r"(forwarded|escalated|sent|passed)\b[^.!?\n]*manager|manager will (contact|call)|"
    r"менеджерге (жіберілді|берілді)|менеджер[^.!?\n]*хабарласады)[^.!?\n]*[.!?]?", re.IGNORECASE)


def _intent_heuristic(message: str, mode: str) -> str:
    if mode in ("question", "complaint", "faq"):
        return mode
    return "complaint" if _COMPLAINT_RE.search(message) else "question"


def _best_sentences(text: str, query: str, limit: int = 3) -> str:
    terms = set(tokenize(query))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    scored = sorted(sentences, key=lambda s: len(terms & set(tokenize(s))), reverse=True)
    chosen = [s for s in scored[:limit] if terms & set(tokenize(s))]
    # Keep original order for readability.
    return " ".join(s for s in sentences if s in chosen)


class SupportAgent(BaseAgent):
    id = "agent-support-001"
    slug = "support"
    name = "AI Customer Support Agent"
    description = ("Answers customer questions, FAQs and complaints strictly from the company's own uploaded "
                   "documents (RAG), cites sources, and prepares a manager handoff when information is missing.")
    version = "1.0.0"
    category = "customer_support"
    capabilities = ("knowledge_base_qa", "faq", "complaint_handling", "source_citation",
                    "escalation_detection", "manager_handoff_draft")
    accepted_file_purposes = ("knowledge_base",)
    max_files = 10
    options_model = SupportOptions
    output_model = SupportOutput

    def run(self, message: str, options: BaseModel, ctx: AgentContext) -> AgentResult:
        assert isinstance(options, SupportOptions)
        lang = ctx.language
        intent = _intent_heuristic(message, options.mode)
        file_ids = [f.id for f in ctx.files] or None

        kb_count = ctx.db.scalar(select(func.count()).select_from(StoredFile).where(
            StoredFile.company_id == ctx.principal.company_id, StoredFile.purpose == "knowledge_base"))
        if not kb_count and not file_ids:
            return self._escalate(ctx, message, intent, [], reason=t("support_no_kb", lang), retrieved=0)

        top_k = options.top_k or ctx.settings.rag_top_k
        chunks = [c for c in retrieve(ctx.db, company_id=ctx.principal.company_id, query=message,
                                      file_ids=file_ids, top_k=top_k)
                  if c.score >= ctx.settings.rag_min_score]
        if not chunks:
            return self._escalate(ctx, message, intent, [], reason=t("support_no_info", lang), retrieved=0)

        # Build the grounded context with stable fragment ids S1..Sn.
        neutralized = 0
        blocks: list[tuple[str, str]] = []
        id_map: dict[str, RetrievedChunk] = {}
        per_chunk_budget = max(300, (ctx.settings.max_input_tokens // 2) // len(chunks))
        for i, chunk in enumerate(chunks, start=1):
            sanitized = sanitize_document_text(chunk.text)
            neutralized += 1 if sanitized.flagged else 0
            text, _ = ctx.fit_text(sanitized.text, per_chunk_budget)
            sid = f"S{i}"
            id_map[sid] = chunk
            blocks.append((sid, f"[file: {chunk.filename}]\n{text}"))

        user_content = (f"{wrap_untrusted(blocks, kind='untrusted_knowledge_base')}\n\n"
                        f"CUSTOMER MESSAGE:\n{message}")
        messages = [{"role": "system", "content": build_system_prompt(lang, options.mode)},
                    *ctx.history_messages(), {"role": "user", "content": user_content}]
        result = ctx.call_llm(messages, json_mode=True,
                              mock_fn=lambda: self._mock(message, chunks, intent, lang))

        parsed = parse_json_object(result.text)
        warnings: list[str] = []
        if neutralized:
            warnings.append("instruction_like_text_in_documents_ignored")
        if parsed is None:
            warnings.append("model_output_not_valid_json")
            return self._escalate(ctx, message, intent, [], reason=t("support_no_info", lang),
                                  retrieved=len(chunks), neutralized=neutralized, warnings=warnings)

        status = parsed.get("status")
        intent = parsed.get("intent") if parsed.get("intent") in ("question", "complaint", "faq", "other") else intent
        answer = as_str(parsed.get("answer"))
        cited = [sid for sid in as_str_list(parsed.get("source_ids")) if sid in id_map]
        if len(cited) != len(as_str_list(parsed.get("source_ids"))):
            warnings.append("unknown_source_ids_dropped")

        # Strict grounding: an answer without valid citations is not accepted.
        if status == "answered" and not cited:
            warnings.append("answer_without_sources_rejected")
            status = "escalation_required"
            answer = t("support_no_info", lang)
        if status not in ("answered", "escalation_required") or not answer:
            status, answer = "escalation_required", answer or t("support_no_info", lang)

        if _FORWARD_CLAIM_RE.search(answer):
            answer = _FORWARD_CLAIM_RE.sub("", answer).strip() or t("support_no_info", lang)
            warnings.append("unsupported_handoff_claim_removed")

        sources = [self._source(id_map[sid]) for sid in dict.fromkeys(cited)]
        handoff = None
        if status == "escalation_required" or intent == "complaint":
            handoff = self._handoff(parsed.get("handoff"), message, intent, lang)
        response = answer
        if handoff is not None:
            response = f"{answer}\n\n{handoff.delivery_note}"
        data = SupportOutput(intent=intent, answer_status=status, handoff=handoff,
                             retrieved_fragments=len(chunks),
                             neutralized_injection_fragments=neutralized).model_dump()
        return AgentResult(response=response, status="success" if status == "answered" else "escalation_required",
                           sources=sources, data=data, warnings=warnings)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _source(chunk: RetrievedChunk) -> Source:
        excerpt = re.sub(r"\s+", " ", chunk.text).strip()
        return Source(file_id=chunk.file_id, filename=chunk.filename, chunk_index=chunk.chunk_index,
                      score=chunk.score, excerpt=excerpt[:300] + ("…" if len(excerpt) > 300 else ""))

    @staticmethod
    def _handoff(raw: Any, message: str, intent: str, lang: str) -> Handoff:
        raw = raw if isinstance(raw, dict) else {}
        priority = raw.get("priority") if raw.get("priority") in ("low", "normal", "high") else (
            "high" if intent == "complaint" else "normal")
        summary = as_str(raw.get("summary"), 2000) or message[:500]
        return Handoff(summary=summary, category=as_str(raw.get("category"), 100) or intent,
                       priority=priority, missing_information=as_str_list(raw.get("missing_information"), 10),
                       suggested_next_steps=as_str_list(raw.get("suggested_next_steps"), 10),
                       delivered=False, delivery_note=t("handoff_not_delivered", lang))

    def _escalate(self, ctx: AgentContext, message: str, intent: str, sources: list[Source], *, reason: str,
                  retrieved: int, neutralized: int = 0, warnings: list[str] | None = None) -> AgentResult:
        handoff = self._handoff({"missing_information": ["knowledge_base_has_no_answer"]}, message, intent,
                                ctx.language)
        response = reason
        if ctx.llm.mode == "mock":
            response = f"{t('mock_banner', ctx.language)}\n\n{reason}"
        data = SupportOutput(intent=intent, answer_status="escalation_required", handoff=handoff,
                             retrieved_fragments=retrieved,
                             neutralized_injection_fragments=neutralized).model_dump()
        return AgentResult(response=f"{response}\n\n{handoff.delivery_note}", status="escalation_required",
                           sources=sources, data=data, warnings=list(warnings or []))

    @staticmethod
    def _mock(message: str, chunks: list[RetrievedChunk], intent: str, lang: str) -> str:
        """Extractive offline answer: quotes the best-matching sentences of the top fragment."""
        top = chunks[0]
        quote = _best_sentences(sanitize_document_text(top.text).text, message)
        if not quote:
            return json.dumps({"status": "escalation_required", "intent": intent, "answer": "",
                               "source_ids": [], "handoff": None})
        answer = f"{t('mock_banner', lang)}\n\n{quote}"
        return json.dumps({"status": "answered", "intent": intent, "answer": answer, "source_ids": ["S1"],
                           "handoff": None}, ensure_ascii=False)


AGENT = SupportAgent()
