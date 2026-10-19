"""对话 Agent 的节点实现。

五个节点：
  understand  多轮指代消解 —— 把"那上个月呢"补全成独立问句
  route       意图分类：问知识 / 问数据 / 闲聊
  retrieve    RAG 混合检索（只在 knowledge 分支跑）
  answer_kb   带引用作答，强制"只说出处里有的"
  answer_data 走数据链路后解读结果
  answer_chat 闲聊直答

设计取舍：意图分类用**规则 + 小模型兜底**两级。
纯 LLM 分类要先多花一次调用（300~800ms），而且对"毛利率怎么算"这种
明显是问知识的输入，规则就足够判准。规则不确定时才叫模型，
既省延迟又省 token —— 这是工程上很实际的优化，值得在面试里讲。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.ai.provider import LLMProvider
from app.modules.knowledge.retriever import Retrieved, format_context, retrieve

log = get_logger(__name__)


def _trace(node: str, status: str, detail: str, started: float) -> dict[str, Any]:
    return {
        "node": node,
        "status": status,
        "detail": detail,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


# --------------------------------------------------------------------------
# 1. 多轮指代消解
# --------------------------------------------------------------------------
# 出现这些词，说明这句话脱离上下文就"不成立"——必须回看历史才能懂
_ANAPHORA = (
    "那", "这个", "那个", "它", "上面", "刚才", "继续", "还有", "另外",
    "呢", "换成", "再看", "同样", "也是", "对比",
)


def understand_node(state: dict, *, provider: LLMProvider) -> dict:
    """把带指代的追问补全成独立问句。

    为什么这步不能省：多轮对话里用户极少把话说全。
    "那上个月呢" 直接丢给检索或 Text2SQL，两个分支都会答错 ——
    检索不知道"那"指什么，SQL 生成不知道要查哪个指标。
    两轮问答系统上线的第一类 bug 基本都是这个。
    """
    started = time.perf_counter()
    q = (state.get("question") or "").strip()
    history = state.get("history") or []

    # 没有历史，或这轮本身已经很完整 —— 不用花一次调用
    if not history or not any(w in q for w in _ANAPHORA) or len(q) > 30:
        return {
            "standalone_question": q,
            "trace": [_trace("understand", "ok", "问句本身完整，无需指代消解", started)],
        }

    recent = "\n".join(f"{m['role']}：{m['content'][:200]}" for m in history[-6:])
    system = """你是对话上下文补全器。根据之前的对话，把用户这一轮不完整的问题补成一个可以独立理解的问句。

要求：
1. 只输出补全后的问句，不要解释。
2. 保持用户原意，不要新增他没问的内容。
3. 如果确实无法判断指代对象，原样输出用户的问题。"""

    user = f"之前的对话：\n{recent}\n\n用户这一轮：{q}\n\n补全后的独立问句："

    try:
        standalone = provider.complete(system, user, max_tokens=120, temperature=0.0).strip()
        standalone = standalone.splitlines()[0].strip() or q
    except AppError:
        return {
            "standalone_question": q,
            "trace": [_trace("understand", "failed", "补全失败，使用原问题", started)],
        }

    detail = (
        f"「{q}」→「{standalone}」"
        if standalone != q
        else "无需补全"
    )
    return {
        "standalone_question": standalone,
        "trace": [_trace("understand", "ok", detail, started)],
    }


# --------------------------------------------------------------------------
# 2. 意图路由
# --------------------------------------------------------------------------
# 数据型信号：出现指标词 + 时间/维度词，且不像在问"定义"
_DATA_HINTS = (
    "多少", "幾", "排行", "排名", "top", "前几", "占比", "趋势", "增长",
    "环比", "同比", "对比", "分别", "各个", "每个", "列出", "统计",
)
_TIME_HINTS = (
    "今年", "去年", "上月", "上个月", "本月", "这个月", "季度", "周", "日",
    "月份", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月",
    "10月", "11月", "12月",
)
# 知识型信号：在问"是什么/怎么做/为什么/规则"
_KB_HINTS = (
    "怎么算", "如何计算", "是什么", "什么是", "定义", "口径", "公式", "规则",
    "为什么", "流程", "怎么办", "怎么查", "排查", "区别", "含义", "包含哪些",
    "包括哪些", "有哪些", "怎么处理", "如何", "能不能", "支持吗", "原理",
)

_METRICS = ("营收", "销售额", "收入", "利润", "毛利", "成本", "退货", "订单", "客户", "数量", "金额")


def _rule_intent(q: str) -> tuple[str | None, str]:
    """规则先判。返回 (意图 | None, 判断依据)。

    设计原则：只在高置信度时给结论，拿不准就返回 None 交给模型。
    宁可多花一次调用，也不要规则误判后把用户带错分支。
    """
    ql = q.lower()

    kb_hit = [w for w in _KB_HINTS if w in ql]
    data_hit = [w for w in _DATA_HINTS if w in ql]
    has_metric = any(w in ql for w in _METRICS)
    has_time = any(w in ql for w in _TIME_HINTS)

    # 强知识信号：问定义/公式/口径，这类问题数据表答不了
    if kb_hit and not (has_metric and (has_time or "多少" in ql)):
        return "knowledge", f"命中知识型词：{'/'.join(kb_hit[:3])}"

    # 强数据信号：有指标 + 有"多少/排名/趋势" + 有时间或维度
    if has_metric and data_hit and (has_time or len(data_hit) >= 1):
        # 排除"毛利率怎么算"这种其实是问公式的
        if not any(w in ql for w in ("怎么算", "如何计算", "公式", "定义", "口径")):
            return "data", f"命中指标与统计词：{'/'.join(data_hit[:3])}"

    if kb_hit:
        return "knowledge", f"命中知识型词：{'/'.join(kb_hit[:3])}"

    return None, "规则无法判定"


ROUTE_SYSTEM = """你是意图分类器。判断用户这句话属于哪一类，只输出一个词：

