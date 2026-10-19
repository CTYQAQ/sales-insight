import { useCallback, useEffect, useState } from 'react'
import { App as AntApp, Button, Card, Form, Input, Modal, Select, Table, Tag } from 'antd'
import { authApi, type UserInfo } from '../api/api'
import { errMsg } from '../api/client'

// 角色标签不用彩色：admin 用实心墨色，其余中性灰 —— 彩色预算只给数据与告警状态
const ROLE_COLOR: Record<string, string> = { admin: '#141414', analyst: 'default', viewer: 'default' }
const ROLE_LABEL: Record<string, string> = { admin: '管理员', analyst: '分析师', viewer: '查看者' }

export default function Users() {
  const { message } = AntApp.useApp()
  const [users, setUsers] = useState<UserInfo[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    try { setUsers(await authApi.users()) } catch (e) { message.error(errMsg(e)) }
  }, [message])

  useEffect(() => { void load() }, [load])

  const create = async (values: Record<string, unknown>) => {
    try {
      const scoped = String(values.region_scope ?? '').split(',').map((s) => s.trim()).filter(Boolean)
      await authApi.createUser({ ...values, region_scope: scoped })
      message.success('用户已创建')
      setOpen(false); form.resetFields(); void load()
    } catch (e) {
      message.error(errMsg(e))
    }
  }

  return (
    <div>
      <h1 className="page-title">用户管理</h1>
      <p className="page-desc">
        区域范围留空表示可看全部区域；填写后该用户所有看板、报告、对话查询都只会命中这些区域
      </p>

      <Card extra={<Button type="primary" onClick={() => setOpen(true)}>新建用户</Button>}>
        <Table
          rowKey="id" size="small" pagination={false} dataSource={users}
          columns={[
            { title: '用户名', dataIndex: 'username' },
            { title: '邮箱', dataIndex: 'email', render: (v?: string) => v ?? '-' },
            { title: '角色', dataIndex: 'role', render: (v: string) => <Tag color={ROLE_COLOR[v]}>{ROLE_LABEL[v]}</Tag> },
            {
              title: '可见区域', dataIndex: 'region_scope',
              render: (v: string[]) => v?.length ? v.map((r) => <Tag key={r}>{r}</Tag>) : <Tag>全部</Tag>,
            },
            { title: '状态', dataIndex: 'is_active', render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag> },
            { title: '创建时间', dataIndex: 'created_at', render: (v?: string) => v?.slice(0, 16) ?? '-' },
            {
              title: '操作',
              render: (_, r) => (
                <Button size="small" danger onClick={async () => {
                  await authApi.updateUser(r.id, { is_active: !r.is_active }); void load()
                }}>{r.is_active ? '停用' : '启用'}</Button>
              ),
            },
          ]}
        />
      </Card>

      <Modal title="新建用户" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="创建">
        <Form form={form} layout="vertical" onFinish={create} initialValues={{ role: 'viewer' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}><Input /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}><Input.Password /></Form.Item>
          <Form.Item name="email" label="邮箱"><Input /></Form.Item>
          <Form.Item name="role" label="角色">
            <Select options={[
              { value: 'admin', label: '管理员（全部权限）' },
              { value: 'analyst', label: '分析师（可接入数据、配告警）' },
              { value: 'viewer', label: '查看者（只读）' },
            ]} />
          </Form.Item>
          <Form.Item name="region_scope" label="可见区域" extra="多个用英文逗号分隔，如 east,south；留空表示全部">
            <Input placeholder="留空=全部区域" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
