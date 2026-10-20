import { useEffect, useRef, useState } from 'react'
import {
  Alert, App as AntApp, Avatar, Button, Card, Collapse, Empty, Input, List,
  Segmented, Space, Spin, Table, Tag, Timeline, Tooltip, Typography,
} from 'antd'
import {
  BranchesOutlined, BulbOutlined, DatabaseOutlined, RobotOutlined,
  SendOutlined, ThunderboltOutlined, UserOutlined,
} from '@ant-design/icons'
import {
  aiApi, type AgentChatResp, type AgentTraceItem, type ChatResp,
  type DialogMsg, type DialogResp, type DialogSource, type RetrievedChunk,
} from '../api/api'
import { errMsg } from '../api/client'

const DATA_EXAMPLES = [
  '华东区上个月营收多少',
  '哪个区域的毛利最高',
  '销售额前十的客户分别是多少',
]

const KB_EXAMPLES = [
  '毛利率怎么算',
  '华东区包括哪些省份',
  '报表数字和财务对不上怎么办',
  '华中区有没有江西',
]

/** 节点名 → 展示用中文。后端用英文标识，前端做一次映射，避免界面上中英混杂。 */
const NODE_LABEL: Record<string, string> = {
  // 对话 Agent
  understand: '理解上下文',
  route: '判断意图',
  retrieve: '检索知识库',
  answer_kb: '依据知识作答',
  answer_data: '查询数据',
  answer_chat: '对话应答',
  // 数据 Agent（嵌套在 answer_data 内部时不会单独出现，留着备用）
  rewrite: '理解问题',
  generate_sql: '生成 SQL',
  guard: '安全校验',
  execute: '执行查询',
  reflect: '反思修正',
  answer: '生成结论',
}

const STATUS_COLOR: Record<string, string> = {
  ok: 'green',
  retry: 'orange',
  failed: 'red',
  blocked: 'volcano',
}

const STATUS_TEXT: Record<string, string> = {
  ok: '完成',
  retry: '重试',
  failed: '失败',
  blocked: '拦截',
}

/** 意图标签：彩色预算只给"查数据"（这个产品的核心动作），其余中性灰 */
const INTENT_META: Record<string, { label: string; tint?: boolean; icon: React.ReactNode }> = {
  knowledge: { label: '查知识库', icon: <BulbOutlined /> },
  data: { label: '查数据', tint: true, icon: <DatabaseOutlined /> },
  chat: { label: '一般对话', icon: <RobotOutlined /> },
}

type Mode = 'dialog' | 'agent' | 'simple'

/** 一轮对话。前端自己维护，服务端只做无状态请求 —— 重启不丢，多实例不串。 */
interface Turn {
  key: string
  question: string
  resp: DialogResp
}

