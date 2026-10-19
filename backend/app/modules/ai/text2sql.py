"""Text-to-SQL：让业务同事用中文问数据。

安全模型（三重拦截，缺一不可）：
1. 白名单表：LLM 只能看到这四张表的结构，看不到全库。
2. sqlglot 静态解析：非 SELECT / 触及白名单外的表 / 多语句，一律拒绝执行。
3. 区域范围硬注入：无论 LLM 生成什么 SQL，都会被补上region过滤，越权数据出不来。
"""
import time
from datetime import date, timedelta
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import SqlSafetyError, ValidationError
from app.core.logging import get_logger
from app.db.models.sales import FactSales

log = get_logger(__name__)

SCHEMA_DDL = """
表名：fact_sales（销售事实表，每行一笔销售明细）
列：
- date DATE 销售日期
- region_code TEXT 区域编码；关联 dim_region.code
- customer_code TEXT 客户编码；关联 dim_customer.code
- sku TEXT 商品编码；关联 dim_product.sku
- channel TEXT 渠道
- sales_rep TEXT 销售负责人
- order_no TEXT 订单号
- quantity NUMERIC 数量
- unit_price NUMERIC 单价
- revenue NUMERIC 销售额（已含税）
- cost NUMERIC 成本
- gross_profit NUMERIC 毛利

表名：dim_region(code, name, parent_code)
表名：dim_customer(code, name, industry, level, owner)
表名：dim_product(sku, name, category, brand, unit)
"""

SYSTEM_PROMPT = f"""你是企业销售数据的 SQL 分析师，把中文问题翻译成 SQLite 查询。

可用表结构：
{SCHEMA_DDL}

硬性规则：
1. 只输出一条 SQL，不要解释、不要 Markdown 代码块标记。
2. 只能是 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA。
3. 涉及金额一律用 SUM(revenue)，毛利率 = SUM(gross_profit)*100.0/NULLIF(SUM(revenue),0)。
4. 日期比较一律写成 date >= 'YYYY-MM-DD' 的形式。
5. 必须 GROUP BY 分组列，ORDER BY 结果值 DESC。
6. 不要使用 SELECT *。
7. 默认加 LIMIT {settings.TEXT2SQL_ROW_LIMIT}。
"""


def _allowed_tables() -> set[str]:
    return {t.lower() for t in settings.TEXT2SQL_TABLES}


# 维度表变动极少，缓存一小段时间，省掉每次问数都查库
_DIM_CACHE: dict[str, Any] = {"at": 0.0, "text": ""}
_DIM_CACHE_TTL_SEC = 300
# 维度取值注入上限：超出就截断，避免把 prompt 撑爆
_DIM_VALUE_LIMIT = 60


def _rows(session: Session, sql: str) -> list[tuple]:
    try:
        return list(session.execute(text(sql)).all())
    except Exception:  # noqa: BLE001
        # 表还没建好或查询失败时静默降级：宁可少给点上下文，也不能让问数整体挂掉
        log.warning("dim_values_query_failed sql=%s", sql[:60])
        return []


def build_dimension_values(session: Session) -> str:
    """把维度表里的真实取值拼成 prompt 片段。

    为什么要这一步：只给表结构的话，模型只能靠常识猜取值。
    用户问"华东区"，它就会写 `dr.name = '华东区'`，而库里存的是"华东" ——
    JOIN 匹配不上返回 NULL，模型又对着 NULL 编出一句"该期间无销售记录"。
    结论看起来很正常，其实是错的。把取值喂进去才能根治。
    """
    cached = _DIM_CACHE.get("text", "")
    if cached and time.time() - _DIM_CACHE.get("at", 0.0) < _DIM_CACHE_TTL_SEC:
        return cached

    blocks: list[str] = []

    regions = _rows(session, "SELECT code, name FROM dim_region ORDER BY code")
    if regions:
        mapping = "、".join(f"{code}={name}" for code, name in regions[:_DIM_VALUE_LIMIT])
        blocks.append(f"- 区域 dim_region（code=name）：{mapping}")

    channels = _rows(session, "SELECT DISTINCT channel FROM fact_sales WHERE channel IS NOT NULL ORDER BY channel")
    if channels:
        blocks.append(
            "- 渠道 fact_sales.channel 取值："
            + "、".join(str(r[0]) for r in channels[:_DIM_VALUE_LIMIT])
        )

    customers = _rows(session, "SELECT code, name FROM dim_customer ORDER BY code")
    if customers:
        pairs = "、".join(f"{code}={name}" for code, name in customers[:_DIM_VALUE_LIMIT])
        tail = "" if len(customers) <= _DIM_VALUE_LIMIT else f"（仅列出前 {_DIM_VALUE_LIMIT} 个）"
        blocks.append(f"- 客户 dim_customer（code=name）：{pairs}{tail}")

    products = _rows(session, "SELECT sku, name FROM dim_product ORDER BY sku")
    if products:
        pairs = "、".join(f"{sku}={name}" for sku, name in products[:_DIM_VALUE_LIMIT])
        tail = "" if len(products) <= _DIM_VALUE_LIMIT else f"（仅列出前 {_DIM_VALUE_LIMIT} 个）"
        blocks.append(f"- 商品 dim_product（sku=name）：{pairs}{tail}")

    result = "\n".join(blocks)
    _DIM_CACHE.update(at=time.time(), text=result)
    return result


