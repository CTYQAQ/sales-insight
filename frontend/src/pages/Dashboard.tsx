import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Card, Col, Empty, Row, Segmented, Select, Space, Spin, Statistic, Table, Tag, Typography } from 'antd'
import ReactECharts from 'echarts-for-react'
import dayjs from 'dayjs'
import { metricsApi, type BreakItem, type SummaryResp, type TrendItem } from '../api/api'
import { errMsg } from '../api/client'

const fmtMoney = (v: number) =>
  v >= 1_0000_0000 ? `¥${(v / 1_0000_0000).toFixed(2)} 亿` :
  v >= 1_0000 ? `¥${(v / 1_0000).toFixed(1)} 万` : `¥${v.toFixed(0)}`

function DeltaTag({ value, suffix = '%' }: { value: number | null; suffix?: string }) {
  if (value === null || value === undefined) return <Tag>无对比</Tag>
  const up = value >= 0
  return (
    <span style={{ color: up ? '#cf1322' : '#3f8600', fontSize: 13 }}>
      {up ? '▲' : '▼'} {Math.abs(value).toFixed(2)}
      {suffix}
    </span>
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

  const trendOption = {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
    xAxis: { type: 'category' as const, data: trend.map((t) => t.date), boundaryGap: false },
    yAxis: { type: 'value' as const, axisLabel: { formatter: (v: number) => `${(v / 10000).toFixed(0)}万` } },
    series: [{
      type: 'line' as const, data: trend.map((t) => t.value), smooth: true,
      areaStyle: {}, itemStyle: { color: '#1668dc' }, lineStyle: { width: 2 },
    }],
  }

  const barOption = {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 90, right: 40, top: 20, bottom: 30 },
    xAxis: { type: 'value' as const },
    yAxis: { type: 'category' as const, data: breakdown.map((b) => b.name).reverse() },
    series: [{
      type: 'bar' as const,
      data: breakdown.map((b) => b.revenue).reverse(),
      itemStyle: { color: '#1668dc', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right' as const, formatter: (p: { value: number }) => fmtMoney(p.value) },
    }],
  }

  return (
    <div>
      <h2 className="page-title">销售看板</h2>
      <p className="page-desc">
        统计区间 {summary?.period.start} ~ {summary?.period.end}（对比上一个同等长度周期）
        {range.min_date && ` ｜ 数据覆盖 ${range.min_date} ~ ${range.max_date}`}
      </p>

      <Space style={{ marginBottom: 16 }}>
        <Segmented
          value={days}
          onChange={(v) => setDays(Number(v))}
          options={[{ label: '近 7 天', value: 7 }, { label: '近 30 天', value: 30 }, { label: '近 90 天', value: 90 }]}
        />
      </Space>

      <Row gutter={16}>
        <Col span={6}>
          <Card className="kpi-card">
            <Statistic title="销售额" value={summary?.current.revenue ?? 0} formatter={(v) => fmtMoney(Number(v))} />
            <DeltaTag value={summary?.changes.revenue ?? null} />
          </Card>
        </Col>
        <Col span={6}>
          <Card className="kpi-card">
            <Statistic title="毛利" value={summary?.current.gross_profit ?? 0} formatter={(v) => fmtMoney(Number(v))} />
            <DeltaTag value={summary?.changes.gross_profit ?? null} />
          </Card>
        </Col>
        <Col span={6}>
          <Card className="kpi-card">
            <Statistic title="毛利率" value={summary?.current.margin ?? 0} suffix="%" precision={2} />
            <DeltaTag value={summary?.changes.margin_pp ?? null} suffix="pp" />
          </Card>
        </Col>
        <Col span={6}>
          <Card className="kpi-card">
            <Statistic title="订单数" value={summary?.current.orders ?? 0} suffix="单" />
            <DeltaTag value={summary?.changes.orders ?? null} suffix="%" />
          </Card>
        </Col>
      </Row>

      <Card title="销售额趋势" style={{ marginTop: 16 }}>
        <ReactECharts option={trendOption} style={{ height: 300 }} />
      </Card>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={14}>
          <Card
            title="维度排名"
            extra={
              <Select value={dim} onChange={setDim} style={{ width: 120 }}
                options={[
                  { value: 'region', label: '按区域' }, { value: 'product', label: '按商品' },
                  { value: 'customer', label: '按客户' }, { value: 'channel', label: '按渠道' },
                ]}
              />
            }
          >
            <ReactECharts option={barOption} style={{ height: 320 }} />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="异动 TOP5（环比跌幅）">
            <Table
              size="small" rowKey="key" pagination={false}
              dataSource={movers}
              columns={[
                { title: '客户', dataIndex: 'key' },
                { title: '环比', dataIndex: 'change_pct', render: (v: number) => <DeltaTag value={v} /> },
                { title: '差额', dataIndex: 'delta_amount', render: (v: number) => <span style={{ color: '#3f8600' }}>{fmtMoney(v)}</span> },
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
