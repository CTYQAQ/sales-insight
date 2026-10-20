import { useEffect, useState } from 'react'
import {
  Alert, App as AntApp, Button, Card, Empty, Input, List, Popconfirm,
  Space, Spin, Switch, Table, Tag, Typography, Upload,
} from 'antd'
import {
  CloudUploadOutlined, ReloadOutlined,
  SearchOutlined, SyncOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import {
  kbApi, type KnowledgeDoc, type KnowledgeStats, type RetrievedChunk,
} from '../api/api'
import { errMsg } from '../api/client'

export default function Knowledge() {
  const { message } = AntApp.useApp()
  const [stats, setStats] = useState<KnowledgeStats | null>(null)
  const [docs, setDocs] = useState<KnowledgeDoc[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  // 检索调试
  const [probe, setProbe] = useState('')
  const [hybrid, setHybrid] = useState(true)
  const [hits, setHits] = useState<RetrievedChunk[] | null>(null)
  const [probing, setProbing] = useState(false)

  const refresh = async () => {
    setLoading(true)
    try {
      const [s, d] = await Promise.all([kbApi.stats(), kbApi.documents()])
      setStats(s)
      setDocs(d)
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void refresh() }, [])

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true)
    try {
      await fn()
      message.success(okMsg)
      await refresh()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const doSearch = async () => {
    if (!probe.trim()) return
    setProbing(true)
    try {
      setHits(await kbApi.search(probe, 5, hybrid))
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setProbing(false)
    }
  }

  return (
    <div>
      <h1 className="page-title">知识库</h1>
      <p className="page-desc">
        业务口径、流程、排查手册这类"文档型知识"入库后，对话助手才能回答
        "毛利率怎么算"这类问题 —— 数据表里没有这些规则
      </p>

      {stats && !stats.ok && (
        <Alert
          type="error" showIcon style={{ marginBottom: 16 }}
          message="向量库不可用"
          description={stats.error}
        />
      )}

      {/* KPI 条：一个容器分四格，替代四张一样的卡片 */}
      <div className="kpi-strip" style={{ marginBottom: 16 }}>
        {[
          { label: '已入库片段', value: (stats?.chunks ?? 0).toLocaleString() },
          { label: '文档数', value: (stats?.documents ?? 0).toLocaleString() },
          { label: '向量维度', value: (stats?.dim ?? 0).toLocaleString() },
          { label: '检索模式', value: stats?.hybrid ? 'BM25 + 向量' : '纯向量', text: true },
        ].map((c) => (
          <div className="kpi-cell" key={c.label}>
            <div className="kpi-label">{c.label}</div>
            <div className={`kpi-value${'text' in c && c.text ? ' text' : ''}`}>{loading ? '—' : c.value}</div>
          </div>
        ))}
      </div>

      <Card
        title="知识库管理"
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading}>刷新</Button>
          </Space>
        }
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Button
            type="primary" icon={<ThunderboltOutlined />} loading={busy}
            onClick={() => void run(kbApi.seed, '已灌入内置示例知识库')}
          >
            灌入示例知识库
          </Button>

          <Upload
            accept=".md,.txt,.csv,.xlsx,.xls"
            showUploadList={false}
            beforeUpload={(file) => {
              void run(() => kbApi.upload(file), `已上传并入库：${file.name}`)
              return false
            }}
          >
            <Button icon={<CloudUploadOutlined />} loading={busy}>上传文档</Button>
          </Upload>

          <Button
            icon={<SyncOutlined />} loading={busy}
            onClick={() => void run(kbApi.rebuild, '索引已全量重建')}
          >
            重建索引
          </Button>

          <Popconfirm
            title="确定清空知识库？"
            description="会删除所有已入库片段与已上传文件，需要重新灌入。"
            okText="清空" cancelText="取消" okButtonProps={{ danger: true }}
            onConfirm={() => void run(kbApi.reset, '知识库已清空')}
          >
            <Button danger loading={busy}>清空</Button>
          </Popconfirm>
        </Space>

        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message={
            <span style={{ fontSize: 12 }}>
              换 Embedding 模型后必须点「重建索引」：集合名里带了模型指纹，换模型等于换了一个新库，
              旧向量与新向量维度/空间不一致，混用会让检索结果变成噪声且不报错。
            </span>
          }
        />

        <Table
          size="small"
          rowKey="source"
          loading={loading}
          dataSource={docs}
          pagination={false}
          locale={{ emptyText: <Empty description="知识库为空，先点上面的「灌入示例知识库」" /> }}
          columns={[
            { title: '来源文件', dataIndex: 'source', ellipsis: true },
            { title: '首节标题', dataIndex: 'title', ellipsis: true },
            {
              title: '片段数', dataIndex: 'chunks', width: 100, align: 'right' as const,
              render: (v: number) => <span className="num">{v.toLocaleString()}</span>,
            },
          ]}
        />

        {stats && (
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 12 }}>
            当前 Embedding：{stats.embedding} · 存储模式：{stats.mode === 'lite' ? 'lite（本地文件，免 Docker）' : 'standalone'}
          </Typography.Text>
        )}
      </Card>

      <Card
        title="检索调试"
        extra={
          <Space size={6}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>混合检索</Typography.Text>
            <Switch size="small" checked={hybrid} onChange={setHybrid} />
          </Space>
        }
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          关掉混合检索可以对比纯向量结果 —— 问"华中区有没有江西"这类带精确实体的句子时，
          纯向量经常跑偏，BM25 才能把它拉回来。
        </Typography.Paragraph>

        <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
          <Input
            value={probe} onChange={(e) => setProbe(e.target.value)}
            onPressEnter={() => void doSearch()}
            placeholder="输入一句话看召回结果，例如：退货金额在什么月份冲减营收"
          />
          <Button type="primary" icon={<SearchOutlined />} loading={probing} onClick={() => void doSearch()}>
            检索
          </Button>
        </Space.Compact>

        {probing && <Spin />}

        {hits && hits.length === 0 && (
          <Alert type="warning" showIcon message="没有召回任何片段，说明知识库为空或问题与文档差异过大" />
        )}

        {hits && hits.length > 0 && (
          <List
            size="small"
            dataSource={hits}
            renderItem={(it, i) => (
              <List.Item>
                <div style={{ width: '100%' }}>
                  <Space size={6} wrap style={{ marginBottom: 4 }}>
                    <Tag>#{i + 1}</Tag>
                    <Typography.Text strong style={{ fontSize: 13 }}>{it.title}</Typography.Text>
                    <Tag style={{ fontSize: 11, background: '#e8effb', color: '#1d5fbf', borderColor: '#c7d9f4' }} className="num">
                      融合 {it.score.toFixed(4)}
                    </Tag>
                    <Tag style={{ fontSize: 11 }} className="num">
                      BM25 {it.bm25_score.toFixed(2)}{it.bm25_rank > 0 ? ` (#${it.bm25_rank})` : ' 未命中'}
                    </Tag>
                    <Tag style={{ fontSize: 11 }} className="num">
                      向量 {it.vector_score.toFixed(3)}{it.vector_rank > 0 ? ` (#${it.vector_rank})` : ' 未命中'}
                    </Tag>
                    <Tag style={{ fontSize: 11 }} className="num">覆盖 {Math.round(it.coverage * 100)}%</Tag>
                  </Space>
                  <div style={{ fontSize: 12, color: '#666', whiteSpace: 'pre-wrap' }}>
                    {it.text.slice(0, 240)}{it.text.length > 240 ? '…' : ''}
                  </div>
                </div>
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  )
}