def build_system_prompt(session: Session) -> str:
    """基础规则 + 真实维度取值。取值拿不到时降级成纯表结构，不影响可用性。"""
    dims = build_dimension_values(session)
    if not dims:
        return SYSTEM_PROMPT
    return (
        f"{SYSTEM_PROMPT}\n"
        "以下是库里各维度的**真实取值**（生成 SQL 时必须严格照抄，一个字都不要改）：\n"
        f"{dims}\n\n"
        "取值匹配规则：\n"
        "1. 用户说法与上面的取值不一致时（例如问\"华东区\"而取值表里是\"华东\"），"
        "取语义最接近的那个真实值，**严禁自行臆造**。\n"
        "2. 拿不准就用 LIKE 模糊匹配，例如 dr.name LIKE '%华东%'。\n"
    )


def validate_sql(raw_sql: str) -> str:
    """静态校验 + 归一化。不通过就抛 SqlSafetyError，绝不放行。"""
    cleaned = raw_sql.strip().strip(";").strip()
    cleaned = cleaned.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()

    if not cleaned:
        raise SqlSafetyError("生成的查询为空")

    # 多条语句 = 注入的典型征兆，直接拒
    try:
        statements = sqlglot.parse(cleaned, read="sqlite")
    except sqlglot.ParseError as exc:
        raise SqlSafetyError(f"SQL 无法解析：{exc}") from exc
    if len([s for s in statements if s is not None]) != 1:
        raise SqlSafetyError("只允许执行单条查询")

    tree = statements[0]
    if tree is None or tree.key.lower() != "select":
        raise SqlSafetyError("只允许 SELECT 查询")

    tables = {t.name.lower() for t in tree.find_all(sqlglot.exp.Table)}
    banned = tables - _allowed_tables()
    if banned:
        raise SqlSafetyError(f"查询触及了未授权的表：{', '.join(sorted(banned))}")

    # 兜底：禁止出现在注释或字符串里藏一条写操作
    lowered = cleaned.lower()
    for forbidden in ("insert ", "update ", "delete ", "drop ", "alter ", "pragma", "attach "):
        if forbidden in lowered:
            raise SqlSafetyError("检测到不允许的操作关键字")

    return cleaned


def enforce_scope(sql: str, regions: list[str] | None, limit: int | None = None) -> str:
    """把区域权限硬拼进 WHERE。regions 为空表示不限。"""
    tree = sqlglot.parse_one(sql, read="sqlite")

    if regions:
        quoted = ", ".join(f"'{r}'" for r in regions)
        cond = sqlglot.parse_one(f"fact_sales.region_code IN ({quoted})", read="sqlite")
        existing_where = tree.args.get("where")
        if existing_where is not None:
            existing_where.set("this", sqlglot.exp.and_(existing_where.this, cond))
        else:
            tree.set("where", sqlglot.exp.Where(this=cond))

    row_limit = limit or settings.TEXT2SQL_ROW_LIMIT
    existing_limit = tree.args.get("limit")
    if existing_limit is None:
        tree.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(row_limit)))
    else:
        try:
            if int(existing_limit.expression.this) > row_limit:
                existing_limit.set("expression", sqlglot.exp.Literal.number(row_limit))
        except (ValueError, AttributeError):
            existing_limit.set("expression", sqlglot.exp.Literal.number(row_limit))

    return tree.sql(dialect="sqlite")


def execute_sql(session: Session, sql: str) -> list[dict[str, Any]]:
    try:
        rows = session.execute(text(sql)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"查询执行失败：{exc}") from exc
    return [{k: _serialize(v) for k, v in row.items()} for row in rows]


def _serialize(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "quantize"):
        return float(value)
    return value


def default_period(days: int = 30) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()
