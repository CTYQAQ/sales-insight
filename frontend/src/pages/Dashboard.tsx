import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Card, Col, Empty, Row, Segmented, Select, Spin, Table, Typography } from 'antd'
import ReactECharts from 'echarts-for-react'
import dayjs from 'dayjs'
import { metricsApi, type BreakItem, type SummaryResp, type TrendItem } from '../api/api'
import { errMsg } from '../api/client'

const fmtMoney = (v: number) =>
  v >= 1_0000_0000 ? `¥${(v / 1_0000_0000).toFixed(2)} 亿` :
  v >= 1_0000 ? `¥${(v / 1_0000).toFixed(1)} 万` : `¥${v.toFixed(0)}`

const CHART_HUE = '#1d5fbf'
const AXIS_GREY = '#6b6b6b'
const GRID_LINE = '#f0f0f0'
const CHART_FONT = 'Geist, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'

/**
 * 涨跌信号：颜色表达"好/坏"而非"上/下"。销售额类指标上涨是好事，
 * 按国内习惯涨红跌绿；没有对比基准时给灰色破折号，不打信号色。
 */
function Delta({ value, suffix = '%', compact = false }: { value: number | null; suffix?: string; compact?: boolean }) {
  if (value === null || value === undefined) {
    return <span className="kpi-delta flat">—{compact ? '' : ' 无对比'}</span>
  }
  const up = value >= 0
  return (
    <span className={`kpi-delta ${up ? 'up' : 'down'}`}>
      {up ? '▲' : '▼'} {Math.abs(value).toFixed(2)}{suffix}{compact ? '' : ' · 较上一周期'}
    </span>
  )
}

/** KPI 条：一个容器用发丝线分格，替代"四张一样的卡片" */
function KpiStrip({ summary }: { summary: SummaryResp | null }) {
  const cells = [
    { label: '销售额', value: fmtMoney(summary?.current.revenue ?? 0), delta: summary?.changes.revenue ?? null, money: true },
    { label: '毛利', value: fmtMoney(summary?.current.gross_profit ?? 0), delta: summary?.changes.gross_profit ?? null, money: true },
    { label: '毛利率', value: `${(summary?.current.margin ?? 0).toFixed(2)}%`, delta: summary?.changes.margin_pp ?? null, suffix: 'pp' },
    { label: '订单数', value: `${(summary?.current.orders ?? 0).toLocaleString()} 单`, delta: summary?.changes.orders ?? null },
  ]
  return (
    <div className="kpi-strip">
      {cells.map((c) => (
        <div className="kpi-cell" key={c.label}>
          <div className="kpi-label">{c.label}</div>
          <div className={`kpi-value${c.money ? ' money' : ''}`}>{c.value}</div>
          <Delta value={c.delta} suffix={c.suffix ?? '%'} />
        </div>
      ))}
    </div>
  )
}

