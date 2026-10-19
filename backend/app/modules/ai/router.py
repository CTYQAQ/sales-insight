"""AI 能力路由：自动生成报告、对话式问数。"""
import time
from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import DbDep, UserDep
from app.core.config import settings
from app.core.errors import AppError, DependencyError, ValidationError
from app.core.logging import get_logger
from app.db.models.ops import AnalysisReport, Conversation
from app.modules.ai.agent import run_agent, run_chat_agent
from app.modules.ai.provider import get_llm_provider
from app.modules.ai.report import ReportService
from app.modules.ai.text2sql import build_system_prompt, enforce_scope, execute_sql, validate_sql

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["ai"])


class ReportIn(BaseModel):
    days: int = Field(30, ge=7, le=180)
    end: str | None = None


class ChatIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class DialogMsg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=2000)


class DialogIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    # 多轮记忆：由前端带上最近若干轮，服务端不存进程内状态
    history: list[DialogMsg] = Field(default_factory=list)


@router.post("/reports/generate")
def generate_report(payload: ReportIn, session: DbDep, user: UserDep):
    end = date.today()
    if payload.end:
        try:
            end = date.fromisoformat(payload.end)
        except ValueError as exc:
            raise ValidationError("日期格式应为 YYYY-MM-DD") from exc

    service = ReportService(session)
    report = service.generate(
        regions=user.visible_regions(), end=end, days=payload.days, user_id=user.id
    )
    return _serialize_report(report)


