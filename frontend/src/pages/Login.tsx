import { App as AntApp, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { errMsg, setAccessToken } from '../api/client'
import { useAuth } from '../store/AuthContext'

export default function Login() {
  const { login } = useAuth()
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const { message } = AntApp.useApp()

  const onSubmit = async (values: { username: string; password: string }) => {
    setSubmitting(true)
    try {
      await login(values.username, values.password)
      message.success('登录成功')
    } catch (e) {
      setAccessToken(null)
      // 这里原先是 `(e as Error).message`，axios 错误的 message 是英文
      // "Request failed with status code 500"，直接糊到用户脸上毫无信息量。
      // 统一走 errMsg()：后端有 message 就透传，否则按状态码给中文提示。
      message.error(errMsg(e) || '用户名或密码错误')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', padding: 16 }}>
      <Card style={{ width: 380 }} styles={{ body: { padding: 32 } }}>
        {/* 产品标记：三根账柱，最高那根用唯一的数据 hue */}
        <svg width="30" height="30" viewBox="0 0 22 22" aria-hidden="true" style={{ marginBottom: 12 }}>
          <rect x="2" y="9" width="4" height="9" rx="1" fill="#141414" />
          <rect x="9" y="5" width="4" height="13" rx="1" fill="#141414" />
          <rect x="16" y="2" width="4" height="16" rx="1" fill="#1d5fbf" />
        </svg>
        <Typography.Title level={4} style={{ marginBottom: 4, marginTop: 0 }}>销售智能分析平台</Typography.Title>
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 24, fontSize: 13 }}>
          企业销售数据自动分析与预警
        </Typography.Text>
        <Form form={form} layout="vertical" onFinish={onSubmit} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" autoFocus />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 16, fontSize: 12 }}>
          初始账号 admin / admin123，登录后请立即修改密码
        </Typography.Text>
        {/* 只填表单，不要在这里调登录接口：那会真的走一次 token 轮换，
            把 refresh cookie 换成新的却不落地 access token，反而让会话状态错位 */}
        <Button
          type="link" size="small" style={{ paddingLeft: 0, marginTop: 4 }}
          onClick={() => form.setFieldsValue({ username: 'admin', password: 'admin123' })}
        >
          填入默认账号（演示）
        </Button>
      </Card>
    </div>
  )
}
