"""Milvus 向量库封装。

一个 URI 决定两种部署形态，业务代码完全无感：
  - lite（本地开发、免 Docker，**本项目默认**）: MILVUS_CONN=./milvus.db
  - standalone（生产/演示，需 Docker）: MILVUS_CONN=http://127.0.0.1:19530

设计要点：
1. **建表幂等**：进程反复重启不能因为集合已存在就崩。
2. **索引与 load**：Milvus 是先建索引、再 load 进内存才能检索，
   少一步就会报 "collection not loaded"，这是新手最常踩的坑。
3. **向量与模型绑定**：集合名带 embedding 模型指纹。换模型不重建的话，
   新旧向量维度/空间不一致，检索结果会变成噪声 —— 且不会报错，极难排查。

踩坑记录（值得写进面试讲述）：
不要给 pymilvus 传 MILVUS_URI 这个环境变量名。3.x 里 `pymilvus/orm/connections.py`
有个模块级 `Connections()` 单例，import 时就会读 `Config.MILVUS_URI` 并对它做
URI 合法性校验，只认 `http[s]://host:port`。lite 的 `./milvus.db` 会被直接判非法，
抛 ConnectionConfigException，**连业务代码都还没跑到**。所以配置项名必须换掉。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import DependencyError
from app.core.logging import get_logger

log = get_logger(__name__)

_client: Any = None


def _collection_name(base: str, embedding_name: str) -> str:
    """把模型标识塞进集合名，天然隔离不同 embedding 的数据。"""
    safe = "".join(c if c.isalnum() else "_" for c in embedding_name)[:24]
    return f"{base}_{safe}"[:255]


def _ensure_loaded(client: Any, name: str, *, retries: int = 20, interval: float = 0.05) -> bool:
    """确保集合处于 loaded 状态，返回是否成功。

    为什么要轮询而不是调用一次 load_collection 就完事：
    **load_collection() 是异步的**，它只是提交加载任务就返回。
    紧接着 query()/search() 会撞上集合还没 load 完，报
    "is in state 'released'; call load() before search/get/query"。

    这个坑的迷惑性在于：test 环境（进程刚起、数据量小）经常恰好赶上加载完成，
    而重启后的真实服务、或数据量大一点时就必现。
    表现是 BM25 拿不到语料 → 混合检索静默退化成纯向量，且只在 error 日志里留一行。
    """
    import time

    try:
        client.load_collection(collection_name=name)
    except Exception as exc:  # noqa: BLE001
        # 已加载时再 load 会抛异常，属正常情况，不当失败处理
        log.debug("milvus_load_called name=%s msg=%s", name, exc)

    for _ in range(retries):
        try:
            state = client.get_load_state(collection_name=name)
        except Exception:  # noqa: BLE001
            return False
        # pymilvus 返回 {"state": <LoadState.Loaded>}，不同版本可能是枚举或字符串
        raw = state.get("state") if isinstance(state, dict) else state
        text = getattr(raw, "name", str(raw))
        if "Loaded" in str(text):
            return True
        time.sleep(interval)
    return False


def _check_ascii_path(uri: str) -> None:
    """lite 模式的库文件路径不能含非 ASCII 字符（中文等）。

    这是实测踩出来的硬限制，而且报错极其误导人：数据能正常 upsert、
    `get_collection_stats` 也能查到正确的行数，看起来一切正常；
    但底层的 faiss 在**写索引文件**时直接失败：

        RuntimeError: Error in __cdecl faiss::FileIOWriter::FileIOWriter
        Error: 'f' failed: could not open ...\\partitions\\_default\\indexes\\xxx.idx
        for writing: No such file or directory

    目录明明存在，FAISS 就是打不开 —— 因为它内部按窄字符处理路径，
    中文路径转不过去。最终表现为"入库成功、检索为空"，比直接报错难查十倍。

    所以这里在连接阶段就拦下来，把错误翻译成可执行的建议。
    检查整个路径而不只是文件名：父目录中文同样会触发。
    """
    if uri.startswith(("http://", "https://", "unix:")):
        return
    try:
        uri.encode("ascii")
    except UnicodeEncodeError:
        raise DependencyError(
            f"Milvus lite 的库文件路径含非 ASCII 字符，底层 faiss 无法写索引：{uri}\n"
            "解决办法（任选其一）：\n"
            "  1) 把整个项目挪到纯英文路径，例如 C:/projects/sales-insight，"
            "然后 MILVUS_CONN 用回 ./milvus.db；\n"
            "  2) 保持项目位置不变，把 MILVUS_CONN 指向一个纯英文的目录，"
            "例如 MILVUS_CONN=C:/Users/<you>/milvus-data/sales.db；\n"
            "  3) 改用 standalone 模式（MILVUS_CONN=http://127.0.0.1:19530），"
            "不受路径字符集限制。"
        ) from None


def _normalize_uri(uri: str) -> str:
    """把相对文件路径转成绝对路径。

    milvus-lite 是按进程 cwd 解析相对路径的。uvicorn 从 backend/ 启动、
    pytest 从别处启动，同一条配置会指向两个不同的库文件 —— 表现为
    "明明入库成功了，检索却是空的"，非常难查。这里统一钉死成绝对路径。

    层级注意：本文件是 backend/app/modules/knowledge/store.py，
    parents[0]=knowledge, [1]=modules, [2]=app, [3]=backend。
    写错一个下标，数据库就会建到项目根目录去，而且完全不报错。
    """
    if uri.startswith(("http://", "https://", "unix:")):
        return uri
    p = Path(uri)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[3] / p  # -> backend/
    return str(p)


def is_lite() -> bool:
    return not settings.MILVUS_CONN.startswith(("http://", "https://"))


def get_client():
    """惰性单例。首次用到才连，避免没用 RAG 的部署也被迫装 Milvus。"""
    global _client
    if _client is not None:
        return _client

    try:
        from pymilvus import MilvusClient
    except ImportError as exc:  # pragma: no cover
        raise DependencyError("未安装 pymilvus：pip install pymilvus milvus-lite") from exc

    uri = _normalize_uri(settings.MILVUS_CONN)
    _check_ascii_path(uri)
    kwargs: dict[str, Any] = {"uri": uri}
    # lite 不认 token，传了会报错
    if settings.MILVUS_TOKEN and not is_lite():
        kwargs["token"] = settings.MILVUS_TOKEN

    try:
        _client = MilvusClient(**kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if is_lite() and ("Open local milvus failed" in msg or "LOCK" in msg.upper()):
            # lite 是内嵌库，同一个文件同时只能被一个进程打开。
            # 这时候报错原文完全看不出是"另一个进程占着"，必须翻译成人话。
            raise DependencyError(
                f"打开本地 Milvus 失败（{uri}）。"
                "lite 模式同一时间只允许一个进程使用这个库文件 —— "
                "请确认没有另一个后端实例正在运行（比如 8000 端口那个），"
                "关掉它再启动，或改用 standalone 模式（MILVUS_CONN=http://127.0.0.1:19530）"
            ) from exc
        raise DependencyError(
            f"连接 Milvus 失败（uri={uri}）：{exc}。"
            "standalone 模式请确认容器已启动（docker compose up -d milvus），"
            "或改用 lite 模式：MILVUS_CONN=./milvus.db"
        ) from exc

    log.info("milvus_connected uri=%s mode=%s", uri, "lite" if is_lite() else "standalone")
    return _client


def reset_client() -> None:
    """测试或切换配置后重置连接。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
    _client = None


