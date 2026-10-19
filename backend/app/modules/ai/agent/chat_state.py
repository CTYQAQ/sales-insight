"""对话 Agent 的状态定义。

与数据问答 Agent（QAState）的区别：
  QAState 只回答"数据是多少"，流程固定为 改写→生成→校验→执行→解读。
  对话 Agent 要回答**任意问题**，所以先判断意图，再走不同分支：
      问口径/流程  → RAG 检索知识库 → 带引用作答
      问数据       → 复用 QAState 的整条数据链路（作为子图/内嵌调用）
      闲聊/其他    → 直接对话

LangGraph 的 messages 用 add_messages 归约器，天然支持多轮：
每一轮把历史消息一起喂进去，模型就能理解"那上个月呢"这种省略式追问。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

Intent = Literal["knowledge", "data", "chat"]


class ChatTrace(TypedDict):
    """单步记录，前端用来渲染"思考过程"。"""

    node: str
    status: str
    detail: str
    elapsed_ms: int


class ChatState(TypedDict, total=False):
    # ---- 输入 ----
    question: str  # 本轮用户提问
    today: str
    regions: list[str] | None
    history: list[dict[str, str]]  # 之前的对话（role/content），用于多轮指代消解

    # ---- 路由 ----
    intent: Intent
    intent_reason: str
    standalone_question: str  # 指代消解后的独立问句（"那上个月呢" → "上个月营收是多少"）

    # ---- RAG ----
    retrieved: list[dict[str, Any]]  # 命中的知识片段
    sources: list[dict[str, str]]  # 引用来源，回传给前端展示

    # ---- 数据分支（复用 QAState 的结果）----
    sql: str
    rows: list[dict[str, Any]]
    row_count: int

    # ---- 输出 ----
    answer: str
    trace: Annotated[list[ChatTrace], lambda a, b: (a or []) + (b or [])]
    messages: Annotated[list, add_messages]
