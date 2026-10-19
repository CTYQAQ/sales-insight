"""三种数据源连接器，产出统一的 pandas.DataFrame。

约定：所有连接器只负责「把数据取出来」，标准化与入库交给 IngestionService，
这样三源共用同一套清洗与幂等逻辑。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.errors import DependencyError, ValidationError
from app.core.logging import get_logger

log = get_logger(__name__)

SUPPORTED_SUFFIX = {".csv", ".xlsx", ".xls"}


class BaseConnector(ABC):
    source_type: str

    @abstractmethod
    def fetch(self) -> pd.DataFrame: ...


class FileConnector(BaseConnector):
    """本地 Excel / CSV。支持多 sheet 自动拼接。"""

    source_type = "file"

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise ValidationError(f"文件不存在：{self.path}")
        if self.path.suffix.lower() not in SUPPORTED_SUFFIX:
            raise ValidationError(f"仅支持 csv / xlsx / xls，当前为 {self.path.suffix}")

    def fetch(self) -> pd.DataFrame:
        try:
            if self.path.suffix.lower() == ".csv":
                df = pd.read_csv(self.path, encoding_errors="ignore")
            else:
                sheets = pd.read_excel(self.path, sheet_name=None)
                df = pd.concat(sheets.values(), ignore_index=True)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"文件解析失败：{exc}") from exc

        if df.empty:
            raise ValidationError("文件内没有数据行")
        df.columns = [str(c).strip() for c in df.columns]
        log.info("file_connector_read rows=%d cols=%d", len(df), len(df.columns))
        return df


class DatabaseConnector(BaseConnector):
    """外部业务库直连：MySQL / PostgreSQL / SQL Server 等。

    只做 SELECT，只读是最低限度的安全边界。
    """

    source_type = "database"

    def __init__(self, url: str, query: str, *, connect_args: dict[str, Any] | None = None) -> None:
        self.url = url
        self.query = query.strip()
        self.connect_args = connect_args or {}
        self._assert_readonly()

    def _assert_readonly(self) -> None:
        head = self.query.split()[0].lower() if self.query else ""
        if head not in ("select", "with"):
            raise ValidationError("数据库源只允许 SELECT / WITH 查询")

    def fetch(self) -> pd.DataFrame:
        from sqlalchemy import create_engine, text

        try:
            engine = create_engine(self.url, pool_pre_ping=True, connect_args=self.connect_args)
            with engine.connect() as conn:
                df = pd.read_sql(text(self.query), conn)
        except Exception as exc:  # noqa: BLE001
            raise DependencyError(f"外部数据库连接或查询失败：{exc}") from exc

        if df.empty:
            raise ValidationError("查询结果为空")
        log.info("db_connector_read rows=%d cols=%d", len(df), len(df.columns))
        return df


class ApiConnector(BaseConnector):
    """HTTP API 拉取：约定返回 JSON 数组，或 {"data": [...]}。"""

    source_type = "api"

    def __init__(
        self, url: str, *, headers: dict[str, str] | None = None, data_key: str = "data", timeout: int = 30
    ) -> None:
        if not url.lower().startswith(("http://", "https://")):
            raise ValidationError("API 地址必须以 http(s):// 开头")
        self.url = url
        self.headers = headers or {}
        self.data_key = data_key
        self.timeout = timeout

    def fetch(self) -> pd.DataFrame:
        import httpx

        try:
            resp = httpx.get(self.url, headers=self.headers, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise DependencyError(f"API 拉取失败：{exc}") from exc

        rows = payload.get(self.data_key, payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not rows:
            raise ValidationError("API 返回结构不符合预期（需要数组）")
        df = pd.DataFrame(rows)
        log.info("api_connector_read rows=%d cols=%d", len(df), len(df.columns))
        return df


def build_connector(source_type: str, config: dict[str, Any]) -> BaseConnector:
    if source_type == "file":
        return FileConnector(config["path"])
    if source_type == "database":
        return DatabaseConnector(config["url"], config["query"], connect_args=config.get("connect_args"))
    if source_type == "api":
        return ApiConnector(
            config["url"],
            headers=config.get("headers"),
            data_key=config.get("data_key", "data"),
            timeout=int(config.get("timeout", 30)),
        )
    raise ValidationError(f"不支持的数据源类型：{source_type}")
