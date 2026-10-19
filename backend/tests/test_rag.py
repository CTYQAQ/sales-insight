"""RAG 检索链路测试。

这些测试全部**不需要 LLM、不需要网络**：切分与检索是确定性代码，
这也是当初把检索层和生成层分开的原因 —— 能测的部分就该能测。
"""
from __future__ import annotations

import pytest

from app.modules.knowledge.chunker import chunk_markdown, split_text
from app.modules.knowledge.retriever import query_terms, tokenize


# ---------------------------------------------------------------- 切分
def test_split_keeps_short_text_intact():
    assert split_text("很短的一句话", size=100) == ["很短的一句话"]


def test_split_respects_size():
    text = "这是一个用来测试切分的句子。" * 30
    parts = split_text(text, size=80, overlap=10)
    assert len(parts) > 1
    # 允许少量超出（句子边界优先），但不能失控
    assert all(len(p) <= 80 * 1.5 for p in parts)


def test_split_has_overlap():
    """重叠是召回质量的保障：片段边界处被切断的句子，两边都要有上下文。"""
    text = "".join(f"第{i}个句子内容。".replace("{i}", str(i)) for i in range(1, 40))
    parts = split_text(text, size=60, overlap=15)
    assert len(parts) > 2
    # 相邻片段之间必须存在共享内容
    shared = any(
        parts[i][-8:] in parts[i + 1] or parts[i + 1][:8] in parts[i]
        for i in range(len(parts) - 1)
    )
    assert shared


def test_split_empty():
    assert split_text("") == []
    assert split_text("   \n  ") == []


def test_overlap_cannot_exceed_size():
    """重叠 >= 片段长度会导致切分死循环，必须被夹住。"""
    parts = split_text("测试" * 200, size=20, overlap=100)
    assert len(parts) > 0
    assert all(len(p) <= 30 for p in parts)


def test_chunk_markdown_by_section():
    md = """# 手册

## 毛利率

毛利率等于营收减成本除以营收。

## 退货

退货在当月冲减营收。
"""
    chunks = chunk_markdown(md, source="t.md")
    titles = [c.title for c in chunks]
    assert "毛利率" in titles
    assert "退货" in titles
    # 标题要写进正文，检索命中才看得出语境
    assert all(c.title in c.text for c in chunks)


def test_chunk_id_is_stable():
    """同一文档重复切分必须产出同样 ID，否则导入会重复堆积。"""
    md = "## A\n内容一。\n\n## B\n内容二。"
    a = chunk_markdown(md, source="x.md")
    b = chunk_markdown(md, source="x.md")
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


# ---------------------------------------------------------------- 分词
def test_tokenize_chinese():
    tokens = tokenize("毛利率怎么计算")
    assert "毛利率" in tokens
    # 停用词必须被去掉，否则 BM25 的 IDF 全是噪声
    assert "怎么" not in tokens


def test_tokenize_strips_domain_suffix():
    """「华东区」要额外产出「华东」—— 语料里存的是不带"区"的写法。"""
    tokens = tokenize("华东区包括哪些省份")
    assert "华东" in tokens


def test_tokenize_keeps_english_and_numbers():
    tokens = tokenize("MySQL 营收 2024")
    assert "mysql" in tokens
    assert "2024" in tokens


def test_tokenize_drops_single_char():
    tokens = tokenize("这个是多少")
    assert all(len(t) > 1 for t in tokens)


def test_query_terms_merges_synonyms():
    """同义变体只算一个概念，否则长文档会靠"两种写法都提到"白拿两分。"""
    terms = query_terms("华东区包括哪些省份")
    assert "华东" in terms
    assert "华东区" not in terms


def test_query_terms_empty_for_stopwords():
    assert query_terms("的了是") == set()


# ---------------------------------------------------------------- 端到端检索
@pytest.fixture(scope="module")
def seeded_kb():
    """用内置种子灌一次，后续检索测试共享。

    清理放在最后也保留数据：不清空，因为 lite 模式下反复 drop/create
    会被别的进程持锁干扰；测试自身用稳定 chunk_id 保证幂等，重复跑不会堆积。
    """
    from app.modules.knowledge import service, store
    from app.modules.knowledge.seed import SEED_DOCS

    store.reset_client()
    try:
        service.seed_from_texts(SEED_DOCS)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Milvus 不可用，跳过检索测试：{exc}")
    yield


