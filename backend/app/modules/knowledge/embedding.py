"""Embedding 提供方。

为什么做成可插拔：embedding 模型是 RAG 里最容易被换掉的一环 ——
今天用本地小模型跑通，明天换 BGE 提效果，后天接云端接口省机器。
把它抽成一个接口，上层（切分、入库、检索）就完全不用动。

本项目实测：DeepSeek 只提供对话模型（deepseek-flash / deepseek-v4-pro），
**没有 embedding 接口**（调 /embeddings 返回 404）。所以默认走本地实现。
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings
from app.core.errors import DependencyError
from app.core.logging import get_logger

log = get_logger(__name__)


class EmbeddingProvider(ABC):
    """把文本转成定长向量。"""

    #: 向量维度。Milvus 建 collection 时依赖它，必须固定。
    dim: int
    #: 写入 Milvus 的模型标识，用于区分不同 embedding 的向量，避免混用不可比。
    name: str

    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]: ...

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]

    def encode_query(self, text: str) -> list[float]:
        """检索侧的编码。默认与 passage 侧同构，BGE 这类不对称模型会覆写它。"""
        return self.encode_one(text)


class LocalHashEmbedding(EmbeddingProvider):
    """本地确定性向量化：字符 n-gram 哈希 + L2 归一化。

    定位很清楚 —— **不是**为了追求检索质量，而是为了让整条 RAG 链路
    （切分 → 入库 → 检索 → 重排 → 生成）在**没有任何外部依赖**的情况下能跑通、能测试。

    用 n-gram 而不是纯词袋：中文没有空格，按字滑窗切 n-gram 才能捕捉
    "毛利率""环比"这类复合词的部分匹配，比单字好得多。

    局限（写出来是为了面试时能主动承认）：它没有任何语义理解能力，
    "营收"和"销售额"在它眼里是两个无关的词，同义改写会失效。
    要真正的语义检索，换成 BGE 或云端 embedding 接口即可。
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"local-hash-{dim}"

    @staticmethod
    def _ngrams(text: str, n: int = 2) -> list[str]:
        # 只保留中英文与数字，丢掉标点，避免噪声进入向量
        clean = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
        if len(clean) < n:
            return [clean] if clean else []
        return [clean[i : i + n] for i in range(len(clean) - n + 1)]

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            grams = self._ngrams(text) or [text or ""]
            for g in grams:
                # 用 md5 而不是内置 hash：内置 hash 每次进程启动盐不同，会导致
                # 重启后向量全变、历史数据检索不到 —— 这是很隐蔽的一致性 bug。
                h = int.from_bytes(hashlib.md5(g.encode("utf-8")).digest()[:8], "big")
                idx = h % self.dim
                # 带符号投影，减少哈希碰撞带来的系统性偏移
                sign = 1.0 if (h >> 63) & 1 == 0 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class BGELocalEmbedding(EmbeddingProvider):
    """本地 BGE 小模型（sentence-transformers）。

    BGE 中文小模型（bge-small-zh-v1.5，512 维）只有 24M 参数，CPU 上单条编码几十毫秒，
    够本地知识库用，且**真的具备语义能力**："营收"和"销售额"能对上。

    两个容易踩的点：
    1. **必须加 query 指令前缀**。BGE 的语义不对称设计：检索时给 query 加
       `为这个句子生成表示以用于检索相关文章：` 效果明显更好，而 passage 不加。
       用错（或都加/都不加）不会报错，只是召回质量悄悄变差 —— 属于"降级不报警"那类问题。
    2. **必须 L2 归一化**。Milvus 用 COSINE 度量时理论上会自动归一，但显式归一
       能让"点积"和"余弦"结果一致，换度量方式不翻车。`normalize_embeddings=True` 干这个。
    """

    #: BGE 官方推荐的检索指令前缀（中文版）
    QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

    @staticmethod
    def _resolve_local(cache_dir: str, model_name: str) -> str | None:
        """在 cache_dir 里找已下载的模型目录。

        ModelScope 的落盘结构和 HF 不同（`models/BAAI--bge-small-zh-v1.5/snapshots/master`），
        而 HF 用 `models/models--BAAI--bge-small-zh-v1.5/snapshots/<hash>`。
        两种都扫一遍，找到就用本地路径 —— 避免每次启动都去联网校验。
        """
        if not cache_dir:
            return None
        root = Path(cache_dir)
        if not root.is_dir():
            return None

        # 配置也可能直接指向一个本地模型目录
        direct = Path(model_name)
        if direct.is_dir():
            return str(direct)

        safe = model_name.replace("/", "--")
        candidates = [
            root / "models" / safe / "snapshots" / "master",  # ModelScope
            root / "models" / f"models--{safe}",               # HF
            root / safe,                                        # 手工解压
        ]
        for c in candidates:
            if not c.is_dir():
                continue
            # HF 结构下面还有一层 hash 目录
            if (c / "config.json").is_file():
                return str(c)
            for sub in sorted(p for p in c.iterdir() if p.is_dir()):
                if (sub / "config.json").is_file():
                    return str(sub)
        return None

    def __init__(self, *, model_name: str, dim: int, cache_dir: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底提示
            raise DependencyError(
                "EMBEDDING_BACKEND=bge 需要 sentence-transformers + torch。"
                "安装：pip install sentence-transformers torch"
            ) from exc

        self.dim = dim
        self.name = f"bge-{model_name}"

        # 优先用本地已下载的目录；找不到才按仓库名联网拉
        target = self._resolve_local(cache_dir, model_name) or model_name
        try:
            self._model = SentenceTransformer(target, cache_folder=cache_dir or None)
        except Exception as exc:  # noqa: BLE001
            raise DependencyError(
                f"BGE 模型加载失败（{target}）：{exc}。"
                "国内网络建议先设 HF_ENDPOINT=https://hf-mirror.com，"
                "或用 ModelScope 下载到 EMBEDDING_MODEL_DIR 指向的目录。"
            ) from exc

        # 实际维度以模型为准，配错直接报错而不是等到写 Milvus 才炸。
        # sentence-transformers 6.x 把方法从 get_sentence_embedding_dimension 更名为
        # get_embedding_dimension，两个都试一遍以兼容新旧版本。
        actual = None
        for getter in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            fn = getattr(self._model, getter, None)
            if callable(fn):
                actual = fn()
                break
        if actual and actual != dim:
            raise DependencyError(
                f"BGE 模型实际维度是 {actual}，但配置 EMBEDDING_DIM={dim}。"
                f"请把 EMBEDDING_DIM 改成 {actual}，或换用对应维度的模型。"
            )

    def encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vecs]

    def encode_query(self, text: str) -> list[float]:
        """检索侧专用：带指令前缀，与 passage 侧不对称。"""
        return self.encode([self.QUERY_INSTRUCTION + text])[0]


