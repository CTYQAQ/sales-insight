"""
Agent 节点实现。

分工原则（也就是"为什么要拆成多个 agent 而不是一个大 Prompt"）：
- 每个节点只做一件事，Prompt 短、职责单一，出错时容易定位是"理解错了"还是"写错了 SQL"。
- 单个巨型 Prompt 里塞规则，规则一多就开始互相干扰，且无法在中途插入确定性校验。
- 中间插入 validate_sql / enforce_scope 这两个**非 LLM** 的硬闸门，这是安全底线。

节点清单：
  rewrite      → 用户口语 → 规整问句 + 维度匹配提示
  generate_sql → 规整问句 → SQL
  guard        → 安全校验 + 权限注入 + 兜底（纯代码，不过 LLM）
  execute      → 执行查询
  reflect      → 失败/空结果时分析原因，决定怎么改
  answer       → 依据查询结果写结论
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.ai.provider import LLMProvider
from app.modules.ai.text2sql import (
    build_dimension_values,
    enforce_scope,
    execute_sql,
    validate_sql,
)

log = get_logger(__name__)


def _trace(node: str, status: str, detail: str, started: float) -> dict[str, Any]:
    return {
        "node": node,
        "status": status,
        "detail": detail,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }


# --------------------------------------------------------------------------
# 1. Query Rewrite
# --------------------------------------------------------------------------
REWRITE_SYSTEM = """你是销售数据问答的「提问改写器」。把用户的口语化问题改写成 SQL 分析师能一次看懂的规整问句。

要求：
1. 只输出改写后的一句话，不要解释、不要前后缀。
2. 把相对时间换算成明确日期区间（今天日期会给你）。
3. 如果用户说的维度词与给定的真实取值不完全一致（如"华东区" vs "华东"），
   在改写结果后用 `|` 分隔附上提示，格式：`规整问题 | 匹配提示`。
   没有需要提示的就不要加 `|`。
4. 保留用户原本的统计意图（求和/比较/排名/趋势），不要自己加需求。
5. 去掉寒暄和语气词。"""


def rewrite_node(state: dict, *, provider: LLMProvider, session: Session) -> dict:
    started = time.perf_counter()
    dims = build_dimension_values(session)

    user = (
        f"今天日期：{state['today']}\n\n"
        f"各维度真实取值：\n{dims or '（无）'}\n\n"
        f"用户问题：{state['question']}\n\n"
        "请输出改写后的问句："
    )

    try:
        raw = provider.complete(REWRITE_SYSTEM, user, max_tokens=300, temperature=0.0)
        raw = raw.strip().splitlines()[0].strip()
    except AppError:
        # 改写失败不该拖垮整条链路 —— 直接用原问题继续，降级可接受
        log.warning("rewrite_failed_fallback_to_raw_question")
        return {
            "rewritten": state["question"],
            "rewrite_hint": "",
            "trace": [_trace("rewrite", "failed", "改写失败，回退使用原问题", started)],
        }

    rewritten, _, hint = raw.partition("|")
    return {
        "rewritten": rewritten.strip() or state["question"],
        "rewrite_hint": hint.strip(),
        "trace": [
            _trace(
                "rewrite",
                "ok",
                f"已改写为「{rewritten.strip()[:60]}」" + (f"（提示：{hint.strip()}）" if hint.strip() else ""),
                started,
            )
        ],
    }


# --------------------------------------------------------------------------
# 2. 生成 SQL
# --------------------------------------------------------------------------
SQL_SYSTEM_TMPL = """你是企业销售数据的 SQL 分析师，把中文问题翻译成 SQLite 查询。

可用表结构：
{schema}

各维度真实取值：
{dims}