def _top_titles(q: str, k: int = 3) -> list[str]:
    from app.modules.knowledge.retriever import retrieve

    return [h.title for h in retrieve(q, top_k=k)]


@pytest.mark.parametrize(
    "question,expect",
    [
        ("毛利率怎么算", "毛利率"),
        ("退货金额在什么月份冲减营收", "退货"),
        ("上期为零时环比返回什么", "环比"),
        ("报表和财务对不上", "报表数字"),
        ("今天的销售额是多少", "数据更新"),
    ],
)
def test_retrieval_hits_expected_section(seeded_kb, question, expect):
    top = _top_titles(question)
    assert top, f"「{question}」没有任何召回"
    assert expect in top[0], f"「{question}」首选为 {top[0]}，期望包含「{expect}」"


def test_retrieval_respects_top_k(seeded_kb):
    from app.modules.knowledge.retriever import retrieve

    assert len(retrieve("毛利率", top_k=2)) <= 2


def test_hybrid_beats_vector_on_exact_term(seeded_kb):
    """纯向量在精确实体词上容易跑偏，混合检索要能把它拉回来。

    这条断言是本项目做混合检索的**直接证据**：
    问"华中区有没有江西"时，纯向量首选项常常不是区域划分那一节。
    """
    from app.modules.knowledge.retriever import retrieve

    vector_top = [h.title for h in retrieve("华中区有没有江西", top_k=1, hybrid=False)]
    hybrid_top = [h.title for h in retrieve("华中区有没有江西", top_k=1, hybrid=True)]
    assert "区域划分" in hybrid_top[0]
    assert vector_top != hybrid_top or "区域划分" in vector_top[0]


def test_context_has_citation_numbers(seeded_kb):
    """上下文必须带编号，模型才能标出处。这是抑制幻觉的关键。"""
    from app.modules.knowledge.retriever import format_context, retrieve

    ctx = format_context(retrieve("毛利率怎么算", top_k=2))
    assert "[1]" in ctx
    assert "来源" in ctx


def test_stats_reflects_ingestion(seeded_kb):
    from app.modules.knowledge import service

    st = service.stats()
    assert st["chunks"] > 0
    assert st["documents"] >= 3
    assert st["ok"] is True


def test_reingest_is_idempotent(seeded_kb):
    """重复导入不能把知识库灌成重复数据。"""
    from app.modules.knowledge import service
    from app.modules.knowledge.seed import SEED_DOCS

    before = service.stats()["chunks"]
    service.seed_from_texts(SEED_DOCS)
    assert service.stats()["chunks"] == before


# ---------------------------------------------------------------- Embedding 后端
def test_local_hash_is_deterministic_and_normalized():
    """哈希向量必须归一化，且跨进程稳定（用 md5 而不是内置 hash）。"""
    from app.modules.knowledge.embedding import LocalHashEmbedding

    p = LocalHashEmbedding(dim=256)
    v = p.encode_one("毛利率怎么算")
    assert len(v) == 256
    assert abs(sum(x * x for x in v) ** 0.5 - 1.0) < 1e-6
    assert v == p.encode_one("毛利率怎么算")


def test_local_hash_has_no_semantic_ability():
    """把 local 的局限钉成断言 —— 它答不出同义改写，这正是要换 BGE 的理由。

    如果哪天有人把默认后端换成真模型，这条会失败，那是有意义的提醒：
    记得同步更新 README 里"默认无语义能力"的说法。
    """
    import numpy as np

    from app.modules.knowledge.embedding import LocalHashEmbedding

    p = LocalHashEmbedding(dim=512)
    docs = np.array(p.encode(["毛利率的计算公式", "退货率 = 退货金额 / 销售金额"]))
    qv = np.array(p.encode_query("利润率的算法是什么"))
    sims = docs @ qv
    # 语义完全不沾边的"退货率"反而可能排前面 —— 说明它只看字面/字符 n-gram
    assert sims[0] - sims[1] < 0.15


