"""销售指标聚合服务（仓储层的读侧）。

只负责取数与算指标，不含任何 AI 调用；所有查询都接受 region_scope，
由路由层注入，保证越权数据根本不会进入内存。
"""
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.sales import DimCustomer, DimProduct, DimRegion, FactSales

Dimension = Literal["region", "product", "customer", "channel"]
Metric = Literal["revenue", "gross_profit", "quantity"]


def _period_bounds(end: date, days: int) -> tuple[date, date]:
    return end - timedelta(days=days - 1), end


class MetricsService:
    def __init__(self, session: Session, regions: list[str] | None = None):
        self.session = session
        self.regions = regions  # None = 不限制

    # ---------- 内部 ----------
    def _scoped(self, stmt):
        if self.regions is not None:
            stmt = stmt.where(FactSales.region_code.in_(self.regions))
        return stmt

    def _sum(self, start: date, end: date) -> dict[str, Any]:
        stmt = select(
            func.coalesce(func.sum(FactSales.revenue), 0).label("revenue"),
            func.coalesce(func.sum(FactSales.gross_profit), 0).label("gross_profit"),
            func.coalesce(func.sum(FactSales.quantity), 0).label("quantity"),
            func.count(func.distinct(FactSales.order_no)).label("orders"),
        ).where(FactSales.date.between(start, end))
        row = self.session.execute(self._scoped(stmt)).one()
        revenue = float(row.revenue or 0)
        profit = float(row.gross_profit or 0)
        return {
            "revenue": round(revenue, 2),
            "gross_profit": round(profit, 2),
            "quantity": round(float(row.quantity or 0), 2),
            "orders": int(row.orders or 0),
            "margin": round(profit / revenue * 100, 2) if revenue else 0.0,
        }

    @staticmethod
    def _pct_change(cur: float, prev: float) -> float | None:
        if prev == 0:
            return None
        return round((cur - prev) / abs(prev) * 100, 2)

    # ---------- 对外接口 ----------
    def summary(self, end: date, *, compare_days: int = 30) -> dict[str, Any]:
        cur_start, cur_end = _period_bounds(end, compare_days)
        prev_start, prev_end = _period_bounds(cur_start - timedelta(days=1), compare_days)

        cur = self._sum(cur_start, cur_end)
        prev = self._sum(prev_start, prev_end)
        return {
            "period": {"start": cur_start.isoformat(), "end": cur_end.isoformat()},
            "previous_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
            "current": cur,
            "previous": prev,
            "changes": {
                "revenue": self._pct_change(cur["revenue"], prev["revenue"]),
                "gross_profit": self._pct_change(cur["gross_profit"], prev["gross_profit"]),
                "orders": self._pct_change(cur["orders"], prev["orders"]),
                "margin_pp": round(cur["margin"] - prev["margin"], 2),
            },
        }

    def trend(self, start: date, end: date, *, metric: Metric = "revenue") -> list[dict[str, Any]]:
        col = getattr(FactSales, metric)
        stmt = (
            select(FactSales.date.label("date"), func.coalesce(func.sum(col), 0).label("value"))
            .where(FactSales.date.between(start, end))
            .group_by(FactSales.date)
            .order_by(FactSales.date)
        )
        rows = self.session.execute(self._scoped(stmt)).all()
        return [{"date": r.date.isoformat(), "value": round(float(r.value or 0), 2)} for r in rows]

    def breakdown(
        self, start: date, end: date, dimension: Dimension = "region", *, limit: int = 10
    ) -> list[dict[str, Any]]:
        joins = {
            "region": (FactSales.region_code, DimRegion, DimRegion.name, DimRegion.code == FactSales.region_code),
            "product": (FactSales.sku, DimProduct, DimProduct.name, DimProduct.sku == FactSales.sku),
            "customer": (FactSales.customer_code, DimCustomer, DimCustomer.name, DimCustomer.code == FactSales.customer_code),
        }

        if dimension == "channel":
            col = FactSales.channel
            stmt = (
                select(
                    col.label("key"),
                    col.label("name"),
                    func.sum(FactSales.revenue).label("revenue"),
                    func.sum(FactSales.gross_profit).label("gross_profit"),
                )
                .where(FactSales.date.between(start, end))
                .group_by(col)
            )
        else:
            fact_col, entity, name_col, on_clause = joins[dimension]
            stmt = (
                select(
                    fact_col.label("key"),
                    name_col.label("name"),
                    func.sum(FactSales.revenue).label("revenue"),
                    func.sum(FactSales.gross_profit).label("gross_profit"),
                )
                .select_from(FactSales)
                .join(entity, on_clause, isouter=True)
                .where(FactSales.date.between(start, end))
                .group_by(fact_col, name_col)
            )

        stmt = stmt.order_by(func.sum(FactSales.revenue).desc()).limit(limit)
        rows = self.session.execute(self._scoped(stmt)).all()
        total = sum(float(r.revenue or 0) for r in rows) or 1.0
        return [
            {
                "key": r.key or "未分配",
                "name": r.name or r.key or "未分配",
                "revenue": round(float(r.revenue or 0), 2),
                "gross_profit": round(float(r.gross_profit or 0), 2),
                "share_pct": round(float(r.revenue or 0) / total * 100, 2),
            }
            for r in rows
        ]

    def top_movers(self, end: date, *, days: int = 30, limit: int = 5) -> dict[str, Any]:
        """贡献度异动：哪些TOP客户/商品环比掉得最狠，是报告里最有信息量的部分。"""
        cur_start, _ = _period_bounds(end, days)
        prev_start, prev_end = _period_bounds(cur_start - timedelta(days=1), days)

        def _agg(start: date, stop: date, col, joins):
            stmt = (
                select(col.label("key"), func.sum(FactSales.revenue).label("value"))
                .where(FactSales.date.between(start, stop))
                .group_by(col)
            )
            return {r.key: float(r.value or 0) for r in self.session.execute(self._scoped(stmt)).all()}

        out: dict[str, Any] = {}
        for label, col in (("customers", FactSales.customer_code), ("products", FactSales.sku)):
            cur_map = _agg(cur_start, end, col, None)
            prev_map = _agg(prev_start, prev_end, col, None)
            deltas = []
            for key, cur_v in cur_map.items():
                prev_v = prev_map.get(key, 0.0)
                if prev_v <= 0:
                    continue
                change = (cur_v - prev_v) / prev_v * 100
                deltas.append(
                    {
                        "key": key,
                        "current": round(cur_v, 2),
                        "previous": round(prev_v, 2),
                        "change_pct": round(change, 2),
                        "delta_amount": round(cur_v - prev_v, 2),
                    }
                )
            deltas.sort(key=lambda x: x["change_pct"])
            out[f"declining_{label}"] = deltas[:limit]
        return out

    def date_range(self) -> tuple[date, date] | None:
        stmt = select(func.min(FactSales.date), func.max(FactSales.date))
        min_d, max_d = self.session.execute(self._scoped(stmt)).one()
        return (min_d, max_d) if min_d else None

    def sample_rows(self, limit: int = 20) -> list[dict[str, Any]]:
        stmt = select(FactSales).order_by(FactSales.date.desc()).limit(limit)
        rows = self.session.execute(self._scoped(stmt)).scalars().all()
        return [
            {
                "date": r.date.isoformat() if r.date else None,
                "region_code": r.region_code,
                "customer_code": r.customer_code,
                "sku": r.sku,
                "channel": r.channel,
                "quantity": float(r.quantity or 0),
                "revenue": float(r.revenue or 0),
                "gross_profit": float(r.gross_profit or 0),
            }
            for r in rows
        ]