- knowledge：问业务口径、指标定义、操作流程、问题排查、产品能力。这类问题要从知识库里查。
- data：要求查具体数字。比如某个时间/区域的销售额、排名、趋势、同比环比。
- chat：打招呼、闲聊、与业务无关的通用问题。

易混淆的判断要点：
- "毛利率怎么算" → knowledge（问公式，不是问数字）
- "上个月毛利率多少" → data（要数字）
- "华东区包括哪些省" → knowledge（问划分规则）
- "华东区上个月卖了多少钱" → data（要数字）

只输出 knowledge / data / chat 三者之一，不要输出其它任何字符。"""


def route_node(state: dict, *, provider: LLMProvider) -> dict:
    started = time.perf_counter()
    q = state.get("standalone_question") or state.get("question") or ""

    intent, reason = _rule_intent(q)
    if intent:
        return {
            "intent": intent,
            "intent_reason": reason,
            "trace": [_trace("route", "ok", f"规则判定为「{intent}」：{reason}", started)],
        }

    try:
        raw = provider.complete(ROUTE_SYSTEM, f"用户：{q}", max_tokens=10, temperature=0.0)
        word = re.sub(r"[^a-z]", "", raw.strip().lower())
        intent = word if word in ("knowledge", "data", "chat") else "knowledge"
        reason = f"规则不确定，模型判定为「{intent}」"
    except AppError:
        # 模型不可用时默认走知识库：答错了至少不会编数字出来，风险最低
        intent = "knowledge"
        reason = "模型调用失败，保守起见走知识库分支"

    return {
        "intent": intent,
        "intent_reason": reason,
        "trace": [_trace("route", "ok", reason, started)],
    }


# --------------------------------------------------------------------------
# 3. 检索
# --------------------------------------------------------------------------
def retrieve_node(state: dict) -> dict:
    started = time.perf_counter()
    q = state.get("standalone_question") or state.get("question") or ""

    try:
        hits: list[Retrieved] = retrieve(q)
    except AppError as exc:
        return {
            "retrieved": [],
            "sources": [],
            "trace": [_trace("retrieve", "failed", f"检索失败：{exc.message}", started)],
        }

    if not hits:
        return {
            "retrieved": [],
            "sources": [],
            "trace": [_trace("retrieve", "ok", "知识库无命中", started)],
        }

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for h in hits:
        key = f"{h.source}::{h.title}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({"source": h.source, "title": h.title})

    top = hits[0]
    return {
        "retrieved": [h.to_dict() for h in hits],
        "sources": sources,
        "trace": [
            _trace(
                "retrieve",
                "ok",
                f"混合检索命中 {len(hits)} 段，最相关：{top.title}（覆盖率 {top.coverage:.0%}）",
                started,
            )
        ],
    }


# --------------------------------------------------------------------------
# 4. 知识型作答
# --------------------------------------------------------------------------
KB_SYSTEM = """你是企业销售分析平台的助手。请依据给出的知识库片段回答用户问题。