@router.get("/reports")
def list_reports(session: DbDep, _: UserDep, limit: int = Query(20, ge=1, le=100)):
    rows = (
        session.query(AnalysisReport)
        .order_by(AnalysisReport.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_report(r, full=False) for r in rows]


@router.get("/reports/{report_id}")
def get_report(report_id: str, session: DbDep, _: UserDep):
    report = session.get(AnalysisReport, report_id)
    if report is None:
        raise AppError("报告不存在", code="not_found")
    return _serialize_report(report)


@router.post("/chat/query")
def chat_query(payload: ChatIn, session: DbDep, user: UserDep):
    """中文提问 → 生成 SQL → 安全校验 → 执行 → AI 解读。"""
    started = time.perf_counter()
    conversation = Conversation(
        id=f"conv-{uuid4().hex[:12]}", user_id=user.id, question=payload.question
    )

    if not settings.TEXT2SQL_ENABLED:
        conversation.status = "blocked"
        conversation.error_message = "对话问数功能已关闭"
        session.add(conversation)
        session.commit()
        raise ValidationError("对话问数功能未启用")

    regions = user.visible_regions()
    region_hint = (
        "不需要写区域过滤条件，系统会自动补充。"
        if regions is None
        else f"只需要关注这些区域：{', '.join(regions)}，但不要写 region_code 的过滤条件，系统会自动补。"
    )
    today = date.today().isoformat()
    user_prompt = f"今天日期是 {today}。\n{region_hint}\n\n问题：{payload.question}\n\n请输出 SQL："

    provider = get_llm_provider()
    # 带上库里维度的真实取值，否则模型会凭常识造词（"华东区" vs 实际的"华东"），
    # JOIN 落空后还会对着 NULL 编出错误结论
    system_prompt = build_system_prompt(session)
    raw_sql = provider.complete(system_prompt, user_prompt, max_tokens=800, temperature=0.0)

    try:
        safe_sql = validate_sql(raw_sql)
        scoped_sql = enforce_scope(safe_sql, regions)
    except ValidationError as exc:
        conversation.status = "blocked"
        conversation.generated_sql = raw_sql
        conversation.error_message = str(exc)
        conversation.elapsed_ms = int((time.perf_counter() - started) * 1000)
        session.add(conversation)
        session.commit()
        raise

    rows = execute_sql(session, scoped_sql)

    try:
        answer = ReportService(session, provider).explain_question(payload.question, rows, scoped_sql)
    except Exception:  # noqa: BLE001
        # 查询已经成功了，解读失败不应该让用户白等，降级成表格直出
        log.exception("chat_explain_failed")
        answer = f"查询成功，共 {len(rows)} 行（AI 解读失败，已为你直接返回数据）。"

    conversation.status = "success"
    conversation.generated_sql = scoped_sql
    conversation.result_preview = {"columns": list(rows[0].keys()) if rows else [], "rows": rows[:10]}
    conversation.answer = answer
    conversation.elapsed_ms = int((time.perf_counter() - started) * 1000)
    session.add(conversation)
    session.commit()

    return {
        "sql": scoped_sql,
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows[:200],
        "answer": answer,
        "elapsed_ms": conversation.elapsed_ms,
    }


@router.post("/chat/agent/query")
def chat_agent_query(payload: ChatIn, session: DbDep, user: UserDep):
    """Agent 版问数：改写 → 生成 → 校验 → 执行 →（失败则反思重来）→ 解读。

    与 /chat/query 的区别：单次翻译只给一次机会，改错了就结束；
    这里在结果不可用时能自己诊断原因并重试，且把每一步都记录在 trace 里。
    """
    started = time.perf_counter()

    if not settings.TEXT2SQL_ENABLED:
        raise ValidationError("对话问数功能未启用")

    conversation = Conversation(
        id=f"conv-{uuid4().hex[:12]}", user_id=user.id, question=payload.question
    )

    provider = get_llm_provider()
    try:
        final = run_agent(
            question=payload.question,
            today=date.today().isoformat(),
            regions=user.visible_regions(),
            session=session,
            provider=provider,
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Agent 内部任何未预期错误，都不能把栈丢给用户
        log.exception("agent_query_failed")
        conversation.status = "failed"
        conversation.error_message = str(exc)
        session.add(conversation)
        session.commit()
        raise DependencyError("Agent 执行异常，请稍后重试") from exc

    rows = final.get("rows") or []
    elapsed = int((time.perf_counter() - started) * 1000)

    conversation.status = "success" if final.get("resolved", True) else "partial"
    conversation.generated_sql = final.get("safe_sql") or final.get("sql") or ""
    conversation.result_preview = {
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows[:10],
        "agent_attempts": final.get("attempt", 0),
    }
    conversation.answer = final.get("answer", "")
    conversation.elapsed_ms = elapsed
    session.add(conversation)
    session.commit()

    return {
        "sql": conversation.generated_sql,
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows[:200],
        "answer": final.get("answer", ""),
        "rewritten": final.get("rewritten", ""),
        "attempts": final.get("attempt", 0),
        "trace": final.get("trace", []),
        "elapsed_ms": elapsed,
    }


@router.post("/chat/dialog")
def chat_dialog(payload: DialogIn, session: DbDep, user: UserDep):
    """对话 Agent：意图路由 → （知识库 RAG / 数据查询 / 闲聊）→ 带引用作答。

    与 /chat/agent/query 的差别：
      - 那个只会查数据，问"毛利率怎么算"就答不了；
      - 这个先判断意图，问口径去翻知识库，问数字才走 SQL，
        并且支持多轮追问（"那上个月呢"）。
    """
    started = time.perf_counter()

    provider = get_llm_provider()
    history = [{"role": m.role, "content": m.content} for m in payload.history[-6:]]

    try:
        final = run_chat_agent(
            question=payload.question,
            today=date.today().isoformat(),
            regions=user.visible_regions(),
            history=history,
            session=session,
            provider=provider,
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("dialog_agent_failed")
        raise DependencyError("对话服务异常，请稍后重试") from exc

    elapsed = int((time.perf_counter() - started) * 1000)
    rows = final.get("rows") or []

    # 落库仅在走数据分支时才有 SQL/结果；知识型问答也存一条，便于回溯
    if settings.TEXT2SQL_ENABLED or final.get("intent") != "data":
        conversation = Conversation(
            id=f"conv-{uuid4().hex[:12]}",
            user_id=user.id,
            question=payload.question,
            answer=final.get("answer", ""),
            generated_sql=final.get("sql", ""),
            status="success",
            elapsed_ms=elapsed,
            result_preview={
                "intent": final.get("intent", ""),
                "sources": final.get("sources", []),
            },
        )
        session.add(conversation)
        session.commit()

    return {
        "answer": final.get("answer", ""),
        "intent": final.get("intent", ""),
        "intent_reason": final.get("intent_reason", ""),
        "standalone_question": final.get("standalone_question", payload.question),
        "sources": final.get("sources", []),
        "retrieved": final.get("retrieved", []),
        "sql": final.get("sql", ""),
        "row_count": len(rows),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows[:200],
        "trace": final.get("trace", []),
        "elapsed_ms": elapsed,
    }


@router.get("/knowledge/stats")
def knowledge_stats(_: UserDep):
    """知识库状态。前端首页展示"已入库 N 段"用。"""
    from app.modules.knowledge import service

    return service.stats()


@router.get("/knowledge/documents")
def knowledge_documents(_: UserDep):
    from app.modules.knowledge import service

    return service.list_documents()


@router.post("/knowledge/upload")
async def knowledge_upload(file: UploadFile, _: UserDep):
    """上传文档入库。支持 md/txt/csv/xlsx。"""
    from app.modules.knowledge import service

    data = await file.read()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationError(f"文件超过 {settings.MAX_UPLOAD_MB}MB 限制")
    return service.ingest_file(file.filename or "upload.md", data)


@router.post("/knowledge/seed")
def knowledge_seed(_: UserDep):
    """灌入内置示例知识库（演示用，幂等）。"""
    from app.modules.knowledge import service
    from app.modules.knowledge.seed import SEED_DOCS

    return service.seed_from_texts(SEED_DOCS)


@router.post("/knowledge/rebuild")
def knowledge_rebuild(_: UserDep):
    """全量重建索引。换了 embedding 模型后必须做这一步。"""
    from app.modules.knowledge import service

    return service.rebuild()


@router.post("/knowledge/reset")
def knowledge_reset(_: UserDep):
    from app.modules.knowledge import service

    return service.reset()


@router.get("/knowledge/search")
def knowledge_search(
    _: UserDep,
    q: str = Query(min_length=1, max_length=200),
    top_k: int = Query(5, ge=1, le=20),
    hybrid: bool = Query(True),
):
    """检索调试接口：能看到每段的向量分、BM25 分、覆盖率，方便调参。"""
    from app.modules.knowledge import service

    return [h.to_dict() for h in service.query(q, top_k=top_k, hybrid=hybrid)]


@router.get("/chat/history")
def chat_history(session: DbDep, user: UserDep, limit: int = Query(10, ge=1, le=50)):
    rows = (
        session.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "question": r.question,
            "answer": r.answer,
            "sql": r.generated_sql,
            "status": r.status,
            "elapsed_ms": r.elapsed_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _serialize_report(r: AnalysisReport, *, full: bool = True) -> dict:
    base = {
        "id": r.id,
        "title": r.title,
        "period_start": r.period_start,
        "period_end": r.period_end,
        "model": r.model,
        "scope": r.scope,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if full:
        base["summary_md"] = r.summary_md
    else:
        base["excerpt"] = (r.summary_md or "")[:120]
    return base
