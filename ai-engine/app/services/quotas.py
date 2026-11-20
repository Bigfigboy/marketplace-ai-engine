"""Demo limits and spend control, computed from the ``agent_runs`` table."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import LimitExceededError
from app.database.models import AgentRun
from app.security.auth import Principal
from app.services.pricing import estimate_cost

# Runs rejected before execution (validation, quota) do not consume quota.
NON_BILLABLE_STATUSES = ("rejected",)


def _day_bounds(settings: Settings) -> tuple[datetime, int]:
    try:
        tz = ZoneInfo(settings.quota_timezone)
    except Exception:  # noqa: BLE001
        tz = UTC
    now = datetime.now(tz)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_left = int((start_local + timedelta(days=1) - now).total_seconds()) + 1
    return start_local.astimezone(UTC), seconds_left


def runs_today(db: Session, settings: Settings, principal: Principal) -> int:
    start, _ = _day_bounds(settings)
    return db.scalar(select(func.count()).select_from(AgentRun).where(
        AgentRun.user_id == principal.user_id, AgentRun.created_at >= start,
        AgentRun.status.not_in(NON_BILLABLE_STATUSES))) or 0


def _spend_today(db: Session, settings: Settings, company_id: str | None = None) -> float:
    start, _ = _day_bounds(settings)
    stmt = select(func.coalesce(func.sum(AgentRun.estimated_cost), 0.0)).where(AgentRun.created_at >= start)
    if company_id:
        stmt = stmt.where(AgentRun.company_id == company_id)
    return float(db.scalar(stmt) or 0.0)


def check_quotas(db: Session, settings: Settings, principal: Principal) -> None:
    _, seconds_left = _day_bounds(settings)
    retry = {"Retry-After": str(seconds_left)}
    if settings.demo_mode and runs_today(db, settings, principal) >= settings.daily_run_limit_per_user:
        raise LimitExceededError(
            f"Daily demo limit of {settings.daily_run_limit_per_user} requests per user reached.",
            code="daily_limit_exceeded", headers=retry)
    if settings.daily_spend_limit is not None and _spend_today(db, settings) >= settings.daily_spend_limit:
        raise LimitExceededError("The engine's daily spend limit has been reached.",
                                 code="spend_limit_exceeded", headers=retry)
    if (settings.company_daily_spend_limit is not None
            and _spend_today(db, settings, principal.company_id) >= settings.company_daily_spend_limit):
        raise LimitExceededError("Your company's daily spend limit has been reached.",
                                 code="company_spend_limit_exceeded", headers=retry)


# Serialises "check quotas + record the run" so concurrent requests cannot all pass the
# same check (check-then-act race). Process-local lock + a PostgreSQL advisory lock that
# covers several replicas sharing one database.
_RESERVE_LOCK = threading.Lock()
_PG_LOCK_KEY = 0x41495F454E47  # arbitrary constant ("AI_ENG")


def reserve_run(db: Session, settings: Settings, principal: Principal, run: AgentRun) -> None:
    """Atomically check limits and persist ``run`` as ``running`` with a worst-case cost reservation.

    The reservation (max input + max output tokens at configured prices) counts against
    spend limits until the run finishes and the actual cost replaces it.
    """
    with _RESERVE_LOCK:
        try:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PG_LOCK_KEY})
            check_quotas(db, settings, principal)
            run.status = "running"
            run.estimated_cost = estimate_cost(settings, settings.max_input_tokens, settings.max_output_tokens)
            db.add(run)
            db.commit()  # also releases the advisory lock
        except Exception:
            db.rollback()
            raise
