"""运营模型：数据源、接入任务、分析报告、告警规则与事件、对话记录。"""
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SourceType(str, enum.Enum):
    FILE = "file"
    DATABASE = "database"
    API = "api"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[SourceType] = mapped_column(Enum(SourceType), index=True)
    # 连接参数：文件源记 {"path": ...}，数据库源记 {"url": ...，"table": ...}，API 源记 {"url": ...}
    # 敏感凭据不落这里，统一走环境变量或凭据表
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # 字段映射：{"date":"下单日期","revenue":"金额",...}
    field_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IngestJob(Base):
    __tablename__ = "ingest_job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    datasource_id: Mapped[str | None] = mapped_column(String(36), index=True)
    datasource_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalysisReport(Base):
    __tablename__ = "analysis_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    period_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(10), nullable=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    summary_md: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AlertComparator(str, enum.Enum):
    LT = "lt"
    GT = "gt"
    DROP_PCT = "drop_pct"   # 环比下降超过 x%
    RISE_PCT = "rise_pct"


class AlertRule(Base):
    __tablename__ = "alert_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    metric: Mapped[str] = mapped_column(String(32))          # revenue / gross_profit / quantity
    dimension: Mapped[str] = mapped_column(String(32), default="total")  # total / region / product / customer
    comparator: Mapped[AlertComparator] = mapped_column(Enum(AlertComparator))
    threshold: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # warning / critical
    channels: Mapped[list] = mapped_column(JSON, default=list)            # ["webhook","email"]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("alert_rule.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(128))
    dimension_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    channels_sent: Mapped[list] = mapped_column(JSON, default=list)
    dedupe_key: Mapped[str] = mapped_column(String(128), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    question: Mapped[str] = mapped_column(Text)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_preview: Mapped[dict] = mapped_column(JSON, default=dict)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")  # success / blocked / failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
