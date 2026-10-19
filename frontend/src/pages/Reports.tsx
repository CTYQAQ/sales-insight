import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Button, Card, Empty, List, Segmented, Space, Spin, Tag, Typography } from 'antd'
import ReactMarkdown from 'react-markdown'
import { aiApi, type ReportItem } from '../api/api'
import { errMsg } from '../api/client'

export default function Reports() {
  const { message } = AntApp.useApp()
  const [days, setDays] = useState(30)
  const [items, setItems] = useState<ReportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [current, setCurrent] = useState<ReportItem | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await aiApi.reports())
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => { void load() }, [load])

  const generate = async () => {
    setGenerating(true)
    try {
      const r = await aiApi.generateReport(days)
      message.success('报告已生成')
      setCurrent(r)
      void load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setGenerating(false)
    }
  }

  const open = async (id: string) => {
    try {
      setCurrent(await aiApi.report(id))
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <h2 className="page-title">AI 销售分析报告</h2>
      <p className="page-desc">
        所有数字都由本地 SQL 聚合算出，AI 只负责解读，不会凭空生成数据
      </p>

      <Space style={{ marginBottom: 16 }} wrap>
        <Segmented
          value={days} onChange={(v) => setDays(Number(v))}
          options={[{ label: '近 7 天', value: 7 }, { label: '近 30 天', value: 30 }, { label: '近 90 天', value: 90 }]}
        />
        <Button type="primary" loading={generating} onClick={() => void generate()}>
          生成报告
        </Button>
      </Space>

      {generating && (
        <Card style={{ marginBottom: 16 }}>
          <Spin /> <span style={{ marginLeft: 8 }}>正在聚合指标并生成解读，通常需要 20-60 秒…</span>
        </Card>
      )}

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <Card title="历史报告" style={{ width: 340, flexShrink: 0 }}>
          {loading ? <Spin /> : items.length === 0 ? <Empty description="还没有报告" /> : (
            <List
              dataSource={items}
              renderItem={(item) => (
                <List.Item
                  onClick={() => void open(item.id)}
                  style={{ cursor: 'pointer', background: current?.id === item.id ? '#f0f7ff' : undefined }}
                >
                  <List.Item.Meta
                    title={<span style={{ fontSize: 13 }}>{item.title}</span>}
                    description={<span style={{ fontSize: 12 }}>
                      {item.created_at?.slice(0, 16)} {item.model && <Tag>{item.model}</Tag>}
                    </span>}
                  />
                </List.Item>
              )}
            />
          )}
        </Card>

        <Card style={{ flex: 1 }} styles={{ body: { minHeight: 400 } }}>
          {current ? (
            <>
              <Typography.Title level={5}>{current.title}</Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                生成模型 {current.model} ｜ {current.created_at?.slice(0, 19)}
              </Typography.Text>
              <div style={{ marginTop: 16, lineHeight: 1.8 }}>
                <ReactMarkdown>{current.summary_md ?? ''}</ReactMarkdown>
              </div>
            </>
          ) : (
            <Empty description="点左侧报告查看，或直接生成一份新的" />
          )}
        </Card>
      </div>
    </div>
  )
}