export default function Dashboard() {
  const { message } = AntApp.useApp()
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(true)
  const [summary, setSummary] = useState<SummaryResp | null>(null)
  const [trend, setTrend] = useState<TrendItem[]>([])
  const [range, setRange] = useState<{ min_date?: string; max_date?: string }>({})
  const [dim, setDim] = useState('region')
  const [breakdown, setBreakdown] = useState<BreakItem[]>([])
  const [movers, setMovers] = useState<{ key: string; change_pct: number; delta_amount: number; current: number }[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [sm, rg] = await Promise.all([metricsApi.summary(days), metricsApi.range()])
      setSummary(sm)
      setRange(rg)
      setHasData(rg.has_data)

      if (!rg.has_data) {
        setTrend([]); setBreakdown([]); setMovers([])
        return
      }
      const start = rg.min_date!
      const end = rg.max_date!
      const [tr, bd, mv] = await Promise.all([
        metricsApi.trend(start, end),
        metricsApi.breakdown(start, end, dim, 10),
        metricsApi.movers(days, 5),
      ])
      setTrend(tr)
      setBreakdown(bd)
      setMovers(mv.declining_customers ?? [])
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setLoading(false)
    }
  }, [days, dim, message])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!hasData || !range.min_date) return
    metricsApi.breakdown(range.min_date, range.max_date!, dim, 10)
      .then(setBreakdown)
      .catch((e) => message.error(errMsg(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dim])

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" tip="加载中" /></div>

  if (!hasData) {
    return (
      <Card>
        <Empty description={<span>还没有销售数据，先到「数据接入」上传一份 Excel / CSV</span>} />
      </Card>
    )
  }

  // 图表 chrome 收到最薄：横向 3-5 根浅网格线、无 y 轴线、单系列不画图例。
  // 直线段不平滑 —— 平滑曲线会在日粒度数据之间"发明"不存在的值。
  const axisBase = {
    axisLabel: { color: AXIS_GREY, fontSize: 12, fontFamily: CHART_FONT },
    axisLine: { show: false },
    axisTick: { show: false },
  }
  const trendOption = {
    textStyle: { fontFamily: CHART_FONT },
    tooltip: { trigger: 'axis' as const },
    grid: { left: 56, right: 24, top: 24, bottom: 32 },
    xAxis: {
      type: 'category' as const,
      data: trend.map((t) => t.date),
      boundaryGap: false,
      ...axisBase,
      axisLine: { show: true, lineStyle: { color: '#e6e6e6' } },
    },
    yAxis: {
      type: 'value' as const,
      ...axisBase,
      splitLine: { lineStyle: { color: GRID_LINE } },
      axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` },
    },
    series: [{
      type: 'line' as const,
      data: trend.map((t) => t.value),
      smooth: false,
      symbol: 'none',
      itemStyle: { color: CHART_HUE },
      lineStyle: { width: 2, color: CHART_HUE },
    }],
  }

  const barOption = {
    textStyle: { fontFamily: CHART_FONT },
    tooltip: { trigger: 'axis' as const },
    grid: { left: 90, right: 64, top: 12, bottom: 28 },
    xAxis: {
      type: 'value' as const,
      ...axisBase,
      splitLine: { show: false },
      axisLabel: { ...axisBase.axisLabel, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` },
    },
    yAxis: {
      type: 'category' as const,
      data: breakdown.map((b) => b.name).reverse(),
      ...axisBase,
    },
    series: [{
      type: 'bar' as const,
      data: breakdown.map((b) => b.revenue).reverse(),
      barMaxWidth: 18,
      // 直角柱；唯一的 hue 给这个主数据元素
      itemStyle: { color: CHART_HUE },
      label: {
        show: true, position: 'right' as const, color: '#0a0a0a', fontSize: 12,
        formatter: (p: { value: number }) => fmtMoney(p.value),
      },
    }],
  }

  return (
    <div>
      <h1 className="page-title">销售看板</h1>
      <p className="page-desc">
        统计区间 {summary?.period.start} ~ {summary?.period.end}（对比上一个同等长度周期）
        {range.min_date && ` ｜ 数据覆盖 ${range.min_date} ~ ${range.max_date}`}
      </p>

      <div style={{ marginBottom: 16 }}>
        <Segmented
          value={days}
          onChange={(v) => setDays(Number(v))}
          options={[{ label: '近 7 天', value: 7 }, { label: '近 30 天', value: 30 }, { label: '近 90 天', value: 90 }]}
        />
      </div>

      <KpiStrip summary={summary} />

      <Card title="销售额趋势" style={{ marginTop: 16 }}>
        <ReactECharts option={trendOption} style={{ height: 280 }} />
      </Card>

      <Row gutter={16} style={{ marginTop: 16 }} align="stretch">
        <Col span={14}>
          <Card
            title="维度排名"
            style={{ height: '100%' }}
            extra={
              <Select value={dim} onChange={setDim} style={{ width: 120 }}
                options={[
                  { value: 'region', label: '按区域' }, { value: 'product', label: '按商品' },
                  { value: 'customer', label: '按客户' }, { value: 'channel', label: '按渠道' },
                ]}
              />
            }
          >
            <ReactECharts option={barOption} style={{ height: 300 }} />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="异动 TOP5（环比跌幅）" style={{ height: '100%' }}>
            <Table
              size="small" rowKey="key" pagination={false}
              dataSource={movers}
              columns={[
                { title: '客户', dataIndex: 'key' },
                {
                  title: '环比', dataIndex: 'change_pct', align: 'right' as const,
                  render: (v: number) => <Delta value={v} compact />,
                },
                {
                  title: '差额', dataIndex: 'delta_amount', align: 'right' as const,
                  render: (v: number) => (
                    <span className="num" style={{ color: v >= 0 ? 'var(--up)' : 'var(--down)' }}>{fmtMoney(v)}</span>
                  ),
                },
              ]}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8, display: 'block' }}>
              更新于 {dayjs().format('YYYY-MM-DD HH:mm')}
            </Typography.Text>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
