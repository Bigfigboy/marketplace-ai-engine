"""Authentication: API keys and (optionally) JWTs issued by the marketplace backend.

The user and company of every request are resolved here, on the server, from verified
credentials. Request bodies never carry ``user_id`` / ``company_id``.

Supported credentials (either header works):
* ``Authorization: Bearer aie_...`` or ``X-API-Key: aie_...`` - an engine API key
  (stored only as a SHA-256 hash).
* ``Authorization: Bearer <JWT>`` - when ``JWT_SECRET`` is set. Required claims:
  ``sub`` (marketplace user id) and ``company_id`` (marketplace company id); optional
  ``role`` ("member" | "admin") and ``company_name``. Users/companies are provisioned on
  first sight, keyed by their marketplace ids.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthError
from app.database.models import ApiKey, Company, User

API_KEY_PREFIX = "aie_"


@dataclass(frozen=True)
class Principal:
    user_id: str
    company_id: str
    role: str
    auth_method: str  # "api_key" | "jwt"
    key_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def rate_limit_key(self) -> str:
        return f"user:{self.user_id}"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_api_key(db: Session, *, company_id: str, user_id: str, name: str = "default",
                   raw_key: str | None = None) -> tuple[ApiKey, str]:
    raw = raw_key or generate_api_key()
    if not raw.startswith(API_KEY_PREFIX) or len(raw) < 24:
        raise ValueError("API keys must start with 'aie_' and be at least 24 characters long")
    record = ApiKey(company_id=company_id, user_id=user_id, name=name,
                    key_prefix=raw[:10], key_hash=hash_api_key(raw))
    db.add(record)
    db.flush()
    return record, raw


def extract_credential(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return None


def authenticate(db: Session, settings: Settings, credential: str) -> Principal:
    if credential.startswith(API_KEY_PREFIX):
        return _authenticate_api_key(db, credential)
    if settings.jwt_secret is not None and credential.count(".") == 2:
        return _authenticate_jwt(db, settings, credential)
    raise AuthError("Invalid credentials", code="invalid_credentials")


def _authenticate_api_key(db: Session, raw_key: str) -> Principal:
    digest = hash_api_key(raw_key)
    record = db.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
    # Constant-time comparison on the digest (defence in depth; lookup is by hash).
    if record is None or not hmac.compare_digest(record.key_hash, digest) or not record.is_active:
        raise AuthError("Invalid credentials", code="invalid_credentials")
    user = db.get(User, record.user_id)
    company = db.get(Company, record.company_id)
    if user is None or company is None or not user.is_active or not company.is_active:
        raise AuthError("Invalid credentials", code="invalid_credentials")
    # Throttle last_used_at writes to once a minute.
    now = datetime.now(UTC)
    last = record.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or (now - last).total_seconds() > 60:
        record.last_used_at = now
        db.commit()
    return Principal(user_id=user.id, company_id=company.id, role=user.role,
                     auth_method="api_key", key_id=record.id)


def _authenticate_jwt(db: Session, settings: Settings, token: str) -> Principal:
    claims = _decode_jwt(settings, token)
    try:
        return _provision_from_claims(db, claims)
    except IntegrityError:
        # Two first-time requests for the same user/company raced; the other one created it.
        db.rollback()
        return _provision_from_claims(db, claims)


def _decode_jwt(settings: Settings, token: str) -> dict:
    import jwt

    assert settings.jwt_secret is not None
    options = {"require": ["exp", "sub"]}
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={**options, "verify_aud": settings.jwt_audience is not None},
            leeway=30,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid credentials", code="invalid_credentials") from exc
    return claims


def _provision_from_claims(db: Session, claims: dict) -> Principal:
    ext_user = str(claims.get("sub") or "").strip()
    ext_company = str(claims.get("company_id") or "").strip()
    if not ext_user or not ext_company or len(ext_user) > 128 or len(ext_company) > 128:
        raise AuthError("Token is missing required claims", code="invalid_credentials")
    role = claims.get("role", "member")
    role = role if role in ("member", "admin") else "member"

    company = db.scalar(select(Company).where(Company.external_id == ext_company))
    if company is None:
        name = str(claims.get("company_name") or f"Company {ext_company}")[:200]
        company = Company(external_id=ext_company, name=name)
        db.add(company)
        db.flush()
    if not company.is_active:
        raise AuthError("Invalid credentials", code="invalid_credentials")
    user = db.scalar(select(User).where(User.company_id == company.id, User.external_id == ext_user))
    if user is None:
        user = User(company_id=company.id, external_id=ext_user, role=role, display_name="")
        db.add(user)
    elif user.role != role:
        user.role = role  # marketplace is the source of truth for roles
    db.commit()
    if not user.is_active:
        raise AuthError("Invalid credentials", code="invalid_credentials")
    return Principal(user_id=user.id, company_id=company.id, role=role, auth_method="jwt")


def issue_test_jwt(settings: Settings, *, sub: str, company_id: str, role: str = "member",
                   ttl_seconds: int = 3600) -> str:
    """Helper for tests and local integration (the marketplace issues real tokens)."""
    import jwt

    assert settings.jwt_secret is not None
    now = int(time.time())
    claims = {"sub": sub, "company_id": company_id, "role": role, "iat": now, "exp": now + ttl_seconds}
    if settings.jwt_audience:
        claims["aud"] = settings.jwt_audience
    if settings.jwt_issuer:
        claims["iss"] = settings.jwt_issuer
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
