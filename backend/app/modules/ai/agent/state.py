"""
Agent 共享状态。

LangGraph 的核心约定：节点之间不直接传参，而是读写一个共享 State。
每个节点返回**增量**（只给出自己改动的字段），由框架负责合并。

这样写的好处是可观测：跑完一次能看到每一步都改了什么，出问题能定位到具体节点，
而不是拿到一团"最后的结果对不上"。
"""
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentTrace(TypedDict):
    """单步执行记录，最终回传给前端做「思考过程」展示。"""

    node: str  # 节点名：rewrite / generate_sql / execute / reflect ...
    status: str  # ok / retry / failed / blocked
    detail: str  # 人可读的一句话说明
    elapsed_ms: int


class QAState(TypedDict, total=False):
    """数据问答 Agent 的状态。

    total=False 表示所有键都可缺省 —— 图是按需生长的，
    不是每个分支都会填满所有字段。
    """

    # ---- 输入 ----
    question: str  # 用户原始问题，全文保留用于最终解读
    today: str  # 今天日期，注入给模型做相对时间换算（"上个月"）
    regions: list[str] | None  # 当前用户可见区域；None = 不限

    # ---- 中间产物 ----
    rewritten: str  # Query Rewrite 之后的规整问题
    rewrite_hint: str  # 改写时一并带出的匹配提示（如"华东区"→"华东"）

    sql: str  # 当前候选 SQL（每轮反思都会覆写）
    safe_sql: str  # 通过安全校验、已注入权限的 SQL
    last_error: str  # 上一轮失败原因，供反思节点修正

    rows: list[dict[str, Any]]  # 查询结果
    row_count: int  # 行数
    is_empty_result: bool  # 查到了结构但结果为空/全 NULL —— 重点反思信号

    # ---- 控制 ----
    attempt: int  # 已尝试生成 SQL 的次数，用来卡重试上限
    max_attempts: int
    resolved: bool  # 是否已拿到可用结果

    # ---- 输出 ----
    answer: str
    trace: Annotated[list[AgentTrace], lambda a, b: (a or []) + (b or [])]
    messages: Annotated[list, add_messages]
