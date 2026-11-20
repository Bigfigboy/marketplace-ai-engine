"""Public HTTP API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import DB, CurrentPrincipal
from app.core.errors import FileValidationError, NotFoundError
from app.database.models import AgentRun
from app.schemas.common import (
    AgentInfo,
    AgentList,
    ErrorResponse,
    FileInfo,
    FileList,
    FileUploadResponse,
    HealthResponse,
    RunList,
    RunRecord,
    RunRequest,
    RunResponse,
    UsageStats,
)
from app.security.auth import Principal
from app.services import files as file_service
from app.services.llm import UnconfiguredProvider
from app.services.llm_providers import PROFILES
from app.services.quotas import runs_today
from app.services.runner import execute_run
from app.services.stats import agent_usage

ERRORS = {
    401: {"model": ErrorResponse, "description": "Missing or invalid credentials"},
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Validation error"},
    429: {"model": ErrorResponse, "description": "Rate, daily or spend limit exceeded"},
}

router = APIRouter()


# ── Health ───────────────────────────────────────────────────────────────────
@router.get("/health", response_model=HealthResponse, tags=["system"], summary="Service health (public)")
def health(request: Request) -> HealthResponse:
    state = request.app.state
    db_status = "ok"
    try:
        with state.session_factory() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "error"
    llm = state.llm
    problem = getattr(llm, "code", None) if isinstance(llm, UnconfiguredProvider) else None
    profile = PROFILES.get(llm.mode)
    return HealthResponse(
        status="ok" if db_status == "ok" and problem is None else "degraded",
        version=__version__, llm_mode=llm.mode, llm_provider=llm.mode,
        llm_billing=profile.billing if profile else "none", llm_configured=problem is None,
        llm_problem=problem, model=llm.model, database=db_status, agents=state.registry.slugs(),
    )


# ── Agents ───────────────────────────────────────────────────────────────────
@router.get("/agents", response_model=AgentList, tags=["agents"], responses=ERRORS, summary="List agents")
def list_agents(request: Request, principal: Principal = CurrentPrincipal) -> AgentList:
    return AgentList(agents=[a.info() for a in request.app.state.registry.all()])


@router.get("/agents/{slug}", response_model=AgentInfo, tags=["agents"], responses=ERRORS,
            summary="Agent metadata, input and output schema")
def get_agent(slug: str, request: Request, principal: Principal = CurrentPrincipal) -> AgentInfo:
    return request.app.state.registry.get(slug).info()


@router.post("/agents/{slug}/run", response_model=RunResponse, tags=["agents"],
             responses={**ERRORS, 413: {"model": ErrorResponse}, 502: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
             summary="Run an agent")
def run_agent(slug: str, body: RunRequest, request: Request, db: Session = DB,
              principal: Principal = CurrentPrincipal) -> RunResponse:
    state = request.app.state
    return execute_run(db=db, settings=state.settings, registry=state.registry, llm=state.llm,
                       principal=principal, slug=slug, request=body)


# ── Files ────────────────────────────────────────────────────────────────────
@router.post("/files/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED,
             tags=["files"], responses={**ERRORS, 413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}},
             summary="Upload a PDF, DOCX or TXT file (max 10 MB)")
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF, DOCX or TXT")],
    purpose: Annotated[str, Form(description="knowledge_base | resume | job_description | brief")] = "knowledge_base",
    db: Session = DB,
    principal: Principal = CurrentPrincipal,
) -> FileUploadResponse:
    settings = request.app.state.settings
    limit = settings.max_file_size_bytes
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > limit:
            raise FileValidationError(f"File exceeds the {settings.max_file_size_mb:g} MB limit.",
                                      code="file_too_large", status_code=413)
    from starlette.concurrency import run_in_threadpool

    record, warnings = await run_in_threadpool(
        file_service.save_upload, db, settings, principal, filename=file.filename, data=bytes(data), purpose=purpose)
    return FileUploadResponse(**FileInfo.model_validate(record).model_dump(), warnings=warnings)


@router.get("/files", response_model=FileList, tags=["files"], responses=ERRORS, summary="List accessible files")
def list_files(purpose: str | None = Query(default=None, max_length=32), db: Session = DB,
               principal: Principal = CurrentPrincipal) -> FileList:
    return FileList(files=[FileInfo.model_validate(f) for f in file_service.list_files(db, principal, purpose)])


@router.get("/files/{file_id}", response_model=FileInfo, tags=["files"], responses=ERRORS, summary="File metadata")
def get_file(file_id: str, db: Session = DB, principal: Principal = CurrentPrincipal) -> FileInfo:
    return FileInfo.model_validate(file_service.get_file(db, principal, file_id))


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["files"], responses=ERRORS,
               summary="Delete a file, its extracted text and knowledge-base chunks")
def delete_file(file_id: str, request: Request, db: Session = DB,
                principal: Principal = CurrentPrincipal) -> Response:
    file_service.delete_file(db, request.app.state.settings, principal, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Runs ─────────────────────────────────────────────────────────────────────
def _runs_query(principal: Principal):
    stmt = select(AgentRun).where(AgentRun.company_id == principal.company_id)
    if not principal.is_admin:
        stmt = stmt.where(AgentRun.user_id == principal.user_id)
    return stmt


@router.get("/runs", response_model=RunList, tags=["runs"], responses=ERRORS,
            summary="Run history (own runs; company-wide for admins)")
def list_runs(agent: str | None = Query(default=None, max_length=64),
              limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
              db: Session = DB, principal: Principal = CurrentPrincipal) -> RunList:
    stmt = _runs_query(principal)
    if agent:
        stmt = stmt.where(AgentRun.agent_slug == agent)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset))
    return RunList(runs=[RunRecord.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunRecord, tags=["runs"], responses=ERRORS, summary="Run metadata")
def get_run(run_id: str, db: Session = DB, principal: Principal = CurrentPrincipal) -> RunRecord:
    run = db.scalar(_runs_query(principal).where(AgentRun.id == run_id))
    if run is None:
        raise NotFoundError("Run not found", code="run_not_found")
    return RunRecord.model_validate(run)


# ── Stats ────────────────────────────────────────────────────────────────────
@router.get("/stats/agents", response_model=UsageStats, tags=["runs"], responses=ERRORS,
            summary="Usage statistics per agent (own; company-wide for admins)")
def usage_stats(request: Request, days: int = Query(default=30, ge=1, le=365), db: Session = DB,
                principal: Principal = CurrentPrincipal) -> UsageStats:
    settings = request.app.state.settings
    return UsageStats(scope="company" if principal.is_admin else "user", period_days=days,
                      agents=agent_usage(db, principal, days=days, company_scope=principal.is_admin),
                      today_runs_by_you=runs_today(db, settings, principal),
                      daily_run_limit=settings.daily_run_limit_per_user if settings.demo_mode else None)
