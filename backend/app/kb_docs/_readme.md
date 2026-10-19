# kb_docs —— 知识库语料目录

放进这个目录的 `.md` / `.txt` / `.csv` / `.xlsx` 会被知识库扫描并切分入索引。

```bash
# 灌入内置示例文档（三篇业务手册，17 个片段）
curl -X POST http://127.0.0.1:8000/api/knowledge/seed -H "Authorization: Bearer <token>"

# 或扫本目录重建
curl -X POST http://127.0.0.1:8000/api/knowledge/rebuild -H "Authorization: Bearer <token>"
```

## 为什么这里默认是空的

真实语料通常是**公司内部文档或第三方课程资料**，既有体积问题也有版权/保密问题，
不适合提交到公开仓库。所以 `.gitignore` 忽略了本目录下除占位文件外的一切。

内置示例文档不依赖这里 —— 它们以 Python 字符串形式写在
`app/modules/knowledge/seed.py` 里，`git clone` 后点一下「灌入示例文档」就能用。

## 切分规则（想看效果时对着调）

- Markdown 按标题切分，标题会被拼进片段正文前缀（`【标题】`），
  这样检索命中标题也能带出上下文。
- 单片段默认 320 字、重叠 60 字（`RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP`）。
- 表格类文件按行组切分，表头会重复拼到每组前面。
