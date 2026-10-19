import { useEffect, useState } from 'react'
import {
  App as AntApp, Alert, Button, Card, Descriptions, Form, Input, Modal, Select,
  Space, Table, Tag, Typography, Upload,
} from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { ingestApi, type DataSourceItem, type IngestResult, type PreviewResp } from '../api/api'
import { errMsg } from '../api/client'

const TYPE_LABEL: Record<string, string> = { file: '文件', database: '数据库', api: 'API' }
const STATUS_COLOR: Record<string, string> = { success: 'green', failed: 'red', running: 'blue', pending: 'default' }

export default function DataIngest() {
  const { message } = AntApp.useApp()
  const [sources, setSources] = useState<DataSourceItem[]>([])
  const [jobs, setJobs] = useState<Record<string, unknown>[]>([])
  const [preview, setPreview] = useState<PreviewResp | null>(null)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()

  const reload = async () => {
    try {
      const [s, j] = await Promise.all([ingestApi.sources(), ingestApi.jobs()])
      setSources(s); setJobs(j)
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  useEffect(() => { void reload() }, [])

  // 预览后要复用同一个文件对象入库，避免让用户把大文件再传一遍
  const [pickedFile, setPickedFile] = useState<File | null>(null)

  const onUpload = async (upFile: File) => {
    setBusy(true)
    try {
      const p = await ingestApi.preview(upFile)
      setPickedFile(upFile)
      setPreview(p)
      if (p.inferred_mapping._error) {
        message.warning(p.inferred_mapping._error)
      } else {
        message.info(`已识别 ${Object.keys(p.inferred_mapping).length} 个字段，确认后入库`)
      }
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const confirmImport = async () => {
    if (!pickedFile) return
    setBusy(true)
    try {
      const r = await ingestApi.upload(pickedFile, pickedFile.name, false)
      setResult(r)
      message.success(
        r.rows_skipped
          ? `入库完成：写入 ${r.rows_written} 行，跳过重复 ${r.rows_skipped} 行`
          : `入库完成：写入 ${r.rows_written} 行`,
      )
      setPreview(null)
      setPickedFile(null)
      void reload()
    } catch (e) {
      message.error(errMsg(e))
    } finally {
      setBusy(false)
    }
  }

  const onCreate = async (values: Record<string, unknown>) => {
    try {
      await ingestApi.createSource({
        name: values.name,
        type: values.type,
        config: JSON.parse(values.config as string),
        field_mapping: values.field_mapping ? JSON.parse(values.field_mapping as string) : {},
      })
      message.success('数据源已创建')
      setCreateOpen(false)
      form.resetFields()
      void reload()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <h1 className="page-title">数据接入</h1>
      <p className="page-desc">支持 Excel / CSV 上传、外部数据库直连、HTTP API 定时拉取三种方式</p>

      <Card title="上传销售明细" style={{ marginBottom: 16 }}>
        <Upload.Dragger
          accept=".csv,.xlsx,.xls"
          showUploadList={false}
          beforeUpload={(upFile) => { void onUpload(upFile as File); return false }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或把文件拖到这里</p>
          <p className="ant-upload-hint">
            系统会按常见中文/英文表头自动识别「日期、区域、客户、商品、销售额、成本」等字段
          </p>
        </Upload.Dragger>

        {preview && (
          <div style={{ marginTop: 16 }}>
            <Alert
              type={preview.inferred_mapping._error ? 'warning' : 'success'}
              showIcon
              message={preview.inferred_mapping._error
                ? `部分字段未能自动识别：${preview.inferred_mapping._error}`
                : `已识别 ${Object.keys(preview.inferred_mapping).length} 个字段映射，共 ${preview.rows_total} 行`}
              style={{ marginBottom: 12 }}
            />
            <Descriptions size="small" column={4} bordered>
              {Object.entries(preview.inferred_mapping).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>{String(v)}</Descriptions.Item>
              ))}
            </Descriptions>
            <Typography.Title level={5} style={{ marginTop: 16 }}>数据预览（前 5 行）</Typography.Title>
            <Table
              size="small" rowKey={(_, i) => String(i)} pagination={false} scroll={{ x: true }}
              dataSource={preview.preview}
              columns={preview.columns.slice(0, 8).map((c) => ({ title: c, dataIndex: c, render: (v: unknown) => String(v ?? '') }))}
            />
            <Space style={{ marginTop: 12 }}>
              <Button type="primary" disabled={!!preview.inferred_mapping._error || busy} onClick={() => void confirmImport()}>
                确认入库
              </Button>
              <Button onClick={() => setPreview(null)}>取消</Button>
            </Space>
          </div>
        )}

        {result && (
          <Alert style={{ marginTop: 12 }} type="success" showIcon
            message={`写入 ${result.rows_written} 行${result.rows_skipped ? `，跳过重复 ${result.rows_skipped} 行` : ''}`} />
        )}
      </Card>

      <Card
        title="数据源列表"
        extra={<Button onClick={() => setCreateOpen(true)}>新增连接源</Button>}
        style={{ marginBottom: 16 }}
      >
        <Table
          rowKey="id" size="small" pagination={false} dataSource={sources}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '类型', dataIndex: 'type', render: (v: string) => <Tag>{TYPE_LABEL[v] ?? v}</Tag> },
            { title: '最近状态', dataIndex: 'last_status', render: (v: string) => v ? <Tag color={STATUS_COLOR[v]}>{v}</Tag> : <Tag>未运行</Tag> },
            {
              title: '操作',
              render: (_, r) => (
                <Space>
                  <Button size="small" onClick={async () => {
                    try { const res = await ingestApi.run(r.id); message.success(`写入 ${res.rows_written} 行`); void reload() }
                    catch (e) { message.error(errMsg(e)) }
                  }}>同步</Button>
                  <Button size="small" danger onClick={async () => {
                    await ingestApi.deleteSource(r.id); void reload()
                  }}>删除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="接入任务历史">
        <Table
          rowKey="id" size="small" dataSource={jobs as Record<string, never>[]}
          pagination={false}
          columns={[
            { title: '数据源', dataIndex: 'datasource_name' },
            { title: '状态', dataIndex: 'status', render: (v: string) => <Tag color={STATUS_COLOR[v]}>{v}</Tag> },
            { title: '读取', dataIndex: 'rows_read', align: 'right' as const, render: (v: number) => <span className="num">{(v ?? 0).toLocaleString()}</span> },
            { title: '写入', dataIndex: 'rows_written', align: 'right' as const, render: (v: number) => <span className="num">{(v ?? 0).toLocaleString()}</span> },
            { title: '错误', dataIndex: 'error_message', render: (v?: string) => v ? <Typography.Text type="danger">{v.slice(0, 60)}</Typography.Text> : '-' },
          ]}
        />
      </Card>

      <Modal
        title="新增数据源" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()} okText="创建"
      >
        <Form form={form} layout="vertical" onFinish={onCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="type" label="类型" initialValue="database" rules={[{ required: true }]}>
            <Select options={[
              { value: 'database', label: '数据库（仅允许 SELECT）' },
              { value: 'api', label: 'HTTP API（返回 JSON 数组）' },
            ]} />
          </Form.Item>
          <Form.Item
            name="config" label="连接配置（JSON）" rules={[{ required: true }]}
            extra={'数据库：{"url":"mysql+pymysql://user:pass@host:3306/db","query":"SELECT * FROM sales"}\nAPI：{"url":"https://erp/api/sales","headers":{"Authorization":"Bearer xxx"}}'}
          >
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="field_mapping" label="字段映射（JSON，可选）" extra='例：{"date":"下单日期","revenue":"含税金额"}'>
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
