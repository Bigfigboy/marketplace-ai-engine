"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AuthError
from app.security.auth import Principal

# Declared for OpenAPI/Swagger ("Authorize" button). Enforcement happens in AuthMiddleware.
_bearer = HTTPBearer(auto_error=False, description="Engine API key (aie_...) or marketplace JWT")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Engine API key (aie_...)")


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_principal(request: Request, _b=Security(_bearer), _k=Security(_api_key)) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:  # defence in depth if middleware is misconfigured
        raise AuthError("Authentication required", code="auth_required")
    return principal


DB = Depends(get_db)
CurrentPrincipal = Depends(get_principal)