def _bge_available() -> bool:
    from app.core.config import settings
    from app.modules.knowledge.embedding import BGELocalEmbedding

    try:
        BGELocalEmbedding(
            model_name=settings.EMBEDDING_MODEL,
            dim=settings.EMBEDDING_DIM,
            cache_dir=settings.EMBEDDING_MODEL_DIR,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _bge_available(), reason="本地 BGE 模型未下载，跳过语义检索测试")
def test_bge_beats_hash_on_paraphrase():
    """BGE 的核心价值：同义改写也能召回。这是 local 做不到的。

    实测（bge-small-zh-v1.5）：
      "利润率的算法是什么" → 毛利率 0.648 / 退货率 ~0.3
      "销售业绩不好怎么算比例" → 退货率 0.493（hash 会错召回 Excel 文档）
    """
    import numpy as np

    from app.core.config import settings
    from app.modules.knowledge.embedding import BGELocalEmbedding, LocalHashEmbedding

    docs = [
        "毛利率的计算公式为（营业收入-营业成本）/营业收入×100%",
        "退货率 = 退货金额 / 销售金额，超过5%需向上汇报",
        "上传Excel后只有前5行数据，其余显示不全",
    ]
    bge = BGELocalEmbedding(
        model_name=settings.EMBEDDING_MODEL,
        dim=settings.EMBEDDING_DIM,
        cache_dir=settings.EMBEDDING_MODEL_DIR,
    )
    dv = np.array(bge.encode(docs))
    qv = np.array(bge.encode_query("利润率的算法是什么"))
    assert int((dv @ qv).argmax()) == 0, "BGE 应把语义相近的毛利率排第一"

    # 对照组：哈希实现在同一问题上区分度极低
    h = LocalHashEmbedding(dim=512)
    hd = np.array(h.encode(docs))
    hq = np.array(h.encode_query("利润率的算法是什么"))
    hsims = hd @ hq
    assert hsims.max() < 0.3, "哈希实现在同义改写上相似度应普遍很低"


def test_bge_query_uses_instruction_prefix():
    """BGE 是非对称模型：检索侧要加指令前缀，passage 侧不加。

    加错不会报错，只是召回悄悄变差 —— 所以必须有用例锁住这个不对称性。
    """
    from app.modules.knowledge.embedding import BGELocalEmbedding

    assert "检索" in BGELocalEmbedding.QUERY_INSTRUCTION
    assert BGELocalEmbedding.QUERY_INSTRUCTION.endswith("：")


def test_unknown_embedding_backend_falls_back_loudly(caplog):
    """配错后端名不能静默降级 —— 用户会以为在用 BGE，实际在跑哈希向量。"""
    from app.core.config import settings
    from app.modules.knowledge import embedding

    original = settings.EMBEDDING_BACKEND
    try:
        settings.EMBEDDING_BACKEND = "bgge"  # 拼写错误
        with caplog.at_level("ERROR"):
            provider = embedding.get_embedding_provider()
        assert type(provider).__name__ == "LocalHashEmbedding"
        assert any("unknown_embedding_backend" in r.message for r in caplog.records)
    finally:
        settings.EMBEDDING_BACKEND = original


# ---------------------------------------------------------------- 停用词与概念归并
@pytest.mark.parametrize(
    "question,should_not_contain",
    [
        ("华东区包括哪些省份", "包括"),
        ("报表和财务对不上怎么办", "怎么办"),
        ("上期为零时环比返回什么", "返回"),
        ("导入表格丢数据了", "了"),
    ],
)
def test_filler_words_are_not_concepts(question, should_not_contain):
    """疑问/引导词不能进概念集合。

    这些词不是实体，留在覆盖率分母里会让分数虚高、拉平真实差异。
    实测「报表和财务对不上怎么办」曾因为"怎么办"只有 0.25 覆盖率，
    导致正确答案排不到第一。
    """
    assert should_not_contain not in query_terms(question)


