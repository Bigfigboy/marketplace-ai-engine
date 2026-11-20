"""Agent execution pipeline shared by every agent.

validate -> resolve language -> load files (access-checked) -> quotas -> guardrails
-> agent.precheck -> agent.run -> cost -> persist ``agent_runs`` -> unified response.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentResult
from app.agents.registry import AgentRegistry
from app.core.config import Settings
from app.core.errors import AppError, LimitExceededError
from app.core.i18n import t
from app.database.models import AgentRun
from app.schemas.common import RunRequest, RunResponse, Usage
from app.security.auth import Principal
from app.security.prompt_guard import detect_override_attempt
from app.services.files import load_files
from app.services.language import detect_language
from app.services.llm import LLMProvider
from app.services.pricing import estimate_cost
from app.services.quotas import check_quotas

logger = logging.getLogger("app.runner")


def _persist(db: Session, run: AgentRun) -> None:
    try:
        db.add(run)
        db.commit()
    except Exception:  # noqa: BLE001 - logging must never mask the real outcome
        db.rollback()
        logger.error("agent_run_persist_failed", extra={"run_id": run.id})


def execute_run(*, db: Session, settings: Settings, registry: AgentRegistry, llm: LLMProvider,
                principal: Principal, slug: str, request: RunRequest) -> RunResponse:
    agent = registry.get(slug)
    run_id = str(uuid.uuid4())
    started = time.perf_counter()

    language = request.language if request.language in ("ru", "kk", "en") else detect_language(
        request.message, default=settings.default_language)

    run = AgentRun(id=run_id, user_id=principal.user_id, company_id=principal.company_id, agent_slug=agent.slug,
                   agent_version=agent.version, status="error", language=language,
                   file_count=len(set(request.file_ids)))

    def _finish(status: str, error_code: str | None = None) -> int:
        elapsed = int((time.perf_counter() - started) * 1000)
        run.status, run.error_code, run.execution_time_ms = status, error_code, elapsed
        _persist(db, run)
        logger.info("agent_run", extra={"run_id": run_id, "agent": agent.slug, "status": status,
                                        "error_code": error_code, "input_tokens": run.input_tokens,
                                        "output_tokens": run.output_tokens, "duration_ms": elapsed,
                                        "llm_mode": run.llm_mode})
        return elapsed

    try:
        options = agent.parse_options(request.options)
        if len(request.message) > settings.max_message_chars:
            raise AppError(f"Message exceeds {settings.max_message_chars} characters.",
                           code="message_too_long", status_code=422)
        if language not in agent.supported_languages:
            raise AppError("Language not supported by this agent.", code="unsupported_language", status_code=422)
        if len(set(request.file_ids)) > agent.max_files:
            raise AppError(f"This agent accepts at most {agent.max_files} files.", code="too_many_files",
                           status_code=422)
        files = load_files(db, settings, principal, request.file_ids)
        bad = [f.id for f in files if f.purpose not in agent.accepted_file_purposes]
        if bad:
            raise AppError(f"This agent accepts files with purpose: {', '.join(agent.accepted_file_purposes) or 'none'}.",
                           code="file_purpose_not_accepted", status_code=422, details={"file_ids": bad})
        check_quotas(db, settings, principal)
    except AppError as exc:
        _finish("rejected", exc.code)
        exc.details = {**exc.details, "run_id": run_id}
        raise

    ctx = AgentContext(run_id=run_id, settings=settings, db=db, principal=principal, language=language,
                       files=files, history=request.history, llm=llm,
                       deadline=time.monotonic() + settings.request_timeout_seconds)
    try:
        texts = [request.message] + [h.content for h in request.history if h.role == "user"]
        if any(detect_override_attempt(x) for x in texts):
            result = AgentResult(response=t("blocked_injection", language), status="blocked",
                                 warnings=["prompt_injection_attempt_blocked"])
        else:
            result = agent.precheck(request.message, options, ctx) or agent.run(request.message, options, ctx)
    except AppError as exc:
        run.input_tokens, run.output_tokens = ctx.input_tokens, ctx.output_tokens
        run.llm_mode, run.model = ctx.llm_mode, ctx.model
        run.estimated_cost = estimate_cost(settings, ctx.input_tokens, ctx.output_tokens) if ctx.input_tokens else None
        _finish("rejected" if exc.status_code < 500 and not isinstance(exc, LimitExceededError) else "error", exc.code)
        exc.details = {**exc.details, "run_id": run_id}
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("agent_crashed", extra={"run_id": run_id, "agent": agent.slug,
                                             "error_type": type(exc).__name__})
        _finish("error", "internal_error")
        raise AppError("Internal error while running the agent", code="internal_error", status_code=500,
                       details={"run_id": run_id}) from None

    if ctx.llm_mode == "openai":
        cost = estimate_cost(settings, ctx.input_tokens, ctx.output_tokens)
    else:
        cost = 0.0  # no paid API call was made (mock mode or no LLM call needed)
    run.input_tokens, run.output_tokens, run.estimated_cost = ctx.input_tokens, ctx.output_tokens, cost
    run.llm_mode, run.model, run.source_count = ctx.llm_mode, ctx.model, len(result.sources)
    elapsed = _finish(result.status)

    return RunResponse(
        run_id=run_id, agent=agent.slug, agent_version=agent.version, status=result.status,  # type: ignore[arg-type]
        response=result.response, language=language, sources=result.sources,  # type: ignore[arg-type]
        usage=Usage(input_tokens=ctx.input_tokens, output_tokens=ctx.output_tokens,
                    total_tokens=ctx.input_tokens + ctx.output_tokens, estimated_cost=cost,
                    currency=settings.price_currency),
        execution_time_ms=elapsed, mode=ctx.llm_mode, model=ctx.model,  # type: ignore[arg-type]
        data=result.data, warnings=list(dict.fromkeys(ctx.warnings + result.warnings)),
    )
