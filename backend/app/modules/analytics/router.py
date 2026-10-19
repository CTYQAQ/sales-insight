"""销售指标查询接口。

所有查询都带上 current_user 的区域范围，越权数据不会进入结果集。
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, UserDep
from app.core.errors import ValidationError
from app.modules.analytics.service import MetricsService

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"日期格式应为 YYYY-MM-DD：{value}") from exc


def _service(session: DbDep, user: UserDep) -> MetricsService:
    return MetricsService(session, user.visible_regions())


@router.get("/summary")
def summary(
    service: Annotated[MetricsService, Depends(_service)],
    end: str | None = Query(None, description="截止日期 YYYY-MM-DD，默认今天"),
    days: int = Query(30, ge=1, le=365),
):
    return service.summary(_parse_date(end, date.today()), compare_days=days)


@router.get("/range")
def data_range(service: Annotated[MetricsService, Depends(_service)]):
    rng = service.date_range()
    if rng is None:
        return {"has_data": False}
    return {"has_data": True, "min_date": rng[0].isoformat(), "max_date": rng[1].isoformat()}


@router.get("/trend")
def trend(
    service: Annotated[MetricsService, Depends(_service)],
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    metric: str = Query("revenue", pattern="^(revenue|gross_profit|quantity)$"),
):
    return service.trend(_parse_date(start, date.today()), _parse_date(end, date.today()), metric=metric)


@router.get("/breakdown")
def breakdown(
    service: Annotated[MetricsService, Depends(_service)],
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    dimension: str = Query("region", pattern="^(region|product|customer|channel)$"),
    limit: int = Query(10, ge=1, le=100),
):
    return service.breakdown(
        _parse_date(start, date.today()),
        _parse_date(end, date.today()),
        dimension=dimension,
        limit=limit,
    )


@router.get("/movers")
def movers(
    service: Annotated[MetricsService, Depends(_service)],
    end: str | None = Query(None),
    days: int = Query(30, ge=7, le=180),
    limit: int = Query(5, ge=1, le=20),
):
    return service.top_movers(_parse_date(end, date.today()), days=days, limit=limit)