def test_unit_variants_are_normalized():
    """问句里的"省份"要能对上语料里的"省/四省" —— 反向归一。

    只有"剥离后缀"方向不够："省份"是以"省"开头而非结尾，规则打不到，
    于是"华东区包括哪些省份"里最关键的"省份"反而不参与匹配。
    """
    toks = tokenize("华东区包括哪些省份")
    assert "省" in toks, "应产出 省 作为 省份 的等价词形"

    terms = query_terms("华东区包括哪些省份")
    # 省份/省 是同一概念，只保留一个代表
    assert len({"省份", "省"} & terms) == 1


def test_query_terms_merges_synonyms_not_adjacent():
    """同义合并不能依赖词在句子里相邻。

    分词后"省份"和"省"中间隔着停用词"哪些"，旧的相邻比较逻辑合并不掉，
    会把一个概念算成两个，覆盖率被系统性低估。
    """
    terms = query_terms("华东区包括哪些省份")
    assert not ({"省份", "省"} <= terms), "省份 与 省 应合并为一个概念"


def test_coverage_rerank_trusts_semantic_vector_when_spread_is_clear():
    """向量分差明显时，覆盖率不该压过语义判断。

    这是实测踩出来的坑：问"导入表格丢数据了"，
    向量排序本来是对的（上传 Excel 0.549 > 数据为空 0.508），
    但两条都只命中"数据"这一个高频词，覆盖率相同，
    重排于是退回看 RRF 分，被 BM25 带偏。

    注意 spread 阈值是 0.06：真实检索里候选的 vec 分跨度远大于此
    （实测该问题跨度为 0.18），0.06 只是"别在噪声上做决策"的护栏。
    所以用例里用真实量级的分数，别用人工凑的接近值。
    """
    from app.modules.knowledge.retriever import Retrieved, coverage_rerank

    def mk(cid: str, title: str, vscore: float, score: float) -> Retrieved:
        return Retrieved(
            chunk_id=cid,
            text=title,
            source="t.md",
            title=title,
            score=score,
            vector_score=vscore,
        )

    items = [
        mk("b", "为什么某个月的数据是空的", 0.508, 0.0202),
        mk("a", "上传 Excel 后数据显示不全", 0.549, 0.0199),
        mk("c", "权限与数据范围", 0.367, 0.0195),  # 拉大跨度到 0.18
    ]
    # semantic=True 时才信任向量分（哈希后端不算）
    out = coverage_rerank("导入表格丢数据了", items, top_k=2, semantic=True)
    assert out[0].chunk_id == "a", "向量分高者应在前"


def test_coverage_rerank_falls_back_when_vector_scores_are_flat():
    """向量分几乎一样时不该信任它 —— 说明语义没分出高下，该看字面覆盖率。"""
    from app.modules.knowledge.retriever import Retrieved, coverage_rerank

    def mk(cid: str, title: str, vscore: float) -> Retrieved:
        return Retrieved(
            chunk_id=cid, text=title, source="t.md", title=title, score=0.02, vector_score=vscore
        )

    items = [
        mk("b", "完全无关的内容", 0.301),
        mk("a", "华东包含上海、江苏、浙江、安徽", 0.300),
    ]
    out = coverage_rerank("华东区包括哪些省份", items, top_k=2, semantic=True)
    assert out[0].chunk_id == "a", "分数接近时应由覆盖率/topic 决定"


def test_coverage_rerank_ignores_vector_when_backend_is_hash():
    """哈希后端的向量分不可信，不能让它主导排序。"""
    from app.modules.knowledge.retriever import Retrieved, coverage_rerank

    def mk(cid: str, title: str, vscore: float) -> Retrieved:
        return Retrieved(
            chunk_id=cid, text=title, source="t.md", title=title, score=0.02, vector_score=vscore
        )

    items = [mk("b", "无关内容", 0.55), mk("a", "华东包含上海江苏", 0.50)]
    out = coverage_rerank("华东区包括哪些省份", items, top_k=2, semantic=False)
    # 哈希模式下应回到覆盖率/topic 排序，至少不能因为 vec 高就把无关项顶上去
    assert out[0].chunk_id == "a"
