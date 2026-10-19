"""告警规则管理路由。"""
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import DbDep, UserDep, require_roles
from app.core.errors import NotFoundError
from app.db.models.identity import Role
from app.db.models.ops import AlertComparator, AlertEvent, AlertRule
from app.modules.alerts.service import AlertService

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

WriteDep = Annotated[object, Depends(require_roles(Role.ADMIN, Role.ANALYST))]


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    metric: str = Field("revenue", pattern="^(revenue|gross_profit|quantity)$")
    dimension: str = Field("total", pattern="^(total|region|product|customer|channel)$")
    comparator: AlertComparator
    threshold: float
    severity: str = Field("warning", pattern="^(warning|critical)$")
    channels: list[str] = Field(default_factory=list)
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    threshold: float | None = None
    enabled: bool | None = None
    channels: list[str] | None = None


def _ser(rule: AlertRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "metric": rule.metric,
        "dimension": rule.dimension,
        "comparator": rule.comparator.value,
        "threshold": rule.threshold,
        "severity": rule.severity,
        "channels": rule.channels,
        "enabled": rule.enabled,
        "last_triggered_at": rule.last_triggered_at.isoformat() if rule.last_triggered_at else None,
    }


@router.get("/rules")
def list_rules(session: DbDep, _: UserDep):
    rows = session.query(AlertRule).order_by(AlertRule.created_at.desc()).all()
    return [_ser(r) for r in rows]


@router.post("/rules", status_code=201)
def create_rule(payload: RuleIn, session: DbDep, user: WriteDep):
    rule = AlertRule(
        id=f"rule-{uuid4().hex[:12]}",
        name=payload.name,
        metric=payload.metric,
        dimension=payload.dimension,
        comparator=payload.comparator,
        threshold=payload.threshold,
        severity=payload.severity,
        channels=payload.channels,
        enabled=payload.enabled,
        created_by=getattr(user, "id", None),
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return _ser(rule)


@router.patch("/rules/{rule_id}")
def patch_rule(rule_id: str, payload: RulePatch, session: DbDep, _: WriteDep):
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise NotFoundError("告警规则")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    session.commit()
    session.refresh(rule)
    return _ser(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, session: DbDep, _: WriteDep):
    rule = session.get(AlertRule, rule_id)
    if rule is None:
        raise NotFoundError("告警规则")
    session.delete(rule)
    session.commit()
    return {"message": "已删除"}


@router.post("/check")
def run_check(session: DbDep, user: UserDep, window_days: int = Query(7, ge=1, le=90)):
    result = AlertService(session).check(regions=user.visible_regions(), window_days=window_days)
    return {
        "evaluated": result["evaluated"],
        "triggered": result["triggered"],
        "events": [
            {
                "id": e.id,
                "rule_name": e.rule_name,
                "dimension_key": e.dimension_key,
                "actual_value": e.actual_value,
                "message": e.message,
                "channels_sent": e.channels_sent,
            }
            for e in result["events"]
        ],
    }


@router.get("/events")
def list_events(session: DbDep, _: UserDep, limit: int = Query(30, ge=1, le=200)):
    rows = (
        session.query(AlertEvent)
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "rule_name": r.rule_name,
            "dimension_key": r.dimension_key,
            "actual_value": r.actual_value,
            "message": r.message,
            "acknowledged": r.acknowledged,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/events/{event_id}/ack")
def ack_event(event_id: str, session: DbDep, _: WriteDep):
    event = session.get(AlertEvent, event_id)
    if event is None:
        raise NotFoundError("告警事件")
    event.acknowledged = True
    session.commit()
    return {"message": "已确认", "at": datetime.utcnow().isoformat()}
