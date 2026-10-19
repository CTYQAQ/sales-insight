"""销售事实表 + 维度表（星型模型）。

三种数据源（Excel/CSV、外部数据库、HTTP API）最终都落成这套结构，
上层所有分析、图表、Text2SQL 只依赖这一层，换源不需要改分析代码。
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DimRegion(Base):
    __tablename__ = "dim_region"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DimCustomer(Base):
    __tablename__ = "dim_customer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(64))
    level: Mapped[str | None] = mapped_column(String(32))  # KA / 大客户 / 中小
    owner: Mapped[str | None] = mapped_column(String(64), comment="销售负责人")


class DimProduct(Base):
    __tablename__ = "dim_product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(64))
    brand: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(16))


class FactSales(Base):
    __tablename__ = "fact_sales"
    __table_args__ = (
        Index("ix_fact_sales_date", "date"),
        Index("ix_fact_sales_region_date", "region_code", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 索引统一在 __table_args__ 里声明（ix_fact_sales_date），避免与列级 index 重复建同名索引
    date: Mapped[date] = mapped_column(Date)

    # 维度外键（可空：小团队数据不完整是常态，不强行阻塞入库）
    region_id: Mapped[int | None] = mapped_column(ForeignKey("dim_region.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("dim_customer.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("dim_product.id"))

    # 冗余维度码：Text2SQL 与聚合查询无需 join 即可按区域过滤，同时也是行级权限过滤字段
    region_code: Mapped[str | None] = mapped_column(String(32), index=True)
    customer_code: Mapped[str | None] = mapped_column(String(64), index=True)
    sku: Mapped[str | None] = mapped_column(String(64), index=True)

    channel: Mapped[str | None] = mapped_column(String(32))
    sales_rep: Mapped[str | None] = mapped_column(String(64))
    order_no: Mapped[str | None] = mapped_column(String(64), index=True)

    quantity: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    cost: Mapped[float | None] = mapped_column(Numeric(18, 2))
    gross_profit: Mapped[float | None] = mapped_column(Numeric(18, 2))

    datasource_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def ensure_dimension_defaults(session) -> None:
    """兜底维度：未知区域/客户/商品，避免事实表外键悬空。"""
    if session.query(DimRegion).filter_by(code="UNKNOWN").first() is None:
        session.add(DimRegion(code="UNKNOWN", name="未分配"))
        session.flush()
