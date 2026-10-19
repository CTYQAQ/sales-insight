import { useCallback, useEffect, useState } from 'react'
import {
  App as AntApp, Button, Card, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography,
} from 'antd'
import { alertApi, type AlertEventItem, type AlertRuleItem } from '../api/api'
import { errMsg } from '../api/client'

const COMPARATOR: Record<string, string> = {
  lt: '低于阈值', gt: '高于阈值', drop_pct: '环比下降超过', rise_pct: '环比上升超过',
}
const METRIC: Record<string, string> = { revenue: '销售额', gross_profit: '毛利', quantity: '数量' }
const DIMENSION: Record<string, string> = {
  total: '总体', region: '按区域', product: '按商品', customer: '按客户', channel: '按渠道',
}

export default function Alerts() {
  const { message } = AntApp.useApp()
  const [rules, setRules] = useState<AlertRuleItem[]>([])
  const [events, setEvents] = useState<AlertEventItem[]>([])
  const [open, setOpen] = useState(false)
  const [checking, setChecking] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    try {
      const [r, e] = await Promise.all([alertApi.rules(), alertApi.events()])
      setRules(r); setEvents(e)
    } catch (e) {
      message.error(errMsg(e))
    }
  }, [message])

  useEffect(() => { void load() }, [load])

  const create = async (values: Record<string, unknown>) => {
    try {
      await alertApi.createRule(values)
      message.success('规则已创建')
      setOpen(false); form.resetFields(); void load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  const check = async () => {
    setChecking(true)
    try {
      const r = await alertApi.check(7)
      message.success(`巡检完成：评估 ${r.evaluated} 条规则，触发 ${r.triggered} 条告警`)
      void load()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setChecking(false)
    }
  }

  return (
    <div>
      <h1 className="page-title">异常告警</h1>
      <p className="page-desc">
        规则巡检后自动推送钉钉/企业微信/邮件；同一规则同一维度在冷却期内只报一次，不会刷屏
      </p>

      <Card
        title="告警规则"
        extra={<Space>
          <Button onClick={() => void check()} loading={checking}>立即巡检</Button>
          <Button type="primary" onClick={() => setOpen(true)}>新建规则</Button>
        </Space>}
        style={{ marginBottom: 16 }}
      >
        <Table
          rowKey="id" size="small" pagination={false} dataSource={rules}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '指标', dataIndex: 'metric', render: (v: string) => METRIC[v] ?? v },
            { title: '维度', dataIndex: 'dimension', render: (v: string) => DIMENSION[v] ?? v },
            {
              title: '条件', align: 'right' as const,
              render: (_, r) => (
                <span className="num">{COMPARATOR[r.comparator]} {r.threshold}{r.comparator.includes('pct') ? '%' : ''}</span>
              ),
            },
            { title: '级别', dataIndex: 'severity', render: (v: string) => <Tag color={v === 'critical' ? 'red' : 'orange'}>{v}</Tag> },
            { title: '启用', dataIndex: 'enabled', render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
            { title: '最近触发', dataIndex: 'last_triggered_at', render: (v?: string) => v ? v.slice(0, 16) : '-' },
            {
              title: '操作',
              render: (_, r) => (
                <Space>
                  <Button size="small" onClick={async () => {
                    await alertApi.patchRule(r.id, { enabled: !r.enabled }); void load()
                  }}>{r.enabled ? '停用' : '启用'}</Button>
                  <Button size="small" danger onClick={async () => {
                    await alertApi.deleteRule(r.id); void load()
                  }}>删除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="告警事件">
        <Table
          rowKey="id" size="small" dataSource={events}
          columns={[
            { title: '规则', dataIndex: 'rule_name' },
            { title: '对象', dataIndex: 'dimension_key' },
            {
              title: '实际值', dataIndex: 'actual_value', align: 'right' as const,
              render: (v: number) => <span className="num">{typeof v === 'number' ? v.toLocaleString() : v ?? '-'}</span>,
            },
            { title: '时间', dataIndex: 'created_at', render: (v?: string) => v ? v.slice(0, 16) : '-' },
            { title: '说明', dataIndex: 'message', render: (v: string) => <Typography.Text style={{ fontSize: 12 }}>{v}</Typography.Text> },
            {
              title: '操作',
              render: (_, r) => r.acknowledged ? <Tag>已确认</Tag> : (
                <Button size="small" onClick={async () => { await alertApi.ack(r.id); void load() }}>确认</Button>
              ),
            },
          ]}
        />
      </Card>

      <Modal title="新建告警规则" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="创建">
        <Form form={form} layout="vertical" onFinish={create} initialValues={{ metric: 'revenue', dimension: 'total', comparator: 'drop_pct', severity: 'warning' }}>
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input placeholder="例如：区域销售额环比暴跌" />
          </Form.Item>
          <Form.Item name="metric" label="监控指标">
            <Select options={Object.entries(METRIC).map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
          <Form.Item name="dimension" label="拆分维度">
            <Select options={Object.entries(DIMENSION).map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
          <Form.Item name="comparator" label="判定方式">
            <Select options={Object.entries(COMPARATOR).map(([v, l]) => ({ value: v, label: l }))} />
          </Form.Item>
          <Form.Item name="threshold" label="阈值" rules={[{ required: true }]}>
            <InputNumber style={{ width: '100%' }} placeholder="下降填 30 表示 30%" />
          </Form.Item>
          <Form.Item name="severity" label="级别">
            <Select options={[{ value: 'warning', label: '警告' }, { value: 'critical', label: '严重' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