铁律：
1. **只能依据给出的片段作答**。片段里没有的信息，直接说"知识库里没有相关说明"，不要用常识补。
2. 每个结论后面用 [1] [2] 标注来源编号，编号对应片段前的方括号。
3. 如果多个片段有冲突，以更具体的那个为准，并指出存在不一致。
4. 用简洁中文，200 字以内，先说结论再展开。
5. 不要罗列片段原文，要组织成通顺的回答。"""


def answer_kb_node(state: dict, *, provider: LLMProvider) -> dict:
    started = time.perf_counter()
    q = state.get("standalone_question") or state.get("question") or ""
    items = state.get("retrieved") or []

    if not items:
        # 没检索到就别让模型硬编，直接给可操作的建议
        answer = (
            "知识库里没有找到相关说明。\n\n"
            "可以试试：换个说法提问，或把这部分业务口径补充到知识库后再问。"
        )
        return {
            "answer": answer,
            "trace": [_trace("answer_kb", "ok", "无命中，如实告知并提供建议", started)],
        }

    # 还原成 Retrieved 对象只是为了复用 format_context
    retrieved = [
        Retrieved(
            chunk_id=d["chunk_id"],
            text=d["text"],
            source=d["source"],
            title=d["title"],
            score=d.get("score", 0.0),
        )
        for d in items
    ]
    context = format_context(retrieved)

    user = f"知识库片段：\n\n{context}\n\n用户问题：{q}\n\n请回答："

    try:
        answer = provider.complete(KB_SYSTEM, user, max_tokens=700, temperature=0.2)
    except AppError as exc:
        return {
            "answer": f"检索到了资料，但生成回答时出错：{exc.message}",
            "trace": [_trace("answer_kb", "failed", "生成失败", started)],
        }

    return {
        "answer": answer.strip(),
        "trace": [
            _trace("answer_kb", "ok", f"依据 {len(items)} 段知识作答，已标注引用", started)
        ],
    }


# --------------------------------------------------------------------------
# 5. 数据型作答
# --------------------------------------------------------------------------
def answer_data_node(
    state: dict, *, provider: LLMProvider, session: Session
) -> dict:
    """复用已有的数据 Agent（改写→生成→校验→执行→反思）。

    这里刻意不重写一套 Text2SQL：那套三重安全围栏 + 自纠错循环已经很完整，
    对话 Agent 只需要"调它"，不该复制一遍。子流程复用是分层的价值所在。
    """
    started = time.perf_counter()
    from app.modules.ai.agent.graph import run_agent

    q = state.get("standalone_question") or state.get("question") or ""

    try:
        final = run_agent(
            question=q,
            today=state.get("today") or "",
            regions=state.get("regions"),
            session=session,
            provider=provider,
        )
    except AppError as exc:
        return {
            "answer": f"查询数据时出错：{exc.message}",
            "trace": [_trace("answer_data", "failed", f"数据链路失败：{exc.message}", started)],
        }

    rows = final.get("rows") or []
    return {
        "answer": final.get("answer", ""),
        "sql": final.get("safe_sql") or final.get("sql") or "",
        "rows": rows,
        "row_count": len(rows),
        "trace": [
            _trace(
                "answer_data",
                "ok" if not final.get("is_empty_result") else "retry",
                f"数据链路完成，尝试 {final.get('attempt', 0)} 次，返回 {len(rows)} 行",
                started,
            )
        ],
    }


# --------------------------------------------------------------------------
# 6. 闲聊
# --------------------------------------------------------------------------
CHAT_SYSTEM = """你是企业销售分析平台的助手。用户在进行一般性对话。

要求：
1. 简洁友好，不要超过 80 字。
2. 适度引导：如果你判断用户其实想问业务数据或业务口径，主动提示他可以怎么问。
3. 不要编造任何业务数字。"""


def answer_chat_node(state: dict, *, provider: LLMProvider) -> dict:
    started = time.perf_counter()
    q = state.get("standalone_question") or state.get("question") or ""

    try:
        answer = provider.complete(CHAT_SYSTEM, q, max_tokens=300, temperature=0.5)
    except AppError as exc:
        answer = f"抱歉，我暂时无法回应：{exc.message}"

    return {
        "answer": answer.strip(),
        "trace": [_trace("answer_chat", "ok", "一般性对话", started)],
    }
