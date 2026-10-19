"""
数据问答 Agent 的状态图。

    START
      ↓
    rewrite            改写问题、对齐维度取值
      ↓
    generate_sql       生成 SQL
      ↓
    guard              安全校验 + 权限注入（纯代码，非 LLM）
      ↓
    execute            执行
      ↓
    [路由] ── 结果可用 ──────────────→ answer → END
      │
      └─ 失败/空结果，且未超次数 → reflect → generate_sql（带错误信息重来）

为什么用图而不是写个 for 循环：
1. **路由显式化**：失败后去哪一步、什么条件下停止，写在边上而不是藏在 if/else 里，
   审阅时一眼能看懂，改流程也不用动节点内部。
2. **状态可观测**：每个节点返回增量，跑完能逐步回放；出错时能精确定位到某一节点。
3. **可插拔**：以后要加"人工确认""缓存命中直接返回""并行查多个候选 SQL"，
   都是加节点加边，不用重写主流程。

关于重试：必须有上限。没有上限的自我反思会变成无限循环 + 无限烧 token。
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.ai.agent.nodes import (
    answer_node,
    execute_node,
    generate_sql_node,
    guard_node,
    reflect_node,
    rewrite_node,
)
from app.modules.ai.agent.state import QAState
from app.modules.ai.provider import LLMProvider

log = get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 3


def _route_after_execute(state: QAState) -> str:
    """执行之后的分岔：拿到有效结果就收尾，否则回去反思重来。"""
    if not state.get("is_empty_result", True):
        return "answer"

    if state.get("attempt", 0) >= state.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        # 次数用尽，别死循环。仍然走到 answer，让它如实说明没查到
        log.warning("agent_retry_budget_exhausted attempts=%s", state.get("attempt"))
        return "answer"

    return "reflect"


def build_agent(
    session: Session, provider: LLMProvider, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
):
    """组装并编译 Agent 图。

    session / provider 通过 partial 绑定进节点，而不是塞进 State ——
    它们是"运行时依赖"，不属于对话数据，混进 State 会让状态既不可序列化也不可回放。
    """
    graph = StateGraph(QAState)

    graph.add_node("rewrite", partial(rewrite_node, provider=provider, session=session))
    graph.add_node("generate_sql", partial(generate_sql_node, provider=provider, session=session))
    graph.add_node("guard", guard_node)
    graph.add_node("execute", partial(execute_node, session=session))
    graph.add_node("reflect", partial(reflect_node, provider=provider, session=session))
    graph.add_node("answer", partial(answer_node, provider=provider))

    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "generate_sql")
    graph.add_edge("generate_sql", "guard")
    graph.add_edge("guard", "execute")

    # 唯一的条件边：结果好不好，决定是收尾还是反思
    graph.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"answer": "answer", "reflect": "reflect"},
    )

    # 反思闭环：带着错误信息回到生成节点重来
    graph.add_edge("reflect", "generate_sql")
    graph.add_edge("answer", END)

    return graph.compile()


def run_agent(
    *,
    question: str,
    today: str,
    regions: list[str] | None,
    session: Session,
    provider: LLMProvider,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> QAState:
    """跑一次问答，返回最终状态（含完整 trace）。"""
    app = build_agent(session, provider, max_attempts=max_attempts)
    initial: QAState = {
        "question": question,
        "today": today,
        "regions": regions,
        "attempt": 0,
        "max_attempts": max_attempts,
        "trace": [],
    }
    return app.invoke(initial)
