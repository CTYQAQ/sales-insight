"""RAG 全链路自检（单进程完成：生成种子 → 切分 → 向量化 → 入 Milvus → 混合检索）。

为什么要单进程一把跑完：本机沙箱会在命令结束后回收部分新建文件/目录，
分步执行时后一步看不到前一步写的种子文件。把整条链路收进一个进程，
既能完成验证，也顺带证明这条链路本身是自洽的。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

OUT = BACKEND / "verify_kb_out.txt"
lines: list[str] = []


def p(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(s, flush=True)


SEEDS = {
    "metrics_handbook.md": """# 销售分析口径手册

## 营收 Revenue

营收等于含税开票金额合计，不含运费、不含押金、不含代收代付。

归属月份按开票日期，不按合同签订日期，也不按收款日期。这是财务与销售最常对不上的地方。

## 成本 Cost

成本只统计直接采购成本，即商品的采购单价乘以数量。

仓储费、物流费、售后维修费这三类不计入成本，它们进的是费用科目。

这也是毛利率看起来比财务口径偏高的原因。

## 毛利率 Gross Margin

毛利率等于营收减成本再除以营收，最后乘以百分之百。

如果该期间营收为零，毛利率返回空值而不是零，因为零会被误读成完全不赚钱。

毛利率低于百分之十五的产品线需要在月度经营会上单独说明原因。

## 退货 Return

退货金额在退货发生当月冲减营收。若退款跨月，以退货单审批日期为准，不看退款到账日期。

退货率等于退货金额除以当月营收，再乘以百分之百。

## 环比与同比

环比等于本期减上期再除以上期，是相邻两个统计周期的比较。月环比就是本月对上月。

同比等于本期减去年同期再除以去年同期。季节性强的品类必须看同比，环比会严重失真。

当上期为零时，环比返回空值，不返回无穷大。

## 区域划分

华东包含上海、江苏、浙江、安徽。

华北包含北京、天津、河北、山西、内蒙古。

华南包含广东、广西、福建、海南。

西南包含四川、重庆、云南、贵州。

华中包含湖北、湖南、河南、江西。

注意华中包含河南和江西，很多外部报表把江西算进华东，本平台不这样处理。

## 数据更新频率

事实表每日凌晨两点增量同步一次，所以当天的数据要到次日才能看到。

如果用户问今天的销售额，正确回答是数据尚未同步，最近可用日期为昨天。

## 权限与数据范围

区域经理只能看到自己所属大区的数据，总部角色可以看全量。

这是行级权限，在 SQL 层强制注入，不依赖前端过滤。
""",
    "faq.md": """# 常见问题与排查指南

## 报表数字和财务系统对不上怎么办

先确认三件事。第一是时间口径，平台按开票日期归月，财务可能按收款日期或签收日期。

第二是成本口径，平台只算直接采购成本，财务的全成本还含仓储物流和分摊费用。

第三是退货处理，平台在退货当月冲减营收，财务有时直接冲原单，导致两个月都变了。

确认完仍对不上，就导出明细表比对单号，差异通常是个别订单的归月错误。

## 为什么某个月的数据是空的

最常见原因是该月尚未同步，历史补数需要手工触发。

其次是区域权限，区域经理账号查别的区域会返回空结果而不是报错。
这是有意的设计，避免通过报错探测其他区域是否存在数据。

## 上传 Excel 后数据显示不全

检查三件事：表头是否在第一行、日期列是否被 Excel 存成了文本、
金额列有没有混入千分位逗号。

千分位逗号是最高频的问题，带逗号的金额会被解析成字符串，聚合时静默变成零。

## AI 生成的查询结果不对

AI 只负责把自然语言翻译成 SQL 并解读查询结果，所有数字都来自本地数据库的真实查询。

如果结果不对，请检查问题里的时间范围和维度名称是否与实际数据一致。

常见误用有两个：说上个月但系统当前没有该月数据，说华东区而实际区域名是华东。

## 能不能对接我们的 MySQL

可以，平台支持 SQLite、MySQL、PostgreSQL 三种数据源，改一个连接串即可。

注意 AI 问数依赖表结构白名单，接入新库需要在配置里登记允许查询的表。
""",
    "ops_manual.md": """# 部署与运维手册

## 本地启动

后端进入 backend 目录，激活虚拟环境后执行 python run.py，默认监听 8000 端口。

