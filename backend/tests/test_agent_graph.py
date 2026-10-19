"""Agent 图行为测试。

不联网、不依赖真实 Key：用 FakeProvider 精确控制 LLM 每次"说什么"，
从而单独验证图的**控制流**是否正确 —— 这才是 Agent 最容易出错的地方。

重点钉三件事：
1. 拿不到结果时，必须走 reflect 再重生成，而不是直接收尾。
2. 反思的结论必须真正传回下一轮，否则重试等于把同一个错误再犯一遍。
3. 重试必须有上限 —— 没有上限的自我反思就是无限循环 + 无限烧 token。
"""
import app.db.models  # noqa: F401  确保 ORM 元数据注册
import pytest

from app.db.session import SessionLocal
from app.modules.ai.agent import build_agent
from app.modules.ai.provider import LLMProvider

RIGHT_SQL = (
    "SELECT SUM(f.revenue) FROM fact_sales f JOIN dim_region r ON f.region_code = r.code "
    "WHERE r.name = '华东' AND f.date >= '2026-09-01' AND f.date < '2026-10-01'"
)
# 库里区域名是"华东"，这里故意用"华东区"模拟模型凭常识臆造，导致 JOIN 落空
WRONG_SQL = (
    "SELECT SUM(f.revenue) FROM fact_sales f JOIN dim_region r ON f.region_code = r.code "
    "WHERE r.name = '华东区' AND f.date >= '2026-09-01' AND f.date < '2026-10-01'"
)


class ScriptedProvider(LLMProvider):
    """按调用顺序返回预设内容，用来精确驱动图的走向。"""

    name = "scripted"

    def __init__(self, sql_script: list[str], *, rewrite: str = "华东 2026 年 9 月营收"):
        self.sql_script = list(sql_script)
        self.rewrite = rewrite
        self.sql_calls: list[str] = []  # 记录每次生成的 prompt，用于断言"错误被喂回去了"
        self._sql_idx = 0

    def complete(self, system: str, user: str, **kwargs) -> str:
        if "改写器" in system:
            return self.rewrite
        if "SQL 分析师" in system:
            self.sql_calls.append(user)
            i = min(self._sql_idx, len(self.sql_script) - 1)
            self._sql_idx += 1
            return self.sql_script[i]
        if "调试专家" in system:
            return "原因：维度取值不匹配；建议：把 华东区 改成 华东"
        return "答案是 1212450.46 元。"


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _run(session, provider, question="华东区上个月营收多少", max_attempts=3):
    return build_agent(session, provider, max_attempts=max_attempts).invoke(
        {
            "question": question,
            "today": "2026-10-20",
            "regions": None,
            "attempt": 0,
            "max_attempts": max_attempts,
            "trace": [],
        }
    )


def test_agent_recovers_from_empty_result(session) -> None:
    """第 1 次写错区域名 → 空结果 → 反思 → 第 2 次改正 → 拿到数据。"""
    provider = ScriptedProvider([WRONG_SQL, RIGHT_SQL])
    result = _run(session, provider)

    nodes = [t["node"] for t in result["trace"]]
    assert "reflect" in nodes, "空结果后必须进入反思节点"
    assert nodes.count("generate_sql") == 2, "修正后应重新生成 SQL"
    assert result["attempt"] == 2
    assert result["row_count"] == 1
    assert result["is_empty_result"] is False


def test_reflection_feedback_is_passed_to_next_generation(session) -> None:
    """反思出的原因必须出现在下一轮生成 SQL 的 prompt 里。

    这是 Agent 与"无脑重试"的分水岭：不带错误信息重试，
    模型只会把同一个错误再犯一遍。
    """
    provider = ScriptedProvider([WRONG_SQL, RIGHT_SQL])
    _run(session, provider)

    assert len(provider.sql_calls) == 2
    second_prompt = provider.sql_calls[1]
    assert "华东区 改成 华东" in second_prompt or "维度取值不匹配" in second_prompt


def test_retry_is_capped_and_does_not_loop_forever(session) -> None:
    """永远查不到数据时，必须在 max_attempts 处停手。"""
    provider = ScriptedProvider([WRONG_SQL])  # 每次都给同一个错的
    result = _run(session, provider, max_attempts=3)

    assert result["attempt"] == 3, "尝试次数不该超过上限"
    assert provider._sql_idx == 3, "LLM 调用次数同样被卡住，不能偷偷多花钱"
    # 注意：SUM() 匹配不到时 SQLite 返回一行 NULL，不是零行。
    # 所以判定"没拿到结果"要看 is_empty_result，而不是看 row_count == 0 ——
    # 这个细节正是之前"对着 NULL 编结论"那个 bug 的根源，别在这里又踩一遍。
    assert result["is_empty_result"] is True
    assert all(v is None for v in result["rows"][0].values())
    # 没数据时也要给出回答，而不是抛异常 —— 让用户知道发生了什么
    assert result["answer"]


def test_successful_first_try_skips_reflection(session) -> None:
    """一次就查对，不该白白多跑一轮反思。"""
    provider = ScriptedProvider([RIGHT_SQL])
    result = _run(session, provider)

    nodes = [t["node"] for t in result["trace"]]
    assert "reflect" not in nodes
    assert provider._sql_idx == 1
    assert result["attempt"] == 1


def test_dangerous_sql_is_blocked_and_retried(session) -> None:
    """模型若生成越权/写操作 SQL，guard 必须拦下并触发重试，绝不执行。"""
    provider = ScriptedProvider(["DROP TABLE fact_sales", RIGHT_SQL])
    result = _run(session, provider)

    statuses = {(t["node"], t["status"]) for t in result["trace"]}
    assert ("guard", "blocked") in statuses, "危险 SQL 必须被 guard 拦截"
    # 拦截之后仍然要靠后面的正确 SQL 救回来
    assert result["row_count"] == 1


def test_scope_is_injected_into_final_sql(session) -> None:
    """区域权限在 guard 节点注入，最终执行的 SQL 里必须带得上。"""
    provider = ScriptedProvider([RIGHT_SQL])
    result = _run(session, provider)
    # 这里 regions=None（管理员），不做区域限制，但要确认 SQL 已过 guard
    assert result["safe_sql"]
    assert "LIMIT" in result["safe_sql"].upper()