硬性规则：
1. 只输出一条 SQL，不要解释、不要 Markdown 代码块标记。
2. 只能是 SELECT，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA。
3. 涉及金额一律用 SUM(revenue)；毛利率 = SUM(gross_profit)*100.0/NULLIF(SUM(revenue),0)。
4. 日期比较一律写成 date >= 'YYYY-MM-DD' 且 date < 'YYYY-MM-DD'。
5. 不要使用 SELECT *。
6. 默认加 LIMIT {row_limit}。
7. 维度取值必须严格照抄上面给出的真实值，**严禁臆造**；拿不准用 LIKE '%关键词%'。"""


def generate_sql_node(state: dict, *, provider: LLMProvider, session: Session) -> dict:
    started = time.perf_counter()
    from app.core.config import settings
    from app.modules.ai.text2sql import SCHEMA_DDL

    system = SQL_SYSTEM_TMPL.format(
        schema=SCHEMA_DDL,
        dims=build_dimension_values(session) or "（无）",
        row_limit=settings.TEXT2SQL_ROW_LIMIT,
    )

    parts = [
        f"今天日期：{state['today']}",
        f"问题：{state.get('rewritten') or state['question']}",
    ]
    if state.get("rewrite_hint"):
        parts.append(f"维度匹配提示：{state['rewrite_hint']}")
    if state.get("last_error"):
        # 关键：把上一轮失败原因喂回去，这是 agent 能自我修正的前提
        parts.append(
            f"\n上一次生成的 SQL 执行失败，原因如下，请针对性修正：\n{state['last_error']}\n"
            f"上一次的 SQL：\n{state.get('sql', '')}"
        )
    parts.append("\n请输出 SQL：")

    raw = provider.complete(system, "\n".join(parts), max_tokens=800, temperature=0.0)
    attempt = state.get("attempt", 0) + 1
    status = "retry" if state.get("last_error") else "ok"
    detail = f"第 {attempt} 次生成 SQL" + ("（针对上次错误修正）" if state.get("last_error") else "")
    return {
        "sql": raw.strip(),
        "attempt": attempt,
        "trace": [_trace("generate_sql", status, detail, started)],
    }


# --------------------------------------------------------------------------
# 3. 安全闸门（纯代码，不经过 LLM）
# --------------------------------------------------------------------------
def guard_node(state: dict) -> dict:
    """白名单 + AST 校验 + 权限注入 + LIMIT 兜底。

    这一段刻意不用 LLM：安全边界必须由确定性代码守，不能交给模型"自觉"。
    """
    started = time.perf_counter()
    try:
        safe = validate_sql(state["sql"])
        safe = enforce_scope(safe, state.get("regions"))
    except AppError as exc:
        return {
            "safe_sql": "",
            "last_error": f"SQL 未通过安全校验：{exc.message}",
            "trace": [_trace("guard", "blocked", f"安全校验拦截：{exc.message}", started)],
        }
    return {
        "safe_sql": safe,
        "trace": [_trace("guard", "ok", "通过安全校验，已注入权限与行数上限", started)],
    }


# --------------------------------------------------------------------------
# 4. 执行
# --------------------------------------------------------------------------
def _is_null_result(rows: list[dict[str, Any]]) -> bool:
    """判定「查到了但没意义」：空表，或只有一行且所有值都是 None。

    为什么要单独判：SUM() 在匹配不到数据时返回一行 NULL，
    它不是"没有结果"，而是"有结果但没值"。不识别这点就会对着 NULL 编结论。
    """
    if not rows:
        return True
    if len(rows) == 1:
        return all(v is None for v in rows[0].values())
    return False


def execute_node(state: dict, *, session: Session) -> dict:
    started = time.perf_counter()
    if not state.get("safe_sql"):
        return {"rows": [], "row_count": 0, "is_empty_result": True, "last_error": state.get("last_error", "")}

    try:
        rows = execute_sql(session, state["safe_sql"])
    except AppError as exc:
        return {
            "rows": [],
            "row_count": 0,
            "is_empty_result": True,
            "last_error": f"SQL 执行报错：{exc.message}",
            "trace": [_trace("execute", "failed", f"执行报错：{exc.message[:80]}", started)],
        }

    empty = _is_null_result(rows)
    detail = (
        "查询返回空结果或全 NULL，需要反思原因"
        if empty
        else f"返回 {len(rows)} 行数据"
    )
    return {
        "rows": rows,
        "row_count": len(rows),
        "is_empty_result": empty,
        "last_error": "" if not empty else state.get("last_error", ""),
        "trace": [_trace("execute", "retry" if empty else "ok", detail, started)],
    }


# --------------------------------------------------------------------------
# 5. 反思（Agent 的灵魂）
# --------------------------------------------------------------------------
REFLECT_SYSTEM = """你是 SQL 调试专家。一段查询没有返回有意义的结果，请判断问题出在哪并给出修正方向。

