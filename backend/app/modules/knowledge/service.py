"""知识库服务层：导入、统计、重建、检索封装。

对外只暴露四个函数，路由层保持极薄：
    ingest_file() / ingest_text()   -> 入库
    stats()                         -> 统计（给前端展示）
    reset()                         -> 清空重建
    query()                         -> 检索（给 Agent 和调试页用）
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import settings
from app.core.errors import DependencyError, ValidationError
from app.core.logging import get_logger
from app.modules.knowledge import store
from app.modules.knowledge.chunker import Chunk, chunk_file, chunk_markdown
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.retriever import Retrieved, retrieve

log = get_logger(__name__)

SUPPORTED = {".md", ".markdown", ".txt", ".csv", ".xlsx", ".xls"}

# backend/ 目录。相对路径配置统一挂到它下面，
# 否则从不同 cwd 启动（uvicorn / pytest / 脚本）会指向不同位置 ——
# 表现是"明明刚写进去，再读就说不存在"，极难排查。
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _abs(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


KB_DIR = _abs(settings.KB_SOURCE_DIR)


def _save_upload(filename: str, data: bytes) -> Path:
    """把上传文件落盘，后续重建索引时可直接复用，不用再传一遍。"""
    KB_DIR.mkdir(parents=True, exist_ok=True)
    # 只取文件名，防目录穿越（../ 之类）
    safe = Path(filename).name
    if Path(safe).suffix.lower() not in SUPPORTED:
        raise ValidationError(
            f"不支持的文件类型：{Path(safe).suffix}，仅支持 "
            + "、".join(sorted(SUPPORTED))
        )
    path = KB_DIR / safe
    path.write_bytes(data)
    return path


def _ingest_chunks(chunks: list[Chunk]) -> dict:
    if not chunks:
        return {"chunks": 0, "documents": 0, "collection": "", "embedding": ""}

    emb = get_embedding_provider()
    log.info("kb_ingest begin chunks=%d embedding=%s", len(chunks), emb.name)

    # 批量编码。一次几百条对 API 后端会超长，按 32 条一批。
    vectors: list[list[float]] = []
    batch = 32
    for i in range(0, len(chunks), batch):
        vectors.extend(emb.encode([c.text for c in chunks[i : i + batch]]))

    store.upsert_chunks(
        [c.to_row() for c in chunks],
        vectors,
        dim=emb.dim,
        embedding_name=emb.name,
    )
    return {
        "chunks": len(chunks),
        "documents": len({c.source for c in chunks}),
        "collection": store._collection_name(settings.MILVUS_COLLECTION, emb.name),
        "embedding": emb.name,
    }


def ingest_text(text: str, *, source: str = "manual.md", title: str = "") -> dict:
    """直接吃文本，方便调试和前端"粘贴一段口径说明"。"""
    if not (text or "").strip():
        raise ValidationError("文本内容为空")
    return _ingest_chunks(chunk_markdown(text, source=source, title=title))


def ingest_file(filename: str, data: bytes) -> dict:
    """上传并入库单个文件。"""
    path = _save_upload(filename, data)
    try:
        chunks = chunk_file(path)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    result = _ingest_chunks(chunks)
    result["source"] = str(path)
    return result


def _is_skipped(p: Path) -> bool:
    """下划线开头的文件是目录说明/占位，不参与索引。

    约定成俗：`_readme.md`、`_draft.md` 这类是给自己看的，
    混进知识库会污染检索（比如问"知识库怎么用"时会命中一段讲配置的说明，
    而不是业务答案）。隐藏文件（.gitkeep）同理。
    """
    return p.name.startswith(("_", "."))


def ingest_dir(dir_path: str | Path) -> dict:
    """批量导入一个目录（首次搭知识库时用，也方便写脚本灌数据）。"""
    root = _abs(str(dir_path))
    if not root.exists():
        raise ValidationError(f"目录不存在：{root}")

    all_chunks: list[Chunk] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED and not _is_skipped(p):
            try:
                all_chunks.extend(chunk_file(p))
            except Exception as exc:  # noqa: BLE001
                log.warning("kb_skip_file file=%s err=%s", p, exc)
    return _ingest_chunks(all_chunks)


def seed_from_texts(docs: dict[str, str]) -> dict:
    """直接把 {文件名: 内容} 一次性入库，不落盘。

    提供这个入口有两个实际理由：
    1. 内置示例知识库可以写在代码里，clone 下来就能用，不依赖额外的数据目录；
    2. 测试要反复重建索引时，不必跟文件系统打交道（本机环境还会回收目录），
       走内存路径最稳。
    """
    all_chunks: list[Chunk] = []
    for name, text in docs.items():
        all_chunks.extend(chunk_markdown(text, source=name))
    result = _ingest_chunks(all_chunks)
    result["documents"] = len(docs)
    return result


def stats() -> dict:
    """统计信息。注意：这里要容忍 Milvus 没起 —— 前端首页不该因此白屏。"""
    emb = get_embedding_provider()
    info = {
        "enabled": settings.RAG_ENABLED,
        "embedding": emb.name,
        "dim": emb.dim,
        "hybrid": settings.RAG_HYBRID,
        "mode": "lite" if store.is_lite() else "standalone",
        "chunks": 0,
        "documents": 0,
        "ok": True,
        "error": "",
    }
    try:
        info["chunks"] = store.count(emb.name)
        rows = store.load_all(emb.name, dim=emb.dim)
        info["documents"] = len({r.get("source", "") for r in rows if r.get("source")})
    except Exception as exc:  # noqa: BLE001
        info["ok"] = False
        info["error"] = str(exc)[:200]
    return info


def list_documents() -> list[dict]:
    emb = get_embedding_provider()
    try:
        rows = store.load_all(emb.name, dim=emb.dim)
    except Exception:  # noqa: BLE001
        return []
    agg: dict[str, dict] = {}
    for r in rows:
        src = r.get("source", "?")
        item = agg.setdefault(src, {"source": src, "title": r.get("title", ""), "chunks": 0})
        item["chunks"] += 1
    return sorted(agg.values(), key=lambda x: x["source"])


def reset() -> dict:
    """清空知识库（换 embedding 模型后必须做，否则新旧向量不可比）。"""
    emb = get_embedding_provider()
    store.drop(emb.name)
    if KB_DIR.exists():
        shutil.rmtree(KB_DIR, ignore_errors=True)
    return {"dropped": emb.name}


def rebuild() -> dict:
    """全量重建索引 —— 换 embedding 模型后的标准动作。

    这里踩过一个坑：最初只从 `KB_DIR` 扫盘重建，结果**内置种子文档被清空了**。
    因为种子是直接进内存入库的、从不落盘，`drop()` 之后无文件可扫，
    于是返回 `{"chunks": 0}` 看起来像个正常结果 —— 用户以为重建成功，
    实际上知识库变空，对话开始答"我不知道"。

    修复：磁盘文件 + 内置种子，两边都重灌。上传过的文件本来就在 KB_DIR 里，
    内置种子则在代码里，谁也不会漏。
    """
    from app.modules.knowledge.seed import SEED_DOCS

    emb = get_embedding_provider()
    store.drop(emb.name)

    pieces: list[Chunk] = []

    if KB_DIR.exists():
        for p in sorted(KB_DIR.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED and not _is_skipped(p):
                try:
                    pieces.extend(chunk_file(p))
                except Exception as exc:  # noqa: BLE001
                    log.warning("kb_skip_file file=%s err=%s", p, exc)

    for name, text in SEED_DOCS.items():
        pieces.extend(chunk_markdown(text, source=name))

    result = _ingest_chunks(pieces)
    result["embedding"] = emb.name
    # documents 按 source 去重算，避免同一次重建里重复计数
    result["documents"] = len({c.source for c in pieces})
    log.info(
        "kb_rebuild done chunks=%d documents=%d embedding=%s",
        result["chunks"],
        result["documents"],
        emb.name,
    )
    return result


def query(q: str, *, top_k: int | None = None, hybrid: bool | None = None) -> list[Retrieved]:
    if not settings.RAG_ENABLED:
        raise DependencyError("RAG 未启用（RAG_ENABLED=false）")
    if not (q or "").strip():
        raise ValidationError("查询内容为空")
    return retrieve(q, top_k=top_k, hybrid=hybrid)
