"""对话 Agent 的状态图。

    START
      ↓
    understand     多轮指代消解（"那上个月呢" → "上个月营收是多少"）
      ↓
    route          意图分类
      ↓
    [条件边] ┬─ knowledge → retrieve → answer_kb → END
             ├─ data      →─────────── answer_data → END
             └─ chat      →─────────── answer_chat → END

为什么用 LangGraph 而不是写个 if/elif：
1. **branches 是显式的边**：新增一类意图（比如"导出报表"）只需加节点加边，
   主流程一行不动。if/elif 写下去会越长越难改。
2. **每步可观测**：trace 天然按节点收集，前端能画成时间线。
3. **和已有数据 Agent 同构**：两个 Agent 用同一套范式，维护成本低。

多轮记忆怎么做：不引入 checkpointer，而是每次请求由前端带上历史
（最近 N 轮 role/content）。理由是本项目是单机部署、无状态服务，
把会话历史交给数据库存比放进程内存更稳 —— 重启不丢，多 worker 也不串。
checkpointer 更适合需要"中断-恢复"的长流程 Agent，这里用不上。
"""
from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.ai.agent.chat_nodes import (
    answer_chat_node,
    answer_data_node,
    answer_kb_node,
    retrieve_node,
    route_node,
    understand_node,
)
from app.modules.ai.agent.chat_state import ChatState
from app.modules.ai.provider import LLMProvider

log = get_logger(__name__)


def _route_by_intent(state: ChatState) -> str:
    intent = state.get("intent") or "knowledge"
    if intent == "data":
        return "answer_data"
    if intent == "chat":
        return "answer_chat"
    return "retrieve"


def build_chat_agent(session: Session, provider: LLMProvider):
    graph = StateGraph(ChatState)

    graph.add_node("understand", partial(understand_node, provider=provider))
    graph.add_node("route", partial(route_node, provider=provider))
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer_kb", partial(answer_kb_node, provider=provider))
    graph.add_node("answer_data", partial(answer_data_node, provider=provider, session=session))
    graph.add_node("answer_chat", partial(answer_chat_node, provider=provider))

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "route")
    graph.add_conditional_edges(
        "route",
        _route_by_intent,
        {
            "retrieve": "retrieve",
            "answer_data": "answer_data",
            "answer_chat": "answer_chat",
        },
    )
    graph.add_edge("retrieve", "answer_kb")
    graph.add_edge("answer_kb", END)
    graph.add_edge("answer_data", END)
    graph.add_edge("answer_chat", END)

    return graph.compile()


def run_chat_agent(
    *,
    question: str,
    today: str,
    regions: list[str] | None,
    history: list[dict[str, str]] | None = None,
    session: Session,
    provider: LLMProvider,
) -> ChatState:
    app = build_chat_agent(session, provider)
    initial: ChatState = {
        "question": question,
        "today": today,
        "regions": regions,
        "history": history or [],
        "trace": [],
    }
    return app.invoke(initial)