def ensure_collection(dim: int, embedding_name: str) -> str:
    """建集合 + 索引（幂等）。返回实际使用的集合名。"""
    client = get_client()
    name = _collection_name(settings.MILVUS_COLLECTION, embedding_name)

    if not client.has_collection(name):
        client.create_collection(
            collection_name=name,
            dimension=dim,
            metric_type="COSINE",  # 向量已 L2 归一化，COSINE 等价于点积，语义检索常用
            auto_id=False,
            primary_field_name="chunk_id",
            vector_field_name="vector",
            max_length=512,
            id_type="string",
            enable_dynamic_field=True,  # 让 text/source/title 这些元数据能随手塞进去
        )
        log.info("milvus_collection_created name=%s dim=%d", name, dim)

    # 已存在也要确保 load —— 没 load 的集合检索会直接报错
    try:
        client.load_collection(collection_name=name)
    except Exception:  # noqa: BLE001
        # 已加载时会抛，属于预期，忽略即可
        pass

    return name


def upsert_chunks(
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    *,
    dim: int,
    embedding_name: str,
) -> int:
    """写入（覆盖同 chunk_id）。返回写入条数。"""
    if not chunks:
        return 0
    if len(chunks) != len(vectors):
        raise DependencyError("切分片段数与向量数不一致，写入中止")

    client = get_client()
    name = ensure_collection(dim, embedding_name)

    rows = [
        {
            "chunk_id": c["chunk_id"],
            "vector": v,
            "text": c["text"],
            # 注意：建表时显式声明的字段必须先补进 schema，
            # 否则新字段不会自动进动态字段区，写入时被静默忽略 ——
            # 表现为"入库成功但 source 全是空"，而且不报任何错。
            "source": c.get("source", ""),
            "title": c.get("title", ""),
            "chunk_index": c.get("chunk_index", 0),
        }
        for c, v in zip(chunks, vectors)
    ]
    try:
        client.upsert(collection_name=name, data=rows)
        client.flush(collection_name=name)
    except Exception as exc:  # noqa: BLE001
        raise DependencyError(f"Milvus 写入失败：{exc}") from exc

    log.info("milvus_upsert n=%d collection=%s", len(rows), name)
    return len(rows)


