"""混合检索：BM25 稀疏召回 + 向量稠密召回，用 RRF 融合。

为什么要混合（这是 RAG 面试的高频问题，要有能说清的答案）：

  纯向量检索的短板 —— **专有名词、产品型号、精确数字**。embedding 是语义压缩，
  它知道"毛利率"和"利润率"相近，但记不住"华北-2024Q3"这种 token。
  业务知识库里恰恰全是这种精确标识，纯向量经常召回一堆语义相近但答非所问的片段。

  纯 BM25 的短板 —— **同义改写**。用户问"赚的钱有多少"，文档写的是"营收"，
  词不重合，BM25 直接给 0 分。

  两者互补：BM25 保精度（字面命中），向量保召回（语义相似）。

融合算法选 RRF（Reciprocal Rank Fusion）而不是加权求和，原因是：
  BM25 分数是**无上界**的（随语料变化），余弦相似度是 [-1,1]。
  直接把两者加权相加，等于让 BM25 单方面主导，权重根本无法调。
  RRF 只用**排名**不用分数：score = Σ 1/(k + rank)，天然免疫量纲问题，
  是工业界最稳的默认选择，且不需要任何调参。
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass

import jieba

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.knowledge import store
from app.modules.knowledge.embedding import get_embedding_provider

log = get_logger(__name__)

# 中文停用词（精简版，够用即可；全量停用词表对短查询反而有害）
# 注意把"有没有/是不是/能不能"这类**疑问套话**也放进来：
# 它们在几乎所有文档里都可能出现，参与匹配只会让无关文档蹭到覆盖率。
_STOPWORDS = set(
    "的 了 是 在 和 与 或 有 无 我 你 他 她 它 这 那 个 些 请问 请 帮我 一下 怎么 如何 "
    "什么 多少 哪些 哪个 为 吗 呢 啊 吧 要 会 能 可以 以及 但是 而且 对于 关于 "
    "有没有 是不是 能不能 会不会 对不对 有没有什么 "
    # 无信息量的动词/引导词：进了 BM25 只会让"包括""告诉"这类词在每篇文档里都命中，
    # 拉低覆盖率分母（"华东区包括哪些省份"曾因此只有 0.33 覆盖率，Top2 混入无关文档）。
    "包括 包含 列出 说明 介绍 解释 告诉 显示 查看 查询 统计 属于 相关 内容 具体 "
    "应该 需要 一般 通常 主要 部分 方面 情况 问题 办法 方式 时候 "
    # 疑问/指代尾巴。"怎么办""对不上怎么办"里的"怎么办"不是实体，
    # 留着会让覆盖率分母虚高（"报表和财务对不上"曾因此只有 0.25 覆盖率）。
    "怎么办 怎么弄 怎么回事 为什么 什么 如何 怎样 能否 是否 可以吗 "
    "返回 得到 获取 计算 得出 表示 意思 含义".split()
)
# 严格来说"哪些/多少"是疑问词而非停用词，去掉它们会丢掉"要枚举"的意图。
# 但把它们留在 BM25 里会让召回跑偏，所以还是过滤，改用查询改写兜住语义。
_TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")

# 领域后缀：jieba 会把"华东区"当成一个词，而语料里写的是"华东"。
# 不剥离的话，用户问"华东区有哪些省份"会因为差一个字而 BM25 全落空 ——
# 这种"差一个字就召回不到"的坑在中文 RAG 里极其常见。
# 处理方式：切词后额外产出一份去掉领域后缀的变体，两种形态都参与匹配。
_DOMAIN_SUFFIXES = ("区", "大区", "区县", "省份", "地区", "部门", "品类", "产品线", "月份")

# 量词/单位类词形映射：语料里写"四省""各省""三个大区"，问句里写"省份""哪些区域"。
# 双向归一，让两边的词形能对上。这是中文检索里"同一个概念多种说法"的典型情况。
_UNIT_VARIANTS = {
    "省份": "省",
    "大区": "区",
    "区域": "区",
    "月份": "月",
    "部门": "部门",
}

# jieba 首次分词要加载词典（约 1~2 秒），放到后台线程预热，
# 避免用户第一个提问卡在初始化上。
_warm = threading.Thread(target=lambda: jieba.lcut("预热"), daemon=True)
_warm.start()


def tokenize(text: str) -> list[str]:
    """中文分词 + 去停用词 + 领域后缀归一。

    为什么不对数字做特殊处理：BM25 里数字恰恰是高区分度词（"2000 万"），
    停用词表里也不该有数字。
    """
    tokens: list[str] = []
    for seg in _TOKEN_RE.findall((text or "").lower()):
        if seg.isascii():  # 英文/数字整段保留
            tokens.append(seg)
            continue
        # 中文段交给 jieba。不额外保单字：单字会让 BM25 的 IDF 严重失真
        # （几乎所有文档都"含有"的常用字权重被压到接近 0，而确实该命中的
        # 短词又被淹没），实测混合检索后的相关性明显变差。
        for t in jieba.lcut(seg):
            t = t.strip()
            if not t:
                continue
            tokens.append(t)
            # 剥离领域后缀，补一个语义等价的词形（华东区 -> 华东）
            for suf in _DOMAIN_SUFFIXES:
                if t.endswith(suf) and len(t) > len(suf) + 1:
                    base = t[: -len(suf)]
                    if base not in _STOPWORDS:
                        tokens.append(base)
                    break
            else:
                # 反向归一：问句里的"省份/地区"要能对上语料里的"四省/各省"。
                # 只有正向剥离不够 —— "省份"是以"省"开头而不是结尾，规则打不到，
                # 结果"华东区包括哪些省份"里最关键的"省份"反而不参与匹配。
                if t in _UNIT_VARIANTS:
                    tokens.append(_UNIT_VARIANTS[t])
            # 极短虚词在 BM25 里只会制造噪声（"不""本""算""进"），单字一律丢弃
            if len(t) == 1 and not t.isdigit():
                tokens.pop()
    return [t for t in tokens if t not in _STOPWORDS]


def query_terms(text: str) -> set[str]:
    """查询的"概念集合"，用于算覆盖率。

    关键点：**同义词要合并成一个概念**。"华东区"和"华东"是同一个实体的两种写法，
    如果各算一个词，那么碰巧同时提到这两个写法的长文档会白拿两分，
    而真正给出答案的短文档只拿一分 —— 排序直接错。

    合并规则是「前缀包含」：一个词是另一个的前缀时，留短的当代表。
    但不只比相邻词 —— 分词后中间可能隔着停用词，`省份` 和 `省` 不是相邻的，
    却仍是同一概念。所以要先按长度降序排，再做一次稳定归并。
    """
    cand = [t for t in tokenize(text) if len(t) >= 2 or t.isdigit()]

    # 长词优先处理，短的留作代表；这样"省份"和"省"都会被压成"省"
    out: set[str] = set()
    for t in sorted(set(cand), key=len, reverse=True):
        # 已有更短的前缀变体在集合里 → 当前词是重复概念，丢弃
        if any(t != s and t.startswith(s) for s in out):
            continue
        # 当前词更短 → 把已被它覆盖的长变体替换掉
        out = {s for s in out if not (s != t and s.startswith(t))}
        out.add(t)
    return out


@dataclass
class Retrieved:
    """一条检索结果，带上分数明细方便排查和展示。"""

    chunk_id: str
    text: str
    source: str
    title: str
    score: float  # 融合后的分数（越大越好）
    vector_score: float = 0.0
    bm25_score: float = 0.0
    vector_rank: int = 0
    bm25_rank: int = 0
    coverage: float = 0.0  # 查询词覆盖率，重排时用
    topic_score: float = 0.0  # 标题与查询的主题匹配度

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "title": self.title,
            "score": round(self.score, 6),
            "vector_score": round(self.vector_score, 4),
            "bm25_score": round(self.bm25_score, 4),
            "vector_rank": self.vector_rank,
            "bm25_rank": self.bm25_rank,
            "coverage": self.coverage,
            "topic_score": self.topic_score,
        }


def _bm25_search(query: str, corpus: list[dict], top_k: int) -> list[tuple[dict, float]]:
    """在内存语料上跑 BM25。返回 [(doc, score)] 降序。"""
    if not corpus:
        return []
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:  # pragma: no cover
        log.warning("rank_bm25_missing fallback=vector_only")
        return []

    tokenized = [tokenize(d.get("text", "")) for d in corpus]
    # 空文档会让 BM25 的 IDF 计算出现除零，先过滤
    valid = [(d, t) for d, t in zip(corpus, tokenized) if t]
    if not valid:
        return []

    docs, toks = zip(*valid)
    bm25 = BM25Okapi(list(toks))
    q = tokenize(query)
    if not q:
        return []
    scores = bm25.get_scores(q)

    ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
    return [(d, float(s)) for d, s in ranked[:top_k] if s > 0]


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    candidate_k: int | None = None,
    hybrid: bool | None = None,
) -> list[Retrieved]:
    """主入口：返回融合后的 top_k 片段。"""
    top_k = top_k or settings.RAG_TOP_K
    candidate_k = candidate_k or settings.RAG_CANDIDATE_K
    hybrid = settings.RAG_HYBRID if hybrid is None else hybrid

    emb = get_embedding_provider()
    vec_hits: list[dict] = []
    try:
        vec_hits = store.search(
            # 用 encode_query 而不是 encode_one：BGE 这类不对称模型在检索侧要加指令前缀，
            # 加错不报错、只是召回悄悄变差，所以统一收口到一个方法上。
            emb.encode_query(query),
            top_k=candidate_k,
            dim=emb.dim,
            embedding_name=emb.name,
        )
    except Exception as exc:  # noqa: BLE001
        # 向量库挂了不该让整个问答崩掉，降级到纯 BM25 仍然能答
        log.warning("vector_search_failed err=%s, fallback=bm25", exc)

    if not hybrid:
        results = [
            Retrieved(
                chunk_id=h["chunk_id"],
                text=h["text"],
                source=h["source"],
                title=h["title"],
                score=h["score"],
                vector_score=h["score"],
                vector_rank=i + 1,
            )
            for i, h in enumerate(vec_hits)
        ]
        return results[:top_k]

    bm25_hits = _bm25_search(query, store.load_all(emb.name, dim=emb.dim), candidate_k)
    if not bm25_hits and not vec_hits:
        return []
    if not bm25_hits:  # BM25 没命中（比如查询全是停用词）就直接用向量结果
        return [
            Retrieved(
                chunk_id=h["chunk_id"], text=h["text"], source=h["source"],
                title=h["title"], score=h["score"], vector_score=h["score"],
                vector_rank=i + 1,
            )
            for i, h in enumerate(vec_hits[:top_k])
        ]

    # ---- RRF 融合 ----
    k = settings.RAG_RRF_K
    merged: dict[str, Retrieved] = {}

    for rank, h in enumerate(bm25_hits, start=1):
        cid = str(h[0]["chunk_id"])
        merged[cid] = Retrieved(
            chunk_id=cid,
            text=h[0].get("text", ""),
            source=h[0].get("source", ""),
            title=h[0].get("title", ""),
            score=1.0 / (k + rank),
            bm25_score=h[1],
            bm25_rank=rank,
        )

    for rank, h in enumerate(vec_hits, start=1):
        cid = str(h["chunk_id"])
        if cid in merged:
            merged[cid].score += 1.0 / (k + rank)
            merged[cid].vector_score = h["score"]
            merged[cid].vector_rank = rank
        else:
            merged[cid] = Retrieved(
                chunk_id=cid,
                text=h["text"],
                source=h["source"],
                title=h["title"],
                score=1.0 / (k + rank),
                vector_score=h["score"],
                vector_rank=rank,
            )

    out = sorted(merged.values(), key=lambda r: r.score, reverse=True)
    # 一路命中、另一路没命中很正常，不算错，但要能看出来
    log.info(
        "rag_retrieve q=%r vec=%d bm25=%d fused=%d",
        query[:40], len(vec_hits), len(bm25_hits), len(out),
    )
    # 融合排序只看名次，再用覆盖率重排一次，压掉"长片段靠运气上榜"
    return coverage_rerank(query, out, top_k=top_k, semantic=not emb.name.startswith("local-hash"))


def coverage_rerank(
    query: str, items: list[Retrieved], *, top_k: int, semantic: bool | None = None
) -> list[Retrieved]:
    """按"查询词覆盖率"重排 —— 纯 RRF 之后的补丁。

    为什么需要它：RRF 只看排名不看内容。一个 1500 字的大章节只要碰巧出现在
    BM25 榜单第 2 名，就能拿到比精准命中短片段更高的融合分。
    实测表现为"问华东区包含哪些省份，排第一的却是一段讲 AI 的无关内容"。

    修法分两层：
      1. **覆盖率**：这条片段命中了查询里多少个不同的概念；
      2. **答案密度**：命中词的密度，以及是否出现了"枚举/定义"型信号。

    第 2 层解决的是一个很实际的问题。问"华东区包括哪些省份"时，
    真正给答案的文档写的是"华东包含上海、江苏、浙江、安徽"，
    而另一篇只是举例提到"说华东区而实际区域名是华东"。
    两者都命中了"华东"这个词，覆盖率一样，但前者显然才是答案。
    区分它们靠的是：前者在密集列举地名，后者只是顺带举例。

    注意这是启发式而非学习排序：不引入模型依赖，可解释、可测试。
    真要再进一步，应该上 BGE-reranker 做 cross-encoder 精排。
    """
    q_terms = query_terms(query)
    if not q_terms:
        return items[:top_k]

    # 每个查询概念的"全库稀有度"，用来给覆盖率加权。
    #
    # 这里踩过一个真实的坑：问"导入表格丢数据了"，向量检索 Top1 明明是对的
    # （「上传 Excel 后数据显示不全」0.549，明显领先），但重排后掉到第 2 名。
    # 原因是查询被切成 {导入, 表格, 数据}，而库里"数据"这个词几乎每篇都有 ——
    # 命中一个满大街都是的词，和命中一个独有实体，在朴素覆盖率里权重一样。
    # 修法是把覆盖率改成按 IDF 加权：越稀有的概念，命中它越算数。
    all_terms: list[set[str]] = [
        query_terms(f"{it.title} {it.text}") for it in items
    ]
    n_docs = len(all_terms) or 1
    idf: dict[str, float] = {}
    for term in q_terms:
        df = sum(1 for toks in all_terms if term in toks)
        # 0.1 平滑：避免"全库都没有"时除零，也压住极端高频词
        idf[term] = math.log((n_docs + 1) / (df + 0.5)) + 0.1
    total_weight = sum(idf.values()) or 1.0

    # 问句里出现"哪些/多少/包括/包含"等词，说明用户要的是枚举或定义，
    # 此时"列举密度"才是强信号；纯事实型提问不该套用这个加权。
    wants_enum = any(w in query for w in ("哪些", "哪个", "多少", "包括", "包含", "有什么"))

    def enum_density(text: str) -> float:
        """枚举密度：连续出现多个实体词的片段更可能是答案。

        用"不同实词数 / 总词数"近似，并按片段长度做归一 ——
        长文限制天然有更多词，不归一的话会系统性偏向长片段。
        """
        toks = query_terms(text)
        if not toks:
            return 0.0
        return len(toks) / (len(toks) + 12)  # 饱和函数，避免极短片段直接封顶

    def topic_score(it: Retrieved) -> float:
        """主题匹配度：查询的核心实体是否出现**开头的位置**。

        为什么用"开头"而不是全篇词频：中文文档是总分结构，小节开头那句
        往往就是定义或结论 —— "华东包含上海、江苏、浙江、安徽"就落在段首。
        而顺带举例的文档，目标词会散落在中后段。这个位置信号比计数更稳，
        也不依赖标点排版（本机实测过用逗号数枚举，换个排版就失效了）。

        标题太抽象时（如"区域划分"），光匹配标题会得 0 分，所以取
        「标题匹配」与「前段命中」两者最大值。
        """
        title_terms = query_terms(it.title)
        from_title = len(q_terms & title_terms) / len(q_terms) if title_terms else 0.0

        # 只看片段前 80 个字符：够覆盖"标题+首句"，又不至于把长片段全算进来
        head_terms = query_terms(it.text[:80])
        from_head = len(q_terms & head_terms) / len(q_terms) if head_terms else 0.0

        return max(from_title, from_head)

    doc_cache: dict[str, tuple[set[str], float]] = {}
    for it in items:
        if it.chunk_id not in doc_cache:
            full = f"{it.title} {it.text}"
            doc_cache[it.chunk_id] = (query_terms(full), enum_density(full))

        dterms, density = doc_cache[it.chunk_id]
        # IDF 加权覆盖率：命中稀有概念比命中高频词更有说服力
        hit_weight = sum(idf[t] for t in (q_terms & dterms))
        cov = hit_weight / total_weight
        topic = topic_score(it)
        it.coverage = round(cov, 4)
        it.topic_score = round(topic, 4)
        it.score *= 0.5 + cov
        if wants_enum:
            # 系数控制在 0.5~1.0，只做微调，不喧宾夺主
            it.score *= 0.5 + 0.5 * density

    # 排序键：主题匹配 > 覆盖率 > 融合分。
    #
    # 但覆盖率不能无条件压过向量分 —— 这是实测踩出来的：
    # 问"导入表格丢数据了"，向量的判断是对的（上传 Excel 0.549 > 数据为空 0.508），
    # 可两边字面都只命中"数据"这一个高频词，覆盖率完全相同，
    # 于是重排退化成看 RRF 分，而 RRF 里 BM25 把"数据为空"排得更靠前（都含"数据"）。
    #
    # 结论：**覆盖率只在向量分接近时才该起作用**。向量分差距明显（>0.06）时，
    # 说明语义已经分出了高下，字面覆盖率这时候只会添乱。
    #
    # 但这条规则只对**真语义模型**成立。local 哈希后端的向量纯靠字符重叠，
    # 分数差异不代表语义质量（几乎每对文本相似度都在 0.1 量级乱跳），
    # 让它主导排序反而更差。所以按 backend 名判断是否信任向量分。
    emb = get_embedding_provider()
    if semantic is None:
        semantic = not emb.name.startswith("local-hash")
    vec_spread = max((it.vector_score for it in items), default=0.0) - min(
        (it.vector_score for it in items), default=0.0
    )
    trust_vector = semantic and vec_spread > 0.06

    if trust_vector:
        return sorted(
            items,
            key=lambda r: (r.vector_score, r.coverage, r.score),
            reverse=True,
        )[:top_k]

    return sorted(
        items,
        key=lambda r: (
            r.topic_score,
            r.coverage,
            r.score,
            -len(doc_cache[r.chunk_id][0]),
        ),
        reverse=True,
    )[:top_k]


def format_context(items: list[Retrieved], *, max_chars: int = 2400) -> str:
    """拼成给大模型的上下文块。

    带来源编号，让模型能标注出处 —— 这是抑制 RAG 幻觉最有效的手段之一：
    强制它"只说有出处的话"，比事后加一堆"不要编造"的指令管用。
    """
    parts: list[str] = []
    used = 0
    for i, it in enumerate(items, start=1):
        block = f"[{i}] 来源：{it.title or it.source}\n{it.text}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)
