"""FastAPI application factory.

Run locally:  uvicorn app.main:app --reload
Swagger UI:   http://localhost:8000/docs
Demo UI:      http://localhost:8000/demo/
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.agents.registry import AgentRegistry
from app.api.routes import router
from app.core.config import BASE_DIR, Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.database.models import ApiKey, Company, User
from app.database.session import build_engine, build_sessionmaker, run_migrations
from app.security.auth import create_api_key, hash_api_key
from app.security.middleware import AuthMiddleware, RequestContextMiddleware
from app.security.rate_limit import SlidingWindowRateLimiter
from app.services.llm import LLMProvider, build_llm_provider

logger = logging.getLogger("app")

DESCRIPTION = """
Unified API for the AI-agent marketplace (Kazakhstan). Agents: **marketing**, **support**, **hr**.

**Authentication:** click *Authorize* and paste an engine API key (`aie_...`) or a marketplace JWT.
The user and company are always derived from the credential, never from the request body.

Each response reports `mode`: `openai` (real OpenAI API call), `mock` (explicit offline mode,
not AI output) or `none` (no LLM call was required, e.g. a policy refusal or escalation).
"""


def _seed_demo_key(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if settings.demo_api_key is None:
        return
    raw = settings.demo_api_key.get_secret_value()
    with app.state.session_factory() as db:
        if db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw))):
            return
        company = Company(name=settings.demo_company_name, external_id="demo-company")
        db.add(company)
        db.flush()
        user = User(company_id=company.id, external_id="demo-admin", display_name="Demo admin", role="admin")
        db.add(user)
        db.flush()
        create_api_key(db, company_id=company.id, user_id=user.id, name="demo", raw_key=raw)
        db.commit()
        logger.info("demo_api_key_seeded", extra={"company_id": company.id})


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_migrate:
            run_migrations(settings.database_url)
        Path(settings.storage_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        _seed_demo_key(app)
        logger.info("startup", extra={"llm_mode": settings.llm_mode, "model": settings.openai_model,
                                      "agents": ",".join(app.state.registry.slugs())})
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title=settings.app_name, version=__version__, description=DESCRIPTION, lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.session_factory = build_sessionmaker(app.state.engine)
    app.state.llm = llm or build_llm_provider(settings)
    app.state.rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute)
    app.state.auth_failure_limiter = SlidingWindowRateLimiter(settings.auth_failures_per_minute)
    registry = AgentRegistry()
    registry.load_modules(settings.agent_module_list)
    app.state.registry = registry

    install_error_handlers(app)
    # Order: the last added middleware runs first (CORS -> context -> auth -> routes).
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.include_router(router)

    demo_dir = BASE_DIR / "demo"
    if settings.enable_demo_ui and demo_dir.is_dir():
        app.mount("/demo", StaticFiles(directory=demo_dir, html=True), name="demo")

        @app.get("/", include_in_schema=False)
        def _root() -> RedirectResponse:
            return RedirectResponse(url="/demo/")
    return app


app = create_app()
