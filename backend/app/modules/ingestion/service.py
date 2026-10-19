"""标准化 + 入库服务（三源共用的落库逻辑）。

设计要点：
1. 列名自动推断：优先用用户配置的 field_mapping，未命中则按别名表猜，
   这样业务同事导出的表头再乱也能先跑起来。
2. 幂等：同一 (order_no, date, sku) 只保留一条，重复接入不会产生重复销售额。
3. 维度自动补齐：遇到没见过的区域/客户/商品先建维度再插事实，外键不悬空。
"""
from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.models.ops import DataSource, IngestJob, JobStatus, SourceType
from app.db.models.sales import DimCustomer, DimProduct, DimRegion, FactSales, ensure_dimension_defaults

log = get_logger(__name__)

TARGET_FIELDS = {
    "date": ["日期", "下单日期", "订单日期", "销售日期", "date", "order_date"],
    "region_code": ["区域编码", "大区编码", "区域代码", "region", "region_code"],
    "region_name": ["区域", "大区", "区域名称", "region_name"],
    "customer_code": ["客户编码", "客户代码", "客户编号", "customer", "customer_code"],
    "customer_name": ["客户名称", "客户", "客户简称", "customer_name"],
    "sku": ["商品编码", "商品代码", "sku", "产品编码", "货号"],
    "product_name": ["商品名称", "产品名称", "品名", "product_name"],
    "quantity": ["数量", "销售数量", "qty", "quantity"],
    "unit_price": ["单价", "销售单价", "price", "unit_price"],
    "revenue": ["销售额", "销售金额", "金额", "收入", "revenue", "amount"],
    "cost": ["成本", "成本额", "销售成本", "cost"],
    "gross_profit": ["毛利", "毛利额", "gross_profit", "profit"],
    "channel": ["渠道", "销售类型", "channel"],
    "sales_rep": ["销售员", "业务员", "负责人", "sales_rep"],
    "order_no": ["订单号", "单号", "order_no", "order_id"],
}


def infer_mapping(columns: list[str], user_mapping: dict[str, str] | None = None) -> dict[str, str]:
    """返回 {标准字段名: 原始列名}。用户配置优先，其次按别名精确匹配。"""
    user_mapping = user_mapping or {}
    lowered = {str(c).strip().lower(): str(c) for c in columns}
    mapping: dict[str, str] = {}

    for target, aliases in TARGET_FIELDS.items():
        if target in user_mapping and user_mapping[target] in columns:
            mapping[target] = user_mapping[target]
            continue
        for alias in aliases:
            if alias.lower() in lowered:
                mapping[target] = lowered[alias.lower()]
                break

    if "revenue" not in mapping:
        raise ValidationError(
            f"未能识别出「销售额」列，请在接入时手动映射。可用列：{', '.join(map(str, columns))}"
        )
    if "date" not in mapping:
        raise ValidationError("未能识别出「日期」列，请在接入时手动映射")
    return mapping


def _to_date(value: Any) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValidationError(f"无法解析日期值：{value!r}")
    return parsed.date()


def _num(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)


