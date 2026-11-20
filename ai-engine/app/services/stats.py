"""Usage statistics per agent, from ``agent_runs``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.database.models import AgentRun
from app.schemas.common import AgentUsageStats
from app.security.auth import Principal


def agent_usage(db: Session, principal: Principal, *, days: int, company_scope: bool) -> list[AgentUsageStats]:
    since = datetime.now(UTC) - timedelta(days=days)

    def count_status(status: str):
        return func.sum(case((AgentRun.status == status, 1), else_=0))

    stmt = (
        select(
            AgentRun.agent_slug,
            func.count(),
            count_status("success"),
            count_status("escalation_required"),
            count_status("blocked"),
            func.sum(case((AgentRun.status.in_(("error", "rejected")), 1), else_=0)),
            func.coalesce(func.sum(AgentRun.input_tokens), 0),
            func.coalesce(func.sum(AgentRun.output_tokens), 0),
            func.sum(AgentRun.estimated_cost),
            func.coalesce(func.avg(AgentRun.execution_time_ms), 0),
        )
        .where(AgentRun.company_id == principal.company_id, AgentRun.created_at >= since)
        .group_by(AgentRun.agent_slug)
        .order_by(AgentRun.agent_slug)
    )
    if not company_scope:
        stmt = stmt.where(AgentRun.user_id == principal.user_id)
    return [
        AgentUsageStats(agent_slug=row[0], runs=row[1], success=row[2] or 0, escalation_required=row[3] or 0,
                        blocked=row[4] or 0, errors=row[5] or 0, input_tokens=int(row[6]), output_tokens=int(row[7]),
                        estimated_cost=round(float(row[8]), 8) if row[8] is not None else None,
                        avg_execution_time_ms=int(row[9]))
        for row in db.execute(stmt).all()
    ]