常见原因，按概率从高到低：
1. **维度取值不匹配**：WHERE 里写的名字和库里存的不一样（最容易犯）。比如库里是"华东"却写了"华东区"。
2. **日期区间错位**：数据没有覆盖问的时间段，或边界写成了 > 而漏掉当天。
3. **关联字段错误**：JOIN 用错了键。
4. **过滤条件过严**：叠加了过多 AND。

请只输出一句简短诊断 + 一条**具体的**修改建议，格式：
`原因：...；建议：...`
不要输出 SQL 本身。"""


def reflect_node(state: dict, *, provider: LLMProvider, session: Session) -> dict:
    """分析失败原因，把结论写进 last_error，下一轮生成 SQL 时会带上它。

    这是整个 Agent 能自我纠错的关键环节：没有它，重试只是把同一个错误再犯一遍。
    """
    started = time.perf_counter()
    dims = build_dimension_values(session)

    user = (
        f"用户问题：{state['question']}\n"
        f"当前 SQL：\n{state.get('safe_sql') or state.get('sql')}\n\n"
        f"返回情况：{state.get('last_error') or '查询成功但结果为空或全为 NULL'}\n\n"
        f"各维度真实取值：\n{dims or '（无）'}\n\n"
        "请诊断原因并给建议："
    )

    try:
        diagnosis = provider.complete(REFLECT_SYSTEM, user, max_tokens=400, temperature=0.0).strip()
    except AppError:
        diagnosis = "原因：保持原思路重试一次；建议：放宽过滤条件，或改用 LIKE 模糊匹配维度值"

    return {
        "last_error": diagnosis,
        "trace": [_trace("reflect", "retry", diagnosis[:100], started)],
    }


# --------------------------------------------------------------------------
# 6. 解读
# --------------------------------------------------------------------------
ANSWER_SYSTEM = """你是数据分析助手，用简洁中文回答业务问题。

铁律：
1. 只能依据给定的查询结果作答，**不允许**补充查询结果以外的任何数字或事实。
2. 结果为空或全为 NULL 时，必须如实说明"没有查到符合条件的数据"，并给出可能原因，
   **绝对不允许**猜测一个数字或用"可能是…"来搪塞。
3. 结尾点出最关键的 1~2 个数字，方便用户直接拿走用。
4. 控制在 150 字以内。"""


def answer_node(state: dict, *, provider: LLMProvider) -> dict:
    started = time.perf_counter()
    import json

    rows = state.get("rows") or []
    preview = json.dumps(rows[:20], ensure_ascii=False, indent=2) if rows else "（空）"

    user = (
        f"用户问题：{state['question']}\n\n"
        f"执行的 SQL：\n{state.get('safe_sql') or '（未产生有效 SQL）'}\n\n"
        f"查询结果（共 {len(rows)} 行，最多展示 20 行）：\n{preview}"
    )

    try:
        answer = provider.complete(ANSWER_SYSTEM, user, max_tokens=600, temperature=0.2)
    except AppError as exc:
        answer = f"查询已完成，但生成解读时出错：{exc.message}"
        return {"answer": answer, "trace": [_trace("answer", "failed", "解读失败", started)]}

    return {
        "answer": answer.strip(),
        "trace": [_trace("answer", "ok", "已依据查询结果生成结论", started)],
    }