class IngestionService:
    def __init__(self, session: Session):
        self.session = session

    # ---------- 维度补齐 ----------
    def _upsert_dimension(self, model, code_field: str, code: str, **extra) -> int | None:
        if not code:
            return None
        stmt = select(model).where(getattr(model, code_field) == code)
        existing = self.session.execute(stmt).scalars().first()
        if existing:
            return existing.id
        obj = model(**{code_field: code}, **extra)
        self.session.add(obj)
        self.session.flush()
        return obj.id

    def _prepare_dataframe(self, df: pd.DataFrame, mapping: dict[str, str] | None) -> list[dict[str, Any]]:
        resolved = infer_mapping(list(df.columns), mapping)
        records: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            raw_revenue = _num(row.get(resolved["revenue"]))
            raw_date = row.get(resolved["date"])
            if pd.isna(raw_date) or raw_revenue == 0:
                continue

            date_val = _to_date(raw_date)
            region_code = str(row.get(resolved.get("region_code", ""), "") or "").strip() or None
            customer_code = str(row.get(resolved.get("customer_code", ""), "") or "").strip() or None
            sku = str(row.get(resolved.get("sku", ""), "") or "").strip() or None

            cost = _num(row.get(resolved["cost"])) if "cost" in resolved else 0.0
            quantity = _num(row.get(resolved["quantity"])) if "quantity" in resolved else 0.0
            unit_price = _num(row.get(resolved["unit_price"])) if "unit_price" in resolved else 0.0
            profit = _num(row.get(resolved["gross_profit"])) if "gross_profit" in resolved else raw_revenue - cost

            records.append(
                {
                    "date": date_val,
                    "region_code": region_code,
                    "region_name": str(row.get(resolved.get("region_name", ""), "") or region_code or "").strip(),
                    "customer_code": customer_code,
                    "customer_name": str(row.get(resolved.get("customer_name", ""), "") or customer_code or "").strip(),
                    "sku": sku,
                    "product_name": str(row.get(resolved.get("product_name", ""), "") or sku or "").strip(),
                    "channel": str(row.get(resolved.get("channel", ""), "") or "").strip() or None,
                    "sales_rep": str(row.get(resolved.get("sales_rep", ""), "") or "").strip() or None,
                    "order_no": str(row.get(resolved.get("order_no", ""), "") or "").strip() or None,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "revenue": raw_revenue,
                    "cost": cost,
                    "gross_profit": profit,
                }
            )
        return records

    def ingest(
        self,
        df: pd.DataFrame,
        *,
        datasource_id: str,
        datasource_name: str,
        field_mapping: dict[str, str] | None = None,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        ensure_dimension_defaults(self.session)

        job = IngestJob(
            id=f"job-{uuid4().hex}",
            datasource_id=datasource_id,
            datasource_name=datasource_name,
            status=JobStatus.RUNNING,
        )
        self.session.add(job)
        self.session.flush()

        try:
            records = self._prepare_dataframe(df, field_mapping)
            if not records:
                raise ValidationError("没有可入库的有效行（日期或销售额为空）")

            if replace_existing:
                self.session.query(FactSales).filter(
                    FactSales.datasource_id == datasource_id
                ).delete(synchronize_session=False)

            written = 0
            seen: set[tuple] = set()
            for rec in records:
                key = (rec.get("order_no"), rec["date"], rec.get("sku"))
                if key in seen:
                    continue
                seen.add(key)

                region_id = self._upsert_dimension(
                    DimRegion, "code", rec["region_code"] or "UNKNOWN", name=rec["region_name"] or "未分配"
                )
                customer_id = (
                    self._upsert_dimension(
                        DimCustomer, "code", rec["customer_code"], name=rec["customer_name"]
                    )
                    if rec["customer_code"]
                    else None
                )
                product_id = (
                    self._upsert_dimension(DimProduct, "sku", rec["sku"], name=rec["product_name"])
                    if rec["sku"]
                    else None
                )

                exists = False
                if rec["order_no"]:
                    stmt = select(func.count(FactSales.id)).where(
                        FactSales.order_no == rec["order_no"],
                        FactSales.date == rec["date"],
                        FactSales.sku == rec["sku"],
                    )
                    exists = self.session.execute(stmt).scalar() > 0
                if exists:
                    continue

                self.session.add(
                    FactSales(
                        date=rec["date"],
                        region_id=region_id,
                        customer_id=customer_id,
                        product_id=product_id,
                        region_code=rec["region_code"],
                        customer_code=rec["customer_code"],
                        sku=rec["sku"],
                        channel=rec["channel"],
                        sales_rep=rec["sales_rep"],
                        order_no=rec["order_no"],
                        quantity=rec["quantity"],
                        unit_price=rec["unit_price"],
                        revenue=rec["revenue"],
                        cost=rec["cost"],
                        gross_profit=rec["gross_profit"],
                        datasource_id=datasource_id,
                    )
                )
                written += 1

            job.rows_read = len(records)
            job.rows_written = written
            job.status = JobStatus.SUCCESS
            job.finished_at = datetime.utcnow()

            source = self.session.get(DataSource, datasource_id)
            if source:
                source.last_run_at = datetime.utcnow()
                source.last_status = "success"

            self.session.commit()
            log.info("ingest_done source=%s read=%d written=%d", datasource_name, len(records), written)
            return {
                "job_id": job.id,
                "status": "success",
                "rows_read": len(records),
                "rows_written": written,
                "rows_skipped": len(records) - written,
            }

        except Exception as exc:  # noqa: BLE001
            self.session.rollback()
            job.status = JobStatus.FAILED
            job.error_message = str(exc)[:1000]
            job.finished_at = datetime.utcnow()
            source = self.session.get(DataSource, datasource_id)
            if source:
                source.last_status = "failed"
            self.session.commit()
            log.exception("ingest_failed source=%s", datasource_name)
            raise
