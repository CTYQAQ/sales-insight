"""Agent 层导出。

两个 Agent：
  run_agent       —— 数据问答（Text2SQL + 自纠错）
  run_chat_agent  —— 对话 Agent（意图路由 + RAG + 多轮记忆）

对话 Agent 在 data 分支内部会调用数据问答 Agent，复用同一套安全围栏，
所以两条链路的安全边界完全一致。
"""
from app.modules.ai.agent.chat_graph import build_chat_agent, run_chat_agent
from app.modules.ai.agent.chat_state import ChatState, ChatTrace
from app.modules.ai.agent.graph import DEFAULT_MAX_ATTEMPTS, build_agent, run_agent
from app.modules.ai.agent.state import AgentTrace, QAState

__all__ = [
    "build_agent",
    "run_agent",
    "QAState",
    "AgentTrace",
    "DEFAULT_MAX_ATTEMPTS",
    "build_chat_agent",
    "run_chat_agent",
    "ChatState",
    "ChatTrace",
]
