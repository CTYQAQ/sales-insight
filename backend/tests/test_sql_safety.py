"""Text2SQL 安全围栏测试。

为什么先测这里：这是整个项目风险最高、也最容易被面试官追问的地方 ——
把一段自然语言交给 LLM 翻译成 SQL 然后执行，等于给模型开了数据库权限。
下面每一条用例都是一个具体的攻击面，它们通过才说明三重围栏是真的在拦，
而不是写在 docstring 里的口号。
"""
import pytest

from app.core.errors import SqlSafetyError
from app.modules.ai.text2sql import enforce_scope, validate_sql


@pytest.mark.parametrize(
    "payload",
    [
        # --- 1. 多语句堆叠：典型的注入手法 ---
        "SELECT * FROM fact_sales; DROP TABLE fact_sales",
        "SELECT revenue FROM fact_sales; DELETE FROM dim_customer WHERE 1=1",
        "SELECT 1; SELECT 2",
        # --- 2. 直接写操作 ---
        "DELETE FROM fact_sales",
        "UPDATE fact_sales SET revenue = 0",
        "DROP TABLE dim_region",
        "ALTER TABLE fact_sales ADD COLUMN x TEXT",
        "PRAGMA table_info(fact_sales)",
        "ATTACH DATABASE '/etc/passwd' AS pwn",
        # --- 3. 越权表：模型不该看到 LHC 的表 ---
        "SELECT * FROM users",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM refresh_tokens",
        "SELECT a.revenue FROM fact_sales a JOIN secret_table b ON a.id = b.id",
        # --- 4. 关键字藏在注释/字符串里试图绕过词法检查 ---
        "SELECT revenue FROM fact_sales WHERE channel = 'iron ; DROP TABLE x'",
        "SELECT revenue /* delete from fact_sales */ FROM fact_sales",
        # --- 5. 空输入 / 纯垃圾 ---
        "",
        "   ",
        "SELECT FROM WHERE ???",
    ],
)
def test_dangerous_sql_is_rejected(payload: str) -> None:
    """以上每一条都必须被拒，任何一条漏网都意味着围栏有洞。"""
    with pytest.raises(SqlSafetyError):
        validate_sql(payload)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(revenue) FROM fact_sales",
        "SELECT region_code, SUM(revenue) AS total FROM fact_sales GROUP BY region_code",
        "SELECT r.name, SUM(f.revenue) FROM fact_sales f JOIN dim_region r ON f.region_code = r.code"
        " WHERE f.date >= '2026-09-01' GROUP BY r.name",
        "```sql\nSELECT COUNT(*) FROM dim_customer\n```",  # 模型偶尔会包代码块
        "SELECT COUNT(*) FROM fact_sales;",  # 单个尾部分号应当被容忍
    ],
)
def test_legitimate_query_is_accepted(sql: str) -> None:
    """正常查询不能被误杀 —— 围太死这个功能就没人用了。"""
    cleaned = validate_sql(sql)
    assert cleaned.lstrip().upper().startswith("SELECT")
    assert "```" not in cleaned
    assert not cleaned.rstrip().endswith(";")


def test_scope_is_forcibly_injected_even_if_llm_asks_about_other_regions() -> None:
    """区域权限不该由用户说了算：即使 SQL 想查所有区域，也会被逼回可见范围。"""
    sql = "SELECT SUM(revenue) FROM fact_sales"
    scoped = enforce_scope(sql, ["east", "north"])

    assert "region_code" in scoped
    assert "east" in scoped and "north" in scoped
    # 用户不可见的 south 绝不能出现在最终 SQL 里
    assert "south" not in scoped


def test_scope_merges_with_existing_where_clause() -> None:
    """已经带 WHERE 的查询， scope 要 AND 上去而不是覆盖原有条件。"""
    sql = "SELECT SUM(revenue) FROM fact_sales WHERE channel = '直销'"
    scoped = enforce_scope(sql, ["east"])
    lower = scoped.lower()

    assert lower.count("where") == 1
    assert "channel" in lower
    assert "region_code" in lower


def test_scope_is_not_added_for_unrestricted_user() -> None:
    """regions=None 表示不限（管理员），这时不该多塞过滤条件污染查询。"""
    scoped = enforce_scope("SELECT SUM(revenue) FROM fact_sales", None)
    assert "region_code" not in scoped


def test_row_limit_is_always_capped() -> None:
    """LLIMIT 兜底：不写 LIMIT 要补，写了超限的要压回去。"""
    assert "LIMIT 500" in enforce_scope("SELECT * FROM fact_sales", None)

    capped = enforce_scope("SELECT * FROM fact_sales LIMIT 99999", None)
    assert "99999" not in capped
    assert "LIMIT 500" in capped

    # 本来就小于上限的，尊重原意不动
    kept = enforce_scope("SELECT * FROM fact_sales LIMIT 10", None)
    assert "LIMIT 10" in kept
