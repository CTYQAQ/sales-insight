"""知识库切分。

RAG 里最容易被低估、但对最终效果影响最大的一环。切得不好，
后面的 embedding 再贵、rerank 再强都救不回来 —— 检索到的片段本身就断了半句话。

本项目文档类型有两类，切法不同：
  1. **Markdown / 纯文本**：按标题层级切，标题作为片段上下文前缀。
  2. **表格（Excel/CSV）**：业务口径说明常以表格给出，一行一个"指标+定义+口径"，
     按行序列化成自然语言片段，比硬切字符串好得多。

中文还有一个坑：不能按空格切。这里用「句子边界优先 + 长度兜底」的贪心策略，
句子边界认中英文句号、问号、分号、换行。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

# 句子边界：中文句号/问号/感叹号/分号 + 英文 + 换行
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """一个待入库片段。"""

    chunk_id: str
    text: str
    source: str
    title: str = ""
    chunk_index: int = 0
    meta: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "title": self.title,
            "chunk_index": self.chunk_index,
        }


def _chunk_id(source: str, index: int, text: str) -> str:
    """稳定 ID：同一份文档重复导入时覆盖而不是堆重复数据。

    不能用 uuid —— 那样每次导入都新增一份，知识库越导越脏，检索全是重复片段。
    """
    raw = f"{source}::{index}::{text[:64]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:24]


def _split_sentences(text: str) -> list[str]:
    parts = [p for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or ([text] if text.strip() else [])


def split_text(
    text: str,
    *,
    size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """把长文本切成 size 左右的片段，片段间保留 overlap 个字符的上下文重叠。

    重叠是必要的：一句话被切断在边界上时，靠 overlap 让两边都带上完整的上下文，
    否则会出现"召回片段读起来不完整"的情况。
    """
    size = size or settings.RAG_CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.RAG_CHUNK_OVERLAP
    overlap = max(0, min(overlap, size // 2))  # 重叠不能超过半个片段，否则会死循环

    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    buf = ""

    for sent in sentences:
        # 单句超过片段长度（比如没有标点的长表格行），只能硬切
        while len(sent) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(sent[:size])
            sent = sent[size - overlap :]

        if len(buf) + len(sent) <= size:
            buf += sent
        else:
            if buf:
                chunks.append(buf)
            # 用上一片段末尾做重叠，保证语义连续
            buf = (buf[-overlap:] if overlap and buf else "") + sent

    if buf.strip():
        chunks.append(buf)

    # 去掉首尾空白与纯空白片段
    return [c.strip() for c in chunks if c.strip()]


def chunk_markdown(text: str, *, source: str, title: str = "") -> list[Chunk]:
    """按 Markdown 标题分节，每节内部再做长度切分。

    为什么要按标题切：业务口径文档的章节就是天然的语义边界，
    "毛利率公式"和"退货流程"混在一个片段里，embedding 会被稀释成四不像。
    """
    lines = (text or "").splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_title = title or Path(source).stem
    cur: list[str] = []

    for line in lines:
        m = _MD_HEADING.match(line)
        if m:
            if cur:
                sections.append((cur_title, cur))
            cur_title = m.group(2).strip()
            cur = []
        else:
            cur.append(line)
    if cur:
        sections.append((cur_title, cur))

    if not sections and text.strip():
        sections = [(title or Path(source).stem, [text])]

    chunks: list[Chunk] = []
    for sec_title, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        for piece in split_text(body):
            idx = len(chunks)
            # 把标题写进片段正文：检索命中后模型能看到"这段属于哪一节"，
            # 避免答非所问；同时标题词也会进入 BM25 的倒排索引。
            enriched = f"【{sec_title}】\n{piece}" if sec_title else piece
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(source, idx, enriched),
                    text=enriched,
                    source=source,
                    title=sec_title,
                    chunk_index=idx,
                )
            )
    return chunks


def chunk_table(rows: list[dict], *, source: str, title: str = "") -> list[Chunk]:
    """表格按行转自然语言片段。

    一行 = 一个业务事实。把"列名: 值"拼成一句话，embedding 和 BM25 都友好，
    也符合大模型阅读习惯。多行合成一段时按 split_text 控制长度。
    """
    title = title or Path(source).stem
    lines: list[str] = []
    for row in rows:
        pairs = [f"{k}：{v}" for k, v in row.items() if v not in (None, "")]
        if pairs:
            lines.append("；".join(pairs))

    chunks: list[Chunk] = []
    for piece in split_text("\n".join(lines)):
        idx = len(chunks)
        enriched = f"【{title}】\n{piece}" if title else piece
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(source, idx, enriched),
                text=enriched,
                source=source,
                title=title,
                chunk_index=idx,
            )
        )
    return chunks


def chunk_file(path: str | Path) -> list[Chunk]:
    """按扩展名分派。目前支持 .md / .txt / .csv（.xlsx 走 openpyxl）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")

    source = str(p)
    suffix = p.suffix.lower()

    if suffix in (".md", ".markdown", ".txt"):
        return chunk_markdown(p.read_text(encoding="utf-8", errors="ignore"), source=source)

    if suffix == ".csv":
        raw = p.read_text(encoding="utf-8-sig", errors="ignore")
        rows = list(csv.DictReader(io.StringIO(raw)))
        return chunk_table(rows, source=source)

    if suffix in (".xlsx", ".xls"):
        from openpyxl import load_workbook

        wb = load_workbook(p, read_only=True, data_only=True)
        chunks: list[Chunk] = []
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = [str(c) if c is not None else f"col{i}" for i, c in enumerate(next(it, []))]
            rows = [dict(zip(header, r)) for r in it if any(c is not None for c in r)]
            sub = chunk_table(rows, source=f"{source}#{ws.title}", title=ws.title)
            # 同一文件多个 sheet 时 chunk_index 要接续，避免 ID 冲突
            for c in sub:
                c.chunk_index += len(chunks)
                c.chunk_id = _chunk_id(source, c.chunk_index, c.text)
            chunks.extend(sub)
        wb.close()
        return chunks

    raise ValueError(f"暂不支持的文件类型：{suffix}（支持 .md/.txt/.csv/.xlsx）")
