import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Layout, Menu, Spin, Tag, Typography } from 'antd'
import {
  BellOutlined, BookOutlined, DashboardOutlined, DatabaseOutlined, FileTextOutlined,
  MessageOutlined, TeamOutlined,
} from '@ant-design/icons'
import { useAuth } from './store/AuthContext'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import DataIngest from './pages/DataIngest'
import Reports from './pages/Reports'
import Alerts from './pages/Alerts'
import Chat from './pages/Chat'
import Knowledge from './pages/Knowledge'
import Users from './pages/Users'

const { Sider, Content, Header } = Layout

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  analyst: '分析师',
  viewer: '查看者',
}

function Shell() {
  const { user, logout } = useAuth()
  const loc = useLocation()
  const navigate = useNavigate()

  const items = [
    { key: '/', icon: <DashboardOutlined />, label: '销售看板' },
    { key: '/ingest', icon: <DatabaseOutlined />, label: '数据接入' },
    { key: '/reports', icon: <FileTextOutlined />, label: 'AI 报告' },
    { key: '/alerts', icon: <BellOutlined />, label: '异常告警' },
    { key: '/chat', icon: <MessageOutlined />, label: '对话助手' },
    { key: '/knowledge', icon: <BookOutlined />, label: '知识库' },
    ...(user?.role === 'admin'
      ? [{ key: '/users', icon: <TeamOutlined />, label: '用户管理' }]
      : []),
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={200} style={{ borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: '18px 16px', fontWeight: 600, fontSize: 15 }}>
          销售智能分析
        </div>
        {/* 关键：走客户端路由。以前这里写的是 window.location.href = key，
            每次点菜单都是一次整页刷新，内存里的 access token 当场归零，
            于是表现为"点哪都要重新登录"。 */}
        <Menu mode="inline" selectedKeys={[loc.pathname]} items={items} onClick={({ key }) => navigate(key)} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff', padding: '0 20px', display: 'flex',
            alignItems: 'center', justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Typography.Text type="secondary">
            {user?.region_scope?.length
              ? `数据范围：${user.region_scope.join(' / ')}`
              : '数据范围：全部区域'}
          </Typography.Text>
          <span>
            <Tag color="blue">{ROLE_LABEL[user?.role ?? 'viewer']}</Tag>
            <Typography.Text strong style={{ marginRight: 12 }}>{user?.username}</Typography.Text>
            <a onClick={logout}>退出</a>
          </span>
        </Header>
        <Content style={{ padding: 20 }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/ingest" element={<DataIngest />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="/users" element={user?.role === 'admin' ? <Users /> : <Navigate to="/" />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  const { user, loading } = useAuth()

  // 会话恢复完成前先挡住：否则子路由组件会抢在 access token 就绪前发请求，
  // 拿到 401 再被判定为未登录，白闪一次登录页。
  if (loading) {
    return (
      <div
        style={{
          display: 'flex', flexDirection: 'column', gap: 12,
          alignItems: 'center', justifyContent: 'center', minHeight: '100vh',
        }}
      >
        <Spin size="large" />
        <Typography.Text type="secondary">正在恢复会话…</Typography.Text>
      </div>
    )
  }

  return user ? <Shell /> : <Login />
}
