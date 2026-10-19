"""告警规则引擎。

支持四种判定：
- lt / gt：本期指标与绝对阈值比
- drop_pct / rise_pct：本期与上期的环比变化率

去重：同一规则同一维度键在 ALERT_DEDUPE_MINUTES 内只报一次，避免刷屏。
"""
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.ops import AlertComparator, AlertEvent, AlertRule
from app.db.models.sales import FactSales
from app.modules.alerts.notifiers import dispatch_notification

log = get_logger(__name__)

_METRIC_COLUMN = {
    "revenue": FactSales.revenue,
    "gross_profit": FactSales.gross_profit,
    "quantity": FactSales.quantity,
}

_KEY_COLUMN = {
    "region": FactSales.region_code,
    "product": FactSales.sku,
    "customer": FactSales.customer_code,
    "channel": FactSales.channel,
}


def _aggregate(
    session: Session, metric: str, dimension: str, start: date, end: date, regions: list[str] | None
) -> dict[str, float]:
    col = _METRIC_COLUMN[metric]
    stmt = select(func.coalesce(func.sum(col), 0)).where(FactSales.date.between(start, end))

    if dimension == "total":
        return {"total": float(session.execute(stmt).scalar() or 0)}

    key_col = _KEY_COLUMN[dimension]
    stmt = select(key_col, func.coalesce(func.sum(col), 0).label("v")).where(
        FactSales.date.between(start, end)
    )
    if regions is not None:
        stmt = stmt.where(FactSales.region_code.in_(regions))
    stmt = stmt.group_by(key_col)
    return {r[0] or "未分配": float(r[1] or 0) for r in session.execute(stmt).all()}


class AlertService:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, regions: list[str] | None = None, window_days: int = 7) -> dict[str, Any]:
        """评估全部启用规则，返回本次触发的告警清单。"""
        rules = self.session.execute(
            select(AlertRule).where(AlertRule.enabled.is_(True))
        ).scalars().all()

        triggered: list[AlertEvent] = []
        evaluated = 0

        for rule in rules:
            evaluated += 1
            try:
                events = self._evaluate_rule(rule, regions=regions, window_days=window_days)
                triggered.extend(events)
            except Exception:  # noqa: BLE001
                # 单条规则失败不能拖垮整个巡检
                log.exception("alert_rule_failed rule=%s", rule.name)

        self.session.commit()
        return {"evaluated": evaluated, "triggered": len(triggered), "events": triggered}

    def _evaluate_rule(
        self, rule: AlertRule, *, regions: list[str] | None, window_days: int
    ) -> list[AlertEvent]:
        end = date.today()
        start = end - timedelta(days=window_days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=window_days - 1)

        current = _aggregate(self.session, rule.metric, rule.dimension, start, end, regions)
        events: list[AlertEvent] = []

        # 环比类规则需要上一期数据
        previous = (
            _aggregate(self.session, rule.metric, rule.dimension, prev_start, prev_end, regions)
            if rule.comparator in (AlertComparator.DROP_PCT, AlertComparator.RISE_PCT)
            else {}
        )

        for key, cur_value in current.items():
            if rule.comparator in (AlertComparator.DROP_PCT, AlertComparator.RISE_PCT):
                prev_value = previous.get(key, 0.0)
                if prev_value <= 0:
                    continue
                actual = (cur_value - prev_value) / prev_value * 100
                hit = (
                    actual <= -rule.threshold
                    if rule.comparator == AlertComparator.DROP_PCT
                    else actual >= rule.threshold
                )
                expected = (
                    f"环比 {'-' if rule.comparator == AlertComparator.DROP_PCT else '+'}{rule.threshold}%"
                )
                actual_display = round(actual, 2)
            else:
                actual = cur_value
                hit = (
                    cur_value < rule.threshold
                    if rule.comparator == AlertComparator.LT
                    else cur_value > rule.threshold
                )
                expected = f"{'低于' if rule.comparator == AlertComparator.LT else '高于'} {rule.threshold}"
                actual_display = round(cur_value, 2)

            if not hit:
                continue

            dedupe_key = f"{rule.id}:{key}"
            if self._recently_sent(dedupe_key):
                continue

            label = "总体" if rule.dimension == "total" else f"{rule.dimension}={key}"
            event = AlertEvent(
                id=f"evt-{uuid4().hex[:16]}",
                rule_id=rule.id,
                rule_name=rule.name,
                dimension_key=key,
                actual_value=actual_display,
                expected_value=rule.threshold,
                message=(
                    f"【{rule.name}】{label}：{rule.metric} 实际 {actual_display}，"
                    f"触发条件（{expected}）。统计区间 {start} ~ {end}"
                ),
                dedupe_key=dedupe_key,
            )
            sent = dispatch_notification(event.message, rule.channels)
            event.channels_sent = sent

            self.session.add(event)
            rule.last_triggered_at = datetime.utcnow()
            events.append(event)
            log.warning("alert_triggered rule=%s key=%s actual=%s", rule.name, key, actual_display)

        return events

    def _recently_sent(self, dedupe_key: str) -> bool:
        since = datetime.utcnow() - timedelta(minutes=settings.ALERT_DEDUPE_MINUTES)
        stmt = select(func.count(AlertEvent.id)).where(
            AlertEvent.dedupe_key == dedupe_key,
            AlertEvent.created_at >= since,
        )
        return self.session.execute(stmt).scalar() > 0