def search(
    vector: list[float],
    *,
    top_k: int,
    dim: int,
    embedding_name: str,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    """向量检索。返回 [{chunk_id, text, source, title, score}]，score 越大越相似。"""
    client = get_client()
    name = _collection_name(settings.MILVUS_COLLECTION, embedding_name)
    if not client.has_collection(name):
        return []  # 知识库还没建，别炸，交给上层走降级

    ensure_collection(dim, embedding_name)

    # 与 load_all 同理：集合可能是 released，search 前必须等 load 完成
    if not _ensure_loaded(client, name):
        log.error("milvus_collection_not_loaded collection=%s —— 向量检索不可用", name)
        return []

    kwargs: dict[str, Any] = {
        "collection_name": name,
        "data": [vector],
        "limit": top_k,
        "output_fields": ["text", "source", "title", "chunk_index"],
    }
    # 注意：filter 传空串是合法的（等价不过滤），但传 None 会报错，
    # 所以只在真有过滤条件时才加这个参数。
    if expr:
        kwargs["filter"] = expr
    else:
        kwargs["search_params"] = {"metric_type": "COSINE"}

    try:
        hits = client.search(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise DependencyError(f"Milvus 检索失败：{exc}") from exc

    out: list[dict[str, Any]] = []
    # search 返回的是二维结构：外层=每个 query 向量的结果，内层=top_k 命中
    for group in hits or []:
        for hit in group:
            entity = hit.get("entity", {}) or {}
            out.append(
                {
                    "chunk_id": hit.get("chunk_id") or hit.get("id"),
                    "score": float(hit.get("distance", 0.0)),
                    "text": entity.get("text", ""),
                    "source": entity.get("source", ""),
                    "title": entity.get("title", ""),
                }
            )
    return out


def load_all(embedding_name: str, *, limit: int = 2000, dim: int) -> list[dict[str, Any]]:
    """把整个集合捞出来 —— 给 BM25 用。

    为什么需要这个：BM25 是词频统计，必须持有全量文档才能算 IDF。
    数据量大了这套做法会撑爆内存，生产上应该换成独立的倒排索引
    （Elasticsearch / Milvus 2.5+ 的 BM25 Function）。这里数据量在千级，
    直接全量拉取足够，而且少引入一个中间件。

    踩坑：query() 要求集合处于 loaded 状态，release 之后直接报
    "is in state 'released'; call load() before search/get/query"。
    之前这里 except 后只打一条 warning 就返回空列表，结果是
    **BM25 静默退化成空**，混合检索悄悄变成纯向量检索，排序质量下降
    但完全不报错 —— 这类"降级不报警"是最难查的一类问题。
    现在改成先确保 load，失败再降级，日志用 error 级别。
    """
    client = get_client()
    name = _collection_name(settings.MILVUS_COLLECTION, embedding_name)
    if not client.has_collection(name):
        return []

    # 必须先确保 load 完成：lite/standalone 重启后集合可能是 released，
    # 而 load_collection() 是异步的，不等它就会 query 到 "released" 错误。
    if not _ensure_loaded(client, name):
        log.error(
            "milvus_collection_not_loaded collection=%s —— BM25 将无可用语料（混合检索退化）",
            name,
        )
        return []

    try:
        rows = client.query(
            collection_name=name,
            filter='chunk_id != ""',
            output_fields=["chunk_id", "text", "source", "title"],
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("milvus_query_all_failed collection=%s err=%s —— BM25 将无可用语料", name, exc)
        return []
    return [dict(r) for r in rows]


def count(embedding_name: str) -> int:  # noqa: D401
    client = get_client()
    name = _collection_name(settings.MILVUS_COLLECTION, embedding_name)
    if not client.has_collection(name):
        return 0
    try:
        return int(client.get_collection_stats(collection_name=name).get("row_count", 0))
    except Exception:  # noqa: BLE001
        return 0


def drop(embedding_name: str) -> None:
    client = get_client()
    name = _collection_name(settings.MILVUS_COLLECTION, embedding_name)
    if client.has_collection(name):
        client.drop_collection(collection_name=name)
        log.warning("milvus_collection_dropped name=%s", name)