class OpenAICompatEmbedding(EmbeddingProvider):
    """任何 OpenAI 兼容的 /embeddings 接口。

    除了云端服务，也可以指向本地 BGE 服务（如 text-embeddings-inference、
    Xinference、LM Studio 等），它们通常都暴露 OpenAI 兼容协议。
    这样"本地 BGE"和"云端 API"共用一份实现，只改 base_url / model。
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int) -> None:
        if not api_key:
            raise DependencyError("embedding 接口需要 API Key，请在 .env 中配置 EMBEDDING_API_KEY")
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)
        self._model = model
        self.dim = dim
        self.name = f"api-{model}"

    def encode(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self._client.embeddings.create(model=self._model, input=texts)
        except Exception as exc:  # noqa: BLE001
            raise DependencyError(f"Embedding 服务调用失败：{exc}") from exc

        vectors = [d.embedding for d in resp.data]
        for v in vectors:
            if len(v) != self.dim:
                raise DependencyError(
                    f"Embedding 维度不一致：配置为 {self.dim}，实际返回 {len(v)}。"
                    "改模型后必须重建知识库，否则新旧向量不可比。"
                )
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    """按配置构造。默认本地实现，保证零外部依赖即可启动。"""
    backend = (settings.EMBEDDING_BACKEND or "local").lower()

    if backend == "api":
        return OpenAICompatEmbedding(
            base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dim=settings.EMBEDDING_DIM,
        )

    if backend == "bge":
        return BGELocalEmbedding(
            model_name=settings.EMBEDDING_MODEL,
            dim=settings.EMBEDDING_DIM,
            cache_dir=settings.EMBEDDING_MODEL_DIR,
        )

    if backend not in ("local", ""):
        # 这里之前是静默 fallback，用户明明配了 bge 却在跑哈希向量，
        # 表现为"检索质量没变化"且查不到原因。改成 error 级 + 说清后果。
        log.error(
            "unknown_embedding_backend backend=%s fallback=local "
            "(可选值：local / bge / api，请检查 .env 拼写)",
            backend,
        )
    return LocalHashEmbedding(dim=settings.EMBEDDING_DIM)