export default function Chat() {
  const { message } = AntApp.useApp()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<Mode>('dialog')
  const [result, setResult] = useState<ChatResp | null>(null)
  const [agentResult, setAgentResult] = useState<AgentChatResp | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [history, setHistory] = useState<Record<string, unknown>[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadHistory = async () => {
    // 侧栏"最近提问"只作入口，5 行足够；8 行在演示数据下全是重复问题
    try { setHistory(await aiApi.chatHistory(5)) } catch { /* 历史加载失败不影响主流程 */ }
  }
  useEffect(() => { void loadHistory() }, [])

  // 新消息到达后自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, busy])

  const ask = async (question: string) => {
    const text = question.trim()
    if (!text) return
    setBusy(true)
    setResult(null)
    setAgentResult(null)
    try {
      if (mode === 'dialog') {
        // 只带最近 6 条，控制 token 和延迟
        const hist: DialogMsg[] = turns
          .flatMap((t) => [
            { role: 'user' as const, content: t.question },
            { role: 'assistant' as const, content: t.resp.answer },
          ])
          .slice(-6)
        const resp = await aiApi.dialog(text, hist)
        setTurns((prev) => [...prev, { key: `${Date.now()}`, question: text, resp }])
        setQ('')
      } else if (mode === 'agent') {
        setAgentResult(await aiApi.agentChat(text))
      } else {
        setResult(await aiApi.chat(text))
      }
      void loadHistory()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const shown: ChatResp | null = agentResult ?? result
  const usedRetry = (agentResult?.attempts ?? 0) > 1
  const examples = mode === 'dialog' ? [...KB_EXAMPLES, ...DATA_EXAMPLES] : DATA_EXAMPLES

  return (
    <div>
      <h1 className="page-title">对话助手</h1>
      <p className="page-desc">
        自动判断你在问业务口径还是问具体数字：问口径去知识库检索并给出来源，
        问数字走 SQL 查询；生成的 SQL 仍会经过白名单校验与数据权限注入
      </p>

      <Space style={{ marginBottom: 12 }} wrap>
        <Segmented
          value={mode}
          onChange={(v) => { setMode(v as Mode); setResult(null); setAgentResult(null) }}
          options={[
            { label: '对话（RAG）', value: 'dialog', icon: <BulbOutlined /> },
            { label: 'Agent 问数', value: 'agent', icon: <BranchesOutlined /> },
            { label: '单次问数', value: 'simple', icon: <ThunderboltOutlined /> },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {mode === 'dialog'
            ? '意图路由 + 混合检索（BM25 + 向量）+ 多轮追问'
            : mode === 'agent'
              ? '多节点协作：会先理解问题、失败时自己反思并重试'
              : '一次生成 SQL 即结束，不做自我修正'}
        </Typography.Text>
      </Space>

      <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
        <Input
          value={q} onChange={(e) => setQ(e.target.value)}
          onPressEnter={() => void ask(q)}
          placeholder={mode === 'dialog' ? '试试：毛利率怎么算 / 华东区上个月营收多少' : '例如：华东区最近 30 天销售额是多少？'}
          size="large"
        />
        <Button type="primary" size="large" loading={busy} icon={<SendOutlined />} onClick={() => void ask(q)}>
          提问
        </Button>
      </Space.Compact>

      <Space wrap style={{ marginBottom: 16 }}>
        {examples.map((e) => (
          <Button key={e} size="small" onClick={() => { setQ(e); void ask(e) }}>{e}</Button>
        ))}
      </Space>

      {/* ---------------- 对话模式 ---------------- */}
      {mode === 'dialog' && (
        <>
          {turns.length === 0 && !busy && (
            <Card><Empty description="还没有对话，试试上面的示例问题" /></Card>
          )}

          {turns.map((t) => (
            <div key={t.key} style={{ marginBottom: 20 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 8 }}>
                <Avatar size={28} icon={<UserOutlined />} style={{ background: '#141414', flexShrink: 0 }} />
                <Typography.Text strong style={{ paddingTop: 4 }}>{t.question}</Typography.Text>
              </div>

              {/* 不用 Space + styles.item：那会把 100% 宽也套到头像上，答案列被挤成半宽 */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <Avatar size={28} icon={<RobotOutlined style={{ color: '#0a0a0a' }} />} style={{ background: '#e9ebee', flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space size={6} wrap style={{ marginBottom: 6 }}>
                    <Tag
                      icon={INTENT_META[t.resp.intent]?.icon}
                      style={INTENT_META[t.resp.intent]?.tint
                        ? { background: '#e8effb', color: '#1d5fbf', borderColor: '#c7d9f4' }
                        : undefined}
                    >
                      {INTENT_META[t.resp.intent]?.label ?? t.resp.intent}
                    </Tag>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {t.resp.intent_reason}
                    </Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      · {t.resp.elapsed_ms} ms
                    </Typography.Text>
                  </Space>

                  {t.resp.standalone_question && t.resp.standalone_question !== t.question && (
                    <Alert
                      type="info" showIcon style={{ marginBottom: 8 }}
                      message={<span style={{ fontSize: 12 }}>
                        结合上下文理解为：{t.resp.standalone_question}
                      </span>}
                    />
                  )}

                  <Card size="small" style={{ background: '#fafafa' }}>
                    <Typography.Paragraph style={{ lineHeight: 1.8, marginBottom: 0, whiteSpace: 'pre-wrap' }}>
                      {t.resp.answer}
                    </Typography.Paragraph>
                  </Card>

                  {t.resp.sources.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>参考来源：</Typography.Text>
                      {t.resp.sources.map((s: DialogSource, i) => (
                        <Tooltip key={`${s.source}-${i}`} title={s.source}>
                          <Tag style={{ fontSize: 11, marginTop: 4 }}>
                            [{i + 1}] {s.title}
                          </Tag>
                        </Tooltip>
                      ))}
                    </div>
                  )}

                  {t.resp.sql && (
                    <Collapse
                      size="small" ghost style={{ marginTop: 6 }}
                      items={[{
                        key: 'sql',
                        label: <span style={{ fontSize: 12 }}>执行的 SQL（已注入权限过滤）</span>,
                        children: (
                          <pre className="mono" style={{
                            background: '#f5f5f5', padding: 10, borderRadius: 6,
                            overflowX: 'auto', fontSize: 12, margin: 0,
                          }}>{t.resp.sql}</pre>
                        ),
                      }]}
                    />
                  )}

                  {t.resp.rows.length > 0 && (
                    <Card size="small" style={{ marginTop: 8 }}>
                      <Table
                        size="small" rowKey={(_, i) => String(i)} scroll={{ x: true }}
                        dataSource={t.resp.rows}
                        columns={t.resp.columns.map((c) => ({
                          title: c, dataIndex: c,
                          render: (v: unknown) => typeof v === 'number' ? v.toLocaleString() : String(v ?? ''),
                        }))}
                        pagination={false}
                      />
                    </Card>
                  )}

                  <Collapse
                    size="small" ghost style={{ marginTop: 4 }}
                    items={[{
                      key: 'trace',
                      label: <span style={{ fontSize: 12 }}>执行轨迹（{t.resp.trace.length} 步）</span>,
                      children: <TraceTimeline trace={t.resp.trace} />,
                    }]}
                  />

                  {t.resp.retrieved.length > 0 && (
                    <Collapse
                      size="small" ghost
                      items={[{
                        key: 'retrieved',
                        label: <span style={{ fontSize: 12 }}>检索明细（可看向量分与 BM25 分）</span>,
                        children: <RetrievedList items={t.resp.retrieved} />,
                      }]}
                    />
                  )}
                </div>
              </div>
            </div>
          ))}

          {busy && (
            <Card style={{ marginBottom: 16 }}>
              <Spin />
              <span style={{ marginLeft: 12 }}>正在判断意图并检索…</span>
            </Card>
          )}
          <div ref={bottomRef} />
        </>
      )}

      {/* ---------------- 数据 Agent / 单次模式 ---------------- */}
      {mode !== 'dialog' && busy && (
        <Card style={{ marginBottom: 16 }}>
          <Spin />
          <span style={{ marginLeft: 12 }}>
            {mode === 'agent' ? 'Agent 正在分析问题、生成并执行查询…' : '正在生成 SQL 并执行…'}
          </span>
        </Card>
      )}

      {mode !== 'dialog' && agentResult && agentResult.trace.length > 0 && (
        <Card
          title="Agent 执行轨迹"
          style={{ marginBottom: 16 }}
          extra={<Tag color={usedRetry ? 'orange' : 'green'}>
            {usedRetry ? `自我修正 ${agentResult.attempts - 1} 次` : '一次即答对'}
          </Tag>}
        >
          {usedRetry && (
            <Alert
              type="warning" showIcon style={{ marginBottom: 12 }}
              message="本次查询首次未拿到有效结果，Agent 已自动诊断原因并重新生成 SQL"
            />
          )}
          <TraceTimeline trace={agentResult.trace} />
          {agentResult.rewritten && agentResult.rewritten !== q && (
            <>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>问题被理解为：</Typography.Text>
              <Typography.Text style={{ fontSize: 13 }}>{agentResult.rewritten}</Typography.Text>
            </>
          )}
        </Card>
      )}

      {mode !== 'dialog' && shown && (
        <Card title="查询过程" style={{ marginBottom: 16 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>执行的 SQL（已注入权限过滤）</Typography.Text>
          <pre className="mono" style={{ background: '#fafafa', padding: 12, borderRadius: 6, overflowX: 'auto' }}>
            {shown.sql || '（未产生有效 SQL）'}
          </pre>
          <Space wrap>
            <Tag>{shown.row_count} 行</Tag>
            <Tag>{shown.elapsed_ms} ms</Tag>
            {agentResult && <Tag color="blue">Agent · {agentResult.attempts} 次尝试</Tag>}
          </Space>
        </Card>
      )}

      {mode !== 'dialog' && shown && (
        <Card title="AI 解读" style={{ marginBottom: 16 }}>
          <Typography.Paragraph style={{ lineHeight: 1.8 }}>{shown.answer}</Typography.Paragraph>
        </Card>
      )}

      {mode !== 'dialog' && shown && shown.rows.length > 0 && (
        <Card title="数据明细" style={{ marginBottom: 16 }}>
          <Table
            size="small" rowKey={(_, i) => String(i)} scroll={{ x: true }}
            dataSource={shown.rows}
            columns={shown.columns.map((c) => ({
              title: c, dataIndex: c,
              render: (v: unknown) => typeof v === 'number' ? v.toLocaleString() : String(v ?? ''),
            }))}
            pagination={false}
          />
        </Card>
      )}

      <Card title="最近提问">
        {history.length === 0 ? <Empty description="还没有提问记录" /> : (
          <List
            size="small"
            dataSource={history}
            renderItem={(h) => (
              <List.Item
                onClick={() => { const question = String(h.question); setQ(question); void ask(question) }}
                style={{ cursor: 'pointer' }}
              >
                <List.Item.Meta
                  title={<span style={{ fontSize: 13 }}>{String(h.question)}</span>}
                  description={h.status === 'blocked'
                    ? <Typography.Text type="danger" style={{ fontSize: 12 }}>已被安全策略拦截</Typography.Text>
                    : <Tooltip title={String(h.sql ?? '')}>
                        <span style={{ fontSize: 12 }}>{String(h.created_at ?? '').slice(0, 16)}</span>
                      </Tooltip>}
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  )
}

function TraceTimeline({ trace }: { trace: AgentTraceItem[] }) {
  return (
    <Timeline
      items={trace.map((t, i) => ({
        key: i,
        color: STATUS_COLOR[t.status] ?? 'gray',
        children: (
          <div>
            <Space size={6} wrap>
              <Typography.Text strong style={{ fontSize: 13 }}>
                {NODE_LABEL[t.node] ?? t.node}
              </Typography.Text>
              <Tag color={STATUS_COLOR[t.status]} style={{ marginInlineEnd: 0 }}>
                {STATUS_TEXT[t.status] ?? t.status}
              </Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t.elapsed_ms} ms
              </Typography.Text>
            </Space>
            <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>{t.detail}</div>
          </div>
        ),
      }))}
    />
  )
}

/** 检索明细：把向量分 / BM25 分 / 覆盖率摊开，便于验证混合检索真的生效了 */
function RetrievedList({ items }: { items: RetrievedChunk[] }) {
  return (
    <List
      size="small"
      dataSource={items}
      renderItem={(it, i) => (
        <List.Item>
          <div style={{ width: '100%' }}>
            <Space size={6} wrap style={{ marginBottom: 2 }}>
              <Tag>[{i + 1}]</Tag>
              <Typography.Text strong style={{ fontSize: 12 }}>{it.title}</Typography.Text>
              <Tag style={{ fontSize: 11 }} className="num">
                BM25 {it.bm25_score.toFixed(2)}
                {it.bm25_rank > 0 ? ` (#${it.bm25_rank})` : ' 未命中'}
              </Tag>
              <Tag style={{ fontSize: 11 }} className="num">
                向量 {it.vector_score.toFixed(3)}
                {it.vector_rank > 0 ? ` (#${it.vector_rank})` : ' 未命中'}
              </Tag>
              <Tag style={{ fontSize: 11 }} className="num">覆盖 {Math.round(it.coverage * 100)}%</Tag>
            </Space>
            <div style={{ fontSize: 12, color: '#666', whiteSpace: 'pre-wrap' }}>
              {it.text.slice(0, 160)}{it.text.length > 160 ? '…' : ''}
            </div>
          </div>
        </List.Item>
      )}
    />
  )
}