前端进入 frontend 目录执行 npm run dev，默认监听 5173 端口，已配置代理到 8000。

## 环境变量改动为什么不生效

配置是启动时一次性加载并缓存的，改了 .env 必须重启后端进程。

开发模式的自动重载只监听 Python 文件，改 .env 不会触发重载。

## 向量库选择

默认使用 Milvus 的 lite 模式，数据落在本地文件，不需要 Docker。

数据量超过百万级或者需要多实例共享时，改用 standalone 模式，把连接串改成 HTTP 地址即可。

## 换 Embedding 模型后检索变空

这是因为集合名里带了模型指纹，换模型等于换了一个新集合。

正确做法是换完模型后重建索引，把已有文档重新灌一遍。
""",
}


def main() -> int:
    from app.core.config import settings
    from app.modules.knowledge import service, store
    from app.modules.knowledge.embedding import get_embedding_provider
    from app.modules.knowledge.retriever import format_context, retrieve, tokenize

    p("=== 0. 环境 ===")
    emb = get_embedding_provider()
    p(f"embedding: {emb.name}  dim={emb.dim}")
    p(f"milvus_conn={settings.MILVUS_CONN}  lite={store.is_lite()}")
    p(f"hybrid={settings.RAG_HYBRID}  top_k={settings.RAG_TOP_K}  candidate_k={settings.RAG_CANDIDATE_K}")

    p("\n=== 1. 写种子文档并灌库 ===")
    from app.modules.knowledge.seed import SEED_DOCS

    store.reset_client()
    p(f"reset: {service.reset()}")
    res = service.seed_from_texts(SEED_DOCS)
    p(f"ingest: {json.dumps(res, ensure_ascii=False)}")
    st = service.stats()
    p(f"stats: {json.dumps(st, ensure_ascii=False)}")

    p("\n=== 2. 中文分词 ===")
    for q in ["毛利率怎么计算", "华东区包含哪些省份", "退货率口径", "华中区有没有江西"]:
        p(f"  {q} -> {tokenize(q)}")

    p("\n=== 3. 混合检索结果（每问 top3）===")
    queries = [
        "毛利率怎么算",
        "华东区包括哪些省份",
        "退货金额在什么月份冲减营收",
        "报表和财务对不上",
        "上期为零时环比返回什么",
        "能不能接 MySQL",
        "今天的销售额是多少",
        "换模型后检索变空了",
    ]
    for q in queries:
        p(f"\n-- Q: {q}")
        try:
            for i, h in enumerate(retrieve(q, top_k=3), 1):
                txt = h.text.replace("\n", " ")[:46]
                p(
                    f"   [{i}] fused={h.score:.5f} vec={h.vector_score:.3f}(r{h.vector_rank})"
                    f" bm25={h.bm25_score:.3f}(r{h.bm25_rank}) cov={h.coverage:.2f} topic={h.topic_score:.2f} | {h.title} | {txt}"
                )
        except Exception as exc:  # noqa: BLE001
            p(f"   ERR {type(exc).__name__}: {exc}")

    p("\n=== 4. 纯向量 vs 混合（看 BM25 是否补上精确词）===")
    for q in ["报表和财务对不上", "华中区有没有江西", "换模型后检索变空了"]:
        v = [h.title for h in retrieve(q, top_k=3, hybrid=False)]
        hh = [h.title for h in retrieve(q, top_k=3, hybrid=True)]
        p(f"\n-- {q}")
        p(f"   纯向量: {v}")
        p(f"   混合  : {hh}")

    p("\n=== 5. 上下文拼装（喂给 LLM 的格式）===")
    p(format_context(retrieve("毛利率计算公式和营收为零时的处理", top_k=2)))

    p("\n=== 6. 幂等性（重复导入不增长）===")
    before = service.stats()["chunks"]
    service.seed_from_texts(SEED_DOCS)
    after = service.stats()["chunks"]
    p(f"chunks before={before} after={after} -> {'OK 幂等' if before == after else 'FAIL 重复写入'}")

    p("\n=== 7. 文档清单 ===")
    p(json.dumps(service.list_documents(), ensure_ascii=False, indent=1))

    p("\n=== ALL DONE ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        import traceback

        p(f"FATAL {type(exc).__name__}: {exc}")
        p(traceback.format_exc())
        sys.exit(1)
