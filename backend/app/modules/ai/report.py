"""AI 自动生成销售分析报告。

流程：算出确定性指标 → 把指标喂给 LLM 做解读 → 落库。
关键原则：**LLM 只负责解读已经算准的数字，绝不让它凭空造数据**，
所以报告里出现的每一个百分比，来源都是本地 SQL 聚合结果。
"""
import json
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.errors import DependencyError, ValidationError
from app.core.logging import get_logger
from app.db.models.ops import AnalysisReport
from app.modules.ai.provider import LLMProvider, get_llm_provider
from app.modules.analytics.service import MetricsService

log = get_logger(__name__)

REPORT_SYSTEM = """你是资深销售运营分析师，服务于企业销售管理层。
你的职责是把给定的数据指标翻译成**能直接拿去开会的业务结论**。

要求：
1. 只使用提供的指标数据，禁止编造数据、禁止臆测外部信息。
2. 先给「一句话结论」，再分点展开，最后给建议。
3. 每个结论后面必须带上支撑它的数字。
4. 明确指出异常，不要粉饰：哪项在跌、跌了多少、可能是哪块贡献的。
5. 建议必须是具体到可执行动作的建议（比如"重点跟进本周流失的TOP3客户"），不要写套话。
6. 用 Markdown 输出，控制在 800 字以内。"""


def _fmt(v: Any) -> str:
    if v is None:
        return "无对比数据"
    return f"{v:+.2f}%"


class ReportService:
    def __init__(self, session: Session, provider: LLMProvider | None = None):
        self.session = session
        self._provider = provider

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    def _build_facts(self, regions: list[str] | None, end: date, days: int) -> dict[str, Any]:
        svc = MetricsService(self.session, regions)
        rng = svc.date_range()
        anchor = end
        facts = {
            "summary": svc.summary(anchor, compare_days=days),
            "by_region": svc.breakdown(*self._window(anchor, days), dimension="region", limit=8),
            "by_product": svc.breakdown(*self._window(anchor, days), dimension="product", limit=8),
            "by_customer": svc.breakdown(*self._window(anchor, days), dimension="customer", limit=8),
            "by_channel": svc.breakdown(*self._window(anchor, days), dimension="channel", limit=6),
            "movers": svc.top_movers(anchor, days=days, limit=5),
        }
        if rng:
            facts["data_coverage"] = {"min": rng[0].isoformat(), "max": rng[1].isoformat()}
        return facts

    @staticmethod
    def _window(end: date, days: int) -> tuple[date, date]:
        from datetime import timedelta

        return end - timedelta(days=days - 1), end

    def generate(
        self,
        *,
        regions: list[str] | None,
        end: date | None = None,
        days: int = 30,
        user_id: int | None = None,
        save: bool = True,
    ) -> AnalysisReport:
        anchor = end or date.today()
        facts = self._build_facts(regions, anchor, days)
        summary = facts["summary"]

        if summary["current"]["revenue"] == 0:
            raise ValidationError("所选区间内没有销售数据，先接入数据再生成报告")

        user_prompt = f"""请基于以下数据，写一份面向销售管理层的分析报告。

## 分析口径
- 本期：{summary['period']['start']} ~ {summary['period']['end']}
- 上期：{summary['previous_period']['start']} ~ {summary['previous_period']['end']}

## 核心指标
{json.dumps(summary, ensure_ascii=False, indent=2)}

## 维度拆解
{json.dumps({k: v for k, v in facts.items() if k.startswith('by_')}, ensure_ascii=False, indent=2)}

## 环比异动（降幅最大）
{json.dumps(facts['movers'], ensure_ascii=False, indent=2)}

请输出报告。"""

        content = self.provider.complete(
            REPORT_SYSTEM, user_prompt, max_tokens=2500, temperature=0.4
        )

        report = AnalysisReport(
            id=f"rpt-{uuid4().hex[:16]}",
            title=f"销售分析报告（{summary['period']['start']} ~ {summary['period']['end']}）",
            period_start=summary["period"]["start"],
            period_end=summary["period"]["end"],
            scope={"regions": regions or "全部", "days": days},
            summary_md=content,
            model=getattr(self.provider, "name", "unknown"),
            generated_by=user_id,
        )

        if save:
            self.session.add(report)
            self.session.commit()
            self.session.refresh(report)
        log.info("report_generated id=%s model=%s", report.id, report.model)
        return report

    def explain_question(self, question: str, query_result: list[dict], generated_sql: str) -> str:
        system = "你是数据分析助手，用简洁中文回答。只能依据给定的查询结果作答，不允许补充查询以外的数据。"
        preview = json.dumps(query_result[:20], ensure_ascii=False, indent=2)
        user = f"""用户问题：{question}

执行的查询：
{generated_sql}

查询结果（最多展示20行，共 {len(query_result)} 行）：
{preview}

请给出结论式回答，控制在 200 字以内，并点出最关键的那个数字。"""
        return self.provider.complete(system, user, max_tokens=800, temperature=0.3)


def ensure_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    raise DependencyError("日期解析失败")
